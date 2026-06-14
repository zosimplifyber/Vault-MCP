"""
Wrike REST API Client
Wraps the Wrike API v4 endpoints.
Base URL (default): https://www.wrike.com/api/v4
Auth: permanent access token sent as ``Authorization: Bearer <token>``.

All methods return a dict: {"error": bool, "status_code": int, "data": Any}
— the same shape as vault_rest_api.VaultRestAPI, so the MCP tools serialize
results identically.

Wrike v4 quirk handled here: complex values (lists/dicts/bools) are
JSON-encoded strings, sent as query params for GET and form fields for
POST/PUT. Errors come back as {"errorDescription": ...} with a non-2xx status.
"""

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://www.wrike.com/api/v4"
MAX_PAGES = 50          # pagination safety cap
REQUEST_TIMEOUT = 30.0

_ERROR_HINTS = {
    401: "Invalid or expired Wrike token (check wrike.token in config.json).",
    403: "Access forbidden — the token's access role lacks permission for this operation.",
    404: "Not found — check the ID.",
    429: "Rate limited by Wrike — slow down and retry.",
}


class WrikeRestAPI:
    """Async client for Wrike API v4."""

    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        allowed_folders: Optional[List[str]] = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        # transport is injected by tests (httpx.MockTransport); None in prod.
        self._transport = transport
        # Folder guard: if set, writes are blocked for tasks located exclusively
        # outside these folders (and their subfolders) unless explicitly allowed.
        self.allowed_folders = list(allowed_folders) if allowed_folders else []
        self._allowed_set: Optional[set] = None      # folder ids in the safe zone
        self._membership: Dict[str, bool] = {}        # task_id -> inside safe zone

    # ------------------------------------------------------------------
    # Encoding / parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode(fields: Dict[str, Any]) -> Dict[str, Any]:
        """Drop None values; JSON-stringify lists/dicts/bools (Wrike v4 wants
        complex params as JSON strings)."""
        out: Dict[str, Any] = {}
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, (dict, list, bool)):
                out[key] = json.dumps(value)
            else:
                out[key] = value
        return out

    @classmethod
    def _error_message(cls, status: int, body: Any) -> str:
        desc = ""
        if isinstance(body, dict):
            desc = body.get("errorDescription") or body.get("error") or ""
        parts = [f"Wrike API error {status}"]
        hint = _ERROR_HINTS.get(status)
        if hint:
            parts.append(hint)
        if desc:
            parts.append(str(desc))
        return ": ".join(parts)

    def _parse(self, resp: httpx.Response) -> Dict[str, Any]:
        status = resp.status_code
        try:
            body: Any = resp.json()
        except (ValueError, json.JSONDecodeError):
            body = resp.text
        if 200 <= status < 300:
            return {"error": False, "status_code": status, "data": body}
        return {"error": True, "status_code": status,
                "data": self._error_message(status, body)}

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = self.base_url + path
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=REQUEST_TIMEOUT
            ) as client:
                resp = await client.request(
                    method, url,
                    params=self._encode(params) if params else None,
                    data=self._encode(data) if data else None,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            logger.warning("Wrike request failed: %s %s — %s", method, path, exc)
            return {"error": True, "status_code": 0,
                    "data": f"HTTP request to Wrike failed: {exc}"}
        return self._parse(resp)

    async def _get_all(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """GET a collection, following ``nextPageToken`` up to MAX_PAGES.
        Returns the merged list under ``data.data`` with a ``data.count``.
        Non-paginated / non-list responses are returned unchanged."""
        page_params: Dict[str, Any] = dict(params or {})
        collected: list = []
        pages = 0
        while True:
            result = await self._request("GET", path, params=page_params)
            if result["error"]:
                return result
            body = result["data"]
            rows = body.get("data") if isinstance(body, dict) else body
            if not isinstance(rows, list):
                return result
            collected.extend(rows)
            pages += 1
            token = body.get("nextPageToken") if isinstance(body, dict) else None
            if not token or pages >= MAX_PAGES:
                break
            page_params = {"nextPageToken": token}
        return {"error": False, "status_code": 200,
                "data": {"data": collected, "count": len(collected)}}

    # ------------------------------------------------------------------
    # Folder guard (write protection)
    # ------------------------------------------------------------------

    async def _ensure_allowed_set(self) -> set:
        """Expand allowed_folders to include all descendant folders, once.
        Empty when no allowlist is configured (guard disabled)."""
        if self._allowed_set is not None:
            return self._allowed_set
        if not self.allowed_folders:
            self._allowed_set = set()
            return self._allowed_set
        f = await self._request("GET", "/folders")
        folders = {x["id"]: x for x in (f["data"].get("data", []))} \
            if not f["error"] and isinstance(f["data"], dict) else {}
        allowed, stack = set(), list(self.allowed_folders)
        while stack:
            fid = stack.pop()
            if fid in allowed:
                continue
            allowed.add(fid)
            fo = folders.get(fid)
            if fo:
                stack.extend(fo.get("childIds", []))
        self._allowed_set = allowed
        return allowed

    def _blocked(self, what: str) -> Dict[str, Any]:
        zone = ", ".join(self.allowed_folders)
        return {"error": True, "status_code": 403, "data": (
            f"Blocked by folder guard: {what} is located exclusively OUTSIDE the "
            f"allowed folders ({zone}) and their subfolders. Ask the user to "
            "confirm this specific out-of-zone edit, then retry the call with "
            "allow_outside=true.")}

    async def prime_folder_guard(self, task_ids: List[str]) -> None:
        """Pre-populate the membership cache for many tasks in one batched GET,
        so subsequent guarded writes don't each fetch parents."""
        if not self.allowed_folders or not task_ids:
            return
        allowed = await self._ensure_allowed_set()
        for i in range(0, len(task_ids), 90):
            chunk = task_ids[i:i + 90]
            res = await self._request("GET", "/tasks/" + ",".join(chunk))
            for t in (res["data"].get("data", []) if not res["error"] else []):
                parents = set(t.get("parentIds", [])) | set(t.get("superParentIds", []))
                self._membership[t["id"]] = bool(parents & allowed)

    async def check_task_access(
        self, task_id: str, allow_outside: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Return a refusal dict if writing this task is blocked, else None."""
        if not self.allowed_folders or allow_outside:
            return None
        allowed = await self._ensure_allowed_set()
        inside = self._membership.get(task_id)
        if inside is None:
            t = await self._request("GET", f"/tasks/{task_id}")
            rows = t["data"].get("data", []) if not t["error"] and isinstance(t["data"], dict) else []
            task = rows[0] if rows else {}
            parents = set(task.get("parentIds", [])) | set(task.get("superParentIds", []))
            inside = bool(parents & allowed)
            self._membership[task_id] = inside
        return None if inside else self._blocked(f"task {task_id}")

    async def check_folder_access(
        self, folder_id: str, allow_outside: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Guard for create-in-folder: the target folder must be in the zone."""
        if not self.allowed_folders or allow_outside:
            return None
        allowed = await self._ensure_allowed_set()
        return None if folder_id in allowed else self._blocked(f"folder {folder_id}")

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_account(self) -> Dict[str, Any]:
        return await self._request("GET", "/account")

    async def list_contacts(self, me: bool = False) -> Dict[str, Any]:
        params = {"me": True} if me else None
        return await self._get_all("/contacts", params)

    async def search_tasks(
        self,
        title: Optional[str] = None,
        status: Optional[str] = None,
        folder_id: Optional[str] = None,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"pageSize": page_size}
        if title:
            params["title"] = title
        if status:
            params["status"] = status
        path = f"/folders/{folder_id}/tasks" if folder_id else "/tasks"
        return await self._get_all(path, params)

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/tasks/{task_id}")

    async def list_folders(self) -> Dict[str, Any]:
        return await self._get_all("/folders")

    async def get_folder(self, folder_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/folders/{folder_id}")

    async def list_projects(self) -> Dict[str, Any]:
        """Folders carrying a ``project`` object. Wrike's /folders tree only
        includes ``project`` when requested via ``fields``; filter client-side."""
        result = await self._get_all("/folders", {"fields": ["project"]})
        if result["error"]:
            return result
        rows = result["data"]["data"]
        projects = [r for r in rows if isinstance(r, dict) and r.get("project")]
        return {"error": False, "status_code": 200,
                "data": {"data": projects, "count": len(projects)}}

    async def get_subtasks(self, task_id: str) -> Dict[str, Any]:
        parent = await self.get_task(task_id)
        if parent["error"]:
            return parent
        body = parent["data"]
        rows = body.get("data") if isinstance(body, dict) else None
        task = rows[0] if rows else {}
        sub_ids = task.get("subTaskIds") or []
        if not sub_ids:
            return {"error": False, "status_code": 200, "data": {"data": []}}
        return await self._request("GET", "/tasks/" + ",".join(sub_ids))

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    @staticmethod
    def _effort_allocation(hours: Optional[float]) -> Optional[Dict[str, Any]]:
        """Build a Wrike effortAllocation object from a duration in hours.
        hours>0 -> a 'Basic' total-effort allocation (totalEffort is minutes);
        hours==0 -> clears effort ('None'); None -> leave effort untouched."""
        if hours is None:
            return None
        if hours <= 0:
            return {"mode": "None"}
        return {"mode": "Basic", "totalEffort": int(round(hours * 60))}

    async def create_task(
        self,
        folder_id: str,
        title: str,
        description: Optional[str] = None,
        status: Optional[str] = None,
        importance: Optional[str] = None,
        start_date: Optional[str] = None,
        due_date: Optional[str] = None,
        responsibles: Optional[List[str]] = None,
        custom_fields: Optional[List[Dict[str, str]]] = None,
        effort_hours: Optional[float] = None,
    ) -> Dict[str, Any]:
        fields: Dict[str, Any] = {
            "title": title, "description": description,
            "status": status, "importance": importance,
            "responsibles": responsibles,
        }
        if start_date or due_date:
            dates: Dict[str, Any] = {}
            if start_date:
                dates["start"] = start_date
            if due_date:
                dates["due"] = due_date
            fields["dates"] = dates
        if custom_fields:
            fields["customFields"] = custom_fields
        ea = self._effort_allocation(effort_hours)
        if ea is not None:
            fields["effortAllocation"] = ea
        return await self._request("POST", f"/folders/{folder_id}/tasks", data=fields)

    async def update_task(
        self,
        task_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        importance: Optional[str] = None,
        start_date: Optional[str] = None,
        due_date: Optional[str] = None,
        add_responsibles: Optional[List[str]] = None,
        remove_responsibles: Optional[List[str]] = None,
        custom_fields: Optional[List[Dict[str, str]]] = None,
        effort_hours: Optional[float] = None,
    ) -> Dict[str, Any]:
        fields: Dict[str, Any] = {
            "title": title, "description": description,
            "status": status, "importance": importance,
            "addResponsibles": add_responsibles,
            "removeResponsibles": remove_responsibles,
        }
        if start_date or due_date:
            dates: Dict[str, Any] = {}
            if start_date:
                dates["start"] = start_date
            if due_date:
                dates["due"] = due_date
            fields["dates"] = dates
        if custom_fields:
            fields["customFields"] = custom_fields
        ea = self._effort_allocation(effort_hours)
        if ea is not None:
            fields["effortAllocation"] = ea
        return await self._request("PUT", f"/tasks/{task_id}", data=fields)

    async def set_task_fields_by_name(
        self,
        task_id: str,
        fields: Dict[str, Any],
        effort_hours: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Set custom fields by human NAME (resolving ids + validating/normalizing
        values) and optionally native effort. Atomic-ish: if any value can't be
        resolved, nothing is written. Returns the update result with an 'applied'
        summary of what was set."""
        from wrike_fields import find_field_def, resolve_field_values

        defs_res = await self.list_custom_fields()
        if defs_res["error"]:
            return defs_res
        field_defs = (defs_res.get("data") or {}).get("data", [])

        # Fetch contacts only when a requested field is a Contacts-type field.
        need_contacts = any(
            (find_field_def(field_defs, n) or {}).get("type") == "Contacts"
            for n in fields)
        contacts: List[Dict[str, Any]] = []
        me_id = ""
        if need_contacts:
            cres = await self.list_contacts()
            if not cres["error"]:
                contacts = (cres.get("data") or {}).get("data", [])
            mres = await self.list_contacts(me=True)
            if not mres["error"]:
                rows = (mres.get("data") or {}).get("data", [])
                me_id = rows[0]["id"] if rows else ""

        custom_fields, errors, applied = resolve_field_values(
            field_defs, contacts, me_id, fields)
        if errors:
            return {"error": True, "status_code": 0,
                    "data": {"message": "No changes written — some fields could "
                             "not be resolved.", "errors": errors,
                             "would_apply": applied}}
        if not custom_fields and effort_hours is None:
            return {"error": True, "status_code": 0,
                    "data": "No resolvable fields or effort provided."}

        result = await self.update_task(
            task_id, custom_fields=custom_fields or None, effort_hours=effort_hours)
        if not result["error"]:
            result["data"] = {"applied": applied, "effort_hours": effort_hours,
                              "task": result["data"]}
        return result

    async def move_task(
        self,
        task_id: str,
        add_parents: Optional[List[str]] = None,
        remove_parents: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return await self._request("PUT", f"/tasks/{task_id}", data={
            "addParents": add_parents, "removeParents": remove_parents,
        })

    # ------------------------------------------------------------------
    # Comments / timelogs
    # ------------------------------------------------------------------

    async def get_comments(self, task_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/tasks/{task_id}/comments")

    async def create_comment(
        self, task_id: str, text: str, plain_text: bool = True
    ) -> Dict[str, Any]:
        return await self._request("POST", f"/tasks/{task_id}/comments",
                                   data={"text": text, "plainText": plain_text})

    async def get_timelogs(self, task_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/tasks/{task_id}/timelogs")

    async def create_timelog(
        self, task_id: str, hours: float, tracked_date: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._request("POST", f"/tasks/{task_id}/timelogs", data={
            "hours": hours, "trackedDate": tracked_date, "comment": comment,
        })

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    async def list_custom_fields(self) -> Dict[str, Any]:
        return await self._request("GET", "/customfields")

    async def list_workflows(self) -> Dict[str, Any]:
        return await self._request("GET", "/workflows")

    async def list_access_roles(self) -> Dict[str, Any]:
        return await self._request("GET", "/access_roles")
