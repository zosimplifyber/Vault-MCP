# BOM → Manufacturing Tasks (Wrike) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read a generated purchasing workbook, reconcile each part's supplier against Vault, group the parts into one order per supplier, and create a Wrike parent task with dependency-chained Purchasing / Manufacturing / Shipping subtasks for each.

**Architecture:** A reader added to `bom_purchasing.py` (beside the writer, sharing its layout constants), a headless engine `wrike_mfg_tasks.py` that reconciles / groups / schedules / renders / creates, a Tk dialog in `gui/wrike_mfg_tasks.py`, and two additions to `wrike_rest_api.py` (subtask parents, dependencies). The engine never imports Tk, so every stage is unit-testable without a display or a network.

**Tech Stack:** Python 3.10+, pandas, openpyxl, httpx (async), pytest with `asyncio_mode = auto`, Tkinter.

**Spec:** `docs/superpowers/specs/2026-07-29-wrike-mfg-tasks-design.md`

---

## Conventions used throughout

- Tests live in `tests/`, run with `python -m pytest` from the project root. `pytest.ini` sets `asyncio_mode = auto`, so async tests need **no** `@pytest.mark.asyncio` marker.
- `tests/conftest.py` already puts the project root on `sys.path`.
- Every Vault and Wrike API method returns `{"error": bool, "status_code": int, "data": Any}`.
- Wrike list endpoints go through `_get_all`, so rows are at `resp["data"]["data"]`. Single `_request` calls return the raw body, so a created task is at `resp["data"]["data"][0]`.
- Commit after every task.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `scripts/probes/probe_wrike_dependency.py` | **New.** One-off live probe: confirms the dependency request body and whether completed tasks come back unfiltered. |
| `bom_purchasing.py` | **Modify.** Export `HDR_ROW` / `UNMATCHED_NOTE_PREFIX`; add `read_purchasing_sheet()` — the inverse of `build_purchasing_sheet`. |
| `wrike_mfg_tasks.py` | **New engine.** `OrderPart`, `ReconcileRow`, `SupplierOrder`, and the stages: load → reconcile → group → schedule → render → create. No Tk, no direct HTTP. |
| `wrike_rest_api.py` | **Modify.** `create_task(super_task_ids=...)`, `add_dependency()`. |
| `gui/wrike_mfg_tasks.py` | **New.** Tk `Toplevel` dialog; worker thread + `queue.Queue`. |
| `gui/launcher.py` | **Modify.** One `_tool_row` tile and its handler. |
| `config.json.example` | **Modify.** `wrike.mfg_tasks` defaults block. |
| `README.md` | **Modify.** Tool section + config table rows. |
| `tests/test_purchasing_sheet_read.py` | **New.** Reader, including the round trip through the writer. |
| `tests/test_wrike_mfg_tasks.py` | **New.** Engine: load, reconcile, group, schedule, render, create, re-run detection. |
| `tests/test_wrike_rest_api.py` | **Modify.** The two new client methods. |

---

## Task 1: Live probe — pin down the dependency body

The spec's one unconfirmed API shape. Do this first; Tasks 10 and 11 depend on the answer.

**Files:**
- Create: `scripts/probes/probe_wrike_dependency.py`

- [ ] **Step 1: Write the probe**

```python
# scripts/probes/probe_wrike_dependency.py
"""One-off probe: confirm the Wrike dependency request body and the default
status filter on a folder's task list.

Two unknowns this answers, both load-bearing for wrike_mfg_tasks.py:

1. What POST /tasks/{id}/dependencies wants. Wrike's docs describe
   predecessorId / successorId / relationType, but the accepted spelling of
   relationType ("FinishToStart" vs "Finish-to-Start") is not something the
   codebase can tell us.
2. Whether GET /folders/{id}/tasks returns COMPLETED tasks when no status
   param is sent. The re-run guard skips a supplier whose order already
   exists; if completed tasks are filtered out by default, a finished order
   would be recreated on the next run.

Creates throwaway tasks in the folder you pass, then deletes them.

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

RELATION_SPELLINGS = ["FinishToStart", "Finish-to-Start", "finish_to_start"]


def _rows(resp):
    data = resp.get("data")
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            return inner
    return []


async def main(folder_id: str) -> None:
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    wcfg = cfg["wrike"]
    api = WrikeRestAPI(token=wcfg["token"],
                       base_url=wcfg.get("base_url", DEFAULT_BASE_URL))

    print("== creating two throwaway tasks ==")
    a = await api.create_task(folder_id, "PROBE predecessor")
    b = await api.create_task(folder_id, "PROBE successor")
    a_id = _rows(a)[0]["id"]
    b_id = _rows(b)[0]["id"]
    print(f"  predecessor={a_id}  successor={b_id}")

    print("== dependency body ==")
    for relation in RELATION_SPELLINGS:
        resp = await api._request(
            "POST", f"/tasks/{b_id}/dependencies",
            data={"predecessorId": a_id, "relationType": relation},
        )
        print(f"  relationType={relation!r} -> error={resp['error']} "
              f"status={resp['status_code']} data={resp['data']}")
        if not resp["error"]:
            print(f"  ACCEPTED SPELLING: {relation!r}")
            break

    print("== completed tasks in an unfiltered folder listing ==")
    await api.update_task(a_id, status="Completed")
    listing = await api.search_tasks(folder_id=folder_id)
    titles = {r.get("title"): r.get("status") for r in _rows(listing)}
    print(f"  'PROBE predecessor' present after completion: "
          f"{'PROBE predecessor' in titles}")
    print(f"  its status in the listing: {titles.get('PROBE predecessor')}")

    print("== cleaning up ==")
    for task_id in (b_id, a_id):
        resp = await api._request("DELETE", f"/tasks/{task_id}")
        print(f"  delete {task_id}: error={resp['error']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: probe_wrike_dependency.py <folderId>")
    asyncio.run(main(sys.argv[1]))
```

- [ ] **Step 2: Run it against a scratch Wrike folder**

Run: `python scripts/probes/probe_wrike_dependency.py <a scratch folder id>`

Get the folder id from `wrike_list_folders`, or create a throwaway project in Wrike first. Expected: one `relationType` spelling reports `error=False`, and the completed-task line prints `True` or `False`.

**Record both answers** — Task 10 hardcodes the accepted spelling, Task 11 depends on the completed-task answer.

If the probe cannot be run (no token, no network), proceed with `"FinishToStart"` and mark the docstring in Task 10 `UNVERIFIED — probe did not run`. Do not silently assume it was confirmed.

- [ ] **Step 3: Commit**

```bash
git add scripts/probes/probe_wrike_dependency.py
git commit -m "probe(wrike): confirm dependency body and folder status default"
```

---

## Task 2: Export the sheet-layout constants

Pure refactor, no behavior change. `HDR_ROW` is currently a bare `3` inside `build_purchasing_sheet` and a duplicated literal in `tests/test_purchasing_sheet_layout.py`; the reader needs both it and the note prefix.

**Files:**
- Modify: `bom_purchasing.py:501` (the `HDR_ROW = 3` local), `bom_purchasing.py:598-609` (the note text)
- Modify: `tests/test_purchasing_sheet_layout.py:16`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_purchasing_sheet_layout.py`, directly under the imports:

```python
def test_the_layout_constants_are_module_level():
    """The reader in read_purchasing_sheet() has to agree with the writer
    about where the header row is and how the trailing note starts. Sharing
    the constants is what keeps them from drifting apart."""
    assert bp.HDR_ROW == 3
    assert bp.UNMATCHED_NOTE_PREFIX == "Unmatched ("
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_purchasing_sheet_layout.py::test_the_layout_constants_are_module_level -v`
Expected: FAIL with `AttributeError: module 'bom_purchasing' has no attribute 'HDR_ROW'`

- [ ] **Step 3: Move the constants to module level**

In `bom_purchasing.py`, add below `VENDOR_COL_WIDTHS` (around line 124):

```python
# The Purchasing tab's layout, shared by the writer and by
# read_purchasing_sheet(). Row 1 is the assembly title bar, row 2 the
# generated-on date, row 3 the column headers.
PURCHASING_SHEET_NAME = "Purchasing"
HDR_ROW = 3

# The writer appends this note below the data table so a $0 line reads as "no
# price found" rather than "free". The reader stops when it sees it.
UNMATCHED_NOTE_PREFIX = "Unmatched ("
```

In `build_purchasing_sheet`, delete the local `HDR_ROW = 3` on line 501 (leave the comment) and change line 478 to use the constant:

```python
    ws.title = PURCHASING_SHEET_NAME
```

In the unmatched-note block around line 605, build the text from the constant:

```python
        nc.value = (f"{UNMATCHED_NOTE_PREFIX}{len(unmatched_nums)}) — "
                    f"no price in reference: " + ", ".join(unmatched_nums))
```

In `tests/test_purchasing_sheet_layout.py`, replace the local constant on line 16:

```python
HDR_ROW = bp.HDR_ROW          # Purchasing tab: title, date, then headers
```

- [ ] **Step 4: Run the full sheet suite to verify nothing moved**

Run: `python -m pytest tests/test_purchasing_sheet_layout.py tests/test_bom_purchasing_inventor.py -v`
Expected: PASS, all tests including the new one.

- [ ] **Step 5: Commit**

```bash
git add bom_purchasing.py tests/test_purchasing_sheet_layout.py
git commit -m "refactor(purchasing): export HDR_ROW and the unmatched-note prefix"
```

---

## Task 3: `read_purchasing_sheet` — the round trip

The headline test: what the writer writes, the reader reads back.

**Files:**
- Modify: `bom_purchasing.py`
- Create: `tests/test_purchasing_sheet_read.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_purchasing_sheet_read.py
"""Reading a generated purchasing workbook back in.

The headline test is a round trip: build a sheet with build_purchasing_sheet,
read it with read_purchasing_sheet, and check the rows survive. That is the
whole reason the reader lives beside the writer — it fails the day a column
moves.
"""
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bom_purchasing as bp  # noqa: E402

BUY = {"Number": "SF-000067", "Title": "SF-000067", "Row Order": "1",
       "Item Qty": 2, "Units": "Each", "Description (Item,CO)": "pull handle",
       "Source": "Buy", "Vendor": "Acme", "Cost Per": 1.5,
       "Lead Time (Business Days)": 5}
MAKE = {"Number": "CD-001200", "Title": "CD-001200", "Row Order": "2",
        "Item Qty": 1, "Units": "Each", "Description (Item,CO)": "adapter plate",
        "Source": "Make", "Vendor": "Machine Shop", "Cost Per": 40.0,
        "Lead Time (Business Days)": 15}


def _df(rows):
    df, err = bp.coerce_bom_dataframe(pd.DataFrame(rows))
    assert err is None, err
    return df


def _sheet(tmp_path, rows, assembly="CD-001608"):
    """Write a purchasing workbook and return its path."""
    out = tmp_path / "CD-001608 Purchasing Sheet.xlsx"
    bp.build_purchasing_sheet(_df(rows), str(out), assembly)
    return str(out)


def test_round_trip_preserves_the_rows(tmp_path):
    path = _sheet(tmp_path, [BUY, MAKE])
    df, assembly, error = bp.read_purchasing_sheet(path)

    assert error is None
    assert assembly == "CD-001608"
    assert len(df) == 2

    by_title = {r["Title"]: r for _i, r in df.iterrows()}
    assert set(by_title) == {"SF-000067", "CD-001200"}

    make = by_title["CD-001200"]
    assert make["Source"] == "Make"
    assert make["Vendor"] == "Machine Shop"
    assert make["Description (Item,CO)"] == "adapter plate"
    assert float(make["Item Qty"]) == 1.0
    assert float(make["Cost Per"]) == 40.0
    assert float(make["Lead Time (Business Days)"]) == 15.0


def test_the_canonical_column_names_come_back(tmp_path):
    """The sheet heads them "Name" and "Description"; the reader restores the
    internal names the rest of the codebase keys on."""
    path = _sheet(tmp_path, [BUY])
    df, _assembly, error = bp.read_purchasing_sheet(path)

    assert error is None
    assert "Title" in df.columns
    assert "Description (Item,CO)" in df.columns
    assert "Name" not in df.columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_purchasing_sheet_read.py -v`
Expected: FAIL with `AttributeError: module 'bom_purchasing' has no attribute 'read_purchasing_sheet'`

- [ ] **Step 3: Implement the reader**

Add to `bom_purchasing.py`, directly after `build_purchasing_sheet`:

```python
# ---------------------------------------------------------------------------
# Reading a generated workbook back in — the inverse of build_purchasing_sheet
# ---------------------------------------------------------------------------

# How far down to hunt for the header row when it is not at HDR_ROW. A sheet
# generated before a future layout change still reads.
_HEADER_SCAN_ROWS = 10

NO_PURCHASING_TAB_ERROR = (
    "This workbook has no 'Purchasing' tab, so it is not a generated "
    "purchasing sheet. Run BOM → Purchasing Sheet first, fill in the "
    "suppliers for the parts you are ordering, and load the workbook it "
    "produces."
)
NO_HEADER_ROW_ERROR = (
    "Could not find the column header row on the 'Purchasing' tab — no row "
    "in the first 10 carries both a 'Source' and a 'Vendor' column."
)


def _sheet_label_to_column() -> dict[str, str]:
    """Header cell text (lower-cased) -> canonical column name.

    Inverts HEADER_LABELS: the sheet heads Title as "Name" and
    Description (Item,CO) as "Description".
    """
    out = {c.lower(): c for c in SHEET_COLUMNS}
    for canonical, label in HEADER_LABELS.items():
        out[label.lower()] = canonical
    return out


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _locate_header_row(ws) -> Optional[int]:
    """Row number of the column headers, or None.

    Tries HDR_ROW first, then scans. Source and Vendor are the two columns
    nothing downstream can work without, so their presence identifies the row.
    """
    labels = _sheet_label_to_column()
    order = [HDR_ROW] + [r for r in range(1, _HEADER_SCAN_ROWS + 1) if r != HDR_ROW]
    for row in order:
        if row > ws.max_row:
            continue
        found = {labels[_cell_text(c.value).lower()]
                 for c in ws[row] if _cell_text(c.value).lower() in labels}
        if {"Source", "Vendor"} <= found:
            return row
    return None


