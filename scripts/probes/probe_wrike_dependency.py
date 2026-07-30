# scripts/probes/probe_wrike_dependency.py
"""One-off probe: confirm the Wrike dependency request body and the default
status filter on a folder's task list.

Two unknowns this answers, both load-bearing for wrike_mfg_tasks.py:

1. What POST /tasks/{id}/dependencies wants. Wrike's docs describe
   predecessorId / successorId / relationType, but the accepted spelling of
   relationType ("FinishToStart" vs "Finish-to-Start") is not something the
   codebase can tell us. Confirmed answer: "FinishToStart" (exact case), on
   POST /tasks/{successorId}/dependencies with body
   {"predecessorId": <id>, "relationType": "FinishToStart"}.
   Gotcha found while probing: bare tasks (no dates) reject the *correct*
   spelling with 400 "Operation is not allowed due to invalid task
   scheduling type" — a different error than the "Parameter 'relationType'
   value is invalid" you get for a wrong spelling. Wrike only allows a
   dependency once both tasks have a start/due date (a schedulable date
   range), so this probe creates both throwaway tasks with dates.
2. Whether GET /folders/{id}/tasks returns COMPLETED tasks when no status
   param is sent. The re-run guard skips a supplier whose order already
   exists; if completed tasks are filtered out by default, a finished order
   would be recreated on the next run.

Creates throwaway tasks in the folder you pass, then deletes them. Cleanup
runs in a `finally` block so a mid-probe exception still removes both tasks.

    python scripts/probes/probe_wrike_dependency.py IEAF...FOLDERID
"""
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from wrike_rest_api import WrikeRestAPI, DEFAULT_BASE_URL  # noqa: E402

# Spellings tried on the "normal" shape: POST /tasks/{successorId}/dependencies
# with body {predecessorId, relationType}. "FinishToStart" is the confirmed
# answer (kept first so it's tried, and accepted, before the wrong spellings).
RELATION_SPELLINGS = ["FinishToStart", "Finish-to-Start", "finish_to_start"]

# Dependencies require both tasks to already have a start/due date (a
# schedulable date range) — without dates Wrike rejects even the correctly
# spelled relationType with "invalid task scheduling type". Use a fixed
# future range so the probe is deterministic.
PROBE_START_DATE = "2026-08-01"
PROBE_DUE_DATE = "2026-08-06"


def _rows(resp):
    data = resp.get("data")
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            return inner
    return []


async def _try_dependency_shapes(api: WrikeRestAPI, a_id: str, b_id: str):
    """Try request shapes until Wrike accepts one. Returns
    (accepted_description, dependency_id) or (None, None) if all fail."""

    # Shape 1: POST /tasks/{successor}/dependencies {predecessorId, relationType}
    for relation in RELATION_SPELLINGS:
        body = {"predecessorId": a_id, "relationType": relation}
        resp = await api._request(
            "POST", f"/tasks/{b_id}/dependencies", data=body)
        print(f"  POST /tasks/{{successor}}/dependencies "
              f"predecessorId+relationType={relation!r} -> "
              f"error={resp['error']} status={resp['status_code']} "
              f"data={resp['data']}")
        if not resp["error"]:
            dep_id = None
            if isinstance(resp["data"], dict):
                rows = resp["data"].get("data")
                if isinstance(rows, list) and rows:
                    dep_id = rows[0].get("id")
            return (f"POST /tasks/{{successorId}}/dependencies "
                    f"body={body!r}", dep_id)

    # Shape 2: POST /tasks/{predecessor}/dependencies {successorId, relationType}
    for relation in RELATION_SPELLINGS:
        body = {"successorId": b_id, "relationType": relation}
        resp = await api._request(
            "POST", f"/tasks/{a_id}/dependencies", data=body)
        print(f"  POST /tasks/{{predecessor}}/dependencies "
              f"successorId+relationType={relation!r} -> "
              f"error={resp['error']} status={resp['status_code']} "
              f"data={resp['data']}")
        if not resp["error"]:
            dep_id = None
            if isinstance(resp["data"], dict):
                rows = resp["data"].get("data")
                if isinstance(rows, list) and rows:
                    dep_id = rows[0].get("id")
            return (f"POST /tasks/{{predecessorId}}/dependencies "
                    f"body={body!r}", dep_id)

    # Shape 3: relationType omitted entirely (let Wrike default it) on both
    # endpoint directions.
    for path, body in (
        (f"/tasks/{b_id}/dependencies", {"predecessorId": a_id}),
        (f"/tasks/{a_id}/dependencies", {"successorId": b_id}),
    ):
        resp = await api._request("POST", path, data=body)
        print(f"  POST {path} body={body!r} (no relationType) -> "
              f"error={resp['error']} status={resp['status_code']} "
              f"data={resp['data']}")
        if not resp["error"]:
            dep_id = None
            if isinstance(resp["data"], dict):
                rows = resp["data"].get("data")
                if isinstance(rows, list) and rows:
                    dep_id = rows[0].get("id")
            return f"POST {path} body={body!r}", dep_id

    return None, None


