# Release Workflow File-Driven Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-point the disabled Release Workflow wizard at the six file- and BOM-driven tools that already work, so a release is one window again.

**Architecture:** A new `release_steps.py` holds six headless step engines that return a common `StepOutcome`; a rewritten `gui/release_workflow.py` renders them. Steps that write to Vault or SharePoint return a staged `pending_apply` callable, so the wizard previews first and writes only on a second click. The brand palette moves out to `gui/theme.py` because eight modules import it from the file being rewritten.

**Tech Stack:** Python 3, Tkinter, pytest, `VaultRestAPI` (REST), `vault_sdk` (PowerShell ↔ .NET SDK bridge for lifecycle changes), pandas (BOM parsing).

**Spec:** `docs/superpowers/specs/2026-07-29-release-workflow-file-driven-design.md`

**Baseline:** `python -m pytest -q` → 377 passed, 1 skipped. Keep it green.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `gui/theme.py` | **New.** Brand palette constants and the shared widget factories. No logic. |
| `release_steps.py` | **New.** Six step engines + the file-list and gate helpers. No Tk import anywhere. |
| `gui/release_workflow.py` | **Rewritten.** Wizard shell only — inputs, step list, output panel, dispatch, apply gate. Re-exports the palette. |
| `scripts/check_file_properties.py` | **Modified.** Optional `api`/`vault_id` params so step 1 reuses the wizard's session. |
| `gui/launcher.py` | **Modified.** Un-break the Release Workflow tile. |
| `tests/test_release_steps.py` | **New.** Engine tests — the bulk of the coverage. |
| `tests/test_launcher_flags.py` | **Modified.** The "Release Workflow is disabled" assertion inverts. |

**Naming locked across all tasks.** Later tasks depend on these exact names:

```python
StepOutcome(ok, summary, lines, pending_apply, result)
file_version_ids(compliance)        -> list[str]
file_master_ids(compliance)         -> list[int]
property_check_blocked(compliance, *, force) -> str | None
run_property_check(file_name, *, api=None, vault_id="")
run_sync_properties(api, vault_id, compliance)
run_release_files(api, vault_id, compliance, *, target_state, state_id=None)
run_purchased_parts_list(bom_path, *, buy_only=True, update_existing=False)
run_publish_deliverables(api, vault_id, bom_path, *, top_assembly="")
run_purchasing_sheet(bom_path, assembly_number, *, output_dir="")
STATUS_REVIEW = "REVIEW"
```

**Engines are synchronous.** The ones that need async call `asyncio.run` internally. The wizard already runs every step on a worker thread, so blocking there is correct and matches today's `_run_async`. This keeps `pending_apply` a plain `Callable[[], StepOutcome]` and keeps the tests free of async plumbing.

---

## Task 1: Extract the theme module

Eight modules import the palette from `gui/release_workflow.py`: `app.py`, `gui/launcher.py`, `gui/file_property_check.py`, `gui/mfg_package.py`, `gui/publish_bom.py`, `gui/purchasing.py`, `gui/purchasing_list_sync.py`, `scripts/release_workflow.py`. Move it first so the rewrite in Task 11 cannot break them.

**Files:**
- Create: `gui/theme.py`
- Modify: `gui/release_workflow.py` (add re-export)
- Test: `tests/test_release_steps.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_release_steps.py`:

```python
# tests/test_release_steps.py
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PALETTE = [
    "DARK_BLUE", "MID_BLUE", "PALE_BLUE", "LIGHT_GRAY", "GRAY_BDR",
    "DARK_GRAY", "WHITE", "OLIVE_GREEN", "RUST_ORANGE", "WARN_AMBER",
]


def test_theme_exports_the_palette():
    from gui import theme
    for name in PALETTE:
        assert hasattr(theme, name), f"theme is missing {name}"
        assert str(getattr(theme, name)).startswith("#")


def test_theme_exports_the_shared_helpers():
    from gui import theme
    for name in ("_resource_path", "_pil_available"):
        assert hasattr(theme, name), f"theme is missing {name}"


def test_release_workflow_still_re_exports_the_palette():
    """Eight modules import these from gui.release_workflow. Keep that working."""
    from gui import release_workflow, theme
    for name in PALETTE + ["_resource_path", "_pil_available"]:
        assert getattr(release_workflow, name) == getattr(theme, name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gui.theme'`

- [ ] **Step 3: Create `gui/theme.py`**

Move the palette block and helpers out of `gui/release_workflow.py:39-88` verbatim:

```python
"""
Simplifyber brand palette and shared widget helpers.

Every GUI module in this package imports its colours from here. Dark blue is
the primary (headers, primary buttons), mid blue is the accent (hover,
secondary text), pale blue is for info cards and hover wells.

This used to live in ``gui/release_workflow.py``; it was extracted so the
workflow wizard could be rewritten without breaking the seven other modules
that import the palette.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DARK_BLUE   = "#1F3864"
MID_BLUE    = "#2E75B6"
PALE_BLUE   = "#EAF3FB"
LIGHT_GRAY  = "#F2F2F2"
GRAY_BDR    = "#CCCCCC"
DARK_GRAY   = "#888888"
WHITE       = "#FFFFFF"
OLIVE_GREEN = "#D8E4BC"   # pass / OK statuses, matching the spreadsheet
RUST_ORANGE = "#C0504D"   # failures, legible on light backgrounds
WARN_AMBER  = "#B7791F"

# Optional Pillow for the brand logo in the header / window icon. The GUIs
# still work without PIL — they fall back to a text-only header.
try:
    from PIL import Image as PILImage, ImageTk  # noqa: F401
    _pil_available = True
except ImportError:
    _pil_available = False


def _resource_path(filename: str) -> str:
    """Return the absolute path to a bundled brand asset (logo, icon)."""
    return str(PROJECT_ROOT / filename)
```

- [ ] **Step 4: Re-export from `gui/release_workflow.py`**

Replace the palette block at `gui/release_workflow.py:39-88` with:

```python
# The palette lives in gui/theme.py. These re-exports exist because eight
# modules still import the names from here; do not remove them without
# updating every importer.
from gui.theme import (  # noqa: F401
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY,
    WHITE, OLIVE_GREEN, RUST_ORANGE, WARN_AMBER,
    PROJECT_ROOT, _pil_available, _resource_path,
)
```

Keep the existing `import os` / `sys.path` setup above it — `gui/release_workflow.py` still needs `sys.path` for its `scripts/` imports.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: PASS (3 tests)

- [ ] **Step 6: Verify no importer broke**

Run: `python -m pytest -q`
Expected: 380 passed, 1 skipped

- [ ] **Step 7: Commit**

```bash
git add gui/theme.py gui/release_workflow.py tests/test_release_steps.py
git commit -m "refactor(gui): extract the brand palette into gui/theme.py"
```

---

## Task 2: `StepOutcome` and the compliance gate

**Files:**
- Create: `release_steps.py`
- Test: `tests/test_release_steps.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_release_steps.py`:

```python
import pytest

import release_steps


def test_outcome_defaults_to_no_pending_apply():
    out = release_steps.StepOutcome(ok=True, summary="done")
    assert out.pending_apply is None
    assert out.lines == []
    assert out.result is None


def test_gate_blocks_when_step_one_has_not_run():
    assert release_steps.property_check_blocked(None, force=False) is not None


def test_gate_blocks_missing_result_even_with_force():
    """Force overrides bad properties, not absent data. Steps 2 and 3 have no
    file list at all without step 1, so there is nothing to force past."""
    assert release_steps.property_check_blocked(None, force=True) is not None


def test_gate_clear_when_everything_passes():
    compliance = {"report": {"failed": 0}, "children": []}
    assert release_steps.property_check_blocked(compliance, force=False) is None


def test_gate_blocks_on_top_level_failure():
    compliance = {"report": {"failed": 2}, "children": []}
    assert release_steps.property_check_blocked(compliance, force=False) is not None


def test_gate_blocks_on_child_failure():
    compliance = {"report": {"failed": 0},
                  "children": [{"report": {"failed": 1}}]}
    assert release_steps.property_check_blocked(compliance, force=False) is not None


def test_gate_blocks_on_child_error():
    compliance = {"report": {"failed": 0},
                  "children": [{"error": "lookup failed", "report": {}}]}
    assert release_steps.property_check_blocked(compliance, force=False) is not None


def test_gate_force_overrides_failures():
    compliance = {"report": {"failed": 2},
                  "children": [{"report": {"failed": 3}}]}
    assert release_steps.property_check_blocked(compliance, force=True) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'release_steps'`

- [ ] **Step 3: Create `release_steps.py`**

```python
"""
Headless engines for the six Release Workflow steps.

Each ``run_*`` function returns a :class:`StepOutcome`. Steps that write to
Vault or SharePoint do the read-only half immediately and hand back a
``pending_apply`` callable holding the write, so the GUI can show a preview and
require a second, explicit click before anything changes.

No Tkinter import belongs in this module — that is what makes it testable.
The GUI wrapper lives in ``gui/release_workflow.py``.

See docs/superpowers/specs/2026-07-29-release-workflow-file-driven-design.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Output-panel tags, matching the tags configured on the wizard's Text widget.
TAG_INFO = "info"
TAG_PASS = "pass"
TAG_FAIL = "fail"
TAG_WARN = "warn"
TAG_DIM = "dim"
TAG_H2 = "h2"


@dataclass
class StepOutcome:
    """What a step reports back to the wizard.

    ``pending_apply`` is the whole preview-then-write mechanism: when it is
    set, the step has computed a preview and staged a write that has NOT
    happened. The wizard renders ``lines``, moves the step to REVIEW, and waits
    for a click. Calling it performs the write and returns the final outcome.
    """
    ok: bool
    summary: str
    lines: list[tuple[str, str]] = field(default_factory=list)
    pending_apply: Optional[Callable[[], "StepOutcome"]] = None
    result: Any = None


def _child_failed(child: dict[str, Any]) -> bool:
    if child.get("error"):
        return True
    return bool((child.get("report") or {}).get("failed", 0))


def property_check_blocked(
    compliance: Optional[dict[str, Any]], *, force: bool
) -> Optional[str]:
    """Return a reason string when Sync / Release must not run, else None.

    Only steps 2 and 3 consult this. The BOM steps are deliberately not gated:
    a purchasing sheet is useful while properties are still being fixed.

    ``force`` covers failing properties. It deliberately does NOT cover a
    missing result — steps 2 and 3 take their file list from step 1, so with no
    step 1 there is no work to force past.
    """
    if not compliance:
        return "Run step 1 (Property Check) first — it supplies the file list."
    if force:
        return None
    failed = bool((compliance.get("report") or {}).get("failed", 0))
    kids = any(_child_failed(c) for c in (compliance.get("children") or []))
    if failed or kids:
        return (
            "Property Check found failures. Fix them and re-run step 1, or "
            "tick 'Force past compliance gate' to continue anyway."
        )
    return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add release_steps.py tests/test_release_steps.py
git commit -m "feat(release-steps): add StepOutcome and the property-check gate"
```