def read_purchasing_sheet(
    path: str,
) -> tuple[pd.DataFrame, str, Optional[str]]:
    """Read a workbook written by build_purchasing_sheet back into a DataFrame.

    Returns ``(df, assembly_number, error)``. ``error`` is None on success; on
    failure it is a message meant to be shown to the user verbatim, and the
    DataFrame is empty.

    Columns come back under their canonical names (Title, not "Name"). Cell A1
    carries the assembly number. Formula cells — Sub Total everywhere, Cost Per
    on assembly rows — read as None, because openpyxl only surfaces a cached
    formula result if Excel has opened and re-saved the file. Callers recompute
    rather than read them.
    """
    if not os.path.isfile(path):
        return pd.DataFrame(), "", f"Workbook not found: {path}"

    try:
        wb = load_workbook(path, data_only=True)
    except Exception as exc:  # noqa: BLE001 — corrupt file, wrong format
        name = os.path.basename(path)
        return pd.DataFrame(), "", f"Could not read {name}: {exc}"

    try:
        if PURCHASING_SHEET_NAME not in wb.sheetnames:
            return pd.DataFrame(), "", NO_PURCHASING_TAB_ERROR
        ws = wb[PURCHASING_SHEET_NAME]

        assembly = _cell_text(ws.cell(row=1, column=1).value)

        hdr_row = _locate_header_row(ws)
        if hdr_row is None:
            return pd.DataFrame(), "", NO_HEADER_ROW_ERROR

        labels = _sheet_label_to_column()
        columns = [labels.get(_cell_text(c.value).lower(), _cell_text(c.value))
                   for c in ws[hdr_row]]

        records: list[dict[str, Any]] = []
        for row in ws.iter_rows(min_row=hdr_row + 1):
            values = [c.value for c in row]
            first = _cell_text(values[0]) if values else ""
            # The trailing "Unmatched (n)" note is a merged full-width cell.
            # Without this it would arrive as a phantom part with a name and
            # no supplier.
            if first.startswith(UNMATCHED_NOTE_PREFIX):
                break
            if not any(_cell_text(v) for v in values):
                break
            records.append({col: val for col, val in zip(columns, values) if col})

        return pd.DataFrame(records, columns=[c for c in columns if c]), assembly, None
    finally:
        wb.close()
```

Add `load_workbook` to the openpyxl imports at the top of the file if it is not already imported, and confirm `Optional` is in the `typing` import list.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_purchasing_sheet_read.py -v`
Expected: PASS, both tests.

- [ ] **Step 5: Commit**

```bash
git add bom_purchasing.py tests/test_purchasing_sheet_read.py
git commit -m "feat(purchasing): read a generated purchasing workbook back in"
```

---

## Task 4: Reader edge cases

**Files:**
- Modify: `tests/test_purchasing_sheet_read.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_purchasing_sheet_read.py`:

```python
import openpyxl  # noqa: E402  (add to the imports at the top of the file)


def test_a_workbook_with_no_purchasing_tab_is_refused(tmp_path):
    """A raw Inventor export looks exactly like this. The message has to name
    the tab, so the remedy is obvious without reading the code."""
    path = tmp_path / "CD-001608 BOM.xlsx"
    pd.DataFrame([{"Item": "1", "Filename": "CD-001612.ipt"}]).to_excel(
        path, index=False)

    df, assembly, error = bp.read_purchasing_sheet(str(path))

    assert error is not None
    assert "Purchasing" in error
    assert df.empty
    assert assembly == ""


def test_a_missing_file_is_refused_not_raised(tmp_path):
    df, _assembly, error = bp.read_purchasing_sheet(str(tmp_path / "nope.xlsx"))
    assert error is not None
    assert "not found" in error.lower()
    assert df.empty


def test_the_header_row_is_found_when_it_is_not_at_hdr_row(tmp_path):
    """A sheet generated before a future layout change still reads."""
    path = tmp_path / "shifted.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = bp.PURCHASING_SHEET_NAME
    ws["A1"] = "CD-009999"
    headers = [bp.HEADER_LABELS.get(c, c) for c in bp.SHEET_COLUMNS]
    ws.append([])                      # push the header down one extra row
    for offset, name in enumerate(headers, 1):
        ws.cell(row=bp.HDR_ROW + 1, column=offset, value=name)
    row = {"Title": "CD-001200", "Source": "Make", "Vendor": "Acme"}
    for offset, col in enumerate(bp.SHEET_COLUMNS, 1):
        ws.cell(row=bp.HDR_ROW + 2, column=offset, value=row.get(col))
    wb.save(path)

    df, assembly, error = bp.read_purchasing_sheet(str(path))

    assert error is None
    assert assembly == "CD-009999"
    assert list(df["Title"]) == ["CD-001200"]


def test_the_unmatched_note_never_becomes_a_row(tmp_path):
    """An unpriced Buy row makes the writer append an "Unmatched (n)" note
    below the table. It is a merged full-width cell, so it would otherwise
    read as a part with a name and nothing else."""
    unpriced = dict(BUY, Vendor=None, **{"Cost Per": None})
    path = _sheet(tmp_path, [unpriced, MAKE])

    df, _assembly, error = bp.read_purchasing_sheet(path)

    assert error is None
    assert len(df) == 2
    assert not any(str(t).startswith(bp.UNMATCHED_NOTE_PREFIX)
                   for t in df["Title"])


def test_sub_total_is_not_readable_and_that_is_expected(tmp_path):
    """Sub Total is an Excel formula. openpyxl with data_only=True returns
    None unless Excel has opened and re-saved the file, so callers recompute
    the line total rather than read this column."""
    path = _sheet(tmp_path, [MAKE])
    df, _assembly, error = bp.read_purchasing_sheet(path)

    assert error is None
    assert df.loc[0, "Sub Total"] is None
```

- [ ] **Step 2: Run the tests to verify which fail**

Run: `python -m pytest tests/test_purchasing_sheet_read.py -v`
Expected: all five pass already if Task 3 was implemented as written. Any that fail point at a real gap in the reader — fix `read_purchasing_sheet`, do not weaken the test.

- [ ] **Step 3: Run the whole suite for regressions**

Run: `python -m pytest -q`
Expected: PASS, no failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_purchasing_sheet_read.py
git commit -m "test(purchasing): cover the reader's edge cases"
```

---

## Task 5: Engine — `OrderPart` and `load_order_parts`

**Files:**
- Create: `wrike_mfg_tasks.py`
- Create: `tests/test_wrike_mfg_tasks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wrike_mfg_tasks.py
"""Unit tests for the BOM → Wrike manufacturing task builder.

No network: Vault and Wrike are both faked. Workbooks are built in-test with
bom_purchasing.build_purchasing_sheet, so the fixtures exercise the real
writer rather than a hand-rolled imitation of it.
"""
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bom_purchasing as bp  # noqa: E402
import wrike_mfg_tasks as wmt  # noqa: E402


def _row(**over):
    base = {"Number": "CD-001200", "Title": "CD-001200", "Row Order": "1",
            "Item Qty": 1, "Units": "Each",
            "Description (Item,CO)": "adapter plate", "Source": "Make",
            "Vendor": "Xometry", "Cost Per": 40.0, "Shipping": 0.0,
            "Tax/Tariff": 0.0, "Lead Time (Business Days)": 15}
    base.update(over)
    return base


def _sheet(tmp_path, rows, assembly="CD-001608"):
    df, err = bp.coerce_bom_dataframe(pd.DataFrame(rows))
    assert err is None, err
    out = tmp_path / "CD-001608 Purchasing Sheet.xlsx"
    bp.build_purchasing_sheet(df, str(out), assembly)
    return str(out)


# --------------------------------------------------------------------- load

def test_load_reads_parts_and_the_assembly_number(tmp_path):
    path = _sheet(tmp_path, [
        _row(Number="CD-001200", Title="CD-001200"),
        _row(Number="SF-000067", Title="SF-000067", Source="Buy",
             Vendor="McMaster-Carr", **{"Item Qty": 4, "Cost Per": 1.5,
                                        "Lead Time (Business Days)": 3,
                                        "Row Order": "2"}),
    ])
    parts, assembly, error = wmt.load_order_parts(path)

    assert error is None
    assert assembly == "CD-001608"
    by_title = {p.title: p for p in parts}
    assert set(by_title) == {"CD-001200", "SF-000067"}

    make = by_title["CD-001200"]
    assert make.kind == wmt.KIND_MAKE
    assert make.sheet_vendor == "Xometry"
    assert make.lead_time_days == 15
    assert make.qty == 1.0
    assert make.line_total == 40.0

    buy = by_title["SF-000067"]
    assert buy.kind == wmt.KIND_BUY
    assert buy.qty == 4.0
    assert buy.line_total == 6.0


def test_line_total_adds_shipping_and_tax(tmp_path):
    path = _sheet(tmp_path, [_row(**{"Item Qty": 2, "Cost Per": 10.0,
                                     "Shipping": 5.0, "Tax/Tariff": 1.5})])
    parts, _assembly, error = wmt.load_order_parts(path)
    assert error is None
    assert parts[0].line_total == 26.5


def test_roll_up_rows_are_excluded_and_their_children_survive(tmp_path):
    """An assembly's Sub Total is a SUM of its children. Ordering both the
    assembly and its children puts the same metal on the PO twice."""
    path = _sheet(tmp_path, [
        _row(Number="CD-001613", Title="CD-001613", **{"Row Order": "1"}),
        _row(Number="CD-001612", Title="CD-001612", **{"Row Order": "1.1"}),
        _row(Number="CD-001577", Title="CD-001577", **{"Row Order": "1.2"}),
    ])
    parts, _assembly, error = wmt.load_order_parts(path)

    assert error is None
    assert {p.title for p in parts} == {"CD-001612", "CD-001577"}


def test_duplicate_titles_collapse_with_summed_qty_and_longest_lead(tmp_path):
    """A sheet lists a part once per place it appears in the BOM. One line
    item per part per order."""
    path = _sheet(tmp_path, [
        _row(**{"Row Order": "1", "Item Qty": 2,
                "Lead Time (Business Days)": 10}),
        _row(**{"Row Order": "2", "Item Qty": 3,
                "Lead Time (Business Days)": 20}),
    ])
    parts, _assembly, error = wmt.load_order_parts(path)

    assert error is None
    assert len(parts) == 1
    assert parts[0].qty == 5.0
    assert parts[0].lead_time_days == 20


def test_a_blank_title_row_is_dropped(tmp_path):
    path = _sheet(tmp_path, [
        _row(**{"Row Order": "1"}),
        _row(Number=None, Title=None, **{"Row Order": "2",
                                         "Description (Item,CO)": "ghost"}),
    ])
    parts, _assembly, error = wmt.load_order_parts(path)

    assert error is None
    assert [p.title for p in parts] == ["CD-001200"]


def test_a_non_numeric_qty_counts_as_one(tmp_path):
    path = _sheet(tmp_path, [_row(**{"Item Qty": "-"})])
    parts, _assembly, error = wmt.load_order_parts(path)

    assert error is None
    assert parts[0].qty == 1.0


def test_anything_other_than_make_is_treated_as_bought(tmp_path):
    path = _sheet(tmp_path, [
        _row(Number="A", Title="A", Source="Buy", **{"Row Order": "1"}),
        _row(Number="B", Title="B", Source="Other", **{"Row Order": "2"}),
    ])
    parts, _assembly, error = wmt.load_order_parts(path)

    assert error is None
    assert {p.kind for p in parts} == {wmt.KIND_BUY}


def test_a_reader_error_is_returned_not_raised(tmp_path):
    path = tmp_path / "CD-001608 BOM.xlsx"
    pd.DataFrame([{"Item": "1"}]).to_excel(path, index=False)

    parts, assembly, error = wmt.load_order_parts(str(path))

    assert error is not None
    assert parts == []
    assert assembly == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wrike_mfg_tasks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wrike_mfg_tasks'`

- [ ] **Step 3: Create the engine module with the load stage**

