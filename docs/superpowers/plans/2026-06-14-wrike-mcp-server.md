# Wrike MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second, independent Wrike MCP server inside the existing Vault-MCP program, surfaced as its own Start/Stop panel in the launcher dashboard and running on its own port.

**Architecture:** A `WrikeRestAPI` httpx client (mirroring `vault_rest_api.py`'s `{"error", "status_code", "data"}` return shape) wrapped by a `FastMCP` instance built in `create_wrike_mcp_server()`. The launcher's `MCPServerController` is generalized to accept a server-factory + port, then instantiated twice (Vault 8765, Wrike 8766). Wrike auth is a permanent bearer token from a new optional `wrike` block in `config.json`; the Wrike server is independent of the Vault session.

**Tech Stack:** Python, `mcp`/`FastMCP`, `httpx` (async + `MockTransport` for tests), `uvicorn`, Tkinter, `pytest` + `pytest-asyncio`.

---

## File Structure

**New files:**
- `wrike_rest_api.py` — `WrikeRestAPI` async client. One responsibility: talk to Wrike v4 and normalize results/errors.
- `wrike_mcp_server.py` — `create_wrike_mcp_server(api, readonly)`. One responsibility: define `wrike_*` MCP tools.
- `tests/__init__.py` — empty, marks the test package.
- `tests/test_wrike_rest_api.py` — client unit tests against `httpx.MockTransport`.
- `tests/test_wrike_mcp_server.py` — tool-registration + readonly-guard tests.
- `requirements-dev.txt` — `pytest`, `pytest-asyncio`.
- `pytest.ini` — `asyncio_mode = auto`.

**Modified files:**
- `config.json.example` — add the optional `wrike` block.
- `gui/launcher.py` — generalize `MCPServerController`; add a reusable `_ServerPanel`; build a Wrike controller + panel; auto-start both.
- `app.py` — headless SSE optionally also starts the Wrike server when a token is present.
- `requirements.txt` — add a comment pointer to `requirements-dev.txt` (no runtime dep change).
- `README.md` — Wrike setup section (token + second Claude endpoint).

**Constant shared across files:** `DEFAULT_BASE_URL = "https://www.wrike.com/api/v4"` is defined in `wrike_rest_api.py` and imported where needed.

---

## Task 1: Test scaffolding (pytest + asyncio)

**Files:**
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Create dev requirements**

`requirements-dev.txt`:
```
# Dev/test-only dependencies (not needed to run the servers).
# Install with:  pip install -r requirements-dev.txt
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 2: Create pytest config**

`pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 3: Create the test package marker**

`tests/__init__.py`: (empty file)

- [ ] **Step 4: Write a smoke test**

`tests/test_smoke.py`:
```python
def test_smoke():
    assert True
```

- [ ] **Step 5: Install dev deps and run**

Run: `python -m pip install -r requirements-dev.txt && python -m pytest tests/test_smoke.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add requirements-dev.txt pytest.ini tests/__init__.py tests/test_smoke.py
git commit -m "test: add pytest scaffolding for Wrike work"
```

---

## Task 2: WrikeRestAPI — request core, auth header, error mapping

**Files:**
- Create: `wrike_rest_api.py`
- Test: `tests/test_wrike_rest_api.py`

Wrike v4 quirk handled here: requests send the bearer token in the
`Authorization` header; complex values (lists/dicts/bools) are JSON-encoded
strings, sent as query params for GET and form fields for POST/PUT. Errors come
back as `{"errorDescription": ...}` with a non-2xx status.

- [ ] **Step 1: Write the failing tests**

`tests/test_wrike_rest_api.py`:
```python
import json
import httpx
import pytest

from wrike_rest_api import WrikeRestAPI, DEFAULT_BASE_URL


def make_api(handler, *, token="test-token", base_url=DEFAULT_BASE_URL):
    return WrikeRestAPI(token=token, base_url=base_url,
                        transport=httpx.MockTransport(handler))


async def test_get_sends_bearer_token_and_returns_data():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"kind": "account", "data": [{"id": "ACC"}]})

    api = make_api(handler)
    result = await api._request("GET", "/account")

    assert result["error"] is False
    assert result["status_code"] == 200
    assert result["data"]["data"] == [{"id": "ACC"}]
    assert seen["auth"] == "Bearer test-token"
    assert seen["url"] == DEFAULT_BASE_URL + "/account"


async def test_error_status_maps_to_friendly_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "not_authorized",
                                         "errorDescription": "Token invalid"})

    api = make_api(handler)
    result = await api._request("GET", "/account")

    assert result["error"] is True
    assert result["status_code"] == 401
    assert "401" in result["data"]
    assert "Token invalid" in result["data"]
    assert "config.json" in result["data"]  # the 401 hint


async def test_complex_values_are_json_encoded_as_form_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"data": [{"id": "T1"}]})

    api = make_api(handler)
    await api._request("POST", "/folders/F1/tasks",
                       data={"title": "Hi", "responsibles": ["KUAA"], "plainText": True})

    # form-encoded; lists/bools JSON-stringified
    assert "title=Hi" in seen["body"]
    assert "responsibles=%5B%22KUAA%22%5D" in seen["body"]  # ["KUAA"] url-encoded
    assert "plainText=true" in seen["body"]


async def test_network_error_is_captured():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    api = make_api(handler)
    result = await api._request("GET", "/account")
    assert result["error"] is True
    assert result["status_code"] == 0
    assert "failed" in result["data"].lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_wrike_rest_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wrike_rest_api'`.

- [ ] **Step 3: Implement the client core**

`wrike_rest_api.py`:
```python
"""
Wrike REST API Client
Wraps the Wrike API v4 endpoints.
Base URL (default): https://www.wrike.com/api/v4
Auth: permanent access token sent as ``Authorization: Bearer <token>``.

All methods return a dict: {"error": bool, "status_code": int, "data": Any}
— the same shape as vault_rest_api.VaultRestAPI, so the MCP tools serialize
results identically.
"""

import json
import logging
from typing import Any, Dict, Optional

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
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_wrike_rest_api.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add wrike_rest_api.py tests/test_wrike_rest_api.py
git commit -m "feat: WrikeRestAPI request core with auth + error mapping"
```

---

## Task 2b: WrikeRestAPI — pagination helper

**Files:**
- Modify: `wrike_rest_api.py`
- Test: `tests/test_wrike_rest_api.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_wrike_rest_api.py`:
```python
async def test_get_all_follows_next_page_token():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if "nextPageToken" not in str(request.url):
            return httpx.Response(200, json={"data": [{"id": "A"}],
                                             "nextPageToken": "tok2"})
        return httpx.Response(200, json={"data": [{"id": "B"}]})

    api = make_api(handler)
    result = await api._get_all("/tasks")

    assert result["error"] is False
    ids = [r["id"] for r in result["data"]["data"]]
    assert ids == ["A", "B"]
    assert result["data"]["count"] == 2
    assert calls["n"] == 2


async def test_get_all_propagates_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errorDescription": "nope"})

    api = make_api(handler)
    result = await api._get_all("/tasks")
    assert result["error"] is True
    assert result["status_code"] == 403
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_wrike_rest_api.py -k get_all -v`
Expected: FAIL — `AttributeError: 'WrikeRestAPI' object has no attribute '_get_all'`.

- [ ] **Step 3: Implement `_get_all`**

Append to the `WrikeRestAPI` class in `wrike_rest_api.py`:
```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_wrike_rest_api.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add wrike_rest_api.py tests/test_wrike_rest_api.py
git commit -m "feat: WrikeRestAPI nextPageToken pagination"
```

---

## Task 3: WrikeRestAPI — endpoint methods

**Files:**
- Modify: `wrike_rest_api.py`
- Test: `tests/test_wrike_rest_api.py`

- [ ] **Step 1: Add failing tests for representative methods**

Append to `tests/test_wrike_rest_api.py`:
```python
def _record_handler(store):
    def handler(request: httpx.Request) -> httpx.Response:
        store["method"] = request.method
        store["path"] = request.url.path
        store["query"] = str(request.url.query.decode())
        store["body"] = request.content.decode()
        return httpx.Response(200, json={"data": [{"id": "OK"}]})
    return handler


async def test_get_task_hits_task_path():
    store = {}
    api = make_api(_record_handler(store))
    await api.get_task("IEAA123")
    assert store["method"] == "GET"
    assert store["path"].endswith("/tasks/IEAA123")


async def test_search_tasks_uses_folder_scope_and_params():
    store = {}
    api = make_api(_record_handler(store))
    await api.search_tasks(title="bolt", folder_id="IEAF9")
    assert store["path"].endswith("/folders/IEAF9/tasks")
    assert "title=bolt" in store["query"]


async def test_create_task_posts_form_fields():
    store = {}
    api = make_api(_record_handler(store))
    await api.create_task("IEAF9", "New task", description="d",
                          responsibles=["KUAA"])
    assert store["method"] == "POST"
    assert store["path"].endswith("/folders/IEAF9/tasks")
    assert "title=New+task" in store["body"]
    assert "responsibles=%5B%22KUAA%22%5D" in store["body"]


async def test_create_comment_posts_text():
    store = {}
    api = make_api(_record_handler(store))
    await api.create_comment("IEAA1", "hello")
    assert store["path"].endswith("/tasks/IEAA1/comments")
    assert "text=hello" in store["body"]


async def test_get_subtasks_reads_subtask_ids_then_batches():
    seq = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seq["n"] += 1
        if seq["n"] == 1:                       # the get_task call
            return httpx.Response(200, json={"data": [{"id": "P", "subTaskIds": ["S1", "S2"]}]})
        # the batch call
        assert request.url.path.endswith("/tasks/S1,S2")
        return httpx.Response(200, json={"data": [{"id": "S1"}, {"id": "S2"}]})

    api = make_api(handler)
    result = await api.get_subtasks("P")
    assert result["error"] is False
    ids = [r["id"] for r in result["data"]["data"]]
    assert ids == ["S1", "S2"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_wrike_rest_api.py -k "task or comment or subtask" -v`
Expected: FAIL — methods not defined.

- [ ] **Step 3: Implement all endpoint methods**

Append to the `WrikeRestAPI` class in `wrike_rest_api.py`:
```python
    # ----- Read --------------------------------------------------------

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

    # ----- Write -------------------------------------------------------

    async def create_task(
        self,
        folder_id: str,
        title: str,
        description: Optional[str] = None,
        status: Optional[str] = None,
        importance: Optional[str] = None,
        start_date: Optional[str] = None,
        due_date: Optional[str] = None,
        responsibles: Optional[list] = None,
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
        add_responsibles: Optional[list] = None,
        remove_responsibles: Optional[list] = None,
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
        return await self._request("PUT", f"/tasks/{task_id}", data=fields)

    async def move_task(
        self,
        task_id: str,
        add_parents: Optional[list] = None,
        remove_parents: Optional[list] = None,
    ) -> Dict[str, Any]:
        return await self._request("PUT", f"/tasks/{task_id}", data={
            "addParents": add_parents, "removeParents": remove_parents,
        })

    # ----- Comments / timelogs ----------------------------------------

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

    # ----- Metadata ----------------------------------------------------

    async def list_custom_fields(self) -> Dict[str, Any]:
        return await self._request("GET", "/customfields")

    async def list_workflows(self) -> Dict[str, Any]:
        return await self._request("GET", "/workflows")

    async def list_access_roles(self) -> Dict[str, Any]:
        return await self._request("GET", "/access_roles")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_wrike_rest_api.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add wrike_rest_api.py tests/test_wrike_rest_api.py
git commit -m "feat: WrikeRestAPI endpoint methods (read/write/comments/timelogs/metadata)"
```

---

## Task 4: Wrike MCP server — tool definitions + readonly guard

**Files:**
- Create: `wrike_mcp_server.py`
- Test: `tests/test_wrike_mcp_server.py`

The server mirrors `mcp_server.py`: `create_wrike_mcp_server()` builds a
`FastMCP`, defines `wrike_*` tools that delegate to `WrikeRestAPI`, and
serializes results with a local `_fmt()`. Write tools check `readonly` first.

- [ ] **Step 1: Write the failing tests**

`tests/test_wrike_mcp_server.py`:
```python
import json
import pytest

from wrike_mcp_server import create_wrike_mcp_server


class FakeAPI:
    """Records calls and returns a canned ok-result."""
    def __init__(self):
        self.calls = []

    def _ok(self, name):
        self.calls.append(name)
        return {"error": False, "status_code": 200, "data": {"data": [name]}}

    async def get_account(self): return self._ok("get_account")
    async def create_task(self, *a, **k):
        self.calls.append(("create_task", a, k))
        return {"error": False, "status_code": 200, "data": {"data": ["t"]}}


async def _tools(mcp):
    return {t.name: t for t in await mcp.list_tools()}


async def test_all_expected_tools_registered():
    mcp = create_wrike_mcp_server(FakeAPI(), readonly=False)
    names = set((await _tools(mcp)).keys())
    expected = {
        "wrike_search_tasks", "wrike_get_task", "wrike_list_folders",
        "wrike_get_folder", "wrike_list_projects", "wrike_get_subtasks",
        "wrike_create_task", "wrike_update_task", "wrike_move_task",
        "wrike_get_comments", "wrike_create_comment",
        "wrike_get_timelogs", "wrike_create_timelog",
        "wrike_list_contacts", "wrike_get_account",
        "wrike_list_custom_fields", "wrike_list_workflows",
        "wrike_list_access_roles",
    }
    assert expected <= names


async def test_readonly_blocks_writes_without_calling_api():
    api = FakeAPI()
    mcp = create_wrike_mcp_server(api, readonly=True)
    result = await mcp.call_tool(
        "wrike_create_task", {"folder_id": "F1", "title": "x"})
    # FastMCP returns (content, structured) — inspect the text payload
    text = result[0][0].text if isinstance(result, tuple) else str(result)
    assert "read-only" in text.lower()
    assert api.calls == []          # API was never touched


async def test_write_allowed_when_not_readonly():
    api = FakeAPI()
    mcp = create_wrike_mcp_server(api, readonly=False)
    await mcp.call_tool("wrike_create_task", {"folder_id": "F1", "title": "x"})
    assert any(c[0] == "create_task" for c in api.calls if isinstance(c, tuple))
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_wrike_mcp_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wrike_mcp_server'`.

- [ ] **Step 3: Implement the server**

`wrike_mcp_server.py`:
```python
"""
Wrike MCP Server
Exposes Wrike API v4 operations as MCP tools via SSE transport.
Built by create_wrike_mcp_server(); run as a second, independent server
alongside the Vault MCP server (see gui/launcher.py / app.py).
"""

import json
import logging
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from wrike_rest_api import WrikeRestAPI

logger = logging.getLogger(__name__)


def create_wrike_mcp_server(api: WrikeRestAPI, readonly: bool = False) -> FastMCP:
    """Build a FastMCP instance wired to a WrikeRestAPI client.

    ``readonly`` makes the create/update/move/comment/timelog tools refuse.
    """
    mcp = FastMCP(
        name="wrike-mcp",
        instructions=(
            "MCP server for Wrike (project management). Authentication uses a "
            "permanent access token from config.json. Tools are prefixed "
            "'wrike_'. IDs are Wrike permalink IDs (e.g. tasks 'IEAA…', folders "
            "'IEAF…', contacts 'KUAA…'). Use wrike_list_folders / wrike_search_tasks "
            "to discover IDs before reading or writing."
        ),
    )

    def _fmt(result: Dict[str, Any]) -> str:
        return json.dumps(result, indent=2, default=str)

    def _readonly_refusal(op: str) -> str:
        return _fmt({
            "error": True,
            "data": (f"Refused: '{op}' is a write operation and the Wrike MCP "
                     "server is in read-only mode (set wrike.readonly=false in "
                     "config.json to enable writes)."),
        })

    # ----- Read --------------------------------------------------------

    @mcp.tool()
    async def wrike_get_account() -> str:
        """Get Wrike account information (id, name, date format, subscription)."""
        return _fmt(await api.get_account())

    @mcp.tool()
    async def wrike_list_contacts(me_only: bool = False) -> str:
        """List contacts/users in the account. Set me_only=true for just the
        token owner."""
        return _fmt(await api.list_contacts(me=me_only))

    @mcp.tool()
    async def wrike_search_tasks(
        title: str = "", status: str = "", folder_id: str = "",
        page_size: int = 100,
    ) -> str:
        """Search/list tasks. Optional title substring, status
        (Active/Completed/Deferred/Cancelled), and folder_id to scope to one
        folder/project."""
        return _fmt(await api.search_tasks(
            title=title or None, status=status or None,
            folder_id=folder_id or None, page_size=page_size))

    @mcp.tool()
    async def wrike_get_task(task_id: str) -> str:
        """Get full detail for one task by its Wrike ID."""
        return _fmt(await api.get_task(task_id))

    @mcp.tool()
    async def wrike_list_folders() -> str:
        """List the folder/project tree (ids, titles, parents)."""
        return _fmt(await api.list_folders())

    @mcp.tool()
    async def wrike_get_folder(folder_id: str) -> str:
        """Get one folder/project's detail by ID."""
        return _fmt(await api.get_folder(folder_id))

    @mcp.tool()
    async def wrike_list_projects() -> str:
        """List folders that are projects (have a project owner/status/dates)."""
        return _fmt(await api.list_projects())

    @mcp.tool()
    async def wrike_get_subtasks(task_id: str) -> str:
        """List the subtasks of a task."""
        return _fmt(await api.get_subtasks(task_id))

    # ----- Write -------------------------------------------------------

    @mcp.tool()
    async def wrike_create_task(
        folder_id: str, title: str, description: str = "", status: str = "",
        importance: str = "", start_date: str = "", due_date: str = "",
        responsibles: Optional[List[str]] = None,
    ) -> str:
        """Create a task in a folder/project. status: Active/Completed/Deferred/
        Cancelled. importance: High/Normal/Low. Dates ISO 'YYYY-MM-DD'.
        responsibles: list of contact IDs."""
        if readonly:
            return _readonly_refusal("wrike_create_task")
        return _fmt(await api.create_task(
            folder_id, title, description=description or None,
            status=status or None, importance=importance or None,
            start_date=start_date or None, due_date=due_date or None,
            responsibles=responsibles or None))

    @mcp.tool()
    async def wrike_update_task(
        task_id: str, title: str = "", description: str = "", status: str = "",
        importance: str = "", start_date: str = "", due_date: str = "",
        add_responsibles: Optional[List[str]] = None,
        remove_responsibles: Optional[List[str]] = None,
    ) -> str:
        """Update a task's fields. Only non-empty fields are sent."""
        if readonly:
            return _readonly_refusal("wrike_update_task")
        return _fmt(await api.update_task(
            task_id, title=title or None, description=description or None,
            status=status or None, importance=importance or None,
            start_date=start_date or None, due_date=due_date or None,
            add_responsibles=add_responsibles or None,
            remove_responsibles=remove_responsibles or None))

    @mcp.tool()
    async def wrike_move_task(
        task_id: str, add_parents: Optional[List[str]] = None,
        remove_parents: Optional[List[str]] = None,
    ) -> str:
        """Move a task between folders by adding/removing parent folder IDs."""
        if readonly:
            return _readonly_refusal("wrike_move_task")
        return _fmt(await api.move_task(
            task_id, add_parents=add_parents or None,
            remove_parents=remove_parents or None))

    # ----- Comments / timelogs ----------------------------------------

    @mcp.tool()
    async def wrike_get_comments(task_id: str) -> str:
        """Get comments on a task."""
        return _fmt(await api.get_comments(task_id))

    @mcp.tool()
    async def wrike_create_comment(task_id: str, text: str) -> str:
        """Post a comment to a task."""
        if readonly:
            return _readonly_refusal("wrike_create_comment")
        return _fmt(await api.create_comment(task_id, text))

    @mcp.tool()
    async def wrike_get_timelogs(task_id: str) -> str:
        """Get time-tracking entries on a task."""
        return _fmt(await api.get_timelogs(task_id))

    @mcp.tool()
    async def wrike_create_timelog(
        task_id: str, hours: float, tracked_date: str, comment: str = "",
    ) -> str:
        """Add a time entry to a task. tracked_date ISO 'YYYY-MM-DD'."""
        if readonly:
            return _readonly_refusal("wrike_create_timelog")
        return _fmt(await api.create_timelog(
            task_id, hours, tracked_date, comment=comment or None))

    # ----- Metadata ----------------------------------------------------

    @mcp.tool()
    async def wrike_list_custom_fields() -> str:
        """List custom field definitions in the account."""
        return _fmt(await api.list_custom_fields())

    @mcp.tool()
    async def wrike_list_workflows() -> str:
        """List workflows and their custom statuses."""
        return _fmt(await api.list_workflows())

    @mcp.tool()
    async def wrike_list_access_roles() -> str:
        """List access roles defined in the account."""
        return _fmt(await api.list_access_roles())

    return mcp
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_wrike_mcp_server.py -v`
Expected: 3 passed. (If the FastMCP `call_tool` return shape differs in the
installed SDK version, adjust the `text` extraction in the test to match — the
assertions on `api.calls` are the load-bearing checks.)

- [ ] **Step 5: Commit**

```bash
git add wrike_mcp_server.py tests/test_wrike_mcp_server.py
git commit -m "feat: Wrike MCP server with wrike_* tools + readonly guard"
```

---

## Task 5: Config — add the `wrike` block

**Files:**
- Modify: `config.json.example`
- Modify: local `config.json` (gitignored — not committed)

- [ ] **Step 1: Add the block to the example**

Edit `config.json.example` so the top-level object also contains (insert after
the `vault` block, keep valid JSON — comma after the `vault` object):
```json
    "wrike": {
        "token": "your-wrike-permanent-access-token-here",
        "base_url": "https://www.wrike.com/api/v4",
        "host": "0.0.0.0",
        "port": 8766,
        "readonly": false
    },
```
Resulting file has `vault`, `wrike`, `server`, `logging` blocks.

- [ ] **Step 2: Mirror the block into local `config.json`**

Add the same `wrike` block to `config.json`. Leave `token` as the placeholder
until the live token is available (Task 9). Do **not** commit `config.json`
(gitignored).

- [ ] **Step 3: Verify JSON parses**

Run: `python -c "import json; json.load(open('config.json.example')); json.load(open('config.json')); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit (example only)**

```bash
git add config.json.example
git commit -m "feat: add optional wrike config block to example"
```

---

## Task 6: Launcher — generalize the controller

**Files:**
- Modify: `gui/launcher.py` (the `MCPServerController` class, ~lines 54-139)

Generalize `MCPServerController` to accept a server-factory callable + port +
display name, instead of hardcoding the Vault server. Vault behavior must stay
identical.

- [ ] **Step 1: Replace the class definition**

Replace the entire `class MCPServerController:` block in `gui/launcher.py` with:
```python
class MCPServerController:
    """Start / stop one SSE MCP server in-process on a background thread.

    Parametrized by ``server_factory`` — a zero-arg callable returning a built
    FastMCP instance — so the same controller drives both the Vault server and
    the Wrike server. ``stop()`` flips uvicorn's ``should_exit`` and joins the
    worker thread; in-flight requests finish first.
    """

    def __init__(
        self,
        server_factory,
        host: str,
        port: int,
        *,
        name: str = "MCP",
        log_level: str = "INFO",
    ) -> None:
        self.server_factory = server_factory
        self.host = host
        self.port = int(port)
        self.name = name
        self.log_level = log_level
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self._last_error: Optional[str] = None

    # ----- Lifecycle -------------------------------------------------------

    def start(self) -> bool:
        if self.is_running():
            return True
        self._last_error = None
        try:
            import uvicorn
        except ImportError as exc:
            self._last_error = f"import failed: {exc}"
            return False

        try:
            mcp = self.server_factory()
            sse_app = mcp.sse_app()
            config = uvicorn.Config(
                app=sse_app, host=self.host, port=self.port,
                log_level=self.log_level.lower(),
                access_log=True,
                log_config=None,
            )
            self._server = uvicorn.Server(config)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"server build failed: {exc}"
            return False

        def runner() -> None:
            try:
                asyncio.run(self._server.serve())
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"server crashed: {exc}"
                logger.exception("%s MCP server crashed", self.name)

        self._thread = threading.Thread(
            target=runner, daemon=True, name=f"{self.name.lower()}-sse")
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._server = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def url(self) -> str:
        host = "localhost" if self.host == "0.0.0.0" else self.host
        return f"http://{host}:{self.port}"

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error
```

- [ ] **Step 2: Update the Vault controller construction**

In `LauncherGUI.__init__`, replace the block that builds `self.mcp_ctrl`
(currently `MCPServerController(self.api, self.vault_id, self.cfg)`) with a
factory-based construction:
```python
        # MCP server controllers (created when their config is present)
        self.mcp_ctrl: Optional[MCPServerController] = None
        if self.api and self.vault_id:
            server_cfg = self.cfg.get("server", {})
            log_level = self.cfg.get("logging", {}).get("level", "INFO")

            def _vault_factory(api=self.api, vault_id=self.vault_id):
                from mcp_server import create_mcp_server
                return create_mcp_server(api=api, vault_id=vault_id)

            self.mcp_ctrl = MCPServerController(
                _vault_factory,
                server_cfg.get("host", "0.0.0.0"),
                server_cfg.get("port", 8765),
                name="Vault",
                log_level=log_level,
            )

        # Wrike controller — independent of the Vault session.
        self.wrike_ctrl: Optional[MCPServerController] = self._build_wrike_ctrl()
```

- [ ] **Step 3: Add the Wrike controller builder method**

Add this method to `LauncherGUI` (near `__init__`):
```python
    def _build_wrike_ctrl(self) -> Optional["MCPServerController"]:
        """Build the Wrike MCP controller from cfg['wrike'] if a token is set.
        Independent of the Vault session — returns None when unconfigured."""
        wrike_cfg = (self.cfg.get("wrike") or {})
        token = wrike_cfg.get("token")
        if not token or token.startswith("your-wrike"):
            return None
        log_level = self.cfg.get("logging", {}).get("level", "INFO")

        def _wrike_factory(wcfg=wrike_cfg):
            from wrike_rest_api import WrikeRestAPI, DEFAULT_BASE_URL
            from wrike_mcp_server import create_wrike_mcp_server
            wapi = WrikeRestAPI(
                token=wcfg["token"],
                base_url=wcfg.get("base_url", DEFAULT_BASE_URL),
            )
            return create_wrike_mcp_server(
                wapi, readonly=bool(wcfg.get("readonly", False)))

        return MCPServerController(
            _wrike_factory,
            wrike_cfg.get("host", "0.0.0.0"),
            wrike_cfg.get("port", 8766),
            name="Wrike",
            log_level=log_level,
        )
```

- [ ] **Step 4: Update the reconnect handler**

In `_handle_signal` (the `reconnect_done` branch), the existing code rebuilds
`self.mcp_ctrl` as `MCPServerController(self.api, self.vault_id, self.cfg)`.
Replace that single construction with the factory form:
```python
                if self.api and self.vault_id:
                    if self.mcp_ctrl and self.mcp_ctrl.is_running():
                        self.mcp_ctrl.stop()
                    server_cfg = self.cfg.get("server", {})
                    log_level = self.cfg.get("logging", {}).get("level", "INFO")

                    def _vault_factory(api=self.api, vault_id=self.vault_id):
                        from mcp_server import create_mcp_server
                        return create_mcp_server(api=api, vault_id=vault_id)

                    self.mcp_ctrl = MCPServerController(
                        _vault_factory,
                        server_cfg.get("host", "0.0.0.0"),
                        server_cfg.get("port", 8765),
                        name="Vault", log_level=log_level,
                    )
```

- [ ] **Step 5: Verify import + construction still works headlessly**

Run: `python -c "import gui.launcher; print('import ok')"`
Expected: `import ok` (no Tk window opened by import).

- [ ] **Step 6: Commit**

```bash
git add gui/launcher.py
git commit -m "refactor: generalize MCPServerController for multiple servers"
```

---

## Task 7: Launcher — Wrike panel + handlers + auto-start

**Files:**
- Modify: `gui/launcher.py`

Mirror the existing MCP panel for Wrike. The simplest low-risk path: add a
dedicated `_build_wrike_panel`, `_refresh_wrike_panel`, and three handlers that
parallel the Vault ones.

- [ ] **Step 1: Call the new panel builder**

In `_build_ui`, add the Wrike panel right after `self._build_mcp_panel()`:
```python
    def _build_ui(self) -> None:
        self._build_header()
        self._build_vault_panel()
        self._build_mcp_panel()
        self._build_wrike_panel()
        self._build_tools_panel()
        self._build_status_bar()
```

- [ ] **Step 2: Add the Wrike panel builder**

Add this method (mirrors `_build_mcp_panel`):
```python
    def _build_wrike_panel(self) -> None:
        card = tk.Frame(self.root, bg=PALE_BLUE,
                        highlightthickness=1, highlightbackground=GRAY_BDR)
        card.pack(fill="x", padx=18, pady=8)

        tk.Label(
            card, text="  WRIKE MCP SERVER",
            bg=DARK_BLUE, fg=WHITE, font=("Arial", 10, "bold"),
            anchor="w", padx=10, pady=6,
        ).pack(fill="x")
        tk.Frame(card, bg=MID_BLUE, height=2).pack(fill="x")

        body = tk.Frame(card, bg=PALE_BLUE, padx=14, pady=10)
        body.pack(fill="x")

        status_row = tk.Frame(body, bg=PALE_BLUE)
        status_row.pack(fill="x")
        self.wrike_status_dot = tk.Label(
            status_row, text="●", bg=PALE_BLUE, fg=DARK_GRAY, font=("Arial", 16))
        self.wrike_status_dot.pack(side="left", padx=(0, 6))
        self.wrike_status_text = tk.Label(
            status_row, text="Stopped", bg=PALE_BLUE, fg=DARK_BLUE,
            font=("Arial", 11, "bold"))
        self.wrike_status_text.pack(side="left")

        self.wrike_open_btn = self._brand_button(
            status_row, "Open in browser", self._on_wrike_open_browser, primary=False)
        self.wrike_open_btn.pack(side="right", padx=(6, 0))
        self.wrike_open_btn.configure(state="disabled")

        self.wrike_stop_btn = self._brand_button(
            status_row, "Stop", self._on_wrike_stop, primary=False)
        self.wrike_stop_btn.pack(side="right", padx=(6, 0))
        self.wrike_stop_btn.configure(state="disabled")

        self.wrike_start_btn = self._brand_button(
            status_row, "Start", self._on_wrike_start, primary=True)
        self.wrike_start_btn.pack(side="right")

        info = tk.Frame(body, bg=PALE_BLUE)
        info.pack(fill="x", pady=(8, 0))
        tk.Label(info, text="Endpoint:", bg=PALE_BLUE, fg=DARK_GRAY,
                 font=("Arial", 9, "bold"), anchor="w", width=12).grid(
                     row=0, column=0, sticky="w")
        self.wrike_url_var = tk.StringVar(value="—")
        tk.Label(info, textvariable=self.wrike_url_var, bg=PALE_BLUE,
                 fg=DARK_BLUE, font=("Consolas", 9), anchor="w").grid(
                     row=0, column=1, sticky="w")
        tk.Label(info, text="SSE:", bg=PALE_BLUE, fg=DARK_GRAY,
                 font=("Arial", 9, "bold"), anchor="w", width=12).grid(
                     row=1, column=0, sticky="w")
        self.wrike_sse_var = tk.StringVar(value="—")
        tk.Label(info, textvariable=self.wrike_sse_var, bg=PALE_BLUE,
                 fg=DARK_BLUE, font=("Consolas", 9), anchor="w").grid(
                     row=1, column=1, sticky="w")
```

- [ ] **Step 3: Add the Wrike refresh method**

Add (mirrors `_refresh_mcp_panel`, plus a "Not configured" state):
```python
    def _refresh_wrike_panel(self) -> None:
        if not self.wrike_ctrl:
            self.wrike_status_dot.configure(fg=DARK_GRAY)
            self.wrike_status_text.configure(
                text="Not configured", fg=DARK_GRAY)
            self.wrike_url_var.set("Add a 'wrike' block with a token to config.json")
            self.wrike_sse_var.set("—")
            self.wrike_start_btn.configure(state="disabled")
            self.wrike_stop_btn.configure(state="disabled")
            self.wrike_open_btn.configure(state="disabled")
            return

        self.wrike_url_var.set(self.wrike_ctrl.url)
        self.wrike_sse_var.set(f"{self.wrike_ctrl.url}/sse")

        if self.wrike_ctrl.is_running():
            self.wrike_status_dot.configure(fg="#1F6B2E")
            self.wrike_status_text.configure(text="Running", fg="#1F6B2E")
            self.wrike_start_btn.configure(state="disabled")
            self.wrike_stop_btn.configure(state="normal")
            self.wrike_open_btn.configure(state="normal")
        else:
            err = self.wrike_ctrl.last_error
            label = "Stopped" if not err else f"Error — {err}"
            color = DARK_GRAY if not err else RUST_ORANGE
            self.wrike_status_dot.configure(fg=color)
            self.wrike_status_text.configure(text=label, fg=color)
            self.wrike_start_btn.configure(state="normal")
            self.wrike_stop_btn.configure(state="disabled")
            self.wrike_open_btn.configure(state="disabled")
```

- [ ] **Step 4: Add the three Wrike handlers**

Add (mirror the Vault handlers):
```python
    def _on_wrike_start(self) -> None:
        if not self.wrike_ctrl:
            messagebox.showwarning(
                "Wrike not configured",
                "Add a 'wrike' block with a permanent access token to "
                "config.json, then restart.", parent=self.root)
            return
        ok = self.wrike_ctrl.start()
        if ok:
            self.status_var.set(f"Wrike MCP server starting on {self.wrike_ctrl.url}")
        else:
            err = self.wrike_ctrl.last_error or "unknown error"
            self.status_var.set(f"Wrike MCP start failed: {err}")
            messagebox.showerror("Wrike MCP start failed", err, parent=self.root)
        self.root.after(400, self._refresh_wrike_panel)

    def _on_wrike_stop(self) -> None:
        if not self.wrike_ctrl:
            return
        self.status_var.set("Stopping Wrike MCP server…")
        self.wrike_ctrl.stop()
        self.status_var.set("Wrike MCP server stopped.")
        self._refresh_wrike_panel()

    def _on_wrike_open_browser(self) -> None:
        if not self.wrike_ctrl or not self.wrike_ctrl.is_running():
            return
        webbrowser.open(self.wrike_ctrl.url)
```

- [ ] **Step 5: Wire refresh + auto-start + close**

In `__init__`, after `self._refresh_mcp_panel()` add `self._refresh_wrike_panel()`.
In `_periodic_status_refresh`, after `self._refresh_mcp_panel()` add
`self._refresh_wrike_panel()`.
In the `auto_start_mcp` block, also auto-start Wrike:
```python
        if auto_start_mcp:
            if self.mcp_ctrl is not None:
                self.root.after(300, self._on_mcp_start)
            if self.wrike_ctrl is not None:
                self.root.after(500, self._on_wrike_start)
```
In `_on_close`, extend the running check to also stop Wrike:
```python
    def _on_close(self) -> None:
        running = [c for c in (self.mcp_ctrl, self.wrike_ctrl)
                   if c is not None and c.is_running()]
        if running:
            confirm = messagebox.askyesno(
                "Stop MCP servers?",
                "An MCP server is running. Closing this window will disconnect "
                "any active MCP clients (Claude Desktop, Claude Code).\n\n"
                "Quit anyway?", parent=self.root, default="no")
            if not confirm:
                return
            for c in running:
                c.stop()
        self.root.destroy()
```

- [ ] **Step 6: Verify import**

Run: `python -c "import gui.launcher; print('ok')"`
Expected: `ok`.

- [ ] **Step 7: Commit**

```bash
git add gui/launcher.py
git commit -m "feat: Wrike MCP server panel in launcher dashboard"
```

---

## Task 8: app.py — headless SSE also serves Wrike

**Files:**
- Modify: `app.py` (`run_sse_headless`)

The GUI path already starts both via the launcher. Make the `--headless` path
also start the Wrike server when a token is present, each on its own port.

- [ ] **Step 1: Replace `run_sse_headless` body to run both servers**

Replace the single-server tail of `run_sse_headless` (from building `mcp` to
`await server.serve()`) with a version that builds a Wrike server too and runs
both uvicorn servers concurrently:
```python
    api = VaultRestAPI(servername=vault_cfg["servername"])
    vault_id = await authenticate(api, vault_cfg)
    mcp = create_mcp_server(api=api, vault_id=vault_id)

    display_host = "localhost" if host == "0.0.0.0" else host
    logger.info("Starting Vault MCP Server  (SSE, headless)")
    logger.info("  SSE endpoint  : http://%s:%d/sse", display_host, port)
    logger.info("  Vault database: %s", vault_cfg["database"])

    servers = [uvicorn.Server(uvicorn.Config(
        app=mcp.sse_app(), host=host, port=port,
        log_level=cfg.get("logging", {}).get("level", "INFO").lower(),
        access_log=True, log_config=None))]

    wrike_cfg = cfg.get("wrike") or {}
    token = wrike_cfg.get("token")
    if token and not token.startswith("your-wrike"):
        from wrike_rest_api import WrikeRestAPI, DEFAULT_BASE_URL
        from wrike_mcp_server import create_wrike_mcp_server
        wapi = WrikeRestAPI(token=token,
                            base_url=wrike_cfg.get("base_url", DEFAULT_BASE_URL))
        wmcp = create_wrike_mcp_server(
            wapi, readonly=bool(wrike_cfg.get("readonly", False)))
        whost = wrike_cfg.get("host", host)
        wport = int(wrike_cfg.get("port", 8766))
        logger.info("Starting Wrike MCP Server (SSE, headless)")
        logger.info("  SSE endpoint  : http://%s:%d/sse",
                    "localhost" if whost == "0.0.0.0" else whost, wport)
        servers.append(uvicorn.Server(uvicorn.Config(
            app=wmcp.sse_app(), host=whost, port=wport,
            log_level=cfg.get("logging", {}).get("level", "INFO").lower(),
            access_log=True, log_config=None)))
    else:
        logger.info("Wrike MCP server not started (no wrike.token in config).")

    await asyncio.gather(*(s.serve() for s in servers))
```

- [ ] **Step 2: Verify import + arg parse**

Run: `python -c "import app; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: headless SSE mode also serves the Wrike MCP server"
```

---

## Task 9: Full test run + live smoke test

**Files:** none (verification only). Requires the real Wrike permanent token.

- [ ] **Step 1: Run the whole unit suite**

Run: `python -m pytest -v`
Expected: all tests pass (smoke + client + server ≈ 15 tests).

- [ ] **Step 2: Put the real token in `config.json`**

Replace `wrike.token` in local `config.json` with the permanent access token.
Set `base_url` to the EU host only if the account is on EU
(`https://app-eu.wrike.com/api/v4`); otherwise leave the default.

- [ ] **Step 3: Live read smoke test (standalone, no MCP)**

Run:
```bash
python -c "import asyncio, json; from wrike_rest_api import WrikeRestAPI; from app import load_config; c=load_config(__import__('pathlib').Path('config.json'))['wrike']; api=WrikeRestAPI(c['token'], c.get('base_url','https://www.wrike.com/api/v4')); print(json.dumps(asyncio.run(api.get_account()), indent=2)[:600])"
```
Expected: `"error": false` and an account record (id, name). A 401 means the
token or `base_url` is wrong.

- [ ] **Step 4: Live list smoke test**

Run the same one-liner but with `api.list_folders()` then `api.search_tasks(page_size=5)`.
Expected: `"error": false` with folder/task records.

- [ ] **Step 5: Launch the dashboard and verify the panel**

Run: `python app.py`
Expected: the launcher opens; the **WRIKE MCP SERVER** panel shows **Running**
on `http://localhost:8766` after auto-start; **MCP SERVER** still runs on 8765.
Click Stop/Start on the Wrike panel and confirm it toggles independently of Vault.

- [ ] **Step 6: Connect Claude and verify tools**

Add the second endpoint to the Claude MCP config (see README in Task 10),
reconnect, and confirm `wrike_get_account` and `wrike_list_folders` return data
through Claude.

- [ ] **Step 7: Commit any fixes**

```bash
git add -A
git commit -m "fix: adjustments from Wrike live smoke test"
```

---

## Task 10: README — Wrike setup docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a Wrike section**

Add a section documenting:
- How to mint a permanent token (Wrike → Apps & Integrations → API →
  Permanent access token → Create token).
- The `wrike` config block and each field (`token`, `base_url`, `host`,
  `port`, `readonly`).
- Data-center note: EU accounts use `https://app-eu.wrike.com/api/v4`.
- The second Claude MCP endpoint: `http://localhost:8766/sse` (alongside the
  Vault endpoint on 8765), with an example entry:
```json
{
  "mcpServers": {
    "vault": { "url": "http://localhost:8765/sse" },
    "wrike": { "url": "http://localhost:8766/sse" }
  }
}
```
- The `readonly` flag and what it blocks.
- The available `wrike_*` tools (one-line list).

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: Wrike MCP server setup and tool reference"
```

---

## Self-Review

**Spec coverage:**
- Permanent-token auth, base_url, readonly → Task 2/3/4/5. ✓
- `{"error","status_code","data"}` shape mirrored → Task 2. ✓
- Pagination via nextPageToken → Task 2b. ✓
- All 18 `wrike_*` tools across read/write/comments-timelogs/metadata → Task 4
  (test asserts the exact set). ✓
- Error mapping 401/403/404/429 → Task 2 (`_ERROR_HINTS`, test on 401). ✓
- Write tools refuse under readonly with no API call → Task 4 (test). ✓
- Second independent server on its own port, own launcher panel, independent of
  Vault session, "Not configured" when no token → Tasks 6–7. ✓
- Generalize `MCPServerController`, preserve Vault behavior → Task 6. ✓
- Headless mode also serves Wrike → Task 8. ✓
- Two uvicorn servers, independent lifecycles → Tasks 6/7/8. ✓
- README + Claude two-endpoint setup → Task 10. ✓
- Testing approach (mocked httpx, readonly refusal, builds all tools) → Tasks
  2/2b/3/4/9. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. The only
intentional placeholder is the `config.json` token value, replaced with the real
token in Task 9 Step 2.

**Type/name consistency:** `WrikeRestAPI(token, base_url, transport)`,
`DEFAULT_BASE_URL`, `_request/_get_all/_encode/_parse/_error_message`,
`create_wrike_mcp_server(api, readonly)`, and `MCPServerController(server_factory,
host, port, *, name, log_level)` are used identically across Tasks 2–8. Tool
names in Task 4's implementation match the set asserted in its test. ✓
