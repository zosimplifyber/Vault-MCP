# Publish BOM Deliverables Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given an Inventor BOM export, queue Vault job-server jobs that publish PDF drawings and STEP files for the Make parts and the top assembly, and report which Make parts have no drawing to publish.

**Architecture:** An async engine module (`publish_bom.py`) does all the work in three separable stages — parse the BOM to a list of file stems, scan Vault to resolve each stem to its model and drawing file versions, submit one job per resolved file. A Tk dialog (`gui/publish_bom.py`) drives those stages from a worker thread and renders the scan result in a table before anything is queued. The engine never imports Tk, so it is unit-testable headless.

**Tech Stack:** Python 3, `pandas` (BOM parsing, already a dependency), `asyncio` + `httpx` via the existing `VaultRestAPI`, `tkinter`/`ttk` for the dialog, `pytest`.

**Spec:** `docs/superpowers/specs/2026-07-28-publish-bom-deliverables-design.md`

---

## Background an engineer needs before starting

**The Vault job server publishes; it does not author.** A PDF job needs an
existing `.idw`/`.dwg`. A Make part with no drawing file is a *reported gap*,
never something the tool creates.

**Job params are PascalCase and the casing is load-bearing.** The job
processor's constructor rejects the job outright on wrong casing, and the REST
response echoes params back camelCased, which is misleading when debugging.
STEP jobs read both `UpdatePdfOption` and `UpdateViewOption` despite the
names; there is no `UpdateStpOption`. The working shapes already live in
`mcp_server.py:998-1069` — copy them, do not re-derive them.

**`api.submit_job` rejects an empty description** with Vault error 155, so
every job must carry one.

**Every `VaultRestAPI` method returns the same envelope:**
`{"error": bool, "status_code": int, "data": ...}`. Check `.get("error")`
before touching `.get("data")`.

**`coerce_bom_dataframe` flattens `Reference` onto `Make`** via
`SOURCE_VALUE_MAP` (`bom_purchasing.py:191-198`). The raw `BOM Structure`
column must be captured *before* coercion or the distinction is lost. This is
the single subtlest thing in this plan.

**Reference BOM:** `tests/fixtures/CD-001608-bom.xlsx` — 22 rows, 13
`Purchased`, 9 `Normal`, no `Part Number` column, one filename duplicated
across two rows.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `publish_bom.py` (create) | Engine. Parse, scan, submit. Async, no Tk, no I/O beyond the Vault API. |
| `gui/publish_bom.py` (create) | Tk Toplevel dialog. Worker thread, results table, log. |
| `gui/launcher.py` (modify) | One `_tool_row` tile + one `_on_open_publish_bom` handler. |
| `tests/test_publish_bom.py` (create) | Engine tests against fixtures and a fake `api`. |
| `tests/fixtures/CD-001608-bom.xlsx` | Already committed. |

Engine stages are pure functions over plain dataclasses so each is testable in
isolation: `load_publish_rows` touches only the filesystem, `scan_rows` and
`submit_jobs` touch only the API.

---

## Task 1: Engine skeleton — types and constants

**Files:**
- Create: `publish_bom.py`
- Test: `tests/test_publish_bom.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_publish_bom.py`:

```python
# tests/test_publish_bom.py
"""Unit tests for the BOM-driven deliverable publisher.

Parsing runs against the real production export
(``tests/fixtures/CD-001608-bom.xlsx``) plus synthetic exports built in-test
for the BOM Structure values that file happens not to contain. Vault access is
faked — nothing here touches the network.
"""
import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import publish_bom  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
REAL_BOM = os.path.join(FIXTURES, "CD-001608-bom.xlsx")


def test_scanrow_counts_one_job_per_resolved_file():
    row = publish_bom.ScanRow(stem="CD-001578", part_number="CD-001578")
    assert row.job_count == 0

    row.model_version_id = "124814"
    assert row.job_count == 1

    row.drawing_version_id = "124815"
    assert row.job_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_publish_bom.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'publish_bom'`

- [ ] **Step 3: Write minimal implementation**

Create `publish_bom.py`:

```python
"""
BOM-driven deliverable publisher.

Given an Inventor BOM export, queue the Vault job-server jobs that publish a
PDF drawing and a STEP file for every Make part, plus the top-level assembly.

This is the upstream half of the manufacturing workflow: it makes sure the
deliverables *exist* in Vault. ``mfg_package.py`` is the downstream half — it
collects deliverables that already exist.

The job server publishes; it cannot author. A Make part with no drawing file
in Vault is reported as a gap, never created. See
``docs/superpowers/specs/2026-07-28-publish-bom-deliverables-design.md``.

This module is the engine. The GUI wrapper lives in ``gui/publish_bom.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

import bom_purchasing
from supplier_pricing.normalize import file_stem

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]

# Vault caps concurrent work anyway; this keeps a 200-row BOM from opening 200
# sockets at once. Same cap vault_state.py uses.
MAX_CONCURRENCY = 8

MODEL_EXTS = ("ipt", "iam")
DRAWING_EXTS = ("idw", "dwg")

# BOM Structure values that are not manufactured in house. Everything else —
# Normal, Phantom, Inseparable, and anything unrecognized — is treated as Make,
# because an unexpected deliverable is a visible row in the scan table whereas
# a silently dropped part is not.
NON_MAKE_STRUCTURES = frozenset({"purchased", "reference"})

# Spellings an Inventor export might use for the BOM Structure column.
STRUCTURE_HEADERS = ("bom structure", "bomstructure", "structure")

STATUS_BOTH = "2 jobs"
STATUS_MODEL_ONLY = "STEP only - no drawing"
STATUS_DRAWING_ONLY = "PDF only - no model"
STATUS_MISSING = "not in Vault"
STATUS_FAILED = "lookup failed"

MISSING_FILENAME_ERROR = (
    "This BOM has no file-name column, so there is no way to tell which CAD "
    "file each row refers to. Re-export the BOM from Inventor with the "
    "'Filename' column included."
)


@dataclass
class PublishRow:
    """One unique CAD file stem to publish deliverables for."""
    stem: str
    part_number: str = ""
    description: str = ""
    is_top: bool = False


@dataclass
class ScanRow:
    """A PublishRow after Vault lookup, carrying whatever files were found."""
    stem: str
    part_number: str = ""
    is_top: bool = False
    model_name: str = ""
    model_version_id: str = ""
    drawing_name: str = ""
    drawing_version_id: str = ""
    status: str = ""

    @property
    def job_count(self) -> int:
        return bool(self.model_version_id) + bool(self.drawing_version_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_publish_bom.py -v`
Expected: PASS — 1 passed

- [ ] **Step 5: Commit**

```bash
git add publish_bom.py tests/test_publish_bom.py
git commit -m "feat(publish-bom): engine skeleton with PublishRow and ScanRow"
```

---

## Task 2: Parse the BOM to publish rows

The Make filter. Read the note about `coerce_bom_dataframe` flattening
`Reference` onto `Make` before writing this.