```python
# wrike_mfg_tasks.py
"""BOM → Wrike manufacturing tasks.

Reads a generated purchasing workbook, reconciles each part's supplier against
the Vault Vendor property, groups the parts into one order per supplier, and
creates a Wrike parent task with dependency-chained Purchasing /
Manufacturing / Shipping subtasks.

One trio per supplier, never one per part: a supplier's order is one PO and
one shipment, so eleven screws from McMaster are one set of tasks with eleven
line items. A Buy-only order has no Manufacturing task — nothing is made.

This module is the engine. The GUI wrapper lives in ``gui/wrike_mfg_tasks.py``.
See ``docs/superpowers/specs/2026-07-29-wrike-mfg-tasks-design.md``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import bom_purchasing

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]

KIND_MAKE = "Make"
KIND_BUY = "Buy"


@dataclass
class OrderPart:
    """One line item on one supplier's order.

    ``title`` is the identity — on the Purchasing tab it is headed "Name" and
    carries the CAD number, which is also the Vault file stem the supplier
    lookup searches for. The sheet hides the Number column, so there is no
    part number to carry alongside it.
    """
    title: str
    description: str = ""
    kind: str = KIND_MAKE
    qty: float = 1.0
    material: str = ""
    revision: str = ""
    unit_cost: float = 0.0
    shipping: float = 0.0
    tax: float = 0.0
    lead_time_days: Optional[int] = None
    sheet_vendor: str = ""

    @property
    def line_total(self) -> float:
        """Recomputed, never read from the sheet: Sub Total is a formula and
        openpyxl returns None for it unless Excel has re-saved the file."""
        return self.unit_cost * self.qty + self.shipping + self.tax


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:            # NaN
            return ""
    except Exception:                 # noqa: BLE001
        pass
    return str(value).strip()


def _to_float(value: Any, default: float = 0.0) -> float:
    text = _text(value).replace(",", "").replace("$", "")
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _to_int_or_none(value: Any) -> Optional[int]:
    text = _text(value).replace(",", "")
    if not text:
        return None
    try:
        return int(round(float(text)))
    except ValueError:
        return None


def load_order_parts(
    sheet_path: str,
    on_progress: Optional[ProgressFn] = None,
) -> tuple[list[OrderPart], str, Optional[str]]:
    """Parse a generated purchasing workbook into orderable line items.

    Returns ``(parts, assembly_number, error)``. ``error`` is None on success;
    on failure it is a message meant to be shown verbatim and ``parts`` is
    empty.

    Roll-up rows are excluded: an assembly's Sub Total is a SUM of its
    children, so ordering both double-counts the cost and puts the same metal
    on the PO twice. Duplicate titles collapse to one line item.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)

    df, assembly, error = bom_purchasing.read_purchasing_sheet(sheet_path)
    if error:
        return [], "", error

    children_map = bom_purchasing.build_children_map(df)

    merged: dict[str, OrderPart] = {}
    order: list[str] = []

    for idx, rec in df.iterrows():
        if children_map.get(idx):
            title = _text(rec.get("Title")) or "(unnamed)"
            logger.info("Excluding roll-up row %s", title)
            progress(f"  {title}: sub-assembly roll-up, ordering its children")
            continue

        title = _text(rec.get("Title"))
        if not title:
            label = _text(rec.get("Row Order")) or "(unnumbered)"
            logger.info("Skipping row %s: no name", label)
            progress(f"  Row {label} has no name; skipped.")
            continue

        source = _text(rec.get("Source")).lower()
        kind = KIND_MAKE if source == "make" else KIND_BUY

        raw_qty = rec.get("Item Qty")
        qty = _to_float(raw_qty, default=0.0)
        if qty <= 0:
            # The sheet's own roll-up formula guards Qty with ISNUMBER for the
            # same reason: the root row often carries "-".
            if _text(raw_qty):
                progress(f"  {title}: quantity {_text(raw_qty)!r} is not a "
                         f"number; counting 1.")
            qty = 1.0

        lead = _to_int_or_none(rec.get("Lead Time (Business Days)"))

        key = title.casefold()
        existing = merged.get(key)
        if existing is None:
            merged[key] = OrderPart(
                title=title,
                description=_text(rec.get("Description (Item,CO)")),
                kind=kind,
                qty=qty,
                material=_text(rec.get("Material")),
                revision=_text(rec.get("Revision")),
                unit_cost=_to_float(rec.get("Cost Per")),
                shipping=_to_float(rec.get("Shipping")),
                tax=_to_float(rec.get("Tax/Tariff")),
                lead_time_days=lead,
                sheet_vendor=_text(rec.get("Vendor")),
            )
            order.append(key)
        else:
            existing.qty += qty
            if lead is not None:
                existing.lead_time_days = max(existing.lead_time_days or 0, lead)
            progress(f"  {title}: appears more than once; quantities summed "
                     f"to {existing.qty:g}.")

    return [merged[k] for k in order], assembly, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wrike_mfg_tasks.py -v`
Expected: PASS, all nine tests.

- [ ] **Step 5: Commit**

```bash
git add wrike_mfg_tasks.py tests/test_wrike_mfg_tasks.py
git commit -m "feat(wrike-tasks): parse a purchasing sheet into order line items"
```

---

## Task 6: Engine — supplier reconcile against Vault

**Files:**
- Modify: `wrike_mfg_tasks.py`
- Modify: `tests/test_wrike_mfg_tasks.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wrike_mfg_tasks.py`:

```python
# ---------------------------------------------------------------- fake vault

class FakeVaultAPI:
    """Records calls and replays canned /file-versions responses.

    ``vendor_map`` maps a part title to the Vendor property value Vault should
    report. A title absent from the map returns no matching file at all.
    """

    def __init__(self, vendor_map=None, errors=(), raises=(), truncate=()):
        self.vendor_map = vendor_map or {}
        self.errors = set(errors)
        self.raises = set(raises)
        self.truncate = set(truncate)
        self.calls = []

    async def search_file_versions(self, vault_id=None, query=None, **kwargs):
        self.calls.append({"vault_id": vault_id, "query": query, **kwargs})
        if query in self.raises:
            raise RuntimeError(f"search blew up for {query}")
        if query in self.errors:
            return {"error": True, "status_code": 500, "data": "boom"}
        results = []
        if query in self.vendor_map:
            results.append(_file_hit(f"{query}.ipt", self.vendor_map[query]))
        if query in self.truncate:
            results = [_file_hit(f"OTHER-{n}.ipt", "")
                       for n in range(wmt.SEARCH_LIMIT)]
        return {"error": False, "status_code": 200,
                "data": {"results": results,
                         "included": {"propertyDefinition": {
                             "PD1": {"displayName": "Vendor"}}}}}


def _file_hit(name, vendor):
    return {"entityType": "FileVersion", "name": name, "id": "1",
            "properties": [{"propertyDefinitionId": "PD1", "value": vendor}]}


async def _reconcile(parts, api):
    return await wmt.reconcile_vendors(api, "1", parts)


def _part(title, vendor, kind=wmt.KIND_MAKE):
    return wmt.OrderPart(title=title, sheet_vendor=vendor, kind=kind)


# ----------------------------------------------------------------- statuses

async def test_agreeing_vendors_are_matched_and_chosen_automatically():
    api = FakeVaultAPI({"CD-001200": "Xometry"})
    rows = await _reconcile([_part("CD-001200", "Xometry")], api)

    assert rows[0].status == wmt.STATUS_MATCHED
    assert rows[0].chosen == "Xometry"
    assert rows[0].resolved


async def test_comparison_ignores_case_and_whitespace():
    """The reference BOM spells it McMASTER-CARR."""
    api = FakeVaultAPI({"SF-000067": "McMASTER-CARR"})
    rows = await _reconcile(
        [_part("SF-000067", "McMaster-Carr", wmt.KIND_BUY)], api)

    assert rows[0].status == wmt.STATUS_MATCHED


async def test_a_genuine_disagreement_blocks():
    api = FakeVaultAPI({"CD-001200": "Xometry"})
    rows = await _reconcile([_part("CD-001200", "Protolabs")], api)

    assert rows[0].status == wmt.STATUS_MISMATCH
    assert rows[0].proposal == ""
    assert not rows[0].resolved


async def test_one_blank_side_proposes_the_populated_one():
    api = FakeVaultAPI({"A": "", "B": "Fictiv"})
    rows = await _reconcile([_part("A", "Xometry"), _part("B", "")], api)

    assert rows[0].status == wmt.STATUS_SHEET_ONLY
    assert rows[0].proposal == "Xometry"
    assert rows[1].status == wmt.STATUS_VAULT_ONLY
    assert rows[1].proposal == "Fictiv"
    assert not rows[0].resolved          # a proposal still needs accepting


async def test_both_blank_blocks():
    api = FakeVaultAPI({"A": ""})
    rows = await _reconcile([_part("A", "")], api)

    assert rows[0].status == wmt.STATUS_BOTH_BLANK
    assert rows[0].proposal == ""


async def test_a_missing_buy_part_proposes_the_sheet_but_a_make_part_blocks():
    """A catalogue screw that was never checked into Vault is routine, and its
    sheet vendor came from the Engineering Purchased Parts list. A missing
    CD-numbered Make part is not routine."""
    api = FakeVaultAPI({})
    rows = await _reconcile([
        _part("ISO 4762 M6", "McMaster-Carr", wmt.KIND_BUY),
        _part("CD-001200", "Xometry", wmt.KIND_MAKE),
    ], api)

    assert rows[0].status == wmt.STATUS_NOT_IN_VAULT
    assert rows[0].proposal == "McMaster-Carr"
    assert rows[1].status == wmt.STATUS_NOT_IN_VAULT
    assert rows[1].proposal == ""


async def test_a_search_error_degrades_only_that_row():
    api = FakeVaultAPI({"B": "Fictiv"}, errors=["A"])
    rows = await _reconcile([_part("A", "Xometry"), _part("B", "")], api)

    assert rows[0].status == wmt.STATUS_LOOKUP_FAILED
    assert rows[0].proposal == "Xometry"    # a transient error is not evidence
    assert rows[1].status == wmt.STATUS_VAULT_ONLY


async def test_a_raising_search_does_not_sink_the_reconcile():
    api = FakeVaultAPI({"B": "Fictiv"}, raises=["A"])
    rows = await _reconcile([_part("A", "Xometry"), _part("B", "")], api)

    assert rows[0].status == wmt.STATUS_LOOKUP_FAILED
    assert len(rows) == 2


async def test_a_full_page_without_the_file_reports_truncation():
    """Saying "not in Vault" when the cap was hit would send someone to fix
    data that is already correct."""
    api = FakeVaultAPI({}, truncate=["A"])
    rows = await _reconcile([_part("A", "Xometry")], api)

    assert rows[0].status == wmt.STATUS_TRUNCATED


async def test_only_exact_basename_matches_count():
    api = FakeVaultAPI({})

    async def search(vault_id=None, query=None, **kwargs):
        return {"error": False, "status_code": 200,
                "data": {"results": [_file_hit("CD-001200-BRACKET.ipt", "Wrong")],
                         "included": {"propertyDefinition": {
                             "PD1": {"displayName": "Vendor"}}}}}

    api.search_file_versions = search
    rows = await _reconcile([_part("CD-001200", "Xometry")], api)

    assert rows[0].vault_vendor == ""
    assert rows[0].status == wmt.STATUS_NOT_IN_VAULT


async def test_non_file_version_hits_are_ignored():
    api = FakeVaultAPI({})

    async def search(vault_id=None, query=None, **kwargs):
        hit = _file_hit("CD-001200.ipt", "Wrong")
        hit["entityType"] = "ItemVersion"
        return {"error": False, "status_code": 200,
                "data": {"results": [hit],
                         "included": {"propertyDefinition": {
                             "PD1": {"displayName": "Vendor"}}}}}

    api.search_file_versions = search
    rows = await _reconcile([_part("CD-001200", "Xometry")], api)

    assert rows[0].vault_vendor == ""


async def test_the_lookup_asks_for_properties():
    """Files ignore the bare propDefIds that items use — the wrong spelling
    returns 200 with the properties silently missing."""
    api = FakeVaultAPI({"CD-001200": "Xometry"})
    await _reconcile([_part("CD-001200", "Xometry")], api)

    assert api.calls[0]["prop_def_ids"] == "all"
    assert api.calls[0]["limit"] == wmt.SEARCH_LIMIT


def test_accept_proposals_resolves_every_amber_row():
    rows = [
        wmt.ReconcileRow(part=_part("A", "X"), status=wmt.STATUS_SHEET_ONLY,
                         proposal="X"),
        wmt.ReconcileRow(part=_part("B", ""), status=wmt.STATUS_MISMATCH,
                         proposal=""),
    ]
    wmt.accept_proposals(rows)

    assert rows[0].chosen == "X"
    assert rows[1].chosen == ""          # reds are never auto-resolved
    assert wmt.unresolved_count(rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wrike_mfg_tasks.py -k reconcile -v`
Expected: FAIL with `AttributeError: module 'wrike_mfg_tasks' has no attribute 'reconcile_vendors'`

- [ ] **Step 3: Implement the reconcile stage**

Append to `wrike_mfg_tasks.py` (and add `import asyncio` to the imports):