async def main(folder_id: str) -> None:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    wcfg = cfg["wrike"]
    api = WrikeRestAPI(token=wcfg["token"],
                       base_url=wcfg.get("base_url", DEFAULT_BASE_URL))

    a_id = None
    b_id = None
    undeleted = []
    try:
        print("== creating two throwaway tasks (with dates -- dependencies "
              "require a schedulable date range) ==")
        a = await api.create_task(folder_id, "PROBE predecessor",
                                   start_date=PROBE_START_DATE,
                                   due_date=PROBE_START_DATE)
        b = await api.create_task(folder_id, "PROBE successor",
                                   start_date=PROBE_DUE_DATE,
                                   due_date=PROBE_DUE_DATE)
        if a["error"] or b["error"]:
            print(f"  FAILED to create tasks: a={a} b={b}")
            return
        a_id = _rows(a)[0]["id"]
        b_id = _rows(b)[0]["id"]
        print(f"  predecessor={a_id}  successor={b_id}")

        print("== dependency body ==")
        accepted, dep_id = await _try_dependency_shapes(api, a_id, b_id)
        if accepted:
            print(f"  ACCEPTED SHAPE: {accepted}")
        else:
            print("  NONE of the tried shapes were accepted.")

        print("== verifying the dependency actually exists ==")
        for tid, label in ((b_id, "successor"), (a_id, "predecessor")):
            dep_resp = await api._request("GET", f"/tasks/{tid}/dependencies")
            print(f"  GET /tasks/{tid}/dependencies ({label}) -> "
                  f"error={dep_resp['error']} status={dep_resp['status_code']} "
                  f"data={dep_resp['data']}")

        print("== completed tasks in an unfiltered folder listing ==")
        upd = await api.update_task(a_id, status="Completed")
        print(f"  update_task(status=Completed) -> error={upd['error']} "
              f"status={upd['status_code']}")
        listing = await api.search_tasks(folder_id=folder_id)
        titles = {r.get("title"): r.get("status") for r in _rows(listing)}
        present = "PROBE predecessor" in titles
        print(f"  'PROBE predecessor' present after completion: {present}")
        print(f"  its status in the listing: {titles.get('PROBE predecessor')}")

    finally:
        print("== cleaning up ==")
        for task_id in (b_id, a_id):
            if not task_id:
                continue
            resp = await api._request("DELETE", f"/tasks/{task_id}")
            print(f"  delete {task_id}: error={resp['error']} "
                  f"status={resp['status_code']}")
            if resp["error"]:
                undeleted.append(task_id)
        if undeleted:
            print(f"  COULD NOT DELETE: {undeleted} -- remove manually.")
        else:
            print("  both throwaway tasks deleted.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: probe_wrike_dependency.py <folderId>")
    asyncio.run(main(sys.argv[1]))
