import json

import httpx
import pytest

from wrike_rest_api import WrikeRestAPI, DEFAULT_BASE_URL


def make_api(handler, *, token="test-token", base_url=DEFAULT_BASE_URL):
    return WrikeRestAPI(token=token, base_url=base_url,
                        transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Request core
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Endpoint methods
# ---------------------------------------------------------------------------

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


async def test_update_task_sends_custom_fields_and_effort():
    store = {}
    api = make_api(_record_handler(store))
    await api.update_task(
        "IEAA1",
        custom_fields=[{"id": "CF1", "value": "Medium-High"}],
        effort_hours=8)
    body = store["body"]
    assert "customFields=" in body
    assert "CF1" in body and "Medium-High" in body
    # effortAllocation -> 8h == 480 minutes, mode Basic
    assert "effortAllocation=" in body
    assert "480" in body
    assert "Basic" in body


def test_effort_allocation_helper_minutes_and_clear():
    from wrike_rest_api import WrikeRestAPI as W
    assert W._effort_allocation(None) is None
    assert W._effort_allocation(0) == {"mode": "None"}
    assert W._effort_allocation(8) == {"mode": "Basic", "totalEffort": 480}
    assert W._effort_allocation(0.25) == {"mode": "Basic", "totalEffort": 15}


async def test_create_task_passes_custom_fields_through():
    store = {}
    api = make_api(_record_handler(store))
    await api.create_task(
        "IEAF9", "t",
        custom_fields=[{"id": "CFOWNER", "value": "KUASHWPR"}])
    assert "customFields=" in store["body"]
    assert "CFOWNER" in store["body"] and "KUASHWPR" in store["body"]


async def test_create_folder_posts_title_to_parent():
    store = {}
    api = make_api(_record_handler(store))
    await api.create_folder("IEAF1", "Step Test")
    assert store["method"] == "POST"
    assert store["path"].endswith("/folders/IEAF1/folders")
    assert "title=Step+Test" in store["body"]


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


async def test_create_task_sends_super_tasks_when_given_a_parent():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"data": [{"id": "IEAASUB"}]})

    api = make_api(handler)
    await api.create_task("IEAF1", "1. Purchasing", super_task_ids=["IEAAPARENT"])

    assert "superTasks=%5B%22IEAAPARENT%22%5D" in seen["body"]


async def test_create_task_omits_super_tasks_when_not_given():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"data": [{"id": "IEAA1"}]})

    api = make_api(handler)
    await api.create_task("IEAF1", "Standalone")

    assert "superTasks" not in seen["body"]