---

## Task 3: File-list derivation

Steps 2 and 3 need two different ID types out of step 1's result: version IDs for the sync job, master IDs for the lifecycle change.

**Files:**
- Modify: `release_steps.py`
- Test: `tests/test_release_steps.py`

- [ ] **Step 1: Write the failing tests**

```python
def _compliance(top=("100", "10"), children=()):
    """Build a compliance result shaped like check_file_name's return value."""
    return {
        "info": {"file_version_id": top[0], "file_id": top[1]},
        "children": [
            {"file_version_id": v, "file_id": m, "file_name": f"F{v}.ipt"}
            for v, m in children
        ],
    }


def test_version_ids_lead_with_the_top_file():
    c = _compliance(children=[("200", "20"), ("300", "30")])
    assert release_steps.file_version_ids(c) == ["100", "200", "300"]


def test_version_ids_dedupe_a_child_that_repeats_the_top():
    c = _compliance(children=[("100", "10"), ("200", "20")])
    assert release_steps.file_version_ids(c) == ["100", "200"]


def test_version_ids_skip_children_that_failed_to_resolve():
    c = _compliance(children=[("200", "20")])
    c["children"].append({"file_version_id": "", "file_id": "",
                          "error": "not found"})
    assert release_steps.file_version_ids(c) == ["100", "200"]


def test_master_ids_are_ints():
    c = _compliance(children=[("200", "20")])
    assert release_steps.file_master_ids(c) == [10, 20]


def test_master_ids_skip_blank_and_unparseable():
    c = _compliance(children=[("200", ""), ("300", "not-a-number")])
    assert release_steps.file_master_ids(c) == [10]


def test_derivation_handles_an_empty_result():
    assert release_steps.file_version_ids({}) == []
    assert release_steps.file_master_ids({}) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: FAIL — `AttributeError: module 'release_steps' has no attribute 'file_version_ids'`

- [ ] **Step 3: Implement**

Add to `release_steps.py`:

```python
def _entries(compliance: dict[str, Any]) -> list[dict[str, Any]]:
    """Top file first, then every CAD BOM child that actually resolved."""
    out = [compliance.get("info") or {}]
    out.extend(compliance.get("children") or [])
    return out