```python
# ---------------------------------------------------------------------------
# Stage 2: reconcile each part's supplier against Vault
# ---------------------------------------------------------------------------

# Vault caps concurrent work anyway; this keeps a 200-row sheet from opening
# 200 sockets at once. Same cap publish_bom.py uses.
MAX_CONCURRENCY = 8

# A title's keyword search also matches its .pdf/.stp siblings, its item, and
# anything carrying the title in a property, so the hit list is much longer
# than the one file wanted.
SEARCH_LIMIT = 50

VENDOR_PROPERTY = "Vendor"
MODEL_EXTS = ("ipt", "iam")

STATUS_MATCHED = "matched"
STATUS_SHEET_ONLY = "sheet only"
STATUS_VAULT_ONLY = "Vault only"
STATUS_MISMATCH = "mismatch"
STATUS_BOTH_BLANK = "both blank"
STATUS_NOT_IN_VAULT = "not in Vault"
STATUS_LOOKUP_FAILED = "lookup failed"
STATUS_TRUNCATED = "search truncated"


@dataclass
class ReconcileRow:
    """An OrderPart plus what Vault says about its supplier.

    ``proposal`` is the value the GUI offers with one click; empty means the
    tool has nothing defensible to suggest and a human must decide.
    ``chosen`` is what will actually be used.
    """
    part: OrderPart
    vault_vendor: str = ""
    status: str = ""
    proposal: str = ""
    chosen: str = ""
    excluded: bool = False

    @property
    def resolved(self) -> bool:
        return self.excluded or bool(self.chosen)


def vendor_key(value: str) -> str:
    """Normalized form for comparing and grouping supplier names.

    Case, surrounding whitespace and runs of internal whitespace all collapse:
    the reference BOM spells it McMASTER-CARR, and a supplier typed as
    "machine  shop" is the same vendor as "Machine Shop".
    """
    return " ".join(_text(value).split()).casefold()


def _same_vendor(left: str, right: str) -> bool:
    return vendor_key(left) == vendor_key(right)


def _search_results(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("results", "items", "data", "value"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []


def _vendor_prop_ids(data: Any) -> set[str]:
    """Property-definition ids whose display name is Vendor.

    The response carries properties as {propertyDefinitionId, value} plus an
    included.propertyDefinition map that names them, so no separate
    /property-definitions call is needed.
    """
    ids: set[str] = set()
    if not isinstance(data, dict):
        return ids
    included = data.get("included")
    defs = included.get("propertyDefinition") if isinstance(included, dict) else None
    if not isinstance(defs, dict):
        return ids
    for pid, meta in defs.items():
        name = ""
        if isinstance(meta, dict):
            name = _text(meta.get("displayName") or meta.get("name"))
        if name.casefold() == VENDOR_PROPERTY.casefold():
            ids.add(pid)
    return ids


def _vendor_of(record: dict[str, Any], vendor_ids: set[str]) -> str:
    props = record.get("properties")
    if isinstance(props, dict):
        for key, value in props.items():
            if _text(key).casefold() == VENDOR_PROPERTY.casefold():
                return _text(value)
        return ""
    if isinstance(props, list):
        for prop in props:
            if not isinstance(prop, dict):
                continue
            pid = _text(prop.get("propertyDefinitionId"))
            name = _text(prop.get("displayName") or prop.get("name"))
            if pid in vendor_ids or name.casefold() == VENDOR_PROPERTY.casefold():
                return _text(prop.get("value"))
    return ""


def _base_stem(name: str) -> str:
    base = os.path.basename(_text(name))
    return base.rsplit(".", 1)[0] if "." in base else base


def _classify(part: OrderPart, vault_vendor: str, *,
              found: bool, failed: bool, truncated: bool) -> tuple[str, str]:
    sheet = part.sheet_vendor

    if truncated:
        return STATUS_TRUNCATED, sheet
    if failed:
        # A transient search error is not evidence about the part.
        return STATUS_LOOKUP_FAILED, sheet
    if not found:
        # A catalogue screw that was never checked in is routine, and the
        # sheet's vendor for a bought part came from the Engineering Purchased
        # Parts list. A missing CD-numbered Make part is not routine.
        return STATUS_NOT_IN_VAULT, sheet if part.kind == KIND_BUY else ""
    if sheet and vault_vendor:
        if _same_vendor(sheet, vault_vendor):
            return STATUS_MATCHED, sheet
        return STATUS_MISMATCH, ""
    if sheet:
        return STATUS_SHEET_ONLY, sheet
    if vault_vendor:
        return STATUS_VAULT_ONLY, vault_vendor
    return STATUS_BOTH_BLANK, ""


async def _reconcile_one(api, vault_id: str, part: OrderPart,
                         progress: ProgressFn) -> ReconcileRow:
    row = ReconcileRow(part=part)

    resp = await api.search_file_versions(
        vault_id=vault_id, query=part.title,
        prop_def_ids="all", latest_only=True, limit=SEARCH_LIMIT,
    )
    if resp.get("error"):
        row.status, row.proposal = _classify(
            part, "", found=False, failed=True, truncated=False)
        progress(f"  {part.title}: lookup failed")
        return row

    hits = _search_results(resp.get("data"))
    vendor_ids = _vendor_prop_ids(resp.get("data"))

    found = False
    for rec in hits:
        # Strictly the version entity, and the basename must EQUAL the title —
        # a substring match pulls in every assembly that references the part.
        if _text(rec.get("entityType")).casefold() != "fileversion":
            continue
        name = _text(rec.get("name"))
        if _base_stem(name).casefold() != part.title.casefold():
            continue
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in MODEL_EXTS:
            continue
        found = True
        vendor = _vendor_of(rec, vendor_ids)
        if vendor:
            row.vault_vendor = vendor
            break

    truncated = not found and len(hits) >= SEARCH_LIMIT
    row.status, row.proposal = _classify(
        part, row.vault_vendor, found=found, failed=False, truncated=truncated)
    if row.status == STATUS_MATCHED:
        row.chosen = row.proposal
    progress(f"  {part.title}: {row.status}")
    return row


async def reconcile_vendors(
    api,
    vault_id: str,
    parts: list[OrderPart],
    on_progress: Optional[ProgressFn] = None,
) -> list[ReconcileRow]:
    """Resolve every part's supplier against the Vault Vendor property.

    Runs at most MAX_CONCURRENCY lookups at once. Output order matches input
    order so the GUI table is stable. A failure on one part degrades that row
    only; it never aborts the reconcile.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def guarded(part: OrderPart) -> ReconcileRow:
        async with sem:
            try:
                return await _reconcile_one(api, vault_id, part, progress)
            except Exception as exc:  # noqa: BLE001 — one bad row must not sink it
                logger.exception("Reconcile failed for %s", part.title)
                progress(f"  {part.title}: lookup failed - {exc}")
                status, proposal = _classify(
                    part, "", found=False, failed=True, truncated=False)
                return ReconcileRow(part=part, status=status, proposal=proposal)

    return list(await asyncio.gather(*(guarded(p) for p in parts)))


def accept_proposals(rows: list[ReconcileRow]) -> int:
    """Take every row's proposal as its chosen supplier. Returns how many.

    Rows with no proposal — a genuine disagreement, or nothing on either side
    — are left alone. Eleven screws must not mean eleven clicks, but a real
    conflict is never resolved on the user's behalf.
    """
    accepted = 0
    for row in rows:
        if not row.chosen and not row.excluded and row.proposal:
            row.chosen = row.proposal
            accepted += 1
    return accepted


def unresolved_count(rows: list[ReconcileRow]) -> int:
    return sum(1 for r in rows if not r.resolved)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wrike_mfg_tasks.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add wrike_mfg_tasks.py tests/test_wrike_mfg_tasks.py
git commit -m "feat(wrike-tasks): reconcile sheet suppliers against Vault"
```

---

## Task 7: Engine — grouping and scheduling

**Files:**
- Modify: `wrike_mfg_tasks.py`
- Modify: `tests/test_wrike_mfg_tasks.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wrike_mfg_tasks.py`:

```python
from datetime import date  # noqa: E402  (add to the imports at the top)

# ----------------------------------------------------------------- grouping

def _resolved(title, supplier, kind=wmt.KIND_MAKE, lead=None, qty=1.0):
    part = wmt.OrderPart(title=title, sheet_vendor=supplier, kind=kind,
                         qty=qty, lead_time_days=lead)
    return wmt.ReconcileRow(part=part, status=wmt.STATUS_MATCHED,
                            proposal=supplier, chosen=supplier)


def test_one_supplier_yields_one_order_however_many_parts():
    rows = [_resolved(f"ISO-{n}", "McMaster-Carr", wmt.KIND_BUY)
            for n in range(11)]
    orders = wmt.group_orders(rows)

    assert len(orders) == 1
    assert len(orders[0].parts) == 11


def test_a_mixed_supplier_gets_one_order_with_a_manufacturing_stage():
    """One PO to MiSUMi means one set of tasks."""
    rows = [_resolved("CD-001366", "MiSUMi", wmt.KIND_MAKE),
            _resolved("ISO 4762", "MiSUMi", wmt.KIND_BUY),
            _resolved("ISO 4032", "MiSUMi", wmt.KIND_BUY)]
    orders = wmt.group_orders(rows)

    assert len(orders) == 1
    assert orders[0].has_make
    assert [p.title for p in orders[0].make_parts] == ["CD-001366"]


def test_a_buy_only_supplier_has_no_manufacturing_stage():
    rows = [_resolved("ISO 4762", "McMaster-Carr", wmt.KIND_BUY)]
    orders = wmt.group_orders(rows)

    assert not orders[0].has_make
    assert [s for s in orders[0].stages] == [wmt.STAGE_PURCHASING,
                                             wmt.STAGE_SHIPPING]


def test_supplier_spellings_collapse_to_one_order():
    rows = [_resolved("A", "Xometry"), _resolved("B", "xometry  ")]
    orders = wmt.group_orders(rows)

    assert len(orders) == 1
    assert orders[0].supplier == "Xometry"    # the first row's spelling


def test_excluded_and_unresolved_rows_contribute_to_no_order():
    keep = _resolved("A", "Xometry")
    dropped = _resolved("B", "Xometry")
    dropped.excluded = True
    blocked = wmt.ReconcileRow(part=wmt.OrderPart(title="C"),
                               status=wmt.STATUS_MISMATCH)
    orders = wmt.group_orders([keep, dropped, blocked])

    assert [p.title for p in orders[0].parts] == ["A"]


# --------------------------------------------------------------- scheduling

def test_business_days_skip_the_weekend():
    friday = date(2026, 8, 7)
    assert wmt.add_business_days(friday, 1) == date(2026, 8, 10)
    assert wmt.add_business_days(friday, 0) == friday


def test_a_weekend_start_snaps_forward():
    saturday = date(2026, 8, 8)
    assert wmt.add_business_days(saturday, 0) == date(2026, 8, 10)


def test_lead_time_drives_manufacturing_for_a_make_order():
    rows = [_resolved("A", "Xometry", wmt.KIND_MAKE, lead=15),
            _resolved("B", "Xometry", wmt.KIND_MAKE, lead=5)]
    orders = wmt.schedule_orders(wmt.group_orders(rows),
                                 start=date(2026, 8, 3),
                                 durations=wmt.Durations())

    stages = {s.stage: s for s in orders[0].schedule}
    assert stages[wmt.STAGE_PURCHASING].start == date(2026, 8, 3)
    assert stages[wmt.STAGE_PURCHASING].due == date(2026, 8, 4)
    assert stages[wmt.STAGE_MANUFACTURING].start == date(2026, 8, 5)
    assert stages[wmt.STAGE_MANUFACTURING].due == date(2026, 8, 25)
    assert stages[wmt.STAGE_SHIPPING].start == date(2026, 8, 26)
    assert stages[wmt.STAGE_SHIPPING].due == date(2026, 8, 28)


def test_lead_time_drives_shipping_when_nothing_is_made():
    """A McMaster order's lead time IS its ship time. Putting it on a stage
    that does not exist would lose it."""
    rows = [_resolved("ISO", "McMaster-Carr", wmt.KIND_BUY, lead=3)]
    orders = wmt.schedule_orders(wmt.group_orders(rows),
                                 start=date(2026, 8, 3),
                                 durations=wmt.Durations())

    stages = {s.stage: s for s in orders[0].schedule}
    assert wmt.STAGE_MANUFACTURING not in stages
    assert stages[wmt.STAGE_SHIPPING].start == date(2026, 8, 5)
    assert stages[wmt.STAGE_SHIPPING].due == date(2026, 8, 7)


def test_a_blank_lead_time_falls_back_to_the_default():
    rows = [_resolved("A", "Xometry", wmt.KIND_MAKE, lead=None)]
    durations = wmt.Durations(purchasing=2, manufacturing=10, shipping=3)
    orders = wmt.schedule_orders(wmt.group_orders(rows),
                                 start=date(2026, 8, 3), durations=durations)

    stages = {s.stage: s for s in orders[0].schedule}
    assert stages[wmt.STAGE_MANUFACTURING].start == date(2026, 8, 5)
    assert stages[wmt.STAGE_MANUFACTURING].due == date(2026, 8, 18)


def test_the_order_span_covers_every_stage():
    rows = [_resolved("A", "Xometry", wmt.KIND_MAKE, lead=15)]
    orders = wmt.schedule_orders(wmt.group_orders(rows),
                                 start=date(2026, 8, 3),
                                 durations=wmt.Durations())

    assert orders[0].start == date(2026, 8, 3)
    assert orders[0].due == date(2026, 8, 28)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wrike_mfg_tasks.py -k "group or business or lead or span" -v`
Expected: FAIL with `AttributeError: module 'wrike_mfg_tasks' has no attribute 'group_orders'`

- [ ] **Step 3: Implement grouping and scheduling**

Append to `wrike_mfg_tasks.py` (add `from datetime import date, timedelta` to the imports):

