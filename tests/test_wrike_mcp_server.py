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

    async def get_account(self):
        return self._ok("get_account")

    async def create_task(self, *a, **k):
        self.calls.append(("create_task", a, k))
        return {"error": False, "status_code": 200, "data": {"data": ["t"]}}


async def _tools(mcp):
    return {t.name: t for t in await mcp.list_tools()}


def _text(result):
    """Extract text from a FastMCP call_tool result across SDK return shapes."""
    # Newer FastMCP returns (content_list, structured_dict)
    if isinstance(result, tuple):
        content = result[0]
    else:
        content = result
    if isinstance(content, list) and content:
        first = content[0]
        return getattr(first, "text", str(first))
    return str(result)


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
    text = _text(result)
    assert "read-only" in text.lower()
    assert api.calls == []          # API was never touched


async def test_write_allowed_when_not_readonly():
    api = FakeAPI()
    mcp = create_wrike_mcp_server(api, readonly=False)
    await mcp.call_tool("wrike_create_task", {"folder_id": "F1", "title": "x"})
    assert any(isinstance(c, tuple) and c[0] == "create_task" for c in api.calls)


async def test_read_tool_returns_api_payload():
    api = FakeAPI()
    mcp = create_wrike_mcp_server(api, readonly=True)
    result = await mcp.call_tool("wrike_get_account", {})
    text = _text(result)
    assert "get_account" in text
    assert "get_account" in api.calls