**Files:**
- Modify: `publish_bom.py`
- Test: `tests/test_publish_bom.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_publish_bom.py`:

```python
# --------------------------------------------------------------------------- parsing

MAKE_STEMS = {
    "CD-001613", "CD-001612", "CD-001577", "CD-001578", "CD-001621",
    "CD-001623", "CD-001620", "CD-001660", "CD-001364",
}


def _write_bom(tmp_path, records, columns=None):
    """Build a synthetic Inventor-shaped export and return its path."""
    df = pd.DataFrame(records, columns=columns)
    path = tmp_path / "synthetic BOM.xlsx"
    df.to_excel(path, index=False)
    return str(path)


def test_the_real_bom_yields_exactly_its_nine_normal_rows():
    rows, error = publish_bom.load_publish_rows(REAL_BOM)
    assert error is None
    assert {r.stem for r in rows} == MAKE_STEMS


def test_purchased_rows_are_excluded_even_with_an_in_house_number():
    """CD-001366.ipt carries a CD number but is marked Purchased.

    BOM Structure is authoritative — a part that needs deliverables but is
    marked Purchased is a BOM error to fix in Inventor, not something this
    tool second-guesses.
    """
    rows, _ = publish_bom.load_publish_rows(REAL_BOM)
    assert "CD-001366" not in {r.stem for r in rows}


def test_reference_rows_are_excluded(tmp_path):
    """coerce_bom_dataframe maps Reference onto Make, so this only passes if
    the raw BOM Structure column was captured before coercion."""
    path = _write_bom(tmp_path, [
        {"Item": "1", "Filename": "CD-000001.ipt", "BOM Structure": "Normal",
         "QTY": "1", "Description": "keep"},
        {"Item": "2", "Filename": "CD-000002.ipt", "BOM Structure": "Reference",
         "QTY": "1", "Description": "drop"},
    ])
    rows, error = publish_bom.load_publish_rows(path)
    assert error is None
    assert {r.stem for r in rows} == {"CD-000001"}


def test_phantom_and_inseparable_and_unknown_structures_are_kept(tmp_path):
    path = _write_bom(tmp_path, [
        {"Item": "1", "Filename": "CD-000001.ipt", "BOM Structure": "Phantom",
         "QTY": "1", "Description": "a"},
        {"Item": "2", "Filename": "CD-000002.ipt", "BOM Structure": "Inseparable",
         "QTY": "1", "Description": "b"},
        {"Item": "3", "Filename": "CD-000003.ipt", "BOM Structure": "",
         "QTY": "1", "Description": "c"},
        {"Item": "4", "Filename": "CD-000004.ipt", "BOM Structure": "Whatever",
         "QTY": "1", "Description": "d"},
    ])
    rows, error = publish_bom.load_publish_rows(path)
    assert error is None
    assert {r.stem for r in rows} == {
        "CD-000001", "CD-000002", "CD-000003", "CD-000004"}


def test_duplicate_filenames_collapse_to_one_stem(tmp_path):
    path = _write_bom(tmp_path, [
        {"Item": "1", "Filename": "CD-000001.ipt", "BOM Structure": "Normal",
         "QTY": "1", "Description": "a"},
        {"Item": "2.1", "Filename": "CD-000001.ipt", "BOM Structure": "Normal",
         "QTY": "4", "Description": "a again"},
    ])
    rows, _ = publish_bom.load_publish_rows(path)
    assert [r.stem for r in rows] == ["CD-000001"]


def test_a_bom_without_a_filename_column_returns_an_error(tmp_path):
    path = _write_bom(tmp_path, [
        {"Item": "1", "Part Number": "SF-001580", "BOM Structure": "Normal",
         "QTY": "1", "Description": "no filename here"},
    ])
    rows, error = publish_bom.load_publish_rows(path)
    assert rows == []
    assert error is not None
    assert "Filename" in error


def test_a_bom_without_a_structure_column_falls_back_to_source(tmp_path):
    """A Vault-canonical BOM already carries Source as Make/Buy."""
    path = _write_bom(tmp_path, [
        {"Filename": "CD-000001.ipt", "Source": "Make",
         "Item Qty": "1", "Number": "CD-000001"},
        {"Filename": "CD-000002.ipt", "Source": "Buy",
         "Item Qty": "1", "Number": "CD-000002"},
    ])
    rows, error = publish_bom.load_publish_rows(path)
    assert error is None
    assert {r.stem for r in rows} == {"CD-000001"}


def test_an_unsupported_extension_returns_an_error_not_an_exception(tmp_path):
    path = tmp_path / "bom.docx"
    path.write_text("not a bom", encoding="utf-8")
    rows, error = publish_bom.load_publish_rows(str(path))
    assert rows == []
    assert error is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_publish_bom.py -v`
Expected: FAIL — `AttributeError: module 'publish_bom' has no attribute 'load_publish_rows'`

- [ ] **Step 3: Write the implementation**

Append to `publish_bom.py`:

```python
# ---------------------------------------------------------------------------
# Stage 1: BOM -> publish rows
# ---------------------------------------------------------------------------

def _norm(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:            # NaN
            return ""
    except Exception:                 # noqa: BLE001
        pass
    return str(value).strip()


def _find_column(columns, candidates) -> Optional[str]:
    """First column whose lower-cased, stripped name is in ``candidates``."""
    lower = {str(c).strip().lower(): c for c in columns}
    for name in candidates:
        if name in lower:
            return lower[name]
    return None


def load_publish_rows(
    bom_file_path: str,
) -> tuple[list[PublishRow], Optional[str]]:
    """Parse a BOM export into the unique CAD file stems to publish.

    Returns ``(rows, error)``. ``error`` is None on success; on failure it is
    a message meant to be shown to the user verbatim, and ``rows`` is empty.

    Manufactured rows are kept: everything except ``Purchased`` and
    ``Reference``. The raw ``BOM Structure`` column is read *before*
    ``coerce_bom_dataframe`` runs, because that function maps ``Reference``
    onto ``Make`` and the distinction cannot be recovered afterwards.
    """
    try:
        raw = bom_purchasing.read_bom_file(bom_file_path)
    except ValueError as exc:
        return [], str(exc)
    except Exception as exc:  # noqa: BLE001 — unreadable file, bad sheet, etc.
        name = os.path.basename(bom_file_path)
        return [], f"Could not read {name}: {exc}"

    structure_col = _find_column(raw.columns, STRUCTURE_HEADERS)
    structures = (
        [_norm(v).lower() for v in raw[structure_col]]
        if structure_col is not None else None
    )

    df, error = bom_purchasing.coerce_bom_dataframe(raw)
    if error:
        return [], error

    file_col = _find_column(df.columns, bom_purchasing.FILE_NAME_HEADERS)
    if file_col is None:
        return [], MISSING_FILENAME_ERROR

    rows: list[PublishRow] = []
    seen: set[str] = set()

    # coerce_bom_dataframe renames and reindexes but never drops rows, so
    # positions still line up with the structures list read off the raw frame.
    for pos, (_idx, rec) in enumerate(df.iterrows()):
        if structures is not None:
            if structures[pos] in NON_MAKE_STRUCTURES:
                continue
        elif _norm(rec.get("Source")).lower() != "make":
            continue

        stem = file_stem(rec.get(file_col))
        if not stem:
            # A Make row with no file name cannot be published. Log it rather
            # than dropping it silently — a blank Filename cell is a BOM
            # problem worth noticing.
            logger.info(
                "Skipping BOM row %s: no file name", _norm(rec.get("Row Order"))
            )
            continue
        key = stem.lower()
        if key in seen:
            continue
        seen.add(key)

        rows.append(PublishRow(
            stem=stem,
            part_number=_norm(rec.get("Number")) or stem,
            description=_norm(rec.get("Description (Item,CO)")),
        ))

    return rows, None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_publish_bom.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add publish_bom.py tests/test_publish_bom.py
git commit -m "feat(publish-bom): parse a BOM export to unique Make file stems"
```