```python
# ---------------------------------------------------------------------------
# Stage 3: group into one order per supplier
# ---------------------------------------------------------------------------

STAGE_PURCHASING = "Purchasing"
STAGE_MANUFACTURING = "Manufacturing"
STAGE_SHIPPING = "Shipping"


@dataclass
class StageSchedule:
    stage: str
    start: date
    due: date


@dataclass
class SupplierOrder:
    """One supplier's order — one PO, one shipment, one set of tasks."""
    supplier: str
    parts: list[OrderPart] = field(default_factory=list)
    schedule: list[StageSchedule] = field(default_factory=list)

    @property
    def make_parts(self) -> list[OrderPart]:
        return [p for p in self.parts if p.kind == KIND_MAKE]

    @property
    def has_make(self) -> bool:
        return bool(self.make_parts)

    @property
    def stages(self) -> list[str]:
        """The stages this order passes through. A Buy-only order skips
        Manufacturing — nothing is made, the supplier ships from stock."""
        if self.has_make:
            return [STAGE_PURCHASING, STAGE_MANUFACTURING, STAGE_SHIPPING]
        return [STAGE_PURCHASING, STAGE_SHIPPING]

    @property
    def piece_count(self) -> float:
        return sum(p.qty for p in self.parts)

    @property
    def total(self) -> float:
        return sum(p.line_total for p in self.parts)

    @property
    def start(self) -> Optional[date]:
        return min((s.start for s in self.schedule), default=None)

    @property
    def due(self) -> Optional[date]:
        return max((s.due for s in self.schedule), default=None)


def group_orders(rows: list[ReconcileRow]) -> list[SupplierOrder]:
    """One order per supplier, in first-seen order.

    Grouping normalizes the supplier name the same way the reconcile
    comparison does, so "xometry" and "Xometry" are one order. The display
    name is the first row's spelling — that is what reaches the task title.
    Excluded and unresolved rows contribute to nothing.
    """
    orders: dict[str, SupplierOrder] = {}
    order: list[str] = []

    for row in rows:
        if row.excluded or not row.chosen:
            continue
        key = vendor_key(row.chosen)
        if key not in orders:
            orders[key] = SupplierOrder(supplier=row.chosen)
            order.append(key)
        orders[key].parts.append(row.part)

    return [orders[k] for k in order]


# ---------------------------------------------------------------------------
# Stage 4: schedule each order forward from a start date
# ---------------------------------------------------------------------------

@dataclass
class Durations:
    """Stage lengths in business days, editable in the GUI.

    ``manufacturing`` is the fallback used when no part in the order carries a
    lead time; ``shipping`` is likewise the fallback for a Buy-only order.
    """
    purchasing: int = 2
    manufacturing: int = 10
    shipping: int = 3


def add_business_days(start: date, days: int) -> date:
    """``days`` business days after ``start``, weekends skipped.

    A start that lands on a weekend snaps forward to the next business day, so
    a Saturday start date never produces a Saturday task. No holiday calendar.
    """
    day = start
    while day.weekday() >= 5:
        day += timedelta(days=1)
    remaining = max(0, days)
    while remaining > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            remaining -= 1
    return day


def _stage_length(order: SupplierOrder, stage: str,
                  durations: Durations) -> int:
    """How many business days a stage runs, in the order's own terms.

    Lead time lands on the stage that consumes it: manufacturing for an order
    with Make parts, shipping for one without.
    """
    if stage == STAGE_PURCHASING:
        return max(1, durations.purchasing)
    if stage == STAGE_MANUFACTURING:
        leads = [p.lead_time_days for p in order.make_parts
                 if p.lead_time_days]
        return max(1, max(leads) if leads else durations.manufacturing)
    if order.has_make:
        return max(1, durations.shipping)
    leads = [p.lead_time_days for p in order.parts if p.lead_time_days]
    return max(1, max(leads) if leads else durations.shipping)


def schedule_orders(orders: list[SupplierOrder], *, start: date,
                    durations: Durations) -> list[SupplierOrder]:
    """Fill in each order's stage dates, forward from ``start``.

    Every order starts on the same date; the stages within an order run back
    to back, which is what the finish-to-start dependencies then express in
    Wrike. Mutates and returns the orders.
    """
    for order in orders:
        order.schedule = []
        cursor = start
        for stage in order.stages:
            length = _stage_length(order, stage, durations)
            stage_start = add_business_days(cursor, 0)
            stage_due = add_business_days(stage_start, length - 1)
            order.schedule.append(
                StageSchedule(stage=stage, start=stage_start, due=stage_due))
            cursor = add_business_days(stage_due, 1)
    return orders
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wrike_mfg_tasks.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add wrike_mfg_tasks.py tests/test_wrike_mfg_tasks.py
git commit -m "feat(wrike-tasks): group parts by supplier and schedule the stages"
```

---

## Task 8: Engine — titles and descriptions

**Files:**
- Modify: `wrike_mfg_tasks.py`
- Modify: `tests/test_wrike_mfg_tasks.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wrike_mfg_tasks.py`:

```python
# ---------------------------------------------------------------- rendering

def _scheduled_order(parts, supplier="Xometry"):
    rows = [wmt.ReconcileRow(part=p, status=wmt.STATUS_MATCHED,
                             proposal=supplier, chosen=supplier)
            for p in parts]
    return wmt.schedule_orders(wmt.group_orders(rows),
                               start=date(2026, 8, 3),
                               durations=wmt.Durations())[0]


def test_the_parent_title_names_the_build_and_supplier():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    assert wmt.parent_title("CD-001608", order) == "CD-001608 - Xometry"


def test_a_stage_title_still_reads_alone_in_a_my_work_queue():
    """Subtasks show detached from their parent in list views."""
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    assert (wmt.stage_title("CD-001608", order, wmt.STAGE_MANUFACTURING)
            == "CD-001608 Xometry - 2. Manufacturing")
    assert (wmt.stage_title("CD-001608", order, wmt.STAGE_PURCHASING)
            == "CD-001608 Xometry - 1. Purchasing")


def test_a_buy_only_order_numbers_shipping_second():
    order = _scheduled_order([wmt.OrderPart(title="ISO", kind=wmt.KIND_BUY)],
                             supplier="McMaster-Carr")
    assert (wmt.stage_title("CD-001608", order, wmt.STAGE_SHIPPING)
            == "CD-001608 McMaster-Carr - 2. Shipping")


def test_the_parent_description_carries_every_part_and_the_total():
    order = _scheduled_order([
        wmt.OrderPart(title="CD-001200", description="adapter plate",
                      qty=2, unit_cost=40.0),
        wmt.OrderPart(title="CD-001201", description="bracket",
                      qty=1, unit_cost=10.0),
    ])
    html = wmt.render_description(order, wmt.STAGE_PARENT,
                                  source_name="CD-001608 Purchasing Sheet.xlsx")

    assert "CD-001200" in html and "CD-001201" in html
    assert "adapter plate" in html
    assert "CD-001608 Purchasing Sheet.xlsx" in html
    assert "90.00" in html                 # 2*40 + 1*10


def test_the_manufacturing_description_lists_only_the_made_parts():
    order = _scheduled_order([
        wmt.OrderPart(title="CD-001200", kind=wmt.KIND_MAKE,
                      material="6061-T6", revision="R3"),
        wmt.OrderPart(title="ISO 4762", kind=wmt.KIND_BUY),
    ])
    html = wmt.render_description(order, wmt.STAGE_MANUFACTURING,
                                  source_name="sheet.xlsx")

    assert "CD-001200" in html
    assert "6061-T6" in html
    assert "ISO 4762" not in html


def test_the_purchasing_description_has_costs_and_a_checklist():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200", qty=2,
                                            unit_cost=40.0)])
    html = wmt.render_description(order, wmt.STAGE_PURCHASING,
                                  source_name="sheet.xlsx")

    assert "80.00" in html
    assert "PO issued" in html


def test_descriptions_are_html_because_wrike_collapses_newlines():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    html = wmt.render_description(order, wmt.STAGE_SHIPPING,
                                  source_name="sheet.xlsx")

    assert "<table" in html or "<br" in html


def test_values_are_escaped():
    order = _scheduled_order([wmt.OrderPart(title="A<b>",
                                            description="1 < 2 & 3")])
    html = wmt.render_description(order, wmt.STAGE_PARENT,
                                  source_name="sheet.xlsx")

    assert "A&lt;b&gt;" in html
    assert "1 &lt; 2 &amp; 3" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wrike_mfg_tasks.py -k "title or description or escaped" -v`
Expected: FAIL with `AttributeError: module 'wrike_mfg_tasks' has no attribute 'parent_title'`

- [ ] **Step 3: Implement rendering**

Append to `wrike_mfg_tasks.py` (add `import html as html_lib` to the imports):

```python
# ---------------------------------------------------------------------------
# Stage 5: titles and descriptions
# ---------------------------------------------------------------------------

STAGE_PARENT = "Parent"

# A plain hyphen with single spaces, not an em dash: the re-run guard compares
# parent titles literally, so the separator has to survive a round trip
# through the API unchanged.
TITLE_SEP = " - "


def parent_title(build: str, order: SupplierOrder) -> str:
    return f"{_text(build)}{TITLE_SEP}{order.supplier}"


def stage_title(build: str, order: SupplierOrder, stage: str) -> str:
    """Carries the build and supplier: a subtask appears detached from its
    parent in list views and in an assignee's My Work queue."""
    number = order.stages.index(stage) + 1
    return f"{_text(build)} {order.supplier}{TITLE_SEP}{number}. {stage}"


def _esc(value: Any) -> str:
    return html_lib.escape(_text(value))


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _qty(value: float) -> str:
    return f"{value:g}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th align='left'>{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (f"<table border='1' cellpadding='4' cellspacing='0'>"
            f"<tr>{head}</tr>{body}</table>")


def render_description(order: SupplierOrder, stage: str, *,
                       source_name: str) -> str:
    """The task body for one stage, as HTML.

    Wrike renders a description as HTML, so a plain string's newlines collapse
    and a part table arrives as one run-on line.
    """
    header = (f"<p><b>Supplier:</b> {_esc(order.supplier)}<br/>"
              f"<b>From:</b> {_esc(source_name)}</p>")

    if stage == STAGE_PARENT:
        rows = [[_esc(p.title), _esc(p.description), _qty(p.qty),
                 _esc(p.kind), _money(p.line_total)] for p in order.parts]
        return (header
                + f"<p>{len(order.parts)} line items, "
                  f"{_qty(order.piece_count)} pcs, "
                  f"{_money(order.total)} estimated.</p>"
                + _table(["Part", "Description", "Qty", "Kind", "Line total"],
                         rows))

    if stage == STAGE_PURCHASING:
        rows = [[_esc(p.title), _esc(p.description), _qty(p.qty),
                 _money(p.unit_cost), _money(p.line_total)]
                for p in order.parts]
        return (header
                + _table(["Part", "Description", "Qty", "Unit", "Line total"],
                         rows)
                + f"<p><b>Order total:</b> {_money(order.total)}</p>"
                + "<p>[ ] PO issued<br/>[ ] Acknowledgement received</p>")

    if stage == STAGE_MANUFACTURING:
        rows = [[_esc(p.title), _esc(p.description), _qty(p.qty),
                 _esc(p.revision), _esc(p.material)]
                for p in order.make_parts]
        return (header
                + _table(["Part", "Description", "Qty", "Rev", "Material"],
                         rows))

    rows = [[_esc(p.title), _esc(p.description), _qty(p.qty)]
            for p in order.parts]
    return (header
            + f"<p>Expect {_qty(order.piece_count)} pcs across "
              f"{len(order.parts)} line items.</p>"
            + _table(["Part", "Description", "Qty"], rows))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wrike_mfg_tasks.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add wrike_mfg_tasks.py tests/test_wrike_mfg_tasks.py
git commit -m "feat(wrike-tasks): render per-stage task titles and descriptions"
```

---

## Task 9: Wrike client — subtask parents

**Files:**
- Modify: `wrike_rest_api.py:300-330`
- Modify: `tests/test_wrike_rest_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wrike_rest_api.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wrike_rest_api.py -k super_tasks -v`
Expected: FAIL with `TypeError: create_task() got an unexpected keyword argument 'super_task_ids'`

- [ ] **Step 3: Add the parameter**

In `wrike_rest_api.py`, add to `create_task`'s signature after `effort_hours`:

```python
        super_task_ids: Optional[List[str]] = None,
```

and inside the body, after the `custom_fields` block:

```python
        if super_task_ids:
            # Makes the new task a subtask of each id. Wrike has no separate
            # "create subtask" endpoint — parentage is set at creation.
            fields["superTasks"] = list(super_task_ids)
```

Update the docstring to mention it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wrike_rest_api.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add wrike_rest_api.py tests/test_wrike_rest_api.py
git commit -m "feat(wrike): create a task as a subtask of an existing parent"
```

---

## Task 10: Wrike client — dependencies

Uses the `relationType` spelling confirmed by Task 1.

**Files:**
- Modify: `wrike_rest_api.py`
- Modify: `tests/test_wrike_rest_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wrike_rest_api.py`:

```python
async def test_add_dependency_posts_to_the_successor():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"data": [{"id": "IEAG1"}]})

    api = make_api(handler)
    result = await api.add_dependency("IEAASUCC", "IEAAPRED")

    assert result["error"] is False
    assert seen["url"].endswith("/tasks/IEAASUCC/dependencies")
    assert "predecessorId=IEAAPRED" in seen["body"]
    assert "relationType=FinishToStart" in seen["body"]


async def test_add_dependency_surfaces_an_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errorDescription": "bad relation"})

    api = make_api(handler)
    result = await api.add_dependency("IEAASUCC", "IEAAPRED")

    assert result["error"] is True
    assert "bad relation" in result["data"]
```

If Task 1's probe reported a different accepted spelling, use that string in both the test and the implementation.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wrike_rest_api.py -k dependency -v`
Expected: FAIL with `AttributeError: 'WrikeRestAPI' object has no attribute 'add_dependency'`

- [ ] **Step 3: Implement the method**

Add to `wrike_rest_api.py`, after `move_task`:

```python
    # Confirmed against the live API by
    # scripts/probes/probe_wrike_dependency.py — Wrike rejects other
    # spellings of relationType outright.
    DEPENDENCY_FINISH_TO_START = "FinishToStart"

    async def add_dependency(
        self,
        task_id: str,
        predecessor_id: str,
        relation_type: str = DEPENDENCY_FINISH_TO_START,
    ) -> Dict[str, Any]:
        """Make ``task_id`` depend on ``predecessor_id``.

        The POST goes to the *successor* — the task that waits. A
        finish-to-start link is what makes a slip in one stage cascade to the
        stages after it on the Wrike Gantt.
        """
        return await self._request(
            "POST", f"/tasks/{task_id}/dependencies",
            data={"predecessorId": predecessor_id,
                  "relationType": relation_type},
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wrike_rest_api.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add wrike_rest_api.py tests/test_wrike_rest_api.py
git commit -m "feat(wrike): add finish-to-start task dependencies"
```

---

## Task 11: Engine — re-run detection and creation

**Files:**
- Modify: `wrike_mfg_tasks.py`
- Modify: `tests/test_wrike_mfg_tasks.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wrike_mfg_tasks.py`:

