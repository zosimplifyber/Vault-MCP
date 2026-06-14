import httpx
import pytest

from wrike_rest_api import WrikeRestAPI

FOLDERS = {"data": [
    {"id": "F_ALLOWED", "title": "Allowed", "childIds": ["F_SUB"]},
    {"id": "F_SUB", "title": "Sub", "childIds": []},
    {"id": "F_OTHER", "title": "Other", "childIds": []},
]}
TASKS = {
    "T_IN": {"id": "T_IN", "parentIds": ["F_SUB"]},        # inside (descendant)
    "T_OUT": {"id": "T_OUT", "parentIds": ["F_OTHER"]},    # exclusively outside
    "T_BOTH": {"id": "T_BOTH", "parentIds": ["F_OTHER", "F_ALLOWED"]},  # partly inside
}


def handler(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    if p.endswith("/folders"):
        return httpx.Response(200, json=FOLDERS)
    if "/tasks/" in p:
        ids = p.rsplit("/tasks/", 1)[1].split(",")
        return httpx.Response(200, json={"data": [TASKS[i] for i in ids if i in TASKS]})
    return httpx.Response(404, json={"errorDescription": "nf"})


def make(allowed):
    return WrikeRestAPI("tok", allowed_folders=allowed,
                        transport=httpx.MockTransport(handler))


async def test_guard_allows_inside():
    assert await make(["F_ALLOWED"]).check_task_access("T_IN") is None


async def test_guard_blocks_outside():
    r = await make(["F_ALLOWED"]).check_task_access("T_OUT")
    assert r and r["error"] and r["status_code"] == 403
    assert "Blocked by folder guard" in r["data"]
    assert "allow_outside=true" in r["data"]


async def test_partly_inside_is_allowed():
    assert await make(["F_ALLOWED"]).check_task_access("T_BOTH") is None


async def test_allow_outside_override():
    assert await make(["F_ALLOWED"]).check_task_access("T_OUT", allow_outside=True) is None


async def test_guard_disabled_without_allowlist():
    assert await make(None).check_task_access("T_OUT") is None


async def test_folder_create_guard():
    api = make(["F_ALLOWED"])
    assert await api.check_folder_access("F_SUB") is None
    r = await api.check_folder_access("F_OTHER")
    assert r and r["status_code"] == 403


async def test_prime_folder_guard_caches_membership():
    api = make(["F_ALLOWED"])
    await api.prime_folder_guard(["T_IN", "T_OUT"])
    assert api._membership == {"T_IN": True, "T_OUT": False}