---

## Task 3: Derive the top assembly stem from the BOM file name

**Files:**
- Modify: `publish_bom.py`
- Test: `tests/test_publish_bom.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_publish_bom.py`:

```python
# --------------------------------------------------------------------------- top assembly

@pytest.mark.parametrize("filename,expected", [
    ("CD-001608 BOM.xlsx", "CD-001608"),
    ("CD-001608 MFG BOM.xlsx", "CD-001608"),
    ("CD-001608.xlsx", "CD-001608"),
    ("cd-001608 bom.xlsx", "cd-001608"),
    ("SF-001922 BOM.csv", "SF-001922"),
    ("bom export.xlsx", ""),
    ("", ""),
])
def test_top_assembly_stem_is_parsed_from_the_file_name(filename, expected):
    assert publish_bom.top_assembly_stem(filename) == expected


def test_top_assembly_stem_ignores_the_directory():
    path = r"C:\Vault Workspace\DESIGNS\PRODUCTION EQUIPMENT\CD-001608 BOM.xlsx"
    assert publish_bom.top_assembly_stem(path) == "CD-001608"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_publish_bom.py -k top_assembly -v`
Expected: FAIL — `AttributeError: module 'publish_bom' has no attribute 'top_assembly_stem'`

- [ ] **Step 3: Write the implementation**

Append to `publish_bom.py`:

```python
# A CAD or item number: two-or-more letters, a hyphen, four-or-more digits.
# Matches CD-001608 and SF-001922 but not "M6" or "4762".
_TOP_STEM_RE = re.compile(r"[A-Za-z]{2,}-\d{4,}")


def top_assembly_stem(bom_file_path: str) -> str:
    """Pull the top-level part number out of a BOM file name.

    ``"CD-001608 BOM.xlsx"`` -> ``"CD-001608"``. Returns "" when the name
    carries no recognizable number, in which case the GUI leaves the top
    assembly field blank and no top-level jobs are queued.
    """
    if not bom_file_path:
        return ""
    base = os.path.splitext(os.path.basename(bom_file_path))[0]
    match = _TOP_STEM_RE.search(base)
    return match.group(0) if match else ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_publish_bom.py -v`
Expected: PASS — 17 passed

- [ ] **Step 5: Commit**

```bash
git add publish_bom.py tests/test_publish_bom.py
git commit -m "feat(publish-bom): derive the top assembly stem from the BOM file name"
```

---

## Task 4: Scan Vault to resolve each stem to its files

**Files:**
- Modify: `publish_bom.py`
- Test: `tests/test_publish_bom.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_publish_bom.py`:

```python
# --------------------------------------------------------------------------- fake api

class FakeAPI:
    """Records calls and replays canned responses.

    ``search_map`` maps a query stem to the ``results`` list the Vault search
    should return. ``search_errors`` holds stems whose search should fail.
    """

    def __init__(self, search_map=None, search_errors=(), submit_errors=(),
                 queue_enabled=True):
        self.search_map = search_map or {}
        self.search_errors = set(search_errors)
        self.submit_errors = set(submit_errors)
        self.queue_enabled = queue_enabled
        self.submitted = []
        self._next_job_id = 1000

    async def search_files(self, vault_id, query, **kwargs):
        if query in self.search_errors:
            return {"error": True, "status_code": 500,
                    "data": {"message": "boom"}}
        return {"error": False, "status_code": 200,
                "data": {"results": self.search_map.get(query, [])}}

    async def get_job_queue_enabled(self, vault_id):
        return {"error": False, "status_code": 200,
                "data": {"value": self.queue_enabled}}

    async def submit_job(self, vault_id, job_type, *, params=None,
                         description=None, priority=None):
        self.submitted.append({
            "job_type": job_type, "params": dict(params or {}),
            "description": description, "priority": priority,
        })
        fvid = (params or {}).get("FileVersionId", "")
        if fvid in self.submit_errors:
            return {"error": True, "status_code": 400,
                    "data": {"message": "Job param error"}}
        self._next_job_id += 1
        return {"error": False, "status_code": 200,
                "data": {"id": str(self._next_job_id)}}


def _hit(name, fvid):
    return {"entityType": "FileVersion", "name": name, "id": fvid}


# --------------------------------------------------------------------------- scan

@pytest.mark.asyncio
async def test_scan_classifies_model_and_drawing():
    api = FakeAPI({"CD-001578": [
        _hit("CD-001578.ipt", "111"),
        _hit("CD-001578.idw", "222"),
    ]})
    rows = await publish_bom.scan_rows(
        api, "1", [publish_bom.PublishRow(stem="CD-001578")])

    assert len(rows) == 1
    assert rows[0].model_name == "CD-001578.ipt"
    assert rows[0].model_version_id == "111"
    assert rows[0].drawing_name == "CD-001578.idw"
    assert rows[0].drawing_version_id == "222"
    assert rows[0].status == publish_bom.STATUS_BOTH


@pytest.mark.asyncio
async def test_scan_reports_a_make_part_with_no_drawing():
    """The gap this tool exists to surface."""
    api = FakeAPI({"CD-001601": [_hit("CD-001601.iam", "333")]})
    rows = await publish_bom.scan_rows(
        api, "1", [publish_bom.PublishRow(stem="CD-001601")])

    assert rows[0].status == publish_bom.STATUS_MODEL_ONLY
    assert rows[0].drawing_version_id == ""
    assert rows[0].job_count == 1


@pytest.mark.asyncio
async def test_scan_reports_a_stem_that_matches_nothing():
    api = FakeAPI({})
    rows = await publish_bom.scan_rows(
        api, "1", [publish_bom.PublishRow(stem="CD-001644")])

    assert rows[0].status == publish_bom.STATUS_MISSING
    assert rows[0].job_count == 0


@pytest.mark.asyncio
async def test_scan_requires_an_exact_basename_match():
    """A substring match would pull in every assembly that uses the part."""
    api = FakeAPI({"CD-001578": [
        _hit("CD-001578-BRACKET.ipt", "999"),
        _hit("CD-001578 REV A.idw", "998"),
        _hit("CD-001578.ipt", "111"),
    ]})
    rows = await publish_bom.scan_rows(
        api, "1", [publish_bom.PublishRow(stem="CD-001578")])

    assert rows[0].model_version_id == "111"
    assert rows[0].drawing_version_id == ""


@pytest.mark.asyncio
async def test_scan_ignores_non_file_version_hits():
    api = FakeAPI({"CD-001578": [
        {"entityType": "Item", "name": "CD-001578.ipt", "id": "777"},
        _hit("CD-001578.ipt", "111"),
    ]})
    rows = await publish_bom.scan_rows(
        api, "1", [publish_bom.PublishRow(stem="CD-001578")])

    assert rows[0].model_version_id == "111"


@pytest.mark.asyncio
async def test_a_search_failure_degrades_only_its_own_row():
    api = FakeAPI(
        {"CD-000002": [_hit("CD-000002.ipt", "111")]},
        search_errors=["CD-000001"],
    )
    rows = await publish_bom.scan_rows(api, "1", [
        publish_bom.PublishRow(stem="CD-000001"),
        publish_bom.PublishRow(stem="CD-000002"),
    ])

    assert rows[0].status == publish_bom.STATUS_FAILED
    assert rows[1].status == publish_bom.STATUS_MODEL_ONLY


@pytest.mark.asyncio
async def test_scan_preserves_input_order():
    api = FakeAPI({})
    stems = [f"CD-00{n:04d}" for n in range(20)]
    rows = await publish_bom.scan_rows(
        api, "1", [publish_bom.PublishRow(stem=s) for s in stems])

    assert [r.stem for r in rows] == stems
```