```python
# ------------------------------------------------------------- fake wrike

class FakeWrike:
    """Records calls and hands back sequential task ids.

    ``existing`` is the list of task dicts already in the folder.
    ``fail_titles`` are titles whose create should fail.
    """

    def __init__(self, existing=(), fail_titles=(), dependency_fails=False):
        self.existing = list(existing)
        self.fail_titles = set(fail_titles)
        self.dependency_fails = dependency_fails
        self.created = []
        self.dependencies = []
        self.search_calls = []
        self._next = 0

    async def search_tasks(self, title=None, status=None, folder_id=None,
                           page_size=100):
        self.search_calls.append({"title": title, "status": status,
                                  "folder_id": folder_id})
        rows = self.existing
        if title:
            rows = [r for r in rows if title.lower() in r["title"].lower()]
        return {"error": False, "status_code": 200,
                "data": {"data": rows, "count": len(rows)}}

    async def create_task(self, folder_id, title, description=None,
                          start_date=None, due_date=None, responsibles=None,
                          super_task_ids=None, **kwargs):
        self.created.append({
            "folder_id": folder_id, "title": title, "description": description,
            "start_date": start_date, "due_date": due_date,
            "responsibles": responsibles, "super_task_ids": super_task_ids,
        })
        if title in self.fail_titles:
            return {"error": True, "status_code": 400, "data": "nope"}
        self._next += 1
        return {"error": False, "status_code": 200,
                "data": {"data": [{"id": f"IEAA{self._next}"}]}}

    async def add_dependency(self, task_id, predecessor_id,
                             relation_type="FinishToStart"):
        self.dependencies.append({"task_id": task_id,
                                  "predecessor_id": predecessor_id,
                                  "relation_type": relation_type})
        if self.dependency_fails:
            return {"error": True, "status_code": 400, "data": "no link"}
        return {"error": False, "status_code": 200, "data": {"data": []}}


OWNERS = {wmt.STAGE_PURCHASING: "KUAAP", wmt.STAGE_MANUFACTURING: "KUAAM",
          wmt.STAGE_SHIPPING: "KUAAS"}


async def _create(orders, wrike, build="CD-001608"):
    return await wmt.create_orders(
        wrike, folder_id="IEAF1", build=build, orders=orders,
        owners=OWNERS, source_name="CD-001608 Purchasing Sheet.xlsx")


# ----------------------------------------------------------------- creation

async def test_a_make_order_creates_a_parent_and_three_subtasks():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200", lead_time_days=15)])
    wrike = FakeWrike()
    result = await _create([order], wrike)

    assert result.orders_created == 1
    assert [c["title"] for c in wrike.created] == [
        "CD-001608 - Xometry",
        "CD-001608 Xometry - 1. Purchasing",
        "CD-001608 Xometry - 2. Manufacturing",
        "CD-001608 Xometry - 3. Shipping",
    ]
    assert wrike.created[0]["super_task_ids"] is None
    assert wrike.created[1]["super_task_ids"] == ["IEAA1"]


async def test_a_buy_only_order_creates_two_subtasks():
    order = _scheduled_order(
        [wmt.OrderPart(title="ISO", kind=wmt.KIND_BUY, lead_time_days=3)],
        supplier="McMaster-Carr")
    wrike = FakeWrike()
    await _create([order], wrike)

    assert len(wrike.created) == 3          # parent + 2 stages
    assert "Manufacturing" not in " ".join(c["title"] for c in wrike.created)


async def test_stages_are_chained_finish_to_start():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike()
    await _create([order], wrike)

    # subtasks are IEAA2, IEAA3, IEAA4 under parent IEAA1
    assert wrike.dependencies == [
        {"task_id": "IEAA3", "predecessor_id": "IEAA2",
         "relation_type": "FinishToStart"},
        {"task_id": "IEAA4", "predecessor_id": "IEAA3",
         "relation_type": "FinishToStart"},
    ]


async def test_a_buy_only_order_chains_purchasing_straight_to_shipping():
    order = _scheduled_order(
        [wmt.OrderPart(title="ISO", kind=wmt.KIND_BUY)],
        supplier="McMaster-Carr")
    wrike = FakeWrike()
    await _create([order], wrike)

    assert len(wrike.dependencies) == 1
    assert wrike.dependencies[0]["predecessor_id"] == "IEAA2"
    assert wrike.dependencies[0]["task_id"] == "IEAA3"


async def test_each_stage_carries_its_own_owner():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike()
    await _create([order], wrike)

    assert wrike.created[1]["responsibles"] == ["KUAAP"]
    assert wrike.created[2]["responsibles"] == ["KUAAM"]
    assert wrike.created[3]["responsibles"] == ["KUAAS"]


async def test_the_parent_spans_the_order_and_belongs_to_purchasing():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200",
                                            lead_time_days=15)])
    wrike = FakeWrike()
    await _create([order], wrike)

    parent = wrike.created[0]
    assert parent["start_date"] == "2026-08-03"
    assert parent["due_date"] == "2026-08-28"
    assert parent["responsibles"] == ["KUAAP"]


async def test_dates_are_sent_as_plain_iso_dates():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike()
    await _create([order], wrike)

    assert wrike.created[1]["start_date"] == "2026-08-03"
    assert wrike.created[1]["due_date"] == "2026-08-04"


# ------------------------------------------------------------------ re-runs

async def test_an_existing_order_is_skipped_and_reported():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike(existing=[{"id": "OLD", "title": "CD-001608 - Xometry",
                                 "status": "Active"}])
    result = await _create([order], wrike)

    assert result.orders_created == 0
    assert result.orders_skipped == 1
    assert wrike.created == []
    assert "CD-001608 - Xometry" in result.skipped_titles


async def test_a_completed_order_still_counts_as_existing():
    """Wrike's folder listing can filter completed tasks out. Sending no
    status param is what keeps a finished order from being recreated."""
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike(existing=[{"id": "OLD", "title": "CD-001608 - Xometry",
                                 "status": "Completed"}])
    result = await _create([order], wrike)

    assert result.orders_skipped == 1
    assert all(c["status"] is None for c in wrike.search_calls)


async def test_a_substring_title_match_is_not_treated_as_existing():
    """Wrike's title filter is a substring match; the comparison is exact."""
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike(existing=[{"id": "OLD",
                                 "title": "CD-001608 - Xometry Rework",
                                 "status": "Active"}])
    result = await _create([order], wrike)

    assert result.orders_created == 1


async def test_a_new_supplier_is_created_while_an_existing_one_is_skipped():
    made = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    bought = _scheduled_order([wmt.OrderPart(title="ISO", kind=wmt.KIND_BUY)],
                              supplier="McMaster-Carr")
    wrike = FakeWrike(existing=[{"id": "OLD", "title": "CD-001608 - Xometry",
                                 "status": "Active"}])
    result = await _create([made, bought], wrike)

    assert result.orders_created == 1
    assert result.orders_skipped == 1
    assert wrike.created[0]["title"] == "CD-001608 - McMaster-Carr"


# --------------------------------------------------------------- failures

async def test_a_failed_subtask_reports_what_was_created_and_moves_on():
    made = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    bought = _scheduled_order([wmt.OrderPart(title="ISO", kind=wmt.KIND_BUY)],
                              supplier="McMaster-Carr")
    wrike = FakeWrike(fail_titles=["CD-001608 Xometry - 2. Manufacturing"])
    result = await _create([made, bought], wrike)

    assert result.failures
    assert any("Manufacturing" in f for f in result.failures)
    # the next supplier still ran
    assert any(c["title"] == "CD-001608 - McMaster-Carr" for c in wrike.created)


async def test_a_failed_parent_skips_its_subtasks_entirely():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike(fail_titles=["CD-001608 - Xometry"])
    result = await _create([order], wrike)

    assert len(wrike.created) == 1
    assert result.orders_created == 0
    assert result.failures


async def test_a_dependency_failure_leaves_the_tasks_in_place():
    """The tasks are the product; the link is the garnish."""
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike(dependency_fails=True)
    result = await _create([order], wrike)

    assert result.orders_created == 1
    assert len(wrike.created) == 4
    assert result.dependency_failures
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wrike_mfg_tasks.py -k "creation or re_run or existing or failure or chained" -v`
Expected: FAIL with `AttributeError: module 'wrike_mfg_tasks' has no attribute 'create_orders'`

- [ ] **Step 3: Implement creation**

Append to `wrike_mfg_tasks.py`:

```python
# ---------------------------------------------------------------------------
# Stage 6: create the tasks
# ---------------------------------------------------------------------------

@dataclass
class CreateResult:
    orders_created: int = 0
    orders_skipped: int = 0
    task_ids: list[str] = field(default_factory=list)
    skipped_titles: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    dependency_failures: list[str] = field(default_factory=list)


def _rows_of(resp: dict[str, Any]) -> list[dict[str, Any]]:
    data = resp.get("data")
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            return [r for r in inner if isinstance(r, dict)]
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def _new_task_id(resp: dict[str, Any]) -> str:
    rows = _rows_of(resp)
    return _text(rows[0].get("id")) if rows else ""


async def _title_exists(wrike, folder_id: str, title: str) -> bool:
    """Whether the folder already holds a task with exactly this title.

    No status filter is passed: a completed order filtered out of the result
    would be recreated on the next run. Wrike's own title filter is a
    substring match, so the exact comparison happens here.
    """
    resp = await wrike.search_tasks(title=title, folder_id=folder_id)
    if resp.get("error"):
        # An unreadable folder must not silently duplicate a board. Treat the
        # order as existing and let the caller report the skip.
        logger.warning("Existence check failed for %r: %s", title,
                       resp.get("data"))
        return True
    return any(_text(r.get("title")) == title for r in _rows_of(resp))


async def create_orders(
    wrike,
    *,
    folder_id: str,
    build: str,
    orders: list[SupplierOrder],
    owners: dict[str, str],
    source_name: str,
    on_progress: Optional[ProgressFn] = None,
) -> CreateResult:
    """Create one parent task plus its stage subtasks for every order.

    Serial, both across orders and within one: creation is cheap, and serial
    keeps the log readable and the API gentle.

    There is no rollback — Wrike has no transaction. A trio that fails halfway
    is reported with the ids that *were* created so it can be cleaned up by
    hand, and the loop moves to the next supplier.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)
    result = CreateResult()

    for order in orders:
        title = parent_title(build, order)

        if await _title_exists(wrike, folder_id, title):
            result.orders_skipped += 1
            result.skipped_titles.append(title)
            progress(f"  {title}: already exists - skipped")
            continue

        owner = owners.get(STAGE_PURCHASING)
        parent_resp = await wrike.create_task(
            folder_id, title,
            description=render_description(order, STAGE_PARENT,
                                           source_name=source_name),
            start_date=order.start.isoformat() if order.start else None,
            due_date=order.due.isoformat() if order.due else None,
            responsibles=[owner] if owner else None,
        )
        parent_id = _new_task_id(parent_resp)
        if parent_resp.get("error") or not parent_id:
            result.failures.append(f"{title}: {parent_resp.get('data')}")
            progress(f"  {title}: FAILED - {parent_resp.get('data')}")
            continue

        result.task_ids.append(parent_id)
        progress(f"  {title}: created")

        by_stage = {s.stage: s for s in order.schedule}
        previous_id = ""
        for stage in order.stages:
            sched = by_stage.get(stage)
            stage_owner = owners.get(stage)
            sub_title = stage_title(build, order, stage)
            resp = await wrike.create_task(
                folder_id, sub_title,
                description=render_description(order, stage,
                                               source_name=source_name),
                start_date=sched.start.isoformat() if sched else None,
                due_date=sched.due.isoformat() if sched else None,
                responsibles=[stage_owner] if stage_owner else None,
                super_task_ids=[parent_id],
            )
            task_id = _new_task_id(resp)
            if resp.get("error") or not task_id:
                result.failures.append(f"{sub_title}: {resp.get('data')}")
                progress(f"    {stage}: FAILED - {resp.get('data')}")
                continue

            result.task_ids.append(task_id)
            progress(f"    {stage}: created")

            if previous_id:
                dep = await wrike.add_dependency(task_id, previous_id)
                if dep.get("error"):
                    # The tasks are the product; the link is the garnish.
                    result.dependency_failures.append(
                        f"{sub_title}: {dep.get('data')}")
                    progress(f"    {stage}: dependency not linked")
            previous_id = task_id

        result.orders_created += 1

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wrike_mfg_tasks.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add wrike_mfg_tasks.py tests/test_wrike_mfg_tasks.py
git commit -m "feat(wrike-tasks): create supplier trios and skip existing orders"
```

---

## Task 12: GUI dialog

No tests — the GUI is untested throughout this repo. Verification is manual.

**Files:**
- Create: `gui/wrike_mfg_tasks.py`

- [ ] **Step 1: Write the dialog**

