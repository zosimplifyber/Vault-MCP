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
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        # transport is injected by tests (httpx.MockTransport); None in prod.
        self._transport = transport

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