def file_version_ids(compliance: dict[str, Any]) -> list[str]:
    """File-version IDs for every file in the assembly, top first.

    Order is deterministic and de-duplicated: a child used more than once, or
    one that repeats the top-level file, is synced once.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for entry in _entries(compliance):
        fid = str(entry.get("file_version_id") or "").strip()
        if fid and fid not in seen:
            seen.add(fid)
            ids.append(fid)
    return ids


def file_master_ids(compliance: dict[str, Any]) -> list[int]:
    """File *master* IDs — what the SDK lifecycle call takes, not version IDs.

    Anything blank or non-numeric is dropped rather than guessed at; a bad ID
    would fail the whole lifecycle batch.
    """
    ids: list[int] = []
    seen: set[int] = set()
    for entry in _entries(compliance):
        raw = entry.get("file_id")
        if raw in (None, ""):
            continue
        try:
            mid = int(raw)
        except (TypeError, ValueError):
            logger.debug("Skipping unparseable file master id %r", raw)
            continue
        if mid not in seen:
            seen.add(mid)
            ids.append(mid)
    return ids
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: PASS (18 tests)

- [ ] **Step 5: Commit**

```bash
git add release_steps.py tests/test_release_steps.py
git commit -m "feat(release-steps): derive file version and master IDs from step 1"
```

---

## Task 4: Session reuse in `check_file_name`

`check_file_name` builds its own `VaultRestAPI` and signs in from `config.json` (`scripts/check_file_properties.py:460-473`), ignoring any live session. The wizard already holds one.

**Files:**
- Modify: `scripts/check_file_properties.py:436-475`
- Test: `tests/test_check_file_properties.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_check_file_properties.py`:

```python
def test_check_file_name_reuses_a_supplied_session(monkeypatch):
    """With api and vault_id supplied, no sign-in happens."""
    import check_file_properties as cfp

    class BoomAPI:
        async def create_session(self, **_kw):
            raise AssertionError("must not sign in when a session is supplied")

    async def fake_fetch_file(api, vault_id, name):
        assert vault_id == "V1"
        return {"record": {}, "file_version_id": "9", "file_id": "8",
                "properties": {"Category Name": "Part"}, "note": ""}

    monkeypatch.setattr(cfp, "fetch_file", fake_fetch_file)

    result = asyncio.run(cfp.check_file_name(
        "CD-001659.ipt", api=BoomAPI(), vault_id="V1", recursive=False))

    assert result["file_name"] == "CD-001659.ipt"
    assert result["info"]["file_version_id"] == "9"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_check_file_properties.py::test_check_file_name_reuses_a_supplied_session -q`
Expected: FAIL — `TypeError: check_file_name() got an unexpected keyword argument 'api'`

- [ ] **Step 3: Implement**

In `scripts/check_file_properties.py`, change the signature at line 436:

```python
async def check_file_name(
    file_name: str,
    *,
    config_path: Path = CONFIG_PATH,
    rules_path: Path = DEFAULT_RULES_PATH,
    category_override: str = "",
    recursive: bool = False,
    bom_limit: int = 500,
    api: Any = None,
    vault_id: str = "",
) -> dict[str, Any]:
```

Extend the docstring's existing note with:

```
    Pass ``api`` and ``vault_id`` together to reuse an already-authenticated
    session (the GUI has one); omit both to sign in from ``config_path`` as
    before.
```

Then replace the sign-in block (lines 452-473) with:

```python
    rules = load_json(rules_path)

    if api is not None and vault_id:
        # Caller handed us a live session — don't sign in a second time.
        pass
    else:
        cfg = load_json(config_path)
        vault_cfg = cfg.get("vault") or {}
        for key in ("servername", "username", "password", "database"):
            if not vault_cfg.get(key):
                raise RuntimeError(f"config.json is missing vault.{key}")

        api = VaultRestAPI(servername=vault_cfg["servername"])
        sign_in = await api.create_session(
            database=vault_cfg["database"],
            username=vault_cfg["username"],
            password=vault_cfg["password"],
        )
        if sign_in["error"]:
            raise RuntimeError(f"Vault sign-in failed: {sign_in['data']}")

        vault_id = str(
            (sign_in["data"].get("vaultInformation") or {}).get("id", "")
            or sign_in["data"].get("vaultId", "")
            or ""
        )
```

Note: `rules` is loaded before the branch because the config is only needed on
the sign-in path.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_check_file_properties.py -q`
Expected: PASS — all existing tests plus the new one

- [ ] **Step 5: Commit**

```bash
git add scripts/check_file_properties.py tests/test_check_file_properties.py
git commit -m "feat(property-check): let callers supply an existing Vault session"
```

---

## Task 5: Step 1 engine — Property Check

**Files:**
- Modify: `release_steps.py`
- Test: `tests/test_release_steps.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_property_check_reports_a_clean_assembly(monkeypatch):
    clean = {
        "file_name": "CD-001659.iam",
        "info": {"file_version_id": "100", "file_id": "10",
                 "properties": {"Revision": "A", "State": "Work in Progress"}},
        "report": {"total": 5, "passed": 5, "failed": 0, "results": []},
        "children": [], "children_error": None,
        "category_resolved": "Assembly",
    }
    monkeypatch.setattr(release_steps, "_check_file_name", lambda **kw: clean)

    out = release_steps.run_property_check("CD-001659.iam")

    assert out.ok is True
    assert out.pending_apply is None
    assert out.result is clean
    assert "5/5" in out.summary


def test_property_check_is_not_ok_when_rules_fail(monkeypatch):
    dirty = {
        "file_name": "CD-001659.iam",
        "info": {"file_version_id": "100", "file_id": "10", "properties": {}},
        "report": {"total": 5, "passed": 3, "failed": 2, "results": [
            {"property": "Revision", "passed": False, "value": "",
             "failures": ["must not be empty"]},
        ]},
        "children": [], "children_error": None,
        "category_resolved": "Assembly",
    }
    monkeypatch.setattr(release_steps, "_check_file_name", lambda **kw: dirty)

    out = release_steps.run_property_check("CD-001659.iam")

    assert out.ok is False
    # The failing property must be named, not just counted.
    assert any("Revision" in text for text, _tag in out.lines)
    # The result is still carried so steps 2/3 can run under Force.
    assert out.result is dirty


def test_property_check_surfaces_a_vault_error(monkeypatch):
    def boom(**_kw):
        raise RuntimeError("file 'CD-001659.iam' not found in Vault")
    monkeypatch.setattr(release_steps, "_check_file_name", boom)

    out = release_steps.run_property_check("CD-001659.iam")

    assert out.ok is False
    assert out.result is None
    assert "not found" in out.summary


def test_property_check_counts_failing_children(monkeypatch):
    result = {
        "file_name": "CD-001659.iam",
        "info": {"file_version_id": "100", "file_id": "10", "properties": {}},
        "report": {"total": 5, "passed": 5, "failed": 0, "results": []},
        "children": [
            {"file_name": "A.ipt", "file_version_id": "200", "file_id": "20",
             "category_resolved": "Part",
             "report": {"failed": 1, "results": [
                 {"property": "Material", "passed": False, "value": "",
                  "failures": ["must not be empty"]}]}},
            {"file_name": "B.ipt", "file_version_id": "300", "file_id": "30",
             "category_resolved": "Part", "report": {"failed": 0, "results": []}},
        ],
        "children_error": None, "category_resolved": "Assembly",
    }
    monkeypatch.setattr(release_steps, "_check_file_name", lambda **kw: result)

    out = release_steps.run_property_check("CD-001659.iam")

    assert out.ok is False
    assert any("A.ipt" in text for text, _tag in out.lines)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: FAIL — no attribute `_check_file_name`

- [ ] **Step 3: Implement**

Add to `release_steps.py`:

```python
def _check_file_name(**kwargs: Any) -> dict[str, Any]:
    """Thin seam over check_file_properties.check_file_name.

    Imported lazily and wrapped so tests can substitute it without a live
    Vault, and so a missing scripts/ path fails at the step rather than at
    module import.
    """
    import asyncio

    from check_file_properties import check_file_name
    return asyncio.run(check_file_name(**kwargs))


def run_property_check(
    file_name: str, *, api: Any = None, vault_id: str = "",
) -> StepOutcome:
    """Step 1 — run the file property rules over the assembly and its CAD BOM.

    Read-only: there is no ``pending_apply``. The result dict is carried on
    ``StepOutcome.result`` because steps 2 and 3 take their file list from it.
    """
    try:
        result = _check_file_name(
            file_name=file_name, recursive=True, api=api, vault_id=vault_id,
        )
    except Exception as exc:  # noqa: BLE001 — surfaced to the user verbatim
        return StepOutcome(ok=False, summary=str(exc),
                           lines=[(f"  [fail] {exc}", TAG_FAIL)])

    lines: list[tuple[str, str]] = []
    report = result.get("report") or {}
    total = report.get("total", 0)
    passed = report.get("passed", 0)
    failed = report.get("failed", 0)
    category = (result.get("category_resolved")
                or result.get("category_raw") or "(no rule set)")
    props = (result.get("info") or {}).get("properties") or {}

    lines.append((f"  Top file — {file_name}  [{category}]", TAG_H2))
    lines.append((f"    revision={props.get('Revision', '?')!r}  "
                  f"state={props.get('State', '?')!r}", TAG_DIM))
    lines.append((f"    {'PASS' if not failed else 'FAIL'}  "
                  f"({passed}/{total} checks pass)",
                  TAG_PASS if not failed else TAG_FAIL))

    for r in report.get("results") or []:
        if r.get("passed"):
            continue
        value = r.get("value")
        shown = "(empty)" if value in (None, "") else str(value)[:40]
        lines.append((f"      • {r['property']:24s} = {shown}", TAG_FAIL))
        for reason in r.get("failures") or []:
            lines.append((f"          → {reason}", TAG_FAIL))

    children = result.get("children") or []
    bad_children = [c for c in children if _child_failed(c)]
    if result.get("children_error"):
        lines.append((f"  [warn] CAD BOM: {result['children_error']}", TAG_WARN))
    if children:
        lines.append(("", TAG_DIM))
        lines.append((f"  CAD BOM — {len(children)} file(s), "
                      f"{len(bad_children)} with problems",
                      TAG_PASS if not bad_children else TAG_FAIL))
    for child in bad_children:
        name = child.get("file_name") or "?"
        if child.get("error"):
            lines.append((f"      • {name:20s} ERROR: {child['error']}", TAG_FAIL))
            continue
        bad = [r for r in ((child.get("report") or {}).get("results") or [])
               if not r.get("passed")]
        names = ", ".join(b["property"] for b in bad)
        lines.append((f"      • {name:20s} {len(bad)} fail · {names}", TAG_FAIL))

    ok = not failed and not bad_children
    summary = (f"Property Check: {passed}/{total} on the top file, "
               f"{len(bad_children)} of {len(children)} child file(s) failing.")
    return StepOutcome(ok=ok, summary=summary, lines=lines, result=result)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: PASS (22 tests)

- [ ] **Step 5: Commit**

```bash
git add release_steps.py tests/test_release_steps.py
git commit -m "feat(release-steps): add the Property Check step engine"
```

---

## Task 6: Step 2 engine — Sync Properties

This is the first step with a `pending_apply`. The "preview writes nothing" test is the important one — repeat that pattern for Tasks 7, 8 and 9.

**Files:**
- Modify: `release_steps.py`
- Test: `tests/test_release_steps.py`

- [ ] **Step 1: Write the failing tests**

```python
class RecordingAPI:
    """Fake VaultRestAPI that records job submissions instead of making them."""

    def __init__(self):
        self.submitted = []

    async def submit_job(self, **kwargs):
        self.submitted.append(kwargs)
        return {"error": False, "data": {"job": {"id": str(len(self.submitted))}}}


class NoWriteAPI:
    """Fails the test if anything tries to write during a preview."""

    async def submit_job(self, **kwargs):
        raise AssertionError("preview must not submit jobs")


def test_sync_preview_lists_files_and_writes_nothing():
    c = _compliance(children=[("200", "20"), ("300", "30")])
    out = release_steps.run_sync_properties(NoWriteAPI(), "V1", c)

    assert out.ok is True
    assert out.pending_apply is not None       # staged, not done
    assert "3" in out.summary


def test_sync_apply_submits_one_job_per_file_version():
    api = RecordingAPI()
    c = _compliance(children=[("200", "20"), ("300", "30")])

    applied = release_steps.run_sync_properties(api, "V1", c).pending_apply()

    assert applied.ok is True
    assert applied.pending_apply is None       # terminal
    assert [j["params"]["FileVersionId"] for j in api.submitted] == \
        ["100", "200", "300"]


def test_sync_uses_pascal_case_job_params():
    """Vault's /jobs Params keys are case-sensitive PascalCase; camelCase is
    accepted with a 200 and silently ignored."""
    api = RecordingAPI()
    release_steps.run_sync_properties(api, "V1", _compliance()).pending_apply()

    assert "FileVersionId" in api.submitted[0]["params"]
    assert api.submitted[0]["job_type"] == "Autodesk.Vault.SyncProperties"


def test_sync_reports_a_failed_submission():
    class HalfBrokenAPI:
        def __init__(self):
            self.calls = 0

        async def submit_job(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                return {"error": True, "data": "queue is disabled"}
            return {"error": False, "data": {"job": {"id": "1"}}}

    c = _compliance(children=[("200", "20")])
    applied = release_steps.run_sync_properties(
        HalfBrokenAPI(), "V1", c).pending_apply()

    assert applied.ok is False
    assert "1 failed" in applied.summary


def test_sync_with_no_files_is_ok_and_stages_nothing():
    out = release_steps.run_sync_properties(NoWriteAPI(), "V1", {})
    assert out.ok is True
    assert out.pending_apply is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: FAIL — no attribute `run_sync_properties`

- [ ] **Step 3: Implement**

```python
def run_sync_properties(
    api: Any, vault_id: str, compliance: dict[str, Any],
) -> StepOutcome:
    """Step 2 — queue Autodesk.Vault.SyncProperties for every file.

    Preview lists the files; the apply submits. Job params must be PascalCase:
    Vault's /jobs endpoint is case-sensitive there even though its JSON
    response echoes camelCase.
    """
    import asyncio

    version_ids = file_version_ids(compliance)
    if not version_ids:
        return StepOutcome(
            ok=True, summary="No files to sync.",
            lines=[("  [warn] No CAD files found — nothing to sync.", TAG_WARN)],
        )

    names = _names_by_version_id(compliance)
    lines = [(f"  {len(version_ids)} file(s) will be synced:", TAG_INFO)]
    lines += [(f"      · {names.get(fid, fid)}", TAG_DIM) for fid in version_ids]

    def apply() -> StepOutcome:
        async def submit_all() -> tuple[int, int, list[tuple[str, str]]]:
            ok_n = bad_n = 0
            out: list[tuple[str, str]] = []
            for fid in version_ids:
                name = names.get(fid, fid)
                resp = await api.submit_job(
                    vault_id=vault_id,
                    job_type="Autodesk.Vault.SyncProperties",
                    params={"FileVersionId": fid},
                    description=f"SyncProperties: {name}",
                    priority=10,
                )
                if resp["error"]:
                    out.append((f"    [fail] {name}: {resp['data']}", TAG_FAIL))
                    bad_n += 1
                else:
                    data = resp["data"] or {}
                    job_id = str((data.get("job") or {}).get("id")
                                 or data.get("id") or "?")
                    out.append((f"    [ok]   {name}  (job {job_id})", TAG_PASS))
                    ok_n += 1
            return ok_n, bad_n, out

        ok_n, bad_n, out = asyncio.run(submit_all())
        return StepOutcome(
            ok=bad_n == 0,
            summary=f"Sync Properties: {ok_n} queued, {bad_n} failed.",
            lines=out,
        )

    return StepOutcome(
        ok=True,
        summary=f"{len(version_ids)} file(s) ready to sync — click Apply to queue.",
        lines=lines, pending_apply=apply,
    )


def _names_by_version_id(compliance: dict[str, Any]) -> dict[str, str]:
    """Map file-version id -> display name, for readable log lines."""
    names: dict[str, str] = {}
    info = compliance.get("info") or {}
    top_id = str(info.get("file_version_id") or "")
    if top_id:
        names[top_id] = str(compliance.get("file_name") or top_id)
    for child in compliance.get("children") or []:
        cid = str(child.get("file_version_id") or "")
        if cid:
            names[cid] = str(child.get("file_name") or cid)
    return names
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: PASS (27 tests)

- [ ] **Step 5: Commit**

```bash
git add release_steps.py tests/test_release_steps.py
git commit -m "feat(release-steps): add the Sync Properties step with a preview gate"
```

---

## Task 7: Step 3 engine — Release Files

**Files:**
- Modify: `release_steps.py`
- Test: `tests/test_release_steps.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.fixture
def fake_sdk(monkeypatch):
    """Stub the vault_sdk bridge — the real one shells out to PowerShell."""
    calls = {"updated": []}

    def lookup_file(master_id):
        return {"found": True, "masterId": master_id}

    def find_state_id_for_file(record, name):
        return 42 if name == "Released" else None

    def update_file_lifecycle_states(masters, state_id, comment=""):
        calls["updated"].append((list(masters), state_id, comment))
        return {"updated": len(masters)}

    class VaultSDKError(Exception):
        pass

    monkeypatch.setattr(release_steps, "_sdk", lambda: type("SDK", (), {
        "lookup_file": staticmethod(lookup_file),
        "find_state_id_for_file": staticmethod(find_state_id_for_file),
        "update_file_lifecycle_states": staticmethod(update_file_lifecycle_states),
        "VaultSDKError": VaultSDKError,
    }))
    return calls


def test_release_preview_resolves_the_state_and_writes_nothing(fake_sdk):
    c = _compliance(children=[("200", "20")])
    out = release_steps.run_release_files(None, "V1", c, target_state="Released")

    assert out.ok is True
    assert out.pending_apply is not None
    assert fake_sdk["updated"] == []           # nothing moved
    assert "42" in out.summary                 # resolved state id is visible


def test_release_apply_promotes_every_master_id(fake_sdk):
    c = _compliance(children=[("200", "20")])
    applied = release_steps.run_release_files(
        None, "V1", c, target_state="Released").pending_apply()

    assert applied.ok is True
    masters, state_id, _comment = fake_sdk["updated"][0]
    assert masters == [10, 20]
    assert state_id == 42


def test_release_honours_an_explicit_state_id_override(fake_sdk):
    release_steps.run_release_files(
        None, "V1", _compliance(), target_state="Anything", state_id=99,
    ).pending_apply()

    _masters, state_id, _comment = fake_sdk["updated"][0]
    assert state_id == 99


def test_release_fails_when_the_state_cannot_be_resolved(fake_sdk):
    out = release_steps.run_release_files(
        None, "V1", _compliance(), target_state="Nonexistent")

    assert out.ok is False
    assert out.pending_apply is None
    assert fake_sdk["updated"] == []


def test_release_with_no_files_is_ok_and_stages_nothing(fake_sdk):
    out = release_steps.run_release_files(
        None, "V1", {}, target_state="Released")
    assert out.ok is True
    assert out.pending_apply is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: FAIL — no attribute `run_release_files`

- [ ] **Step 3: Implement**

```python
def _sdk():
    """Import the vault_sdk bridge lazily.

    It shells out to PowerShell and the .NET SDK, so importing it at module
    load would make every release_steps import pay for a bridge that most
    steps never touch. A fixture replaces this in tests.
    """
    import vault_sdk
    return vault_sdk


def run_release_files(
    api: Any, vault_id: str, compliance: dict[str, Any], *,
    target_state: str, state_id: Optional[int] = None,
) -> StepOutcome:
    """Step 3 — promote every file to the target lifecycle state.

    The state must be resolved inside the *file* lifecycle definition, not the
    item one, so we look up the first file and search its own definition.
    ``state_id`` short-circuits that when the user sets the override field.
    """
    masters = file_master_ids(compliance)
    if not masters:
        return StepOutcome(
            ok=True, summary="No files to release.",
            lines=[("  [warn] No CAD files found to release.", TAG_WARN)],
        )

    sdk = _sdk()
    resolved = state_id
    if resolved is None:
        try:
            first = sdk.lookup_file(masters[0])
        except Exception as exc:  # noqa: BLE001
            return StepOutcome(ok=False, summary=f"File lookup failed: {exc}",
                               lines=[(f"  [fail] {exc}", TAG_FAIL)])
        if not first.get("found"):
            msg = f"Could not look up file masterId={masters[0]}"
            return StepOutcome(ok=False, summary=msg,
                               lines=[(f"  [fail] {msg}", TAG_FAIL)])
        resolved = sdk.find_state_id_for_file(first, target_state)

    if resolved is None:
        msg = (f"Could not resolve a lifecycle state id for {target_state!r} "
               f"in the file's lifecycle. Set 'State ID (override)'.")
        return StepOutcome(ok=False, summary=msg,
                           lines=[(f"  [fail] {msg}", TAG_FAIL)])

    lines = [
        (f"  {len(masters)} file(s) will move to '{target_state}' "
         f"(state_id={resolved}).", TAG_INFO),
        ("  This changes lifecycle state in Vault and cannot be undone "
         "from here.", TAG_WARN),
    ]

    def apply() -> StepOutcome:
        try:
            result = sdk.update_file_lifecycle_states(
                masters, resolved,
                comment=f"Released via Release Workflow to {target_state}",
            )
        except Exception as exc:  # noqa: BLE001
            return StepOutcome(ok=False, summary=f"Release failed: {exc}",
                               lines=[(f"  [fail] {exc}", TAG_FAIL)])
        moved = result.get("updated", len(masters))
        return StepOutcome(
            ok=True, summary=f"Released {moved} file(s) to '{target_state}'.",
            lines=[(f"  [ok] Released {moved} file(s).", TAG_PASS)],
        )

    return StepOutcome(
        ok=True,
        summary=(f"{len(masters)} file(s) ready to move to '{target_state}' "
                 f"(state_id={resolved}) — click Apply."),
        lines=lines, pending_apply=apply,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: PASS (32 tests)

- [ ] **Step 5: Commit**

```bash
git add release_steps.py tests/test_release_steps.py
git commit -m "feat(release-steps): add the Release Files step with a preview gate"
```

---

## Task 8: Step 4 engine — BOM → Purchased Parts List

`add_missing_bom_rows(client, df, *, dry_run, sources, update_existing)` returns
`{"missing", "checked", "already_present", "existing_count", "created", "updated", "errors", "by_source", "rows", "dry_run"}`.
The SharePoint client comes from `supplier_pricing.cli._connect_client()`, which
connects **non-interactively** — it works from inside the wizard process.

**Files:**
- Modify: `release_steps.py`
- Test: `tests/test_release_steps.py`

- [ ] **Step 1: Write the failing tests**

```python
def _install_list_sync(monkeypatch, *, report=None, connect_error=None):
    """Stub the BOM parse, the SharePoint connect, and the sync call."""
    seen = {"dry_runs": []}

    def fake_connect():
        if connect_error:
            raise RuntimeError(connect_error)
        return object()

    def fake_add(client, df, *, dry_run=True, sources=None,
                 update_existing=False):
        seen["dry_runs"].append(dry_run)
        base = {"missing": ["SF-001902", "SF-001905"], "checked": 42,
                "already_present": 40, "existing_count": 900, "created": 0,
                "updated": 0, "errors": [], "by_source": {"Buy": 2},
                "rows": [], "dry_run": dry_run}
        out = dict(base, **(report or {}))
        if not dry_run:
            out["created"] = len(out["missing"])
        return out

    monkeypatch.setattr(release_steps, "_list_sync_deps",
                        lambda: (fake_connect, fake_add,
                                 lambda _p: ("DF", None)))
    return seen


def test_purchased_parts_preview_is_a_dry_run(monkeypatch):
    seen = _install_list_sync(monkeypatch)
    out = release_steps.run_purchased_parts_list("C:/bom.xlsx")

    assert out.ok is True
    assert out.pending_apply is not None
    assert seen["dry_runs"] == [True]          # nothing written
    # Each addition is named, so ISO/DIN fasteners that never match are visible
    # before they are added.
    assert any("SF-001902" in text for text, _tag in out.lines)


def test_purchased_parts_apply_writes(monkeypatch):
    seen = _install_list_sync(monkeypatch)
    applied = release_steps.run_purchased_parts_list("C:/bom.xlsx").pending_apply()

    assert applied.ok is True
    assert seen["dry_runs"] == [True, False]
    assert "2" in applied.summary


def test_purchased_parts_stages_nothing_when_list_is_current(monkeypatch):
    _install_list_sync(monkeypatch, report={"missing": [], "by_source": {}})
    out = release_steps.run_purchased_parts_list("C:/bom.xlsx")

    assert out.ok is True
    assert out.pending_apply is None           # nothing to apply
    assert "0" in out.summary or "up to date" in out.summary.lower()


def test_purchased_parts_explains_a_sharepoint_auth_failure(monkeypatch):
    _install_list_sync(monkeypatch, connect_error="not signed in")
    out = release_steps.run_purchased_parts_list("C:/bom.xlsx")

    assert out.ok is False
    assert "supplier_pricing probe" in out.summary
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: FAIL — no attribute `_list_sync_deps`

- [ ] **Step 3: Implement**

```python
def _list_sync_deps():
    """Return (connect_client, add_missing_bom_rows, bom_dataframe_from_file).

    Bundled behind one seam so a single monkeypatch replaces the whole
    SharePoint path in tests.
    """
    import bom_list_sync
    from supplier_pricing.cli import _connect_client
    return (_connect_client, bom_list_sync.add_missing_bom_rows,
            bom_list_sync.bom_dataframe_from_file)


# The non-interactive Graph connect fails this way when the token cache is
# empty; the fix is a one-off interactive probe, so say so rather than
# surfacing a bare "not signed in".
_PROBE_HINT = ("Run once in a terminal to sign in:\n"
               "    python -m supplier_pricing probe")


def run_purchased_parts_list(
    bom_path: str, *, buy_only: bool = True, update_existing: bool = False,
) -> StepOutcome:
    """Step 4 — add BOM parts missing from the Engineering Purchased Parts list.

    Preview is a dry run that names every proposed addition. That matters:
    the list is keyed on SF-###### numbers, so ISO/DIN fasteners in an Inventor
    BOM never match and would otherwise be added silently as new parts.
    """
    connect, add_rows, parse_bom = _list_sync_deps()

    df, err = parse_bom(bom_path)
    if err:
        return StepOutcome(ok=False, summary=f"BOM could not be read: {err}",
                           lines=[(f"  [fail] {err}", TAG_FAIL)])

    sources = {"Buy", "Other"} if buy_only else None

    def sync(dry_run: bool) -> dict[str, Any]:
        client = connect()
        return add_rows(client, df, dry_run=dry_run, sources=sources,
                        update_existing=update_existing)

    try:
        report = sync(dry_run=True)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "not signed in" in msg.lower():
            msg = f"{msg}\n{_PROBE_HINT}"
        return StepOutcome(ok=False, summary=msg,
                           lines=[(f"  [fail] {msg}", TAG_FAIL)])

    missing = report.get("missing") or []
    checked = report.get("checked", 0)
    present = report.get("already_present", 0)

    lines = [(f"  Dry run — {checked} BOM part(s) checked, {present} already "
              f"listed, {len(missing)} new.", TAG_INFO)]
    for name in missing:
        lines.append((f"      + {name}", TAG_WARN))
    if report.get("skipped_no_name"):
        lines.append((f"  [warn] {report['skipped_no_name']} row(s) had no file "
                      f"name and were skipped.", TAG_WARN))

    if not missing:
        return StepOutcome(
            ok=True,
            summary=f"Purchased Parts List is up to date — 0 of {checked} missing.",
            lines=lines,
        )

    def apply() -> StepOutcome:
        try:
            applied = sync(dry_run=False)
        except Exception as exc:  # noqa: BLE001
            return StepOutcome(ok=False, summary=str(exc),
                               lines=[(f"  [fail] {exc}", TAG_FAIL)])
        created = applied.get("created", 0)
        errors = applied.get("errors") or []
        out = [(f"  [ok] Added {created} part(s) to the list.", TAG_PASS)]
        for e in errors:
            out.append((f"    [fail] {e.get('name')}: {e.get('error')}", TAG_FAIL))
        return StepOutcome(
            ok=not errors,
            summary=f"Purchased Parts List: added {created}, {len(errors)} failed.",
            lines=out,
        )

    return StepOutcome(
        ok=True,
        summary=f"{len(missing)} part(s) missing from the list — click Apply to add.",
        lines=lines, pending_apply=apply,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: PASS (36 tests)

- [ ] **Step 5: Commit**

```bash
git add release_steps.py tests/test_release_steps.py
git commit -m "feat(release-steps): add the Purchased Parts List step with a dry-run gate"
```

---

## Task 9: Step 5 engine — BOM → Publish Deliverables

`publish_bom.scan_bom(api, vault_id, bom_file_path, *, top_assembly, on_progress)`
returns `(list[ScanRow], error)`. `publish_bom.submit_jobs(api, vault_id, scan_rows, on_progress, *, priority)`
returns `{"submitted", "failed", "jobs"}`. Both are async.

**Files:**
- Modify: `release_steps.py`
- Test: `tests/test_release_steps.py`

- [ ] **Step 1: Write the failing tests**

```python
def _scan_row(stem, *, model="m.ipt", drawing="d.idw", status="2 jobs"):
    import publish_bom
    return publish_bom.ScanRow(
        stem=stem, description="", is_top=False,
        model_name=model, model_version_id="1" if model else "",
        drawing_name=drawing, drawing_version_id="2" if drawing else "",
        status=status,
    )


def _install_publish(monkeypatch, rows, error=None, submitted=None):
    seen = {"submits": []}

    async def fake_scan(api, vault_id, path, *, top_assembly="",
                        on_progress=None):
        return list(rows), error

    async def fake_submit(api, vault_id, scan_rows, on_progress=None, *,
                          priority=10):
        seen["submits"].append(list(scan_rows))
        return submitted or {"submitted": sum(r.job_count for r in scan_rows),
                             "failed": 0, "jobs": []}

    monkeypatch.setattr(release_steps, "_publish_deps",
                        lambda: (fake_scan, fake_submit))
    return seen


def test_publish_preview_scans_without_submitting(monkeypatch):
    seen = _install_publish(monkeypatch, [_scan_row("CD-001659")])
    out = release_steps.run_publish_deliverables(None, "V1", "C:/bom.xlsx")

    assert out.ok is True
    assert out.pending_apply is not None
    assert seen["submits"] == []


def test_publish_preview_names_parts_with_no_drawing(monkeypatch):
    rows = [_scan_row("CD-001659"),
            _scan_row("CD-001700", drawing="", status="STEP only - no drawing")]
    _install_publish(monkeypatch, rows)
    out = release_steps.run_publish_deliverables(None, "V1", "C:/bom.xlsx")

    assert any("CD-001700" in text for text, _tag in out.lines)


def test_publish_apply_submits_the_scanned_rows(monkeypatch):
    seen = _install_publish(monkeypatch, [_scan_row("CD-001659")])
    applied = release_steps.run_publish_deliverables(
        None, "V1", "C:/bom.xlsx").pending_apply()

    assert applied.ok is True
    assert len(seen["submits"][0]) == 1


def test_publish_surfaces_a_parse_error(monkeypatch):
    _install_publish(monkeypatch, [], error="This BOM has no file-name column")
    out = release_steps.run_publish_deliverables(None, "V1", "C:/bom.xlsx")

    assert out.ok is False
    assert out.pending_apply is None
    assert "file-name column" in out.summary


def test_publish_stages_nothing_when_no_row_has_a_job(monkeypatch):
    _install_publish(monkeypatch, [
        _scan_row("CD-001700", model="", drawing="", status="not in Vault")])
    out = release_steps.run_publish_deliverables(None, "V1", "C:/bom.xlsx")

    assert out.pending_apply is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: FAIL — no attribute `_publish_deps`

- [ ] **Step 3: Implement**

```python
def _publish_deps():
    """Return (scan_bom, submit_jobs) from the publish_bom engine."""
    import publish_bom
    return publish_bom.scan_bom, publish_bom.submit_jobs


def run_publish_deliverables(
    api: Any, vault_id: str, bom_path: str, *, top_assembly: str = "",
) -> StepOutcome:
    """Step 5 — queue PDF and STEP publish jobs for every Make part.

    The scan reports Make parts with no drawing as gaps. The job server
    publishes a PDF *from* an existing drawing; it cannot author one, so those
    gaps are reported and never fixed here.
    """
    import asyncio

    scan_bom, submit_jobs = _publish_deps()

    rows, error = asyncio.run(
        scan_bom(api, vault_id, bom_path, top_assembly=top_assembly))
    if error:
        return StepOutcome(ok=False, summary=error,
                           lines=[(f"  [fail] {error}", TAG_FAIL)])

    total_jobs = sum(r.job_count for r in rows)
    gaps = [r for r in rows if r.job_count < 2]

    lines = [(f"  Scanned {len(rows)} part(s) — {total_jobs} job(s) to queue.",
              TAG_INFO)]
    for r in gaps:
        lines.append((f"      ! {r.stem:20s} {r.status}", TAG_WARN))
    if gaps:
        lines.append(("  Gaps are reported, never created — the job server "
                      "cannot author a missing drawing.", TAG_DIM))

    if not total_jobs:
        return StepOutcome(
            ok=False,
            summary=f"Nothing to publish — no resolved files among {len(rows)} part(s).",
            lines=lines,
        )

    def apply() -> StepOutcome:
        result = asyncio.run(submit_jobs(api, vault_id, rows))
        submitted = result.get("submitted", 0)
        failed = result.get("failed", 0)
        return StepOutcome(
            ok=failed == 0,
            summary=f"Publish Deliverables: {submitted} queued, {failed} failed.",
            lines=[(f"  [ok] Queued {submitted} job(s), {failed} failed.",
                    TAG_PASS if not failed else TAG_FAIL)],
        )

    return StepOutcome(
        ok=True,
        summary=(f"{total_jobs} job(s) ready across {len(rows)} part(s), "
                 f"{len(gaps)} gap(s) — click Apply to queue."),
        lines=lines, pending_apply=apply,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: PASS (41 tests)

- [ ] **Step 5: Commit**

```bash
git add release_steps.py tests/test_release_steps.py
git commit -m "feat(release-steps): add the Publish Deliverables step with a scan gate"
```

---

## Task 10: Step 6 engine — BOM → Purchasing Sheet

`bom_purchasing.generate_from_file(bom_file_path, assembly_number, output_dir="", reference_path="")`
returns `{"output_path", "matched_parts", "total_purchased_parts", "unmatched_parts", "warnings"}`
or `{"error": True, "message": ...}`. It writes `{assembly_number}-PurchasingExport.xlsx`
into `output_dir`, defaulting to the BOM's own folder.

**No apply gate** — this writes a local spreadsheet and touches neither Vault
nor SharePoint. A gate guarding nothing trains people to click through gates.

**Files:**
- Modify: `release_steps.py`
- Test: `tests/test_release_steps.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_purchasing_sheet_writes_in_one_click(monkeypatch):
    monkeypatch.setattr(release_steps, "_generate_sheet", lambda **kw: {
        "output_path": "C:/out/CD-001659-PurchasingExport.xlsx",
        "matched_parts": 38, "total_purchased_parts": 42,
        "unmatched_parts": ["ISO-4762-M4x12"], "warnings": [],
    })

    out = release_steps.run_purchasing_sheet("C:/bom.xlsx", "CD-001659")

    assert out.ok is True
    assert out.pending_apply is None           # deliberately ungated
    assert "CD-001659-PurchasingExport.xlsx" in out.summary


def test_purchasing_sheet_reports_unmatched_parts(monkeypatch):
    monkeypatch.setattr(release_steps, "_generate_sheet", lambda **kw: {
        "output_path": "C:/out/x.xlsx", "matched_parts": 1,
        "total_purchased_parts": 2, "unmatched_parts": ["ISO-4762-M4x12"],
        "warnings": ["price missing"],
    })

    out = release_steps.run_purchasing_sheet("C:/bom.xlsx", "CD-001659")

    assert any("ISO-4762-M4x12" in text for text, _tag in out.lines)
    assert any("price missing" in text for text, _tag in out.lines)


def test_purchasing_sheet_surfaces_an_error(monkeypatch):
    monkeypatch.setattr(release_steps, "_generate_sheet", lambda **kw: {
        "error": True, "message": "BOM file not found: C:/bom.xlsx"})

    out = release_steps.run_purchasing_sheet("C:/bom.xlsx", "CD-001659")

    assert out.ok is False
    assert "not found" in out.summary
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: FAIL — no attribute `_generate_sheet`

- [ ] **Step 3: Implement**

```python
def _generate_sheet(**kwargs: Any) -> dict[str, Any]:
    """Thin seam over bom_purchasing.generate_from_file, for testing."""
    import bom_purchasing
    return bom_purchasing.generate_from_file(**kwargs)


def run_purchasing_sheet(
    bom_path: str, assembly_number: str, *, output_dir: str = "",
) -> StepOutcome:
    """Step 6 — build the branded purchasing workbook.

    Deliberately has no apply gate: it writes one .xlsx to disk and touches
    neither Vault nor SharePoint. ``output_dir`` defaults to the BOM's folder.
    """
    result = _generate_sheet(
        bom_file_path=bom_path, assembly_number=assembly_number,
        output_dir=output_dir,
    )
    if result.get("error"):
        msg = result.get("message", "sheet generation failed")
        return StepOutcome(ok=False, summary=msg,
                           lines=[(f"  [fail] {msg}", TAG_FAIL)])

    path = result.get("output_path", "")
    matched = result.get("matched_parts", 0)
    total = result.get("total_purchased_parts", 0)
    unmatched = result.get("unmatched_parts") or []
    warnings = result.get("warnings") or []

    lines = [(f"  [ok] Wrote {path}", TAG_PASS),
             (f"  {matched}/{total} purchased part(s) matched the reference "
              f"file.", TAG_INFO)]
    for part in unmatched:
        lines.append((f"      ? {part}  (no reference match)", TAG_WARN))
    for w in warnings:
        lines.append((f"  [warn] {w}", TAG_WARN))

    return StepOutcome(
        ok=True,
        summary=f"Purchasing Sheet written: {path}",
        lines=lines, result=result,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_release_steps.py -q`
Expected: PASS (44 tests)

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: ~422 passed, 1 skipped. The exact count depends on how the tests
above were split; what matters is **zero failures** and the 377 baseline tests
still passing.

- [ ] **Step 6: Commit**

```bash
git add release_steps.py tests/test_release_steps.py
git commit -m "feat(release-steps): add the Purchasing Sheet step"
```

---

## Task 11: Wizard shell — inputs, step list, REVIEW status

Rewrite `ReleaseWorkflowGUI`. Keep the existing header, output panel, status bar
and `_brand_button` code as-is; replace the inputs, `STEPS`, and status handling.

**Files:**
- Modify: `gui/release_workflow.py`
- Test: `tests/test_release_workflow_gui.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_release_workflow_gui.py`:

```python
# tests/test_release_workflow_gui.py
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

tk = pytest.importorskip("tkinter")


def _make_gui():
    from gui.release_workflow import ReleaseWorkflowGUI
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    gui = ReleaseWorkflowGUI(root)
    root.update_idletasks()
    return root, gui


def test_the_six_steps_are_the_file_driven_ones():
    from gui.release_workflow import STEPS
    names = [name for _num, name, _desc in STEPS]
    assert names == [
        "Property Check",
        "Sync Properties",
        "Release Files",
        "BOM → Purchased Parts List",
        "BOM → Publish Deliverables",
        "BOM → Purchasing Sheet",
    ]


def test_the_retired_item_steps_are_gone():
    from gui.release_workflow import STEPS
    names = " ".join(name for _num, name, _desc in STEPS)
    for retired in ("Readiness report", "Download local", "Inventor rebuild",
                    "Release items", "Compliance check"):
        assert retired not in names


def test_review_is_a_distinct_status():
    """A step waiting on a human must not look like one still calling Vault."""
    from gui.release_workflow import (
        STATUS_REVIEW, STATUS_RUNNING, STATUS_TAGS)
    assert STATUS_REVIEW in STATUS_TAGS
    assert STATUS_TAGS[STATUS_REVIEW] != STATUS_TAGS[STATUS_RUNNING]


def test_the_window_takes_both_inputs():
    root, gui = _make_gui()
    try:
        assert gui.top_file_var.get() == ""
        assert gui.bom_path_var.get() == ""
    finally:
        root.destroy()


def test_changing_the_top_file_resets_the_vault_steps_only():
    from gui.release_workflow import STATUS_OK, STATUS_PENDING
    root, gui = _make_gui()
    try:
        for num in ("1", "2", "3", "4", "5", "6"):
            gui._update_step_label(num, STATUS_OK)
        gui.top_file_var.set("CD-001659.iam")
        root.update_idletasks()
        assert gui.statuses["1"] == STATUS_PENDING
        assert gui.statuses["3"] == STATUS_PENDING
        assert gui.statuses["5"] == STATUS_OK      # BOM steps untouched
    finally:
        root.destroy()


def test_changing_the_bom_resets_the_bom_steps_only():
    from gui.release_workflow import STATUS_OK, STATUS_PENDING
    root, gui = _make_gui()
    try:
        for num in ("1", "2", "3", "4", "5", "6"):
            gui._update_step_label(num, STATUS_OK)
        gui.bom_path_var.set("C:/bom.xlsx")
        root.update_idletasks()
        assert gui.statuses["4"] == STATUS_PENDING
        assert gui.statuses["6"] == STATUS_PENDING
        assert gui.statuses["1"] == STATUS_OK      # Vault steps untouched
    finally:
        root.destroy()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_release_workflow_gui.py -q`
Expected: FAIL — `STEPS` still holds the seven item-driven names

- [ ] **Step 3: Replace the step table and statuses**

In `gui/release_workflow.py`, replace `STEPS` (lines 95-119) with:

```python
STEPS = [
    ("1", "Property Check",
     "Run the file property rules over the assembly and its CAD BOM"),
    ("2", "Sync Properties",
     "Submit Autodesk.Vault.SyncProperties for every file"),
    ("3", "Release Files",
     "Promote every file to the target lifecycle state"),
    ("4", "BOM → Purchased Parts List",
     "Add parts missing from the Engineering Purchased Parts list"),
    ("5", "BOM → Publish Deliverables",
     "Queue PDF and STEP publish jobs for every Make part"),
    ("6", "BOM → Purchasing Sheet",
     "Build the branded purchasing workbook"),
]

# Steps 1-3 work from the top file name; 4-6 work from the BOM export. Changing
# one input must only invalidate the steps that read it.
VAULT_STEPS = ("1", "2", "3")
BOM_STEPS = ("4", "5", "6")

STATUS_PENDING  = "PENDING"
STATUS_RUNNING  = "RUNNING"
STATUS_REVIEW   = "REVIEW"
STATUS_OK       = "OK"
STATUS_SKIPPED  = "SKIPPED"
STATUS_FAILED   = "FAILED"
STATUS_BLOCKED  = "BLOCKED"

STATUS_TAGS = {
    STATUS_PENDING: (DARK_GRAY,    PALE_BLUE,   "·"),
    STATUS_RUNNING: (WHITE,        MID_BLUE,    "▶"),
    # Amber, not blue: a step waiting on a human must not read as one still
    # talking to Vault.
    STATUS_REVIEW:  (WHITE,        WARN_AMBER,  "?"),
    STATUS_OK:      (DARK_BLUE,    OLIVE_GREEN, "✓"),
    STATUS_SKIPPED: (DARK_GRAY,    LIGHT_GRAY,  "—"),
    STATUS_FAILED:  (WHITE,        RUST_ORANGE, "✗"),
    STATUS_BLOCKED: (WHITE,        RUST_ORANGE, "■"),
}
```

- [ ] **Step 4: Replace the inputs section**

Replace `_build_input_section` (lines 261-365). Keep the `label` and `entry`
helpers verbatim; change the fields:

```python
        # Row 0 — top file name (drives steps 1-3)
        label(inputs, "Top File").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.top_file_var = tk.StringVar()
        tf_frame = tk.Frame(inputs, bg=LIGHT_GRAY)
        tf_frame.grid(row=0, column=1, sticky="ew", padx=(0, 14))
        entry(tf_frame, self.top_file_var, width=18).pack(
            side="left", fill="x", expand=True)
        self._brand_button(
            tf_frame, "Search…", self._open_search_dialog, primary=False,
        ).pack(side="left", padx=(6, 0))

        label(inputs, "Target State").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.target_state_var = tk.StringVar(value="Released")
        self._apply_combobox_style()
        self.target_state_combo = ttk.Combobox(
            inputs, textvariable=self.target_state_var,
            values=["Released"], width=18, style="Vault.TCombobox",
        )
        self.target_state_combo.grid(row=0, column=3, sticky="ew", padx=(0, 14))

        label(inputs, "State ID (override)").grid(
            row=0, column=4, sticky="w", padx=(0, 6))
        self.target_state_id_var = tk.StringVar()
        entry(inputs, self.target_state_id_var, width=8).grid(
            row=0, column=5, sticky="ew", padx=(0, 14))

        # Row 1 — BOM export (drives steps 4-6)
        label(inputs, "BOM Export").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.bom_path_var = tk.StringVar()
        entry(inputs, self.bom_path_var, width=10).grid(
            row=1, column=1, columnspan=5, sticky="ew",
            padx=(0, 6), pady=(10, 0))
        self._brand_button(
            inputs, "Browse…", self._browse_bom, primary=False,
        ).grid(row=1, column=6, sticky="w", pady=(10, 0))

        # Row 2 — toggles
        toggles = tk.Frame(inputs, bg=LIGHT_GRAY)
        toggles.grid(row=2, column=0, columnspan=8, sticky="w", pady=(10, 0))

        self.force_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            toggles, text="Force past compliance gate", variable=self.force_var,
            bg=LIGHT_GRAY, fg=DARK_BLUE, activebackground=LIGHT_GRAY,
            activeforeground=DARK_BLUE, selectcolor=WHITE, font=("Arial", 9),
        ).pack(side="left", padx=(0, 16))

        self.buy_only_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            toggles, text="Buy/Other rows only (list sync)",
            variable=self.buy_only_var,
            bg=LIGHT_GRAY, fg=DARK_BLUE, activebackground=LIGHT_GRAY,
            activeforeground=DARK_BLUE, selectcolor=WHITE, font=("Arial", 9),
        ).pack(side="left", padx=(0, 16))

        # A stale step 1 result must never feed steps 2-3 after the file name
        # changes, and a stale scan must never feed a submit after the BOM
        # changes. Mirrors publish_bom's _invalidate_scan.
        self.top_file_var.trace_add(
            "write", lambda *_a: self._invalidate(VAULT_STEPS))
        self.bom_path_var.trace_add(
            "write", lambda *_a: self._invalidate(BOM_STEPS))
```

Delete `_browse_workfolder`, `_browse_top_iam`, `workfolder_var`, `top_iam_var`,
`visible_inventor_var`, and `soap_version_var`. Add:

```python
    def _browse_bom(self) -> None:
        f = filedialog.askopenfilename(
            title="Pick an exported BOM",
            filetypes=[("BOM export", "*.xlsx *.xls *.csv *.txt"),
                       ("All files", "*.*")],
        )
        if f:
            self.bom_path_var.set(f)

    def _invalidate(self, nums: tuple[str, ...]) -> None:
        """Reset the given steps to PENDING and drop anything they staged."""
        if self.busy:
            return
        for num in nums:
            if self.statuses.get(num) != STATUS_PENDING:
                self._update_step_label(num, STATUS_PENDING)
        if self.pending_step in nums:
            self._clear_pending()
        if "1" in nums:
            self.compliance = None
```

- [ ] **Step 5: Add the new instance state**

In `__init__`, replace `self.downloads = []` with:

```python
        # A step that staged a write parks it here until the user clicks Apply.
        self.pending_apply: Optional[Callable[[], Any]] = None
        self.pending_step: Optional[str] = None
```

Add `_clear_pending` now, in the same task — `_invalidate` above already calls
it, and Task 12 relies on it too:

```python
    def _clear_pending(self) -> None:
        """Drop a staged write without performing it, and restore the button."""
        self.pending_apply = None
        self.pending_step = None
        self.btn_run.configure(text="  Run next step  ")
```

- [ ] **Step 6: Run to verify it passes**

Run: `python -m pytest tests/test_release_workflow_gui.py -q`
Expected: PASS (6 tests)

- [ ] **Step 7: Commit**

```bash
git add gui/release_workflow.py tests/test_release_workflow_gui.py
git commit -m "feat(release-workflow): file-driven steps, dual inputs, REVIEW status"
```

---

## Task 12: Wizard dispatch and the apply gate

**Files:**
- Modify: `gui/release_workflow.py`
- Test: `tests/test_release_workflow_gui.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_staged_step_moves_to_review_and_relabels_the_button():
    from gui.release_workflow import STATUS_REVIEW, WorkerSignal
    import release_steps

    root, gui = _make_gui()
    try:
        staged = release_steps.StepOutcome(
            ok=True, summary="2 file(s) ready", lines=[("preview", "info")],
            pending_apply=lambda: release_steps.StepOutcome(
                ok=True, summary="done"),
        )
        gui._handle_signal(WorkerSignal("step_done", ("2", staged, False)))
        root.update_idletasks()

        assert gui.statuses["2"] == STATUS_REVIEW
        assert gui.pending_step == "2"
        assert "Apply" in gui.btn_run["text"]
    finally:
        root.destroy()


def test_run_all_halts_at_a_pending_apply():
    """A release must never write to Vault or SharePoint unattended."""
    from gui.release_workflow import STATUS_PENDING, WorkerSignal
    import release_steps

    root, gui = _make_gui()
    try:
        staged = release_steps.StepOutcome(
            ok=True, summary="staged",
            pending_apply=lambda: release_steps.StepOutcome(ok=True, summary="x"),
        )
        # run_all_after=True — the sequence must still stop here.
        gui._handle_signal(WorkerSignal("step_done", ("2", staged, True)))
        root.update_idletasks()

        assert gui.statuses["3"] == STATUS_PENDING   # never started
    finally:
        root.destroy()


def test_skipping_a_staged_step_discards_the_write():
    from gui.release_workflow import STATUS_SKIPPED, WorkerSignal
    import release_steps

    fired = []
    root, gui = _make_gui()
    try:
        staged = release_steps.StepOutcome(
            ok=True, summary="staged",
            pending_apply=lambda: fired.append(1),
        )
        gui._handle_signal(WorkerSignal("step_done", ("2", staged, False)))
        gui._on_skip()
        root.update_idletasks()

        assert fired == []                       # nothing was written
        assert gui.statuses["2"] == STATUS_SKIPPED
        assert gui.pending_apply is None
        assert "Apply" not in gui.btn_run["text"]
    finally:
        root.destroy()


def test_a_finished_step_goes_straight_to_ok():
    from gui.release_workflow import STATUS_OK, WorkerSignal
    import release_steps

    root, gui = _make_gui()
    try:
        done = release_steps.StepOutcome(ok=True, summary="clean")
        gui._handle_signal(WorkerSignal("step_done", ("1", done, False)))
        root.update_idletasks()

        assert gui.statuses["1"] == STATUS_OK
        assert gui.pending_step is None
    finally:
        root.destroy()


def test_vault_steps_need_a_top_file_and_bom_steps_need_a_bom():
    root, gui = _make_gui()
    try:
        assert gui._missing_input_for("2") is not None   # no top file yet
        assert gui._missing_input_for("5") is not None   # no BOM yet
        gui.top_file_var.set("CD-001659.iam")
        gui.bom_path_var.set("C:/bom.xlsx")
        assert gui._missing_input_for("2") is None
        assert gui._missing_input_for("5") is None
    finally:
        root.destroy()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_release_workflow_gui.py -q`
Expected: FAIL — `_handle_signal` unpacks `(num, ok, run_all_after)`, not an outcome

- [ ] **Step 3: Replace the step runners with engine calls**

Delete `_run_step_1_compliance` through `_run_step_7_release_items`,
`_log_compliance_summary`, `_child_status`, `_guess_top_assembly`,
`_compliance_blocked`, and the `check_item_properties` / `release_workflow`
imports at lines 70-82. Add at the top:

```python
import release_steps  # noqa: E402
```

Then add:

```python
    def _missing_input_for(self, num: str) -> Optional[str]:
        """Return why this step cannot run yet, or None when it can."""
        if num in VAULT_STEPS and not self.top_file_var.get().strip():
            return "Enter a top file name (e.g. CD-001659.iam) first."
        if num in BOM_STEPS and not self.bom_path_var.get().strip():
            return "Browse to an exported BOM first."
        return None

    def _step_runner(self, num: str) -> Callable[[], Any]:
        """Return a zero-arg callable that runs this step on the worker thread."""
        top_file = self.top_file_var.get().strip()
        bom_path = self.bom_path_var.get().strip()
        target_state = self.target_state_var.get().strip() or "Released"
        state_id = self._target_state_id_or_none()
        buy_only = self.buy_only_var.get()

        def gated(fn: Callable[[], Any]) -> Callable[[], Any]:
            """Steps 2 and 3 only: refuse when Property Check is not clean."""
            def run() -> Any:
                reason = release_steps.property_check_blocked(
                    self.compliance, force=self.force_var.get())
                if reason:
                    return release_steps.StepOutcome(
                        ok=False, summary=reason,
                        lines=[(f"  [blocked] {reason}", "fail")])
                return fn()
            return run

        return {
            "1": lambda: release_steps.run_property_check(
                top_file, api=self.api, vault_id=self.vault_id),
            "2": gated(lambda: release_steps.run_sync_properties(
                self.api, self.vault_id, self.compliance)),
            "3": gated(lambda: release_steps.run_release_files(
                self.api, self.vault_id, self.compliance,
                target_state=target_state, state_id=state_id)),
            "4": lambda: release_steps.run_purchased_parts_list(
                bom_path, buy_only=buy_only),
            "5": lambda: release_steps.run_publish_deliverables(
                self.api, self.vault_id, bom_path,
                top_assembly=Path(bom_path).stem if bom_path else ""),
            "6": lambda: release_steps.run_purchasing_sheet(
                bom_path, Path(bom_path).stem if bom_path else "BOM"),
        }[num]
```

- [ ] **Step 4: Rewrite dispatch and the signal handler**

Replace `_run_step` and the `step_done` branch of `_handle_signal`:

```python
    def _run_step(self, num: str, *, run_all_after: bool) -> None:
        missing = self._missing_input_for(num)
        if missing:
            messagebox.showwarning("Missing input", missing)
            return
        if num in VAULT_STEPS and not self._ensure_signed_in_ui():
            return

        name = next((n for k, n, *_ in STEPS if k == num), "?")
        self._banner(num, name)
        self._update_step_label(num, STATUS_RUNNING)
        self._set_busy(True)
        self.status_var.set(f"Step {num} ({name}) running…")

        runner = self._step_runner(num)

        def thread_main() -> None:
            try:
                outcome = runner()
            except Exception as exc:  # noqa: BLE001
                outcome = release_steps.StepOutcome(
                    ok=False, summary=f"{type(exc).__name__}: {exc}",
                    lines=[(f"  [error] {type(exc).__name__}: {exc}", "fail")])
            self.q.put(WorkerSignal("step_done", (num, outcome, run_all_after)))

        self.worker_thread = threading.Thread(target=thread_main, daemon=True)
        self.worker_thread.start()

    def _run_pending_apply(self) -> None:
        """Perform the write a step staged. Only reachable from the Apply button."""
        num, apply_fn = self.pending_step, self.pending_apply
        if not num or not apply_fn:
            return
        self._clear_pending()
        self._update_step_label(num, STATUS_RUNNING)
        self._set_busy(True)
        self.status_var.set(f"Step {num} applying…")

        def thread_main() -> None:
            try:
                outcome = apply_fn()
            except Exception as exc:  # noqa: BLE001
                outcome = release_steps.StepOutcome(
                    ok=False, summary=f"{type(exc).__name__}: {exc}",
                    lines=[(f"  [error] {type(exc).__name__}: {exc}", "fail")])
            self.q.put(WorkerSignal("step_done", (num, outcome, False)))

        self.worker_thread = threading.Thread(target=thread_main, daemon=True)
        self.worker_thread.start()
```

`_clear_pending` is already defined — Task 11 Step 5 added it. Do not add a
second copy.

The `step_done` branch of `_handle_signal` becomes:

```python
        elif sig.kind == "step_done":
            num, outcome, run_all_after = sig.payload
            for line, tag in outcome.lines:
                self._write(line, tag)
            if num == "1" and outcome.result is not None:
                self.compliance = outcome.result
                self.btn_save_report.configure(state="normal")

            if outcome.pending_apply is not None:
                # Staged, not written. Park it and wait for a human.
                self.pending_apply = outcome.pending_apply
                self.pending_step = num
                self._update_step_label(num, STATUS_REVIEW)
                self.btn_run.configure(text="  Apply  ")
                self._set_busy(False)
                self.status_var.set(outcome.summary)
                return   # never auto-continue a Run all through a write

            self._update_step_label(
                num, STATUS_OK if outcome.ok else STATUS_FAILED)
            self._set_busy(False)
            self.status_var.set(outcome.summary)
            if not outcome.ok:
                return
            if run_all_after:
                nxt = self._next_pending_step()
                if nxt:
                    self._run_step(nxt, run_all_after=True)
                else:
                    self.status_var.set("All steps complete.")
```

- [ ] **Step 5: Route the buttons through the pending gate**

```python
    def _on_run_next(self) -> None:
        if self.busy:
            return
        if self.pending_apply is not None:
            self._run_pending_apply()
            return
        nxt = self._next_pending_step()
        if not nxt:
            messagebox.showinfo("Workflow complete", "No more pending steps.")
            return
        self._run_step(nxt, run_all_after=False)

    def _on_skip(self) -> None:
        if self.busy:
            return
        if self.pending_apply is not None:
            num = self.pending_step
            self._clear_pending()
            self._update_step_label(num, STATUS_SKIPPED)
            self._write(f"\n[skipped] Step {num} — nothing was written.", "dim")
            self.status_var.set(f"Step {num} skipped.")
            return
        nxt = self._next_pending_step()
        if not nxt:
            return
        self._update_step_label(nxt, STATUS_SKIPPED)
        self._write(f"\n[skipped] Step {nxt}", "dim")
        self.status_var.set(f"Step {nxt} skipped.")
```

Add `_ensure_signed_in_ui`, a Tk-thread wrapper that warns instead of logging:

```python
    def _ensure_signed_in_ui(self) -> bool:
        if self.api is not None and self.vault_id:
            return True
        messagebox.showwarning(
            "Not signed in",
            "This step needs a Vault session. Open the workflow from the "
            "launcher, or click Reconnect there first.")
        return False
```

Also update `_on_reset` and `set_part_number`: replace `self.downloads = []`
with `self._clear_pending()`, and rename `set_part_number` to `set_top_file`,
setting `self.top_file_var`.

- [ ] **Step 6: Run to verify it passes**

Run: `python -m pytest tests/test_release_workflow_gui.py -q`
Expected: PASS (11 tests)

- [ ] **Step 7: Commit**

```bash
git add gui/release_workflow.py tests/test_release_workflow_gui.py
git commit -m "feat(release-workflow): dispatch through release_steps with an apply gate"
```

---

## Task 13: Search dialog — items to files

`SearchDialog` calls `api.search_items` (line 1670). The input is a file name
now, so it must call `api.search_files` (`vault_rest_api.py:513`).

**Files:**
- Modify: `gui/release_workflow.py:1469-1829`
- Test: `tests/test_release_workflow_gui.py`

- [ ] **Step 1: Write the failing test**

```python
def test_search_summarises_a_file_record():
    from gui.release_workflow import _summarise_file_for_search
    row = _summarise_file_for_search({
        "name": "CD-001659.iam",
        "properties": {"Revision": "A", "State": "Work in Progress",
                       "Category Name": "Engineering"},
    })
    assert row["file_name"] == "CD-001659.iam"
    assert row["revision"] == "A"
    assert row["state"] == "Work in Progress"


def test_search_falls_back_to_flat_fields():
    from gui.release_workflow import _summarise_file_for_search
    row = _summarise_file_for_search({
        "name": "CD-001659.ipt", "Revision": "B", "State": "Released"})
    assert row["revision"] == "B"
    assert row["state"] == "Released"


def test_search_columns_are_file_shaped():
    from gui.release_workflow import SearchDialog
    ids = [c[0] for c in SearchDialog.COLUMNS]
    assert ids[0] == "file_name"
    assert "number" not in ids
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_release_workflow_gui.py -q`
Expected: FAIL — no `_summarise_file_for_search`

- [ ] **Step 3: Implement**

Replace `SearchDialog.COLUMNS`:

```python
    COLUMNS = [
        ("file_name",   "File Name",   220),
        ("revision",    "Rev",          50),
        ("state",       "State",       130),
        ("category",    "Category",    170),
        ("folder",      "Folder",      280),
    ]
```

Replace the `search_items` call at line 1670 with:

```python
                resp = asyncio.run(self.parent.api.search_files(
                    vault_id=self.parent.vault_id,
                    query=query,
                    limit=limit,
                ))
```

Replace `_summarise_item_for_search` with:

```python
def _summarise_file_for_search(record: dict[str, Any]) -> dict[str, str]:
    """Pick out the fields the search dialog shows for a file record.

    Vault returns file properties either flattened at the root or nested under
    ``properties``; this normalises both.
    """
    props = record.get("properties")
    props = props if isinstance(props, dict) else {}

    def pick(*keys: str, default: str = "") -> str:
        for source in (record, props):
            for k in keys:
                v = source.get(k)
                if v not in (None, ""):
                    return str(v)
        return default

    return {
        "file_name": pick("name", "Name", "fileName"),
        "revision":  pick("revision", "Revision"),
        "state":     pick("state", "State", "lifecycleState"),
        "category":  pick("category", "Category Name", "categoryName"),
        "folder":    pick("folderPath", "Folder Path", "path"),
    }
```

Update `_extract_rows` to call `_summarise_file_for_search`, and its key list
from `("results", "items", "itemVersions", ...)` to
`("results", "files", "fileVersions", "data", "value", "records")`.

In `_on_use_selected`, replace the `number` lookup with:

```python
        name = str(row.get("file_name") or "").strip()
        if not name:
            messagebox.showwarning(
                "No file name",
                "Selected row has no file name — pick a different result.",
                parent=self.win)
            return
        self.parent.set_top_file(name)
```

Update the dialog's prefill at line 1497 to read `parent_gui.top_file_var`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_release_workflow_gui.py -q`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add gui/release_workflow.py tests/test_release_workflow_gui.py
git commit -m "feat(release-workflow): search files instead of retired items"
```

---

## Task 14: Un-break the launcher tile

**Files:**
- Modify: `gui/launcher.py:578-587`
- Test: `tests/test_launcher_flags.py:28-35`

- [ ] **Step 1: Update the failing test**

Replace `test_item_master_tools_are_flagged_broken` in
`tests/test_launcher_flags.py`:

```python
def test_mfg_package_is_still_flagged_broken():
    """MFG Order Package still resolves parts through Vault items."""
    root, gui = _make_gui()
    try:
        btn = gui.tool_buttons["MFG Order Package"]
        assert str(btn["state"]) == "disabled"
    finally:
        root.destroy()


def test_release_workflow_is_enabled_again():
    """The wizard was rewritten onto files, so it is off the broken list."""
    root, gui = _make_gui()
    try:
        btn = gui.tool_buttons["Release Workflow"]
        assert str(btn["state"]) != "disabled"
    finally:
        root.destroy()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_launcher_flags.py -q`
Expected: FAIL — Release Workflow is still disabled

- [ ] **Step 3: Implement**

In `gui/launcher.py`, replace the Release Workflow `_tool_row` call:

```python
        self._tool_row(
            body,
            "Release Workflow",
            "Walk one assembly through release: property check, sync "
            "properties, release the files, then BOM → purchased parts, "
            "published deliverables, and purchasing sheet.",
            "Open Workflow",
            self._on_open_workflow,
            primary=True,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_launcher_flags.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -q`
Expected: ~437 passed, 1 skipped. **Zero failures** is the bar; every one of
the 377 baseline tests must still pass.

- [ ] **Step 6: Commit**

```bash
git add gui/launcher.py tests/test_launcher_flags.py
git commit -m "feat(launcher): re-enable the Release Workflow tile"
```

---

## Task 15: Manual smoke test

Automated tests never touch a live Vault. Confirm the wizard end to end before
calling this done.

- [ ] **Step 1: Launch**

Run: `python app.py --gui`
Expected: the dashboard opens, Vault shows **Connected**, and the Release
Workflow tile is enabled with no ⛔ marker.

- [ ] **Step 2: Run step 1 against a real assembly**

Click **Open Workflow**, use **Search…** to find a `.iam`, click
**Run next step**.
Expected: the output panel shows the top file verdict and a CAD BOM roll-up.
Steps 4–6 stay runnable regardless of the verdict.

- [ ] **Step 3: Confirm the gate**

On an assembly with failing properties, click **Run next step** for step 2.
Expected: `[blocked]` in the output, step 2 marked FAILED, nothing queued.
Tick **Force past compliance gate** and re-run.
Expected: it proceeds to a REVIEW preview.

- [ ] **Step 4: Confirm the apply gate holds**

With step 2 in REVIEW, click **Skip step**.
Expected: no jobs appear in the Vault Explorer queue.

Then re-run step 2 and click **Apply**.
Expected: jobs appear in the queue.

- [ ] **Step 5: Confirm Run all halts**

Reset, set both inputs, click **Run all remaining**.
Expected: it runs step 1, then stops at step 2's REVIEW rather than writing.

- [ ] **Step 6: Record the results**

Append a `## Live verification, <date>` section to the spec noting what was
run, against which assembly, and anything that behaved differently — matching
the pattern in `2026-07-28-publish-bom-deliverables-design.md`.

```bash
git add docs/superpowers/specs/2026-07-29-release-workflow-file-driven-design.md
git commit -m "docs(release-workflow): record the live verification run"
```