`pytest-asyncio` is needed for these. Check `requirements-dev.txt` and
`pytest.ini` in Step 2 and add it if missing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_publish_bom.py -k scan -v`
Expected: FAIL — `AttributeError: module 'publish_bom' has no attribute 'scan_rows'`

If the failure is instead `async def functions are not natively supported`,
install and register `pytest-asyncio` first:

```bash
python -m pip install pytest-asyncio
```

Add `pytest-asyncio` to `requirements-dev.txt`, and add this to the
`[pytest]` section of `pytest.ini` so the `@pytest.mark.asyncio` markers
resolve:

```ini
asyncio_mode = auto
```

- [ ] **Step 3: Write the implementation**

Append to `publish_bom.py`:

```python
# ---------------------------------------------------------------------------
# Stage 2: scan Vault for each stem's model and drawing
# ---------------------------------------------------------------------------

def _search_results(data: Any) -> list[dict[str, Any]]:
    """Pull the hit list out of a search-results response body."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("results", "items", "data", "value"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []


def _error_text(resp: dict[str, Any]) -> str:
    data = resp.get("data")
    if isinstance(data, dict):
        for key in ("message", "detail", "error"):
            v = data.get(key)
            if isinstance(v, str) and v:
                return v
    return f"HTTP {resp.get('status_code', '?')}"


def _status_for(row: ScanRow) -> str:
    if row.model_version_id and row.drawing_version_id:
        return STATUS_BOTH
    if row.model_version_id:
        return STATUS_MODEL_ONLY
    if row.drawing_version_id:
        return STATUS_DRAWING_ONLY
    return STATUS_MISSING


async def _scan_one(api, vault_id: str, row: PublishRow,
                    progress: ProgressFn) -> ScanRow:
    out = ScanRow(stem=row.stem, part_number=row.part_number,
                  is_top=row.is_top)

    resp = await api.search_files(
        vault_id=vault_id, query=row.stem,
        search_sub_folders=True, latest_only=True, limit=20,
    )
    if resp.get("error"):
        out.status = STATUS_FAILED
        progress(f"  {row.stem}: lookup failed - {_error_text(resp)}")
        return out

    for rec in _search_results(resp.get("data")):
        if rec.get("entityType") != "FileVersion":
            continue
        name = _norm(rec.get("name"))
        # Require the basename to EQUAL the stem. A loose containment check
        # pulls in every assembly that references this part.
        if file_stem(name).lower() != row.stem.lower():
            continue
        fvid = _norm(rec.get("id"))
        if not fvid:
            continue
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in MODEL_EXTS and not out.model_version_id:
            out.model_name, out.model_version_id = name, fvid
        elif ext in DRAWING_EXTS and not out.drawing_version_id:
            out.drawing_name, out.drawing_version_id = name, fvid

    out.status = _status_for(out)
    progress(f"  {row.stem}: {out.status}")
    return out


async def scan_rows(
    api,
    vault_id: str,
    rows: list[PublishRow],
    on_progress: Optional[ProgressFn] = None,
) -> list[ScanRow]:
    """Resolve every PublishRow to its model and drawing file versions.

    Runs at most ``MAX_CONCURRENCY`` searches at once. Output order matches
    input order so the GUI table is stable. A search failure degrades that one
    row to ``STATUS_FAILED``; it never aborts the scan.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def guarded(row: PublishRow) -> ScanRow:
        async with sem:
            return await _scan_one(api, vault_id, row, progress)

    return list(await asyncio.gather(*(guarded(r) for r in rows)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_publish_bom.py -v`
Expected: PASS — 24 passed

- [ ] **Step 5: Commit**

```bash
git add publish_bom.py tests/test_publish_bom.py requirements-dev.txt pytest.ini
git commit -m "feat(publish-bom): resolve BOM stems to their Vault model and drawing files"
```

---

## Task 5: Submit the jobs

**Files:**
- Modify: `publish_bom.py`
- Test: `tests/test_publish_bom.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_publish_bom.py`:

```python
# --------------------------------------------------------------------------- submit

def _scanned(stem="CD-001578", model="CD-001578.ipt", model_id="111",
             drawing="CD-001578.idw", drawing_id="222", is_top=False):
    row = publish_bom.ScanRow(stem=stem, part_number=stem, is_top=is_top,
                              model_name=model, model_version_id=model_id,
                              drawing_name=drawing, drawing_version_id=drawing_id)
    row.status = publish_bom._status_for(row)
    return row


@pytest.mark.asyncio
async def test_submit_uses_the_right_job_type_per_extension():
    api = FakeAPI()
    await publish_bom.submit_jobs(api, "1", [
        _scanned(model="CD-001578.ipt", drawing="CD-001578.idw"),
        _scanned(stem="CD-001613", model="CD-001613.iam", model_id="333",
                 drawing="CD-001613.dwg", drawing_id="444"),
    ])

    types = {j["job_type"] for j in api.submitted}
    assert types == {
        "Autodesk.Vault.PDF.Create.idw",
        "Autodesk.Vault.STEP.Create.ipt",
        "Autodesk.Vault.PDF.Create.dwg",
        "Autodesk.Vault.STEP.Create.iam",
    }


@pytest.mark.asyncio
async def test_step_and_pdf_params_match_the_shapes_the_job_processor_accepts():
    """PascalCase, and STEP reads UpdatePdfOption despite the name.

    The job processor's constructor rejects the job outright on wrong casing,
    and the REST response echoes params back camelCased, which makes this easy
    to get wrong twice.
    """
    api = FakeAPI()
    await publish_bom.submit_jobs(api, "1", [_scanned()])

    step = next(j for j in api.submitted if "STEP" in j["job_type"])
    pdf = next(j for j in api.submitted if "PDF" in j["job_type"])

    assert step["params"] == {
        "FileVersionId": "111",
        "UpdatePdfOption": "False",
        "UpdateViewOption": "False",
    }
    assert pdf["params"] == {
        "FileVersionId": "222",
        "UpdateViewOption": "False",
    }


@pytest.mark.asyncio
async def test_every_job_carries_a_non_empty_description():
    """Vault error 155 ("Illegal null parameter") otherwise."""
    api = FakeAPI()
    await publish_bom.submit_jobs(api, "1", [_scanned()])

    assert api.submitted
    assert all(j["description"] for j in api.submitted)


@pytest.mark.asyncio
async def test_a_row_with_no_drawing_queues_only_the_step_job():
    api = FakeAPI()
    result = await publish_bom.submit_jobs(
        api, "1", [_scanned(drawing="", drawing_id="")])

    assert len(api.submitted) == 1
    assert "STEP" in api.submitted[0]["job_type"]
    assert result["submitted"] == 1


@pytest.mark.asyncio
async def test_one_failing_submit_does_not_stop_the_rest():
    api = FakeAPI(submit_errors=["111"])
    result = await publish_bom.submit_jobs(api, "1", [
        _scanned(),
        _scanned(stem="CD-001613", model="CD-001613.iam", model_id="333",
                 drawing="CD-001613.idw", drawing_id="444"),
    ])

    assert len(api.submitted) == 4
    assert result["failed"] == 1
    assert result["submitted"] == 3


@pytest.mark.asyncio
async def test_submit_reports_job_ids():
    api = FakeAPI()
    result = await publish_bom.submit_jobs(api, "1", [_scanned()])

    assert len(result["jobs"]) == 2
    assert all(j["job_id"] for j in result["jobs"])


@pytest.mark.asyncio
async def test_rows_with_nothing_found_queue_nothing():
    api = FakeAPI()
    result = await publish_bom.submit_jobs(
        api, "1", [_scanned(model="", model_id="", drawing="", drawing_id="")])

    assert api.submitted == []
    assert result["submitted"] == 0


@pytest.mark.asyncio
async def test_a_disabled_queue_warns_but_still_submits():
    messages = []
    api = FakeAPI(queue_enabled=False)
    result = await publish_bom.submit_jobs(
        api, "1", [_scanned()], on_progress=messages.append)

    assert any("disabled" in m.lower() for m in messages)
    assert result["submitted"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_publish_bom.py -k submit -v`
Expected: FAIL — `AttributeError: module 'publish_bom' has no attribute 'submit_jobs'`

- [ ] **Step 3: Write the implementation**

Append to `publish_bom.py`:

```python
# ---------------------------------------------------------------------------
# Stage 3: submit the jobs
# ---------------------------------------------------------------------------

def _queue_is_disabled(data: Any) -> bool:
    """True only when the response clearly says the queue is off."""
    if isinstance(data, bool):
        return not data
    if isinstance(data, dict):
        for key in ("value", "enabled", "isEnabled", "jobQueueEnabled"):
            v = data.get(key)
            if isinstance(v, bool):
                return not v
            if isinstance(v, str) and v.strip().lower() in ("true", "false"):
                return v.strip().lower() == "false"
    return False


def _planned_jobs(row: ScanRow) -> list[tuple[str, str, str]]:
    """(kind, file name, file version id) for each job this row implies."""
    jobs: list[tuple[str, str, str]] = []
    if row.drawing_version_id:
        jobs.append(("PDF", row.drawing_name, row.drawing_version_id))
    if row.model_version_id:
        jobs.append(("STEP", row.model_name, row.model_version_id))
    return jobs


def _job_spec(kind: str, name: str, fvid: str) -> tuple[str, dict[str, str]]:
    """JobType and Params for one job.

    Param keys are PascalCase because the job processor's constructor rejects
    the job otherwise. STEP reads both UpdatePdfOption and UpdateViewOption
    despite the names; there is no UpdateStpOption.
    """
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if kind == "PDF":
        return (
            f"Autodesk.Vault.PDF.Create.{ext}",
            {"FileVersionId": fvid, "UpdateViewOption": "False"},
        )
    return (
        f"Autodesk.Vault.STEP.Create.{ext}",
        {
            "FileVersionId": fvid,
            "UpdatePdfOption": "False",
            "UpdateViewOption": "False",
        },
    )


async def submit_jobs(
    api,
    vault_id: str,
    scan_rows_in: list[ScanRow],
    on_progress: Optional[ProgressFn] = None,
    *,
    priority: int = 10,
) -> dict[str, Any]:
    """Queue one job per resolved file. Fire and forget — nothing is polled.

    Submits serially: job submission is cheap, and serial keeps the log
    readable and the queue ordered. A failed submit is logged and counted; the
    loop continues.

    Returns ``{"submitted": int, "failed": int, "jobs": [...]}``.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)

    queue_resp = await api.get_job_queue_enabled(vault_id=vault_id)
    if not queue_resp.get("error") and _queue_is_disabled(queue_resp.get("data")):
        progress(
            "WARNING: the Vault job queue is disabled. Jobs will be queued but "
            "sit unprocessed until a Job Processor agent comes online."
        )

    submitted = 0
    failed = 0
    jobs: list[dict[str, str]] = []

    for row in scan_rows_in:
        for kind, name, fvid in _planned_jobs(row):
            job_type, params = _job_spec(kind, name, fvid)
            resp = await api.submit_job(
                vault_id=vault_id,
                job_type=job_type,
                params=params,
                description=f"{kind} Create: {name}",
                priority=priority,
            )
            if resp.get("error"):
                failed += 1
                progress(f"  {name}: {kind} submit FAILED - {_error_text(resp)}")
                continue

            data = resp.get("data")
            job_id = _norm(data.get("id")) if isinstance(data, dict) else ""
            submitted += 1
            jobs.append({"file": name, "kind": kind, "job_id": job_id})
            progress(f"  {name}: {kind} queued (job {job_id or '?'})")

    return {"submitted": submitted, "failed": failed, "jobs": jobs}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_publish_bom.py -v`
Expected: PASS — 32 passed

- [ ] **Step 5: Commit**

```bash
git add publish_bom.py tests/test_publish_bom.py
git commit -m "feat(publish-bom): submit PDF and STEP jobs for resolved files"
```

---

## Task 6: Wire the top assembly into a scan

Ties Tasks 2-4 together: the caller needs one entry point that parses a BOM,
appends the top assembly, and scans everything.

**Files:**
- Modify: `publish_bom.py`
- Test: `tests/test_publish_bom.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_publish_bom.py`:

```python
# --------------------------------------------------------------------------- scan_bom

@pytest.mark.asyncio
async def test_scan_bom_appends_the_top_assembly_row():
    api = FakeAPI({
        "CD-001608": [_hit("CD-001608.iam", "900"), _hit("CD-001608.idw", "901")],
    })
    rows, error = await publish_bom.scan_bom(
        api, "1", REAL_BOM, top_assembly="CD-001608")

    assert error is None
    assert len(rows) == 10          # 9 Make rows + the top assembly
    top = [r for r in rows if r.is_top]
    assert len(top) == 1
    assert top[0].stem == "CD-001608"
    assert top[0].job_count == 2    # the top assembly gets both


@pytest.mark.asyncio
async def test_scan_bom_with_a_blank_top_assembly_scans_only_the_bom():
    api = FakeAPI({})
    rows, error = await publish_bom.scan_bom(api, "1", REAL_BOM, top_assembly="")

    assert error is None
    assert len(rows) == 9
    assert not any(r.is_top for r in rows)


@pytest.mark.asyncio
async def test_scan_bom_does_not_duplicate_a_top_assembly_already_in_the_bom():
    api = FakeAPI({})
    rows, _ = await publish_bom.scan_bom(
        api, "1", REAL_BOM, top_assembly="CD-001613")

    assert [r.stem for r in rows].count("CD-001613") == 1


@pytest.mark.asyncio
async def test_scan_bom_surfaces_a_parse_error_without_calling_vault(tmp_path):
    path = _write_bom(tmp_path, [
        {"Item": "1", "Part Number": "SF-001580", "BOM Structure": "Normal",
         "QTY": "1", "Description": "no filename"},
    ])
    api = FakeAPI({})
    rows, error = await publish_bom.scan_bom(api, "1", path, top_assembly="")

    assert rows == []
    assert error is not None
    assert api.submitted == []


def test_summarize_counts_models_drawings_jobs_and_gaps():
    rows = [
        _scanned(),                                              # both
        _scanned(stem="CD-2", drawing="", drawing_id=""),        # no drawing
        _scanned(stem="CD-3", model="", model_id="",
                 drawing="", drawing_id=""),                     # nothing
    ]
    summary = publish_bom.summarize(rows)

    assert summary["rows"] == 3
    assert summary["models"] == 2
    assert summary["drawings"] == 1
    assert summary["jobs"] == 3
    assert summary["missing_drawing"] == 1
    assert summary["not_found"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_publish_bom.py -k "scan_bom or summarize" -v`
Expected: FAIL — `AttributeError: module 'publish_bom' has no attribute 'scan_bom'`

- [ ] **Step 3: Write the implementation**

Append to `publish_bom.py`:

```python
# ---------------------------------------------------------------------------
# Top-level entry points
# ---------------------------------------------------------------------------

async def scan_bom(
    api,
    vault_id: str,
    bom_file_path: str,
    *,
    top_assembly: str = "",
    on_progress: Optional[ProgressFn] = None,
) -> tuple[list[ScanRow], Optional[str]]:
    """Parse a BOM, append the top assembly, and resolve everything in Vault.

    Returns ``(scan_rows, error)``. On a parse error the list is empty, the
    message is meant to be shown verbatim, and Vault is never called.

    ``top_assembly`` is a stem such as ``"CD-001608"``; blank skips the
    top-level row. A top assembly that already appears in the BOM is not
    duplicated.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)

    rows, error = load_publish_rows(bom_file_path)
    if error:
        return [], error
    progress(f"{len(rows)} Make part(s) in the BOM.")

    top = _norm(top_assembly)
    if top:
        if any(r.stem.lower() == top.lower() for r in rows):
            progress(f"Top assembly {top} is already a BOM row; not repeating it.")
        else:
            rows.append(PublishRow(stem=top, part_number=top, is_top=True))
            progress(f"Top assembly: {top}")

    if not rows:
        return [], "No Make parts found in this BOM."

    progress("Resolving files in Vault...")
    return await scan_rows(api, vault_id, rows, progress), None


def summarize(rows: list[ScanRow]) -> dict[str, int]:
    """Counts for the GUI's summary line."""
    return {
        "rows": len(rows),
        "models": sum(1 for r in rows if r.model_version_id),
        "drawings": sum(1 for r in rows if r.drawing_version_id),
        "jobs": sum(r.job_count for r in rows),
        "missing_drawing": sum(1 for r in rows if r.status == STATUS_MODEL_ONLY),
        "not_found": sum(1 for r in rows if r.status == STATUS_MISSING),
        "failed": sum(1 for r in rows if r.status == STATUS_FAILED),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_publish_bom.py -v`
Expected: PASS — 37 passed

- [ ] **Step 5: Commit**

```bash
git add publish_bom.py tests/test_publish_bom.py
git commit -m "feat(publish-bom): scan_bom entry point and scan summary"
```

---

## Task 7: The Tk dialog

No tests — the GUI layer is untested throughout this repo, consistent with
`gui/mfg_package.py` and `gui/file_property_check.py`.

**Files:**
- Create: `gui/publish_bom.py`

- [ ] **Step 1: Read the dialog this one mirrors**

Read `gui/mfg_package.py` in full. Reuse without deviating:

- the palette import block (`gui/mfg_package.py:28-40`)
- `self.q: queue.Queue` + `_drain_queue` polled by `self.win.after(100, ...)`
- `_log(msg, tag)` with the `ok` / `err` / `dim` tags
- the `launch_gui(*, api, vault_id, cfg, parent)` factory at the bottom

- [ ] **Step 2: Write the dialog**

Create `gui/publish_bom.py`:

```python
"""
Tkinter dialog for the BOM-driven deliverable publisher.

Opens as a Toplevel from the launcher with the live Vault session attached.
The user browses to an Inventor BOM export, confirms the top assembly, and
clicks Scan — the worker thread resolves every Make part to its model and
drawing in Vault and fills the results table. Submit then queues a PDF job per
drawing and a STEP job per model.

Fire and forget: jobs are queued, not polled. Watch them in Vault Explorer.
"""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
from typing import Any, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from gui.release_workflow import (  # noqa: E402
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY,
    WHITE, RUST_ORANGE, OLIVE_GREEN,
)

# The root-level engine module, not this file. Python resolves the unqualified
# import via PROJECT_ROOT on sys.path; this file is reached as
# ``gui.publish_bom``, so the names do not collide.
import publish_bom  # noqa: E402


class PublishBOMGUI:
    """Toplevel dialog. One per launch — closes when the user clicks Close."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        api: Any = None,
        vault_id: str = "",
        cfg: Optional[dict[str, Any]] = None,
    ) -> None:
        self.api = api
        self.vault_id = vault_id
        self.cfg = cfg or {}
        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.scan_result: list[publish_bom.ScanRow] = []
        self._busy = False

        self.win = tk.Toplevel(master)
        self.win.title("Publish BOM Deliverables")
        self.win.configure(bg=LIGHT_GRAY)
        self.win.geometry("880x640")
        self.win.minsize(760, 520)

        self.bom_path = tk.StringVar()
        self.top_assembly = tk.StringVar()
        self.summary_text = tk.StringVar(value="Pick a BOM and click Scan.")

        self._build_ui()
        self.win.after(100, self._drain_queue)

    # ----- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        header = tk.Frame(self.win, bg=DARK_BLUE, height=54)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="Publish BOM Deliverables", bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 13, "bold"),
        ).pack(side="left", padx=18)
        tk.Frame(self.win, bg=MID_BLUE, height=3).pack(fill="x")

        form = tk.Frame(self.win, bg=LIGHT_GRAY, padx=16, pady=12)
        form.pack(fill="x")

        tk.Label(form, text="BOM file", bg=LIGHT_GRAY, fg=DARK_BLUE,
                 font=("Arial", 10, "bold"), width=13, anchor="w").grid(
            row=0, column=0, sticky="w", pady=4)
        tk.Entry(form, textvariable=self.bom_path, width=58,
                 font=("Arial", 10)).grid(row=0, column=1, sticky="we", pady=4)
        tk.Button(form, text="Browse...", command=self._on_browse,
                  font=("Arial", 9)).grid(row=0, column=2, padx=(8, 0), pady=4)

        tk.Label(form, text="Top assembly", bg=LIGHT_GRAY, fg=DARK_BLUE,
                 font=("Arial", 10, "bold"), width=13, anchor="w").grid(
            row=1, column=0, sticky="w", pady=4)
        tk.Entry(form, textvariable=self.top_assembly, width=26,
                 font=("Arial", 10)).grid(row=1, column=1, sticky="w", pady=4)
        tk.Label(form, text="blank = skip the top-level jobs", bg=LIGHT_GRAY,
                 fg=DARK_GRAY, font=("Arial", 8)).grid(
            row=2, column=1, sticky="w")
        form.columnconfigure(1, weight=1)

        actions = tk.Frame(self.win, bg=LIGHT_GRAY, padx=16)
        actions.pack(fill="x")
        self.scan_btn = tk.Button(
            actions, text="  Scan  ", command=self._on_scan,
            bg=DARK_BLUE, fg=WHITE, font=("Arial", 10, "bold"),
            relief="flat", padx=10, pady=4, cursor="hand2",
        )
        self.scan_btn.pack(side="left")
        self.submit_btn = tk.Button(
            actions, text="  Submit Jobs  ", command=self._on_submit,
            bg=OLIVE_GREEN, fg=WHITE, font=("Arial", 10, "bold"),
            relief="flat", padx=10, pady=4, cursor="hand2",
            state="disabled",
        )
        self.submit_btn.pack(side="left", padx=(10, 0))

        table_frame = tk.Frame(self.win, bg=WHITE, padx=16, pady=10)
        table_frame.pack(fill="both", expand=True)
        columns = ("part", "model", "drawing", "status")
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=10)
        for key, label, width in (
            ("part", "Part", 140),
            ("model", "Model", 210),
            ("drawing", "Drawing", 210),
            ("status", "Status", 180),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w")
        vsb = ttk.Scrollbar(table_frame, orient="vertical",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("gap", foreground=RUST_ORANGE)

        tk.Label(self.win, textvariable=self.summary_text, bg=PALE_BLUE,
                 fg=DARK_BLUE, font=("Arial", 9, "bold"), anchor="w",
                 padx=16, pady=5).pack(fill="x")

        log_frame = tk.Frame(self.win, bg=LIGHT_GRAY, padx=16, pady=(8, 12))
        log_frame.pack(fill="both")
        self.log = tk.Text(log_frame, height=8, bg=WHITE, fg=DARK_GRAY,
                           font=("Consolas", 9), relief="flat",
                           highlightthickness=1, highlightbackground=GRAY_BDR)
        log_vsb = ttk.Scrollbar(log_frame, orient="vertical",
                                command=self.log.yview)
        self.log.configure(yscrollcommand=log_vsb.set, state="disabled")
        log_vsb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("ok", foreground=OLIVE_GREEN)
        self.log.tag_configure("err", foreground=RUST_ORANGE)
        self.log.tag_configure("dim", foreground=GRAY_BDR)

        tk.Button(self.win, text="  Close  ", command=self.win.destroy,
                  font=("Arial", 9)).pack(side="right", padx=16, pady=(0, 12))

    # ----- Logging ----------------------------------------------------------

    def _log(self, msg: str, tag: Optional[str] = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "scan_done":
                    self._render_scan(payload)
                elif kind == "submit_done":
                    self._render_submit(payload)
                elif kind == "error":
                    self._log(payload, "err")
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.win.after(100, self._drain_queue)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.scan_btn.configure(state="disabled" if busy else "normal")

    # ----- Actions ----------------------------------------------------------

    def _on_browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.win, title="Select a BOM export",
            filetypes=[("BOM exports", "*.xlsx *.xls *.csv *.txt"),
                       ("All files", "*.*")],
        )
        if not path:
            return
        self.bom_path.set(path)
        self.top_assembly.set(publish_bom.top_assembly_stem(path))

    def _require_session(self) -> bool:
        if self.api and self.vault_id:
            return True
        messagebox.showwarning(
            "Not signed in",
            "This tool needs an authenticated Vault session. Reconnect from "
            "the launcher and try again.",
            parent=self.win,
        )
        return False

    def _on_scan(self) -> None:
        if self._busy or not self._require_session():
            return
        path = self.bom_path.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showwarning(
                "No BOM selected", "Pick a BOM export first.", parent=self.win)
            return

        self.submit_btn.configure(state="disabled")
        self.scan_result = []
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._set_busy(True)
        self._log(f"Scanning {os.path.basename(path)}")

        top = self.top_assembly.get().strip()

        def runner() -> None:
            try:
                rows, error = asyncio.run(publish_bom.scan_bom(
                    self.api, self.vault_id, path,
                    top_assembly=top,
                    on_progress=lambda m: self.q.put(("log", m)),
                ))
                if error:
                    self.q.put(("error", error))
                    return
                self.q.put(("scan_done", rows))
            except Exception as exc:  # noqa: BLE001 — surface, never crash the GUI
                self.q.put(("error", f"Scan failed: {exc}"))

        threading.Thread(target=runner, daemon=True, name="publish-bom-scan").start()

    def _render_scan(self, rows: list[publish_bom.ScanRow]) -> None:
        self.scan_result = rows
        for row in rows:
            part = f"{row.stem} (top)" if row.is_top else row.stem
            tag = "gap" if row.status != publish_bom.STATUS_BOTH else ""
            self.tree.insert("", "end", values=(
                part, row.model_name or "-", row.drawing_name or "-", row.status,
            ), tags=(tag,))

        s = publish_bom.summarize(rows)
        self.summary_text.set(
            f"{s['rows']} part(s) - {s['models']} model(s) - "
            f"{s['drawings']} drawing(s) - {s['jobs']} job(s) to queue - "
            f"{s['missing_drawing']} missing a drawing - "
            f"{s['not_found']} not in Vault"
        )
        self._log(f"Scan complete: {s['jobs']} job(s) ready to queue.", "ok")
        self._set_busy(False)
        self.submit_btn.configure(state="normal" if s["jobs"] else "disabled")

    def _on_submit(self) -> None:
        if self._busy or not self.scan_result or not self._require_session():
            return
        s = publish_bom.summarize(self.scan_result)
        if not messagebox.askyesno(
            "Queue jobs?",
            f"Queue {s['jobs']} job(s) on the Vault job server?\n\n"
            "Jobs are submitted and not tracked from here — watch their "
            "progress in Vault Explorer.",
            parent=self.win,
        ):
            return

        self._set_busy(True)
        self.submit_btn.configure(state="disabled")
        self._log(f"Submitting {s['jobs']} job(s)...")

        rows = list(self.scan_result)

        def runner() -> None:
            try:
                result = asyncio.run(publish_bom.submit_jobs(
                    self.api, self.vault_id, rows,
                    on_progress=lambda m: self.q.put(("log", m)),
                ))
                self.q.put(("submit_done", result))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("error", f"Submit failed: {exc}"))

        threading.Thread(target=runner, daemon=True,
                         name="publish-bom-submit").start()

    def _render_submit(self, result: dict[str, Any]) -> None:
        self._log("")
        self._log(f"DONE - {result['submitted']} job(s) queued.", "ok")
        if result["failed"]:
            self._log(f"  {result['failed']} submission(s) failed.", "err")
        self._log("Watch the queue in Vault Explorer.", "dim")
        self._set_busy(False)
        # A second run needs a fresh Scan — the guard against queueing twice.
        self.submit_btn.configure(state="disabled")


def launch_gui(
    *,
    api: Any = None,
    vault_id: str = "",
    cfg: Optional[dict[str, Any]] = None,
    parent: Optional[tk.Misc] = None,
) -> None:
    """Open the dialog. ``parent`` should be the launcher root so it opens as a
    Toplevel that does not take over the main loop; pass None to run
    standalone."""
    if parent is None:
        root = tk.Tk()
        root.withdraw()
        gui = PublishBOMGUI(root, api=api, vault_id=vault_id, cfg=cfg)
        gui.win.protocol(
            "WM_DELETE_WINDOW", lambda: (gui.win.destroy(), root.destroy()))
        root.mainloop()
    else:
        PublishBOMGUI(parent, api=api, vault_id=vault_id, cfg=cfg)
```

- [ ] **Step 3: Verify it imports and opens**

Run: `python -c "import gui.publish_bom; print('ok')"`
Expected: `ok`

Run: `python -c "from gui.publish_bom import launch_gui; launch_gui()"`
Expected: the dialog opens. Browse to
`tests/fixtures/CD-001608-bom.xlsx` and confirm the Top assembly field
auto-fills with `CD-001608`. Scan will warn "Not signed in" — that is correct
without a session. Close the window.

- [ ] **Step 4: Commit**

```bash
git add gui/publish_bom.py
git commit -m "feat(publish-bom): Tk dialog with scan preview and submit"
```

---

## Task 8: Launcher tile

**Files:**
- Modify: `gui/launcher.py` (tile in `_build_tools_panel`, handler near
  `_on_open_property_check`, docstring at the top)

- [ ] **Step 1: Add the tile**

In `gui/launcher.py`, inside `_build_tools_panel`, insert this
`self._tool_row(...)` call immediately after the "Property Check" tile and
before the "Open Reports Folder" tile:

```python
        self._tool_row(
            body,
            "BOM → Publish Deliverables",
            "Upload an exported BOM and queue Vault jobs that publish a PDF "
            "drawing and a STEP file for every Make part, plus the top "
            "assembly. Scan first to see which parts have no drawing.",
            "Open Publisher",
            self._on_open_publish_bom,
            primary=False,
        )
```

- [ ] **Step 2: Add the handler**

In `gui/launcher.py`, add this method immediately after
`_on_open_property_check`:

```python
    def _on_open_publish_bom(self) -> None:
        if not (self.api and self.vault_id):
            messagebox.showwarning(
                "Not signed in",
                "Click Reconnect first — the deliverable publisher needs an "
                "authenticated Vault session.",
                parent=self.root,
            )
            return
        try:
            from gui.publish_bom import launch_gui as launch_publish_gui
        except ImportError as exc:
            messagebox.showerror(
                "Publisher unavailable", str(exc), parent=self.root,
            )
            return
        launch_publish_gui(
            api=self.api, vault_id=self.vault_id,
            cfg=self.cfg, parent=self.root,
        )
```

- [ ] **Step 3: Update the launcher docstring**

In the module docstring at the top of `gui/launcher.py`, add this line to the
bullet list, after the Property Check line:

```
  * launch BOM → Publish Deliverables — queue PDF/STEP jobs from a BOM
```

- [ ] **Step 4: Verify**

Run: `python -m pytest tests/test_launcher_flags.py -v`
Expected: PASS — existing launcher tests still pass

Run: `python -c "import gui.launcher; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add gui/launcher.py
git commit -m "feat(publish-bom): add the publisher tile to the launcher"
```

---

## Task 9: Full suite and manual verification

**Files:** none — verification only.

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest -q`
Expected: all tests pass, including the 37 new ones. If anything unrelated
fails, check whether it failed before this branch — do not "fix" a
pre-existing failure as part of this work.

- [ ] **Step 2: Manual smoke test against live Vault**

Launch the app: `python app.py --gui`

1. Confirm the Vault session is connected.
2. Open **BOM → Publish Deliverables**.
3. Browse to `C:\Vault Workspace\DESIGNS\PRODUCTION EQUIPMENT\CD-001608 BOM.xlsx`.
4. Confirm the Top assembly field auto-fills `CD-001608`.
5. Click **Scan**.

Expected: 10 rows. Nine are the `CD-` Make parts listed in the spec's
Reference BOM section; one is `CD-001608 (top)`. `CD-001366` must **not**
appear — it is `Purchased`. Any row whose drawing shows `-` is a real gap
worth confirming against Vault Explorer before proceeding.

6. Click **Submit Jobs**, confirm the dialog.

Expected: one PDF job per drawing and one STEP job per model, each logged with
a job id. Verify in Vault Explorer's job queue that they appear.

- [ ] **Step 3: Record the outcome**

Note the scan counts and whether every job appeared in the queue. If the job
processor rejects a job with "Job param error", the params drifted from the
shapes in `mcp_server.py:998-1069` — compare them before changing anything
else.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix(publish-bom): <what the smoke test turned up>"
```

Skip this step if the smoke test was clean.

---

## Notes for the implementer

**Do not add a polling loop.** Fire and forget is a deliberate decision —
`api.get_job_by_id` exists and it is tempting. The user watches the queue in
Vault Explorer.

**Do not add staleness checks.** Always re-publish is deliberate. There is no
"skip if the PDF is newer than the model" logic to write.

**Do not special-case `CD-`-numbered `Purchased` parts.** `BOM Structure` is
authoritative. `CD-001366.ipt` in the reference BOM is marked `Purchased` and
is correctly excluded.

**The engine must not import tkinter.** That is what keeps it testable.