```python
# gui/wrike_mfg_tasks.py
"""Tkinter dialog for the BOM → Wrike manufacturing task builder.

Opens as a Toplevel from the launcher with the live Vault session and a Wrike
client attached. Load & Reconcile reads the purchasing sheet and checks every
part's supplier against Vault; Preview turns the resolved rows into a task
plan; Create Tasks writes it to Wrike.

Preview is gated on zero unresolved rows, and Create is gated on a fresh
Preview — the guard against writing a board twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sys
import threading
from datetime import date, datetime
from typing import Any, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from gui.release_workflow import (  # noqa: E402
    DARK_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY, WHITE,
    RUST_ORANGE, OLIVE_GREEN,
)

import wrike_mfg_tasks as wmt  # noqa: E402

logger = logging.getLogger(__name__)

# Row colours in the Parts table. Green needs nothing, amber has a proposal to
# accept, red blocks the Preview button.
TAG_OK = "ok"
TAG_PROPOSED = "proposed"
TAG_BLOCKED = "blocked"
TAG_EXCLUDED = "excluded"


class WrikeMfgTasksGUI:
    """Toplevel dialog. One per launch."""

    def __init__(self, master: tk.Misc, *, api: Any = None, vault_id: str = "",
                 wrike: Any = None, cfg: Optional[dict[str, Any]] = None) -> None:
        self.api = api
        self.vault_id = vault_id
        self.wrike = wrike
        self.cfg = cfg or {}
        self.settings = (self.cfg.get("wrike") or {}).get("mfg_tasks") or {}

        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.rows: list[wmt.ReconcileRow] = []
        self.orders: list[wmt.SupplierOrder] = []
        self.projects: list[dict[str, Any]] = []
        self.contacts: list[dict[str, Any]] = []
        self._busy = False
        self._created = False

        self.win = tk.Toplevel(master)
        self.win.title("BOM → Manufacturing Tasks")
        self.win.configure(bg=LIGHT_GRAY)
        self.win.geometry("1040x700")
        self.win.minsize(900, 560)

        self.sheet_path = tk.StringVar()
        self.build = tk.StringVar()
        self.project_label = tk.StringVar()
        self.start_date = tk.StringVar(value=date.today().isoformat())
        self.d_purchasing = tk.StringVar(
            value=str(self.settings.get("purchasing_days", 2)))
        self.d_manufacturing = tk.StringVar(
            value=str(self.settings.get("manufacturing_days", 10)))
        self.d_shipping = tk.StringVar(
            value=str(self.settings.get("shipping_days", 3)))
        self.owner_labels = {
            wmt.STAGE_PURCHASING: tk.StringVar(),
            wmt.STAGE_MANUFACTURING: tk.StringVar(),
            wmt.STAGE_SHIPPING: tk.StringVar(),
        }
        self.summary_text = tk.StringVar(
            value="Pick a purchasing sheet and click Load & Reconcile.")

        self._build_ui()
        # Retargeting the sheet invalidates everything on screen: without this,
        # browsing to a different workbook leaves the old plan in the table
        # with Create still enabled.
        self.sheet_path.trace_add("write", self._invalidate)
        self.win.after(120, self._drain)
        self._load_wrike_metadata()

    # -------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        top = tk.Frame(self.win, bg=WHITE, padx=12, pady=10)
        top.pack(fill="x")

        tk.Label(top, text="Purchasing sheet", bg=WHITE, fg=DARK_GRAY).grid(
            row=0, column=0, sticky="w")
        tk.Entry(top, textvariable=self.sheet_path, width=62).grid(
            row=0, column=1, columnspan=3, sticky="we", padx=6)
        tk.Button(top, text="Browse...", command=self._on_browse).grid(
            row=0, column=4, sticky="w")

        tk.Label(top, text="Build", bg=WHITE, fg=DARK_GRAY).grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        tk.Entry(top, textvariable=self.build, width=18).grid(
            row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        tk.Label(top, text="Wrike project", bg=WHITE, fg=DARK_GRAY).grid(
            row=1, column=2, sticky="e", pady=(6, 0))
        self.project_box = ttk.Combobox(top, textvariable=self.project_label,
                                        state="readonly", width=34)
        self.project_box.grid(row=1, column=3, columnspan=2, sticky="we",
                              padx=6, pady=(6, 0))

        sched = tk.Frame(top, bg=WHITE)
        sched.grid(row=2, column=0, columnspan=5, sticky="w", pady=(8, 0))
        tk.Label(sched, text="Start", bg=WHITE, fg=DARK_GRAY).pack(side="left")
        tk.Entry(sched, textvariable=self.start_date, width=12).pack(
            side="left", padx=(4, 12))
        for label, var in (("Purchasing", self.d_purchasing),
                           ("MFG fallback", self.d_manufacturing),
                           ("Shipping", self.d_shipping)):
            tk.Label(sched, text=label, bg=WHITE, fg=DARK_GRAY).pack(side="left")
            tk.Entry(sched, textvariable=var, width=5).pack(
                side="left", padx=(4, 12))
        tk.Label(sched, text="business days", bg=WHITE, fg=DARK_GRAY,
                 font=("Arial", 8, "italic")).pack(side="left")

        owners = tk.Frame(top, bg=WHITE)
        owners.grid(row=3, column=0, columnspan=5, sticky="w", pady=(8, 0))
        tk.Label(owners, text="Owners", bg=WHITE, fg=DARK_GRAY).pack(side="left")
        self.owner_boxes = {}
        for stage in (wmt.STAGE_PURCHASING, wmt.STAGE_MANUFACTURING,
                      wmt.STAGE_SHIPPING):
            tk.Label(owners, text=stage, bg=WHITE, fg=DARK_GRAY).pack(
                side="left", padx=(12, 4))
            box = ttk.Combobox(owners, textvariable=self.owner_labels[stage],
                               state="readonly", width=20)
            box.pack(side="left")
            self.owner_boxes[stage] = box

        buttons = tk.Frame(top, bg=WHITE)
        buttons.grid(row=4, column=0, columnspan=5, sticky="e", pady=(10, 0))
        self.btn_load = tk.Button(buttons, text="Load & Reconcile",
                                  command=self._on_load)
        self.btn_load.pack(side="left", padx=4)
        self.btn_preview = tk.Button(buttons, text="Preview",
                                     command=self._on_preview, state="disabled")
        self.btn_preview.pack(side="left", padx=4)
        self.btn_create = tk.Button(buttons, text="Create Tasks",
                                    command=self._on_create, state="disabled")
        self.btn_create.pack(side="left", padx=4)

        book = ttk.Notebook(self.win)
        book.pack(fill="both", expand=True, padx=12, pady=(10, 0))

        parts_frame = tk.Frame(book, bg=WHITE)
        self.parts = ttk.Treeview(
            parts_frame, show="headings",
            columns=("part", "desc", "kind", "sheet", "vault", "supplier",
                     "status"))
        for key, label, width in (
            ("part", "Part", 130), ("desc", "Description", 240),
            ("kind", "Kind", 55), ("sheet", "Sheet vendor", 140),
            ("vault", "Vault vendor", 140), ("supplier", "Supplier", 140),
            ("status", "Status", 120),
        ):
            self.parts.heading(key, text=label)
            self.parts.column(key, width=width, anchor="w")
        self.parts.tag_configure(TAG_OK, background=WHITE)
        self.parts.tag_configure(TAG_PROPOSED, background=PALE_BLUE)
        self.parts.tag_configure(TAG_BLOCKED, background=RUST_ORANGE,
                                 foreground=WHITE)
        self.parts.tag_configure(TAG_EXCLUDED, foreground=GRAY_BDR)
        self.parts.pack(fill="both", expand=True, side="left")
        self.parts.bind("<Double-1>", self._on_edit_supplier)
        ttk.Scrollbar(parts_frame, orient="vertical",
                      command=self.parts.yview).pack(side="right", fill="y")
        book.add(parts_frame, text="Parts")

        plan_frame = tk.Frame(book, bg=WHITE)
        self.plan = ttk.Treeview(
            plan_frame, show="headings",
            columns=("supplier", "stage", "start", "due", "owner", "state"))
        for key, label, width in (
            ("supplier", "Supplier", 180), ("stage", "Stage", 140),
            ("start", "Start", 100), ("due", "Due", 100),
            ("owner", "Owner", 160), ("state", "", 130),
        ):
            self.plan.heading(key, text=label)
            self.plan.column(key, width=width, anchor="w")
        self.plan.pack(fill="both", expand=True)
        book.add(plan_frame, text="Task plan")
        self.book = book

        bottom = tk.Frame(self.win, bg=LIGHT_GRAY, padx=12, pady=8)
        bottom.pack(fill="both")
        tk.Button(bottom, text="Accept all proposals",
                  command=self._on_accept_all).pack(side="left")
        tk.Button(bottom, text="Exclude selected",
                  command=self._on_exclude).pack(side="left", padx=6)
        tk.Label(bottom, textvariable=self.summary_text, bg=LIGHT_GRAY,
                 fg=DARK_BLUE).pack(side="left", padx=12)
        tk.Button(bottom, text="Close", command=self.win.destroy).pack(
            side="right")

        self.log = tk.Text(self.win, height=7, bg=WHITE, fg=DARK_GRAY,
                           wrap="word")
        self.log.pack(fill="both", padx=12, pady=(0, 12))

    # ---------------------------------------------------------- helpers

    def _say(self, message: str) -> None:
        self.log.insert("end", message + "\n")
        self.log.see("end")

    def _invalidate(self, *_args) -> None:
        self.orders = []
        self._created = False
        self.btn_preview.configure(state="disabled")
        self.btn_create.configure(state="disabled")
        for item in self.plan.get_children():
            self.plan.delete(item)

    def _durations(self) -> wmt.Durations:
        def _int(var, fallback):
            try:
                return max(1, int(var.get().strip()))
            except (TypeError, ValueError):
                return fallback
        return wmt.Durations(
            purchasing=_int(self.d_purchasing, 2),
            manufacturing=_int(self.d_manufacturing, 10),
            shipping=_int(self.d_shipping, 3),
        )

    def _start(self) -> Optional[date]:
        try:
            return datetime.strptime(self.start_date.get().strip(),
                                     "%Y-%m-%d").date()
        except ValueError:
            return None

    def _selected_id(self, options, label_var, id_key="id"):
        label = label_var.get()
        for row in options:
            if self._label_of(row) == label:
                return row.get(id_key, "")
        return ""

    @staticmethod
    def _label_of(row: dict[str, Any]) -> str:
        return str(row.get("title") or row.get("firstName", "") + " "
                   + row.get("lastName", "")).strip()

    def _run(self, coro_factory, done_key: str) -> None:
        """Run an async engine call on a worker thread."""
        if self._busy:
            return
        self._busy = True

        def worker():
            try:
                result = asyncio.run(coro_factory())
                self.q.put((done_key, result))
            except Exception as exc:  # noqa: BLE001
                logger.exception("%s failed", done_key)
                self.q.put(("error", str(exc)))
            finally:
                self.q.put(("idle", None))

        threading.Thread(target=worker, daemon=True).start()

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._say(payload)
                elif kind == "idle":
                    self._busy = False
                elif kind == "error":
                    self._say(f"ERROR: {payload}")
                    messagebox.showerror("Failed", payload, parent=self.win)
                elif kind == "reconciled":
                    self.rows = payload
                    self._refresh_parts()
                elif kind == "created":
                    self._report_created(payload)
                elif kind == "metadata":
                    self._apply_metadata(payload)
        except queue.Empty:
            pass
        self.win.after(120, self._drain)

    # ------------------------------------------------------------ wrike

    def _load_wrike_metadata(self) -> None:
        if not self.wrike:
            self._say("No Wrike client — check the wrike block in config.json.")
            return

        async def fetch():
            projects = await self.wrike.list_projects()
            contacts = await self.wrike.list_contacts()
            return projects, contacts

        self._run(fetch, "metadata")

    def _apply_metadata(self, payload) -> None:
        projects, contacts = payload
        self.projects = [r for r in _rows(projects)]
        self.contacts = [r for r in _rows(contacts)]
        self.project_box["values"] = [self._label_of(p) for p in self.projects]
        names = [self._label_of(c) for c in self.contacts]
        for stage, box in self.owner_boxes.items():
            box["values"] = names
            saved = (self.settings.get("owners") or {}).get(stage)
            for contact in self.contacts:
                if contact.get("id") == saved:
                    self.owner_labels[stage].set(self._label_of(contact))
        saved_project = self.settings.get("project_id")
        for project in self.projects:
            if project.get("id") == saved_project:
                self.project_label.set(self._label_of(project))
        self._say(f"Wrike: {len(self.projects)} projects, "
                  f"{len(self.contacts)} contacts.")

    # ----------------------------------------------------------- actions

    def _on_browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.win, title="Select a generated purchasing sheet",
            filetypes=[("Excel workbook", "*.xlsx")],
        )
        if path:
            self.sheet_path.set(path)

    def _on_load(self) -> None:
        path = self.sheet_path.get().strip()
        if not path:
            messagebox.showwarning("No sheet", "Pick a purchasing sheet first.",
                                   parent=self.win)
            return

        parts, assembly, error = wmt.load_order_parts(
            path, on_progress=lambda m: self.q.put(("log", m)))
        if error:
            messagebox.showerror("Cannot read sheet", error, parent=self.win)
            self._say(f"ERROR: {error}")
            return
        if not parts:
            self._say("No orderable parts on this sheet.")
            self.summary_text.set("No orderable parts.")
            return
        if assembly and not self.build.get().strip():
            self.build.set(assembly)

        self._say(f"{len(parts)} line items. Checking suppliers against "
                  f"Vault...")
        self._run(
            lambda: wmt.reconcile_vendors(
                self.api, self.vault_id, parts,
                on_progress=lambda m: self.q.put(("log", m))),
            "reconciled",
        )

    def _refresh_parts(self) -> None:
        for item in self.parts.get_children():
            self.parts.delete(item)
        for index, row in enumerate(self.rows):
            if row.excluded:
                tag = TAG_EXCLUDED
            elif row.chosen:
                tag = TAG_OK
            elif row.proposal:
                tag = TAG_PROPOSED
            else:
                tag = TAG_BLOCKED
            self.parts.insert(
                "", "end", iid=str(index), tags=(tag,),
                values=(row.part.title, row.part.description, row.part.kind,
                        row.part.sheet_vendor, row.vault_vendor,
                        row.chosen or row.proposal or "-- pick", row.status))
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        unresolved = wmt.unresolved_count(self.rows)
        suppliers = len({wmt.vendor_key(r.chosen) for r in self.rows
                         if r.chosen and not r.excluded})
        self.summary_text.set(
            f"{len(self.rows)} rows - {suppliers} suppliers - "
            f"{unresolved} unresolved")
        ready = bool(self.rows) and unresolved == 0
        self.btn_preview.configure(state="normal" if ready else "disabled")
        if not ready:
            self.btn_create.configure(state="disabled")

    def _on_accept_all(self) -> None:
        accepted = wmt.accept_proposals(self.rows)
        self._say(f"Accepted {accepted} proposed suppliers.")
        self._invalidate()
        self._refresh_parts()

    def _on_exclude(self) -> None:
        for iid in self.parts.selection():
            self.rows[int(iid)].excluded = True
        self._invalidate()
        self._refresh_parts()

    def _on_edit_supplier(self, _event) -> None:
        selection = self.parts.selection()
        if not selection:
            return
        row = self.rows[int(selection[0])]
        dialog = tk.Toplevel(self.win)
        dialog.title(f"Supplier for {row.part.title}")
        var = tk.StringVar(value=row.chosen or row.proposal
                           or row.part.sheet_vendor or row.vault_vendor)
        tk.Label(dialog, text=f"Sheet: {row.part.sheet_vendor or '--'}    "
                              f"Vault: {row.vault_vendor or '--'}").pack(
            padx=12, pady=(12, 4))
        entry = tk.Entry(dialog, textvariable=var, width=32)
        entry.pack(padx=12, pady=4)
        entry.focus_set()

        def apply():
            row.chosen = var.get().strip()
            row.excluded = False
            dialog.destroy()
            self._invalidate()
            self._refresh_parts()

        tk.Button(dialog, text="Use this supplier", command=apply).pack(
            padx=12, pady=(4, 12))

    def _on_preview(self) -> None:
        start = self._start()
        if start is None:
            messagebox.showwarning(
                "Bad start date",
                "Enter the start date as YYYY-MM-DD.", parent=self.win)
            return
        if not self.build.get().strip():
            messagebox.showwarning("No build", "Enter a build number.",
                                   parent=self.win)
            return

        self.orders = wmt.schedule_orders(
            wmt.group_orders(self.rows), start=start,
            durations=self._durations())

        for item in self.plan.get_children():
            self.plan.delete(item)
        for order in self.orders:
            by_stage = {s.stage: s for s in order.schedule}
            self.plan.insert("", "end", values=(
                order.supplier, "(parent)",
                order.start.isoformat() if order.start else "",
                order.due.isoformat() if order.due else "",
                self.owner_labels[wmt.STAGE_PURCHASING].get(), "new"))
            for stage in order.stages:
                sched = by_stage[stage]
                self.plan.insert("", "end", values=(
                    "", stage, sched.start.isoformat(), sched.due.isoformat(),
                    self.owner_labels[stage].get(), ""))

        tasks = sum(len(o.stages) + 1 for o in self.orders)
        self._say(f"Plan: {len(self.orders)} orders, {tasks} tasks.")
        self.book.select(1)
        self._created = False
        self.btn_create.configure(state="normal")

    def _on_create(self) -> None:
        if self._created:
            return
        folder_id = self._selected_id(self.projects, self.project_label)
        if not folder_id:
            messagebox.showwarning("No project",
                                   "Pick a Wrike project first.",
                                   parent=self.win)
            return
        owners = {stage: self._selected_id(self.contacts, var)
                  for stage, var in self.owner_labels.items()}
        build = self.build.get().strip()
        source = os.path.basename(self.sheet_path.get().strip())

        self.btn_create.configure(state="disabled")
        self._created = True
        self._save_settings(folder_id, owners)
        self._run(
            lambda: wmt.create_orders(
                self.wrike, folder_id=folder_id, build=build,
                orders=self.orders, owners=owners, source_name=source,
                on_progress=lambda m: self.q.put(("log", m))),
            "created",
        )

    def _report_created(self, result: wmt.CreateResult) -> None:
        self._say(
            f"Created {result.orders_created} orders "
            f"({len(result.task_ids)} tasks), "
            f"skipped {result.orders_skipped}, "
            f"{len(result.failures)} failures.")
        for failure in result.failures:
            self._say(f"  FAILED {failure}")
        for failure in result.dependency_failures:
            self._say(f"  dependency not linked: {failure}")
        self.summary_text.set(
            f"{result.orders_created} created - "
            f"{result.orders_skipped} skipped")

    def _save_settings(self, folder_id: str, owners: dict[str, str]) -> None:
        """Remember the picks so they are set once, not every run.

        Written back to config.json, not just the in-memory dict — the point
        is that the next session starts with them already filled in. A write
        failure is logged and shrugged off: losing a preference must never
        take down a run that already created tasks.
        """
        durations = self._durations()
        block = self.cfg.setdefault("wrike", {}).setdefault("mfg_tasks", {})
        block["project_id"] = folder_id
        block["owners"] = {k: v for k, v in owners.items() if v}
        block["purchasing_days"] = durations.purchasing
        block["manufacturing_days"] = durations.manufacturing
        block["shipping_days"] = durations.shipping

        path = self.cfg.get("__path__") or os.path.join(PROJECT_ROOT,
                                                        "config.json")
        try:
            with open(path, encoding="utf-8") as fh:
                on_disk = json.load(fh)
            on_disk.setdefault("wrike", {})["mfg_tasks"] = block
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(on_disk, fh, indent=4)
            self._say("Saved your project, owners and durations to config.json.")
        except Exception as exc:  # noqa: BLE001 — a preference is not the work
            logger.warning("Could not save mfg_tasks settings: %s", exc)
            self._say(f"Could not save settings to config.json: {exc}")


def _rows(resp: dict[str, Any]) -> list[dict[str, Any]]:
    data = resp.get("data") if isinstance(resp, dict) else None
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            return [r for r in inner if isinstance(r, dict)]
    return []


def launch_gui(*, api=None, vault_id="", wrike=None, cfg=None, parent=None):
    """Open the dialog as a child of the launcher window."""
    return WrikeMfgTasksGUI(parent, api=api, vault_id=vault_id, wrike=wrike,
                            cfg=cfg)
```

- [ ] **Step 2: Verify it imports without a display error**

Run: `python -c "import ast,sys; ast.parse(open('gui/wrike_mfg_tasks.py',encoding='utf-8').read()); print('parsed')"`
Expected: `parsed`

- [ ] **Step 3: Verify the palette names exist**

Run: `python -c "from gui.release_workflow import DARK_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY, WHITE, RUST_ORANGE, OLIVE_GREEN; print('ok')"`
Expected: `ok`. If any name is missing, substitute the nearest one that `gui/publish_bom.py` imports.

- [ ] **Step 4: Commit**

```bash
git add gui/wrike_mfg_tasks.py
git commit -m "feat(wrike-tasks): Tk dialog for reconcile, preview and create"
```

---

## Task 13: Launcher tile and config

**Files:**
- Modify: `gui/launcher.py` (tile list around line 628, handlers around line 1036)
- Modify: `config.json.example`

- [ ] **Step 1: Add the tile**

In `gui/launcher.py`, after the `"BOM → Publish Deliverables"` `_tool_row` block:

```python
        self._tool_row(
            body,
            "BOM → Manufacturing Tasks",
            "Load a generated purchasing sheet, confirm each part's supplier "
            "against Vault, and create a Wrike task per supplier order — "
            "purchasing, manufacturing and shipping, chained and dated.",
            "Open Task Builder",
            self._on_open_wrike_mfg_tasks,
            primary=False,
        )
```

- [ ] **Step 2: Add the handler**

After `_on_open_publish_bom`:

```python
    def _on_open_wrike_mfg_tasks(self) -> None:
        if not (self.api and self.vault_id):
            messagebox.showwarning(
                "Not signed in",
                "Click Reconnect first — the task builder checks each part's "
                "supplier against Vault.",
                parent=self.root,
            )
            return
        wrike_cfg = self.cfg.get("wrike") or {}
        token = wrike_cfg.get("token")
        if not token or token.startswith("your-wrike"):
            messagebox.showwarning(
                "Wrike not configured",
                "Add a wrike block with a permanent access token to "
                "config.json before creating tasks.",
                parent=self.root,
            )
            return
        try:
            from wrike_rest_api import WrikeRestAPI, DEFAULT_BASE_URL
            from gui.wrike_mfg_tasks import launch_gui as launch_task_gui
        except ImportError as exc:
            messagebox.showerror(
                "Task builder unavailable", str(exc), parent=self.root,
            )
            return
        wrike = WrikeRestAPI(
            token=token,
            base_url=wrike_cfg.get("base_url", DEFAULT_BASE_URL),
            allowed_folders=wrike_cfg.get("allowed_folders") or None,
        )
        launch_task_gui(api=self.api, vault_id=self.vault_id, wrike=wrike,
                        cfg=self.cfg, parent=self.root)
        self.status_var.set("Launching BOM → Manufacturing Tasks…")
```

- [ ] **Step 3: Add the config defaults**

The GUI writes this block back on every successful create, so the example
file needs it present for the shape to be obvious.

In `config.json.example`, inside the `wrike` block:

```json
        "mfg_tasks": {
            "project_id": "",
            "owners": {},
            "purchasing_days": 2,
            "manufacturing_days": 10,
            "shipping_days": 3
        }
```

- [ ] **Step 4: Verify the launcher still parses and the example is valid JSON**

Run: `python -c "import ast,json; ast.parse(open('gui/launcher.py',encoding='utf-8').read()); json.load(open('config.json.example',encoding='utf-8')); print('ok')"`
Expected: `ok`

- [ ] **Step 5: Launch the dashboard and open the tool**

Run: `python app.py`

Confirm: the **BOM → Manufacturing Tasks** tile appears; clicking **Open Task Builder** opens the dialog; the Wrike project and owner dropdowns populate.

- [ ] **Step 6: Commit**

```bash
git add gui/launcher.py config.json.example
git commit -m "feat(launcher): add the BOM to Manufacturing Tasks tile"
```

---

## Task 14: End-to-end verification against live data

The spec's live-verification section is the deliverable here, matching how Publish Deliverables was proven.

**Files:**
- Modify: `docs/superpowers/specs/2026-07-29-wrike-mfg-tasks-design.md`

- [ ] **Step 1: Generate a real purchasing sheet**

Open the launcher, run **BOM → Purchasing Sheet** for a real assembly, and fill in suppliers for the Make parts.

- [ ] **Step 2: Run a reconcile and record the result**

Load that sheet in the task builder and click **Load & Reconcile**. Record: row count, supplier count, and the count in each reconcile status.

**Establish ground truth independently** — query Vault directly for two or three parts' Vendor property rather than restating the tool's own output.

- [ ] **Step 3: Create tasks for exactly one supplier**

Exclude every row except one supplier's, then Preview and Create. Confirm in Wrike:
- the parent task exists with the full part table
- its subtasks are numbered and nested under it
- the dependency arrows appear on the Gantt
- the dates match the preview

- [ ] **Step 4: Run it a second time and confirm the skip**

Reload the same sheet, resolve the same rows, Preview, Create. Expected: `orders_created=0`, `orders_skipped=1`.

Then mark that parent task **Completed** in Wrike and repeat. Expected: still skipped. If it is recreated, `_title_exists` needs an explicit status list — fix it and add the regression test.

- [ ] **Step 5: Write the results into the spec**

Add a `## Live verification, <date>` section recording the counts, the supplier used, the task ids created, and anything the real data exposed that the fixtures did not.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-07-29-wrike-mfg-tasks-design.md
git commit -m "docs(wrike-tasks): record the live verification run"
```

---

## Task 15: README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the tool**

Add a section after the Property Check section:

```markdown
### BOM → Manufacturing Tasks

Turn a purchasing sheet into Wrike work. Load a workbook generated by
**BOM → Purchasing Sheet**, and the tool reconciles every part's supplier
against the Vault `Vendor` property, groups the parts into one order per
supplier, and creates a Wrike parent task with dependency-chained
**Purchasing → Manufacturing → Shipping** subtasks.

One trio per supplier, never one per part: eleven screws from McMaster-Carr
are one order with eleven line items. A supplier with nothing to make skips
the Manufacturing task — nothing is manufactured, so lead time drives Shipping
instead.

Three steps, each gating the next:

1. **Load & Reconcile** — reads the sheet, looks each part up in Vault.
   Agreeing suppliers are accepted automatically; a blank on one side is
   proposed for one-click acceptance; a genuine disagreement blocks.
2. **Preview** — enabled only when nothing is unresolved. Shows the task plan
   with dates.
3. **Create Tasks** — enabled only after a Preview, and disabled once used.
   A supplier that already has tasks is skipped, not duplicated.

Needs both a live Vault session and a configured `wrike` block. Add
`wrike.mfg_tasks` to remember the stage owners and default durations:

```json
"mfg_tasks": {
    "project_id": "IEAF...",
    "owners": {"Purchasing": "KUAA...", "Manufacturing": "KUAA...",
               "Shipping": "KUAA..."},
    "purchasing_days": 2,
    "manufacturing_days": 10,
    "shipping_days": 3
}
```
```

Add to the config table:

```markdown
| `wrike.mfg_tasks` | Defaults for BOM → Manufacturing Tasks: Wrike project id, per-stage owner contact ids, and stage durations in business days. Written back by the tool when you create tasks. |
```

Add to the project layout block, after `publish_bom.py`:

```
├── wrike_mfg_tasks.py          # BOM → Wrike task engine (reconcile, group, schedule, create)
```

and under `gui/`, after `publish_bom.py`:

```
    └── wrike_mfg_tasks.py      # BOM → Manufacturing Tasks GUI
```

- [ ] **Step 2: Run the whole suite one last time**

Run: `python -m pytest -q`
Expected: PASS, no failures.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): document BOM to Manufacturing Tasks"
```

---

## Spec coverage

| Spec section | Task |
| --- | --- |
| Reading the sheet (header row, A1, HEADER_LABELS, Sub Total, roll-ups, note, duplicates) | 2, 3, 4, 5 |
| Supplier reconcile (statuses, normalization, basename guard, propDefIds, proposals) | 6 |
| Grouping (supplier key, mixed supplier, Buy-only) | 7 |
| Scheduling (business days, lead-time routing, fallbacks) | 7 |
| Task shape (titles, separator, parent dates/owner, HTML descriptions) | 8, 11 |
| Wrike client additions (superTasks, dependencies) | 1, 9, 10 |
| Creation (serial, partial failure, dependency failure) | 11 |
| Re-run detection (exact title, no status filter) | 1, 11, 14 |
| GUI (notebook, gating, accept-all, exclude, config memory) | 12, 13 |
| Error handling table | 3, 4, 5, 6, 11, 12, 13 |
| Testing section | 3, 4, 5, 6, 7, 8, 9, 10, 11 |
| Live verification | 14 |
