# Formed Fiber Design-to-Process Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A launcher tool that produces the Simplifyber Formed Fiber design-to-process handoff PDF, filling material and filenames from Vault, mass and volume from Inventor, and the two press pressures from a machine library — leaving four values to type.

**Architecture:** Three dependency-light engine modules (data model and rules, Vault lookup, PDF rendering) with a Tk form on top. All Vault and Inventor work happens on a worker thread and returns through a queue drained on the Tk thread. Vault access reuses the tested helpers in `scripts/check_file_properties.py`; nothing new is added to `vault_rest_api.py`.

**Tech Stack:** Python 3.10+, tkinter, reportlab (PDF out), pypdf (PDF assertions in tests), pywin32 + Inventor COM (mass/volume), pytest.

**Spec:** `docs/superpowers/specs/2026-08-06-formed-fiber-handoff-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `formed_fiber_handoff.py` *(create)* | Data model, label constants, the Standard Dry Weight rule, machine library, path/filename resolution, document row builders. No network, no Tk, no reportlab. |
| `formed_fiber_vault.py` *(create)* | Vault lookup. Wraps `fetch_file` / `fetch_cad_children` and reshapes their output. |
| `formed_fiber_pdf.py` *(create)* | reportlab renderer. Rows in, PDF on disk out. |
| `machines.json` *(create)* | Machine profiles. |
| `gui/formed_fiber_handoff.py` *(create)* | The Tk form. |
| `scripts/inventor_automation.py` *(modify)* | Add `PhysicalProperties` + `read_part_physical_properties`; add `open_visible` to `open_document`. |
| `gui/launcher.py` *(modify)* | One tool row, one handler. |
| `gui/__init__.py` *(modify)* | Docstring is stale at "four GUIs"; correct it. |
| `requirements.txt` *(modify)* | Declare `pywin32`. |
| `config.json.example` *(modify)* | Add the `handoff` block. |
| `tests/test_formed_fiber_handoff.py` *(create)* | Engine + Vault + PDF tests. |
| `tests/test_formed_fiber_inventor.py` *(create)* | Inventor reader tests against a fake COM object. |
| `tests/test_formed_fiber_gui.py` *(create)* | GUI construction and field-behaviour tests. |

---

## Task 1: Engine — data model and the Standard Dry Weight rule

**Files:**
- Create: `formed_fiber_handoff.py`
- Create: `tests/test_formed_fiber_handoff.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_formed_fiber_handoff.py`:

```python
# tests/test_formed_fiber_handoff.py
"""Engine tests for the Formed Fiber design-to-process handoff."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import formed_fiber_handoff as engine


# --------------------------------------------------------------- standard dry

def test_standard_dry_weight_is_wet_basis():
    """A standard dry part is 5% water BY MASS OF THE FINISHED PART.

    So the bone dry fibre is the other 95% and the divisor is 0.95. The
    tempting alternative -- dry-basis regain, `* 1.05`, the textile
    convention -- gives 105.00 here. This test exists to stop someone
    "correcting" the formula to it.
    """
    assert engine.standard_dry_weight("100") == "105.26"
    assert engine.standard_dry_weight("250") == "263.16"


def test_standard_dry_weight_rejects_unusable_input():
    for bad in ("", "   ", "abc", "0", "-5", None):
        assert engine.standard_dry_weight(bad) == "", f"{bad!r} should give ''"


def test_standard_dry_weight_accepts_decimals_and_whitespace():
    assert engine.standard_dry_weight(" 47.5 ") == "50.00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'formed_fiber_handoff'`

- [ ] **Step 3: Write minimal implementation**

Create `formed_fiber_handoff.py`:

```python
"""
Engine for the Formed Fiber design-to-process handoff document.

Everything about the handoff except how it is drawn (``formed_fiber_pdf``),
where the Vault data comes from (``formed_fiber_vault``) and how it is
collected (``gui.formed_fiber_handoff``): the data model, the field labels
that both the form and the PDF use, the one derived value, the machine
library, and the rules for where the finished PDF goes.

Deliberately dependency-light -- no network, no Tk, no reportlab -- so every
rule in here is testable in isolation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
MACHINES_PATH = PROJECT_ROOT / "machines.json"

# A standard dry part carries 5% water BY MASS OF THE FINISHED STANDARD DRY
# PART, so the bone dry fibre is the other 95%. This is a WET-BASIS moisture
# content, not the dry-basis regain the textile industry usually quotes -- do
# NOT "simplify" this to `* 1.05`, which is a different number (105.00 vs
# 105.26 on a 100 g part). Confirmed with engineering, 2026-08-06.
STANDARD_DRY_FIBRE_FRACTION = 0.95


def standard_dry_weight(bone_dry: Any) -> str:
    """Standard dry weight in grams, as display text, from a bone dry weight.

    Returns "" for anything that is not a positive number, so a blank or
    half-typed entry leaves the field empty instead of showing a bogus value.
    """
    try:
        value = float(str(bone_dry).strip())
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return f"{value / STANDARD_DRY_FIBRE_FRACTION:.2f}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: PASS — 3 passed

- [ ] **Step 5: Add the data model and its test**

Append to `tests/test_formed_fiber_handoff.py`:

```python
# ---------------------------------------------------------------- data model

from datetime import date  # noqa: E402


def test_value_defaults_are_blank_and_not_targets():
    v = engine.Value()
    assert v.text == ""
    assert v.is_target is False


def test_handoff_data_defaults_are_usable():
    """A bare HandoffData must be constructible -- the GUI builds one before
    anything has been picked, and the renderer must cope with it."""
    data = engine.HandoffData()
    assert data.machine == ""
    assert data.material == ""
    assert data.volume == ""
    assert data.bone_dry_weight == engine.Value()
    assert data.generated_on == date.today()
```

Append to `formed_fiber_handoff.py`:

```python
@dataclass(frozen=True)
class Value:
    """One production value plus whether it is a target rather than measured.

    Frozen so it is hashable and therefore safe as a dataclass field default.
    """

    text: str = ""
    is_target: bool = False


@dataclass
class HandoffData:
    """Everything that prints on the handoff document."""

    # Section 1 -- Machine and Process Details. Plain strings: a machine is
    # never a "target".
    machine: str = ""
    vacuum_pressure: str = ""
    press_pressure: str = ""
    machine_characterized: bool = True

    # Section 2 -- Production Details. Material and volume are plain strings
    # for the same reason: one names a material, the other is geometry read
    # off the model. Neither is a measurement with a target counterpart.
    material: str = ""
    volume: str = ""                     # cm³, from Inventor
    dry_thickness: Value = Value()
    wet_thickness: Value = Value()
    wet_weight: Value = Value()
    bone_dry_weight: Value = Value()
    standard_dry_weight: Value = Value()
    dryness: Value = Value()

    # Section 3 -- File References. Pre-rendered "NAME.iam (Rev 3)".
    ga_filename: str = ""
    part_filename: str = ""

    generated_on: date = field(default_factory=date.today)
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: PASS — 5 passed

- [ ] **Step 7: Commit**

```bash
git add formed_fiber_handoff.py tests/test_formed_fiber_handoff.py
git commit -m "feat(handoff): data model and the standard dry weight rule

Standard dry weight is BDW / 0.95, not BDW * 1.05. A standard dry part
carries 5% water by mass of the finished part, so the bone dry fibre is
the other 95% -- wet basis, not the dry-basis regain the textile
convention quotes. The test names that trap explicitly so the formula
does not get 'corrected' later."
```

---

## Task 2: Engine — field labels and document rows

The labels live in the engine because the form and the PDF must agree on them exactly. Row builders live here too, so "section 2 prints eight rows in this order" is testable without opening a PDF.

**Files:**
- Modify: `formed_fiber_handoff.py`
- Modify: `tests/test_formed_fiber_handoff.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_formed_fiber_handoff.py`:

```python
# --------------------------------------------------------------- row builders

def test_blank_value_renders_an_em_dash():
    """A blank must be visibly blank, not ambiguous whitespace."""
    assert engine.render_value(engine.Value()) == engine.EM_DASH
    assert engine.render_text("") == engine.EM_DASH
    assert engine.render_text("   ") == engine.EM_DASH


def test_target_values_are_marked_and_others_are_not():
    assert engine.render_value(engine.Value("2.4", True)) == "2.4 (TARGET)"
    assert engine.render_value(engine.Value("2.4", False)) == "2.4"


def test_production_section_prints_eight_rows_in_order():
    """Material and the new Part Volume row lead, then the six measured
    fields. Volume is not on the paper form -- it was added deliberately."""
    rows = engine.production_rows(engine.HandoffData())
    assert [label for label, _ in rows] == [
        "Final Pressed Part Material",
        "Part Volume [cm³]",
        "Dry Part Thickness [mm]",
        "Wet Part Thickness [mm] – Or Transfer GAPS",
        "Wet Weight [g]",
        "Bone Dry Weight [g]",
        "Standard Dry Weight [g]",
        "Dryness [%]",
    ]


def test_machine_and_file_sections_print_their_rows():
    data = engine.HandoffData(machine="Beckwood 150T", ga_filename="CD-1.iam")
    assert engine.machine_rows(data)[0] == ("Machine – Brand and Model", "Beckwood 150T")
    assert engine.file_rows(data)[0] == ("General Assembly Filename", "CD-1.iam")
    assert len(engine.machine_rows(data)) == 3
    assert len(engine.file_rows(data)) == 2


def test_missing_fields_lists_every_blank_row_by_label():
    data = engine.HandoffData(machine="Beckwood 150T")
    missing = engine.missing_fields(data)
    assert "Machine – Brand and Model" not in missing
    assert "Part Volume [cm³]" in missing
    assert "Dryness [%]" in missing
    # 3 machine + 8 production + 2 file = 13 rows, one of which is filled.
    assert len(missing) == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: FAIL — `AttributeError: module 'formed_fiber_handoff' has no attribute 'render_value'`

- [ ] **Step 3: Write minimal implementation**

Append to `formed_fiber_handoff.py`:

```python
# ---------------------------------------------------------------------------
# Field labels
#
# Single source of truth. The form builds its rows from these and the PDF
# prints these, so the two cannot drift apart.
# ---------------------------------------------------------------------------

EM_DASH = "—"

MACHINE_FIELDS: tuple[tuple[str, str], ...] = (
    ("machine", "Machine – Brand and Model"),
    ("vacuum_pressure", "Vacuum Pressure [bar or barg]"),
    ("press_pressure", "Hot Press Pressing Pressure [bar]"),
)

MATERIAL_LABEL = "Final Pressed Part Material"
VOLUME_LABEL = "Part Volume [cm³]"

# The six values that can be marked as a target. Material and volume are not
# here: neither is a measurement with a target counterpart.
PRODUCTION_FIELDS: tuple[tuple[str, str], ...] = (
    ("dry_thickness", "Dry Part Thickness [mm]"),
    ("wet_thickness", "Wet Part Thickness [mm] – Or Transfer GAPS"),
    ("wet_weight", "Wet Weight [g]"),
    ("bone_dry_weight", "Bone Dry Weight [g]"),
    ("standard_dry_weight", "Standard Dry Weight [g]"),
    ("dryness", "Dryness [%]"),
)

FILE_FIELDS: tuple[tuple[str, str], ...] = (
    ("ga_filename", "General Assembly Filename"),
    ("part_filename", "Final Pressed Part Filename"),
)


def render_text(text: Any) -> str:
    """A plain string as it prints -- em dash when there is nothing."""
    value = str(text or "").strip()
    return value or EM_DASH


def render_value(value: Value) -> str:
    """A production value as it prints, with its target marker."""
    text = str(value.text or "").strip()
    if not text:
        return EM_DASH
    return f"{text} (TARGET)" if value.is_target else text


def machine_rows(data: HandoffData) -> list[tuple[str, str]]:
    return [(label, render_text(getattr(data, name)))
            for name, label in MACHINE_FIELDS]


def production_rows(data: HandoffData) -> list[tuple[str, str]]:
    rows = [
        (MATERIAL_LABEL, render_text(data.material)),
        (VOLUME_LABEL, render_text(data.volume)),
    ]
    rows.extend((label, render_value(getattr(data, name)))
                for name, label in PRODUCTION_FIELDS)
    return rows


def file_rows(data: HandoffData) -> list[tuple[str, str]]:
    return [(label, render_text(getattr(data, name)))
            for name, label in FILE_FIELDS]


def missing_fields(data: HandoffData) -> list[str]:
    """Labels of every row that would print as an em dash.

    The document says to complete every field, so the form warns before
    generating -- but does not block. A partly-filled handoff is sometimes
    exactly what is wanted.
    """
    rows = machine_rows(data) + production_rows(data) + file_rows(data)
    return [label for label, value in rows if value == EM_DASH]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add formed_fiber_handoff.py tests/test_formed_fiber_handoff.py
git commit -m "feat(handoff): field labels and document row builders

Labels live in the engine so the form and the PDF cannot drift apart, and
building the rows here makes 'section 2 prints eight rows in this order'
testable without opening a PDF."
```

---

## Task 3: Engine — machine library

**Files:**
- Create: `machines.json`
- Modify: `formed_fiber_handoff.py`
- Modify: `tests/test_formed_fiber_handoff.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_formed_fiber_handoff.py`:

```python
# ------------------------------------------------------------ machine library

import json  # noqa: E402
import pytest  # noqa: E402


def _write_machines(tmp_path, payload):
    path = tmp_path / "machines.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_machines_reads_every_profile(tmp_path):
    path = _write_machines(tmp_path, {"machines": [
        {"name": "Beckwood 150T", "vacuum_pressure": "-0.9 barg",
         "press_pressure": "120 bar", "characterized": True},
        {"name": "Wabash 50T", "vacuum_pressure": "-0.8 barg",
         "press_pressure": "60 bar", "characterized": False},
    ]})
    machines = engine.load_machines(path)
    assert [m.name for m in machines] == ["Beckwood 150T", "Wabash 50T"]
    assert machines[0].vacuum_pressure == "-0.9 barg"
    assert machines[0].press_pressure == "120 bar"
    assert machines[1].characterized is False


def test_load_machines_never_raises(tmp_path):
    """A broken library must not stop a handoff being written by hand."""
    missing = tmp_path / "nope.json"
    assert engine.load_machines(missing) == []

    malformed = tmp_path / "bad.json"
    malformed.write_text("{not json", encoding="utf-8")
    assert engine.load_machines(malformed) == []

    wrong_shape = _write_machines(tmp_path, {"machines": "not a list"})
    assert engine.load_machines(wrong_shape) == []


def test_load_machines_skips_unusable_rows(tmp_path):
    path = _write_machines(tmp_path, {"machines": [
        {"name": ""},               # no name -- cannot appear in a dropdown
        "not a dict",
        {"name": "Real Press"},
    ]})
    assert [m.name for m in engine.load_machines(path)] == ["Real Press"]


def test_machines_default_to_characterized(tmp_path):
    """Absent flag means characterized. Warning on a machine nobody has
    flagged either way would cry wolf on every existing entry."""
    path = _write_machines(tmp_path, {"machines": [{"name": "Press"}]})
    assert engine.load_machines(path)[0].characterized is True


def test_shipped_machines_json_is_loadable():
    """The file we ship must actually parse."""
    machines = engine.load_machines(engine.MACHINES_PATH)
    assert isinstance(machines, list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: FAIL — `AttributeError: module 'formed_fiber_handoff' has no attribute 'load_machines'`

- [ ] **Step 3: Write minimal implementation**

Append to `formed_fiber_handoff.py`:

```python
# ---------------------------------------------------------------------------
# Machine library
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Machine:
    """One characterized press.

    Pressures are strings, not numbers: the document's own unit is "bar or
    barg", so the value carries its unit rather than the code assuming one.
    """

    name: str
    vacuum_pressure: str = ""
    press_pressure: str = ""
    characterized: bool = True


def load_machines(path: Path | str = MACHINES_PATH) -> list[Machine]:
    """Every machine profile in ``machines.json``, or [] if it cannot be read.

    Never raises. A missing or malformed library must not stop a handoff
    being written -- the form degrades the machine fields to free text and
    says so in the status bar.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return []

    rows = payload.get("machines") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []

    machines: list[Machine] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        machines.append(Machine(
            name=name,
            vacuum_pressure=str(row.get("vacuum_pressure") or "").strip(),
            press_pressure=str(row.get("press_pressure") or "").strip(),
            # Absent means characterized -- warning on every unflagged entry
            # would cry wolf.
            characterized=bool(row.get("characterized", True)),
        ))
    return machines


def find_machine(machines: list[Machine], name: str) -> Machine | None:
    """The profile with this exact name, or None."""
    wanted = str(name or "").strip()
    for machine in machines:
        if machine.name == wanted:
            return machine
    return None
```

- [ ] **Step 4: Create the shipped library**

Create `machines.json`:

```json
{
  "_comment": "Characterized presses for the Formed Fiber design-to-process handoff tool. Picking a machine in the tool fills Vacuum Pressure and Hot Press Pressing Pressure from its entry here, so the two values that belong to the press rather than the part are never retyped. Reloaded on every run -- edits take effect without restarting the tool.\n\nPressures are STRINGS, not numbers, and carry their own unit: the document's field is labelled 'bar or barg', so '-0.9 barg' and '120 bar' both need to print exactly as written.\n\n'characterized': false means the press has not been characterized yet. The tool still generates the handoff, but shows a warning, because the document states that an uncharacterized machine must be characterized before the first production run. Absent means true.\n\nA machine with no 'name' is skipped -- it could not be picked from the dropdown.",

  "machines": [
    {
      "name": "EXAMPLE — replace with a real press",
      "vacuum_pressure": "-0.9 barg",
      "press_pressure": "120 bar",
      "characterized": true,
      "notes": "Not printed on the document. Delete this entry once real presses are added."
    }
  ]
}
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: PASS — 15 passed

- [ ] **Step 6: Commit**

```bash
git add machines.json formed_fiber_handoff.py tests/test_formed_fiber_handoff.py
git commit -m "feat(handoff): machine profile library

Pressures belong to the press, not the part, so picking a machine fills
both. Loading never raises -- a broken library degrades the fields to
free text rather than blocking a handoff."
```

---

## Task 4: Engine — paths, filenames and file references

**Files:**
- Modify: `formed_fiber_handoff.py`
- Modify: `tests/test_formed_fiber_handoff.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_formed_fiber_handoff.py`:

```python
# -------------------------------------------------------------------- paths

from pathlib import Path  # noqa: E402


def test_vault_folder_maps_onto_the_local_workspace():
    got = engine.vault_folder_to_local("$/DESIGNS/PRODUCTION EQUIPMENT/Mold 12",
                                       r"C:\Vault Workspace")
    assert got == Path(r"C:\Vault Workspace") / "DESIGNS" / "PRODUCTION EQUIPMENT" / "Mold 12"


def test_vault_folder_mapping_tolerates_shapes_vault_actually_returns():
    root = r"C:\WS"
    assert engine.vault_folder_to_local("$/A/B", root) == Path(root) / "A" / "B"
    assert engine.vault_folder_to_local("/A/B", root) == Path(root) / "A" / "B"
    assert engine.vault_folder_to_local("$\\A\\B", root) == Path(root) / "A" / "B"
    assert engine.vault_folder_to_local("$/", root) == Path(root)
    assert engine.vault_folder_to_local("", root) == Path(root)


def test_part_local_path_uses_the_parts_own_folder():
    """The pressed part need not live beside its assembly."""
    got = engine.part_local_path("$/DESIGNS/Parts", "CD-001660.ipt", workspace_root=r"C:\WS")
    assert got == Path(r"C:\WS") / "DESIGNS" / "Parts" / "CD-001660.ipt"


def test_handoff_filename_derives_from_the_assembly():
    assert engine.handoff_filename("CD-001659.iam") == "CD-001659-DesignToProcessHandoff.pdf"
    assert engine.handoff_filename("CD-001659") == "CD-001659-DesignToProcessHandoff.pdf"
    # Only the final extension is dropped.
    assert engine.handoff_filename("CD.1659.iam") == "CD.1659-DesignToProcessHandoff.pdf"
    assert engine.handoff_filename("") == "Handoff-DesignToProcessHandoff.pdf"


def test_resolve_output_dir_uses_the_mapped_folder_when_it_exists(tmp_path):
    (tmp_path / "A" / "B").mkdir(parents=True)
    directory, note = engine.resolve_output_dir(
        "$/A/B", workspace_root=str(tmp_path), fallback=str(tmp_path / "nope"))
    assert directory == tmp_path / "A" / "B"
    assert note == ""


def test_resolve_output_dir_falls_back_and_explains(tmp_path):
    fallback = tmp_path / "Downloads"
    fallback.mkdir()
    directory, note = engine.resolve_output_dir(
        "$/Nowhere", workspace_root=str(tmp_path), fallback=str(fallback))
    assert directory == fallback
    assert "Nowhere" in note and str(fallback) in note


def test_file_reference_carries_the_revision():
    assert engine.format_file_reference("CD-001659.iam", "3") == "CD-001659.iam (Rev 3)"
    assert engine.format_file_reference("CD-001659.iam", "") == "CD-001659.iam"
    assert engine.format_file_reference("", "3") == ""


def test_workspace_root_from_config_falls_back_to_the_default():
    assert engine.workspace_root_from_config(None) == engine.DEFAULT_WORKSPACE_ROOT
    assert engine.workspace_root_from_config({}) == engine.DEFAULT_WORKSPACE_ROOT
    assert engine.workspace_root_from_config(
        {"handoff": {"workspace_root": r"D:\WS"}}) == r"D:\WS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: FAIL — `AttributeError: module 'formed_fiber_handoff' has no attribute 'vault_folder_to_local'`

- [ ] **Step 3: Write minimal implementation**

Add near the top of `formed_fiber_handoff.py`, under `MACHINES_PATH`:

```python
# The root of the local Vault working folder. Overridable via
# config.json -> handoff.workspace_root.
DEFAULT_WORKSPACE_ROOT = r"C:\Vault Workspace"
```

Append to `formed_fiber_handoff.py`:

```python
# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def workspace_root_from_config(cfg: dict[str, Any] | None) -> str:
    """The local Vault workspace root from config.json, or the default."""
    handoff_cfg = (cfg or {}).get("handoff") or {}
    return str(handoff_cfg.get("workspace_root") or DEFAULT_WORKSPACE_ROOT)


def vault_folder_to_local(
    folder_path: Any,
    workspace_root: str | Path = DEFAULT_WORKSPACE_ROOT,
) -> Path:
    """Map a Vault folder path onto the local workspace.

    ``$/DESIGNS/Mold 12`` under ``C:\\Vault Workspace`` becomes
    ``C:\\Vault Workspace\\DESIGNS\\Mold 12``. Vault is inconsistent about the
    leading ``$`` and about separators depending on which endpoint answered,
    so both are normalised rather than assumed.
    """
    cleaned = str(folder_path or "").strip().replace("\\", "/")
    if cleaned.startswith("$"):
        cleaned = cleaned[1:]
    parts = [part for part in cleaned.split("/") if part]
    return Path(workspace_root).joinpath(*parts)


def part_local_path(
    folder_path: Any,
    file_name: Any,
    *,
    workspace_root: str | Path = DEFAULT_WORKSPACE_ROOT,
) -> Path:
    """Where a Vault file sits locally. Uses the file's OWN folder."""
    return vault_folder_to_local(folder_path, workspace_root) / str(file_name or "").strip()


def handoff_filename(ga_file_name: Any) -> str:
    """``CD-001659.iam`` -> ``CD-001659-DesignToProcessHandoff.pdf``.

    Mirrors bom_purchasing.py's ``{assembly}-PurchasingExport.xlsx``.
    """
    stem = Path(str(ga_file_name or "").strip()).stem or "Handoff"
    return f"{stem}-DesignToProcessHandoff.pdf"


def resolve_output_dir(
    folder_path: Any,
    *,
    workspace_root: str | Path = DEFAULT_WORKSPACE_ROOT,
    fallback: str | Path | None = None,
) -> tuple[Path, str]:
    """Where the PDF goes: ``(directory, note)``.

    ``note`` is "" when the assembly's own workspace folder was used, and
    explains the substitution otherwise. The folder is never created -- a
    directory invented inside the Vault workspace is a path Vault does not
    know about.
    """
    mapped = vault_folder_to_local(folder_path, workspace_root)
    if str(folder_path or "").strip() and mapped.is_dir():
        return mapped, ""

    if fallback is None:
        import bom_purchasing
        fallback = bom_purchasing.default_output_dir()
    return Path(fallback), (
        f"{mapped} is not on this machine — saving to {fallback} instead."
    )


def format_file_reference(file_name: Any, revision: Any) -> str:
    """``CD-001659.iam`` + ``3`` -> ``CD-001659.iam (Rev 3)``.

    The document asks for filenames "exactly as released, including
    revision", so the revision travels with the name rather than in its own
    column.
    """
    name = str(file_name or "").strip()
    revision_text = str(revision or "").strip()
    if not name:
        return ""
    return f"{name} (Rev {revision_text})" if revision_text else name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: PASS — 23 passed

- [ ] **Step 5: Add the config key**

Modify `config.json.example` — add a `handoff` block after the `purchasing_reference` block (before `"server"`):

```json
    "handoff": {
        "workspace_root": "C:\\Vault Workspace"
    },
```

- [ ] **Step 6: Commit**

```bash
git add formed_fiber_handoff.py tests/test_formed_fiber_handoff.py config.json.example
git commit -m "feat(handoff): output path, filename and file-reference rules

The PDF lands in the assembly's own local Vault workspace folder so one
right-click adds it to Vault. Vault REST v2 has no upload endpoint, so
this is the cheap 90% of an automatic check-in.

The mapping never creates the folder -- a directory invented inside the
workspace is a path Vault does not know about."
```

---

## Task 5: Inventor — invisible document opens

`open_document` currently opens every document visibly. A property read wants it invisible: faster, and it leaves whatever the user has open undisturbed.

**Files:**
- Modify: `scripts/inventor_automation.py:96-120`
- Create: `tests/test_formed_fiber_inventor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_formed_fiber_inventor.py`:

```python
# tests/test_formed_fiber_inventor.py
"""Inventor reader tests.

Every test here runs against a fake COM object. No test may require Inventor
or pywin32 to be installed -- the suite has to pass on a build machine.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import inventor_automation as inv


# ------------------------------------------------------------------- fakes

class FakeMassProperties:
    def __init__(self, mass, volume):
        self.Mass = mass          # Inventor reports database units: kg
        self.Volume = volume      # ... and cm³


class FakeComponentDefinition:
    def __init__(self, mass, volume):
        self.MassProperties = FakeMassProperties(mass, volume)


class FakeDoc:
    def __init__(self, mass=0.5, volume=100.0):
        self.ComponentDefinition = FakeComponentDefinition(mass, volume)
        self.FullFileName = "fake.ipt"
        self.close_calls = []

    def Close(self, skip_save):
        self.close_calls.append(skip_save)


class FakeDocuments:
    def __init__(self, doc):
        self._doc = doc
        self.open_calls = []

    def Open(self, path, visible):
        self.open_calls.append((path, visible))
        return self._doc


class FakeApp:
    def __init__(self, doc):
        self.Documents = FakeDocuments(doc)
        self.Visible = True


@pytest.fixture
def part_file(tmp_path):
    """open_document checks the path exists, so the fake needs a real file."""
    path = tmp_path / "CD-001660.ipt"
    path.write_bytes(b"not really an Inventor part")
    return path


# ------------------------------------------------------------ open_document

def test_open_document_is_visible_by_default(part_file):
    """The release workflow's behaviour must not change."""
    doc = FakeDoc()
    app = FakeApp(doc)
    with inv.open_document(app, part_file):
        pass
    assert app.Documents.open_calls[0][1] is True


def test_open_document_can_open_invisibly(part_file):
    doc = FakeDoc()
    app = FakeApp(doc)
    with inv.open_document(app, part_file, open_visible=False):
        pass
    assert app.Documents.open_calls[0][1] is False


def test_open_document_closes_without_saving_by_default(part_file):
    doc = FakeDoc()
    app = FakeApp(doc)
    with inv.open_document(app, part_file):
        pass
    # Close takes SkipSave, the inverse of save_on_close.
    assert doc.close_calls == [True]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_formed_fiber_inventor.py -v`
Expected: FAIL — `TypeError: open_document() got an unexpected keyword argument 'open_visible'` on the second test (the first and third pass already).

- [ ] **Step 3: Write minimal implementation**

Modify `scripts/inventor_automation.py`, replacing the `open_document` signature, docstring and `Documents.Open` call:

```python
@contextmanager
def open_document(
    app,
    file_path: str | Path,
    *,
    save_on_close: bool = False,
    open_visible: bool = True,
) -> Iterator:
    """Open ``file_path`` in Inventor and yield the resulting Document object.

    The document is closed automatically on exit. Pass ``save_on_close=True``
    to commit changes (caller is responsible for triggering `update`/save).

    Pass ``open_visible=False`` for a read-only property pull: the document
    loads without a window, which is faster and leaves whatever the user has
    on screen undisturbed. The default stays True so the release workflow,
    which wants to see what it is rebuilding, is unaffected.
    """
    p = Path(file_path).expanduser().resolve()
    if not p.exists():
        raise InventorAutomationError(f"File does not exist: {p}")

    logger.info("Inventor: opening %s (visible=%s)", p, open_visible)
    try:
        doc = app.Documents.Open(str(p), open_visible)
    except Exception as exc:  # noqa: BLE001
        raise InventorAutomationError(f"Documents.Open failed for {p}: {exc}") from exc

    try:
        yield doc
    finally:
        try:
            logger.info("Inventor: closing %s (save=%s)", p, save_on_close)
            doc.Close(not save_on_close)  # SkipSave = inverse of save_on_close
        except Exception as exc:  # noqa: BLE001
            logger.warning("Document.Close failed: %s", exc)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_formed_fiber_inventor.py -v`
Expected: PASS — 3 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/inventor_automation.py tests/test_formed_fiber_inventor.py
git commit -m "feat(inventor): allow opening a document invisibly

A read-only property pull does not want a window. Default stays True so
the release workflow, the only current caller, is unaffected."
```

---

## Task 6: Inventor — read mass and volume

**Files:**
- Modify: `scripts/inventor_automation.py`
- Modify: `tests/test_formed_fiber_inventor.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_formed_fiber_inventor.py`:

```python
# ------------------------------------------------- read_part_physical_properties

class FakePythoncom:
    """Records COM apartment bookkeeping so the tests can assert on it."""

    def __init__(self):
        self.init_calls = 0
        self.uninit_calls = 0

    def CoInitialize(self):
        self.init_calls += 1

    def CoUninitialize(self):
        self.uninit_calls += 1


@pytest.fixture
def fake_com(monkeypatch):
    """Patch the COM boundary. Returns (pythoncom, make_app) for assertions."""
    pythoncom = FakePythoncom()
    monkeypatch.setattr(inv, "_import_win32", lambda: (pythoncom, None))
    return pythoncom


def test_mass_converts_kilograms_to_grams(part_file, fake_com, monkeypatch):
    """Inventor's API reports database units -- kg -- regardless of what the
    document displays, so grams is an exact *1000 with no unit parsing."""
    doc = FakeDoc(mass=0.10526, volume=512.5)
    monkeypatch.setattr(inv, "get_inventor_app", lambda **_: FakeApp(doc))

    props = inv.read_part_physical_properties(part_file)

    assert props.mass_g == pytest.approx(105.26)


def test_volume_is_already_in_cubic_centimetres(part_file, fake_com, monkeypatch):
    doc = FakeDoc(mass=0.5, volume=512.5)
    monkeypatch.setattr(inv, "get_inventor_app", lambda **_: FakeApp(doc))

    props = inv.read_part_physical_properties(part_file)

    assert props.volume_cm3 == pytest.approx(512.5)


def test_the_part_is_opened_invisibly_and_not_saved(part_file, fake_com, monkeypatch):
    doc = FakeDoc()
    app = FakeApp(doc)
    monkeypatch.setattr(inv, "get_inventor_app", lambda **_: app)

    inv.read_part_physical_properties(part_file)

    assert app.Documents.open_calls[0][1] is False
    assert doc.close_calls == [True]


def test_com_is_initialised_and_released(part_file, fake_com, monkeypatch):
    """The GUI reads on a worker thread, where COM must be initialised or
    every call fails with an error pointing nowhere near the cause."""
    monkeypatch.setattr(inv, "get_inventor_app", lambda **_: FakeApp(FakeDoc()))

    inv.read_part_physical_properties(part_file)

    assert fake_com.init_calls == 1
    assert fake_com.uninit_calls == 1


def test_com_is_released_even_when_the_read_fails(part_file, fake_com, monkeypatch):
    class Exploding(FakeDoc):
        @property
        def ComponentDefinition(self):
            raise RuntimeError("no component definition")

    monkeypatch.setattr(inv, "get_inventor_app", lambda **_: FakeApp(Exploding()))

    with pytest.raises(inv.InventorAutomationError):
        inv.read_part_physical_properties(part_file)

    assert fake_com.uninit_calls == 1


def test_missing_pywin32_raises_unavailable(part_file, monkeypatch):
    def _boom():
        raise inv.InventorUnavailableError("pywin32 is not installed.")

    monkeypatch.setattr(inv, "_import_win32", _boom)

    with pytest.raises(inv.InventorUnavailableError):
        inv.read_part_physical_properties(part_file)


def test_a_missing_part_file_is_an_automation_error(tmp_path, fake_com, monkeypatch):
    monkeypatch.setattr(inv, "get_inventor_app", lambda **_: FakeApp(FakeDoc()))

    with pytest.raises(inv.InventorAutomationError):
        inv.read_part_physical_properties(tmp_path / "not-there.ipt")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_formed_fiber_inventor.py -v`
Expected: FAIL — `AttributeError: module 'inventor_automation' has no attribute 'read_part_physical_properties'`

- [ ] **Step 3: Write minimal implementation**

In `scripts/inventor_automation.py`, add `dataclass` to the imports at the top:

```python
from dataclasses import dataclass
```

Then append, before the `__all__` block:

```python
# ---------------------------------------------------------------------------
# Physical properties (mass / volume)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhysicalProperties:
    """A part's computed mass and volume, in the units the handoff prints.

    A named result rather than a bare tuple: at the call site, swapping mass
    and volume would otherwise be silent.
    """

    mass_g: float
    volume_cm3: float


def read_part_physical_properties(file_path: str | Path) -> PhysicalProperties:
    """Return the part's computed mass in grams and volume in cm³.

    Read from ``MassProperties``, not from the ``Mass`` / ``Volume``
    iProperty strings. The API reports database units -- kilograms and cubic
    centimetres -- regardless of the document's display units, so mass is an
    exact ``* 1000`` and volume needs no conversion at all. The iProperty
    strings are formatted in the document's units and would need parsing.

    Both values come from ONE document open. Opening Inventor is the slowest
    thing the handoff tool does; doing it twice for two properties of the
    same part would double it.

    COM is initialised here rather than by the caller. The handoff GUI reads
    on a worker thread, where an uninitialised apartment fails with an error
    that points nowhere near the real cause. ``scripts/release_workflow.py``
    calls from the main thread, where this is a harmless no-op.

    Raises ``InventorUnavailableError`` (no Inventor, no pywin32) or
    ``InventorAutomationError`` (open failed, not a part document, properties
    unreadable).
    """
    pythoncom, _ = _import_win32()
    pythoncom.CoInitialize()
    try:
        # Note: get_inventor_app's default visible=True is deliberate here.
        # Passing False would hide the user's already-running Inventor window.
        app = get_inventor_app()
        with open_document(app, file_path, open_visible=False) as doc:
            try:
                mass_properties = doc.ComponentDefinition.MassProperties
                mass_kg = float(mass_properties.Mass)
                volume_cm3 = float(mass_properties.Volume)
            except Exception as exc:  # noqa: BLE001
                raise InventorAutomationError(
                    f"Could not read mass properties from {file_path}. Is it a "
                    f"part (.ipt) with a material assigned? ({exc})"
                ) from exc
        return PhysicalProperties(mass_g=mass_kg * 1000.0, volume_cm3=volume_cm3)
    finally:
        pythoncom.CoUninitialize()
```

Update `__all__` in the same file:

```python
__all__ = [
    "InventorUnavailableError",
    "InventorAutomationError",
    "PhysicalProperties",
    "get_inventor_app",
    "open_document",
    "rebuild_document",
    "save_document",
    "vault_get_latest",
    "vault_check_in",
    "rebuild_and_save_assembly",
    "read_part_physical_properties",
]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_formed_fiber_inventor.py -v`
Expected: PASS — 10 passed

- [ ] **Step 5: Declare pywin32**

Modify `requirements.txt` — append:

```
pywin32>=306            # Inventor COM (mass/volume for the handoff tool, release-workflow rebuilds). Windows + Inventor only; scripts/inventor_automation.py degrades cleanly when it is absent.
```

- [ ] **Step 6: Commit**

```bash
git add scripts/inventor_automation.py tests/test_formed_fiber_inventor.py requirements.txt
git commit -m "feat(inventor): read a part's mass and volume in one open

MassProperties, not the iProperty strings: the API reports database units
(kg, cm3) regardless of the document's display units, so grams is an exact
*1000 and volume needs no conversion. No unit parsing anywhere.

CoInitialize lives in the function because the handoff GUI reads on a
worker thread -- new ground for a module whose only caller so far has been
the CLI. Also declares pywin32, which this module has required since it
was written without ever being listed."
```

---

## Task 7: Vault lookup

**Files:**
- Create: `formed_fiber_vault.py`
- Modify: `tests/test_formed_fiber_handoff.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_formed_fiber_handoff.py`:

```python
# ------------------------------------------------------------- vault lookup

import formed_fiber_vault as vault_lookup  # noqa: E402


def _props(**overrides):
    base = {
        "File Name": "CD-001659.iam", "Revision": "3", "State": "Released",
        "Material": "", "Folder Path": "$/DESIGNS/Mold 12",
        "Category Name": "Assembly - Engineering",
    }
    base.update(overrides)
    return base


async def test_load_assembly_summarises_the_assembly_and_its_children(monkeypatch):
    async def fake_fetch_file(api, vault_id, file_name):
        return {"properties": _props(), "file_version_id": "v-1"}

    async def fake_fetch_children(api, vault_id, version_id, **kwargs):
        assert version_id == "v-1"
        return [{"properties": _props(
            **{"File Name": "CD-001660.ipt", "Revision": "2",
               "Material": "Cellulose Fibre", "Folder Path": "$/DESIGNS/Parts",
               "Category Name": "Part - Engineering"})}]

    monkeypatch.setattr(vault_lookup, "fetch_file", fake_fetch_file)
    monkeypatch.setattr(vault_lookup, "fetch_cad_children", fake_fetch_children)

    result = await vault_lookup.load_assembly(object(), "1", "CD-001659.iam")

    assert result["assembly"]["file_name"] == "CD-001659.iam"
    assert result["assembly"]["revision"] == "3"
    assert result["assembly"]["folder_path"] == "$/DESIGNS/Mold 12"
    assert result["children_error"] == ""
    child = result["children"][0]
    assert child["file_name"] == "CD-001660.ipt"
    assert child["material"] == "Cellulose Fibre"
    assert child["folder_path"] == "$/DESIGNS/Parts"


async def test_load_assembly_reports_a_bom_walk_failure_without_raising(monkeypatch):
    """A failed BOM walk must still return the assembly -- the filenames are
    half the document, and they are already in hand."""
    async def fake_fetch_file(api, vault_id, file_name):
        return {"properties": _props(), "file_version_id": "v-1"}

    async def fake_fetch_children(api, vault_id, version_id, **kwargs):
        raise RuntimeError("CAD BOM walk failed: boom")

    monkeypatch.setattr(vault_lookup, "fetch_file", fake_fetch_file)
    monkeypatch.setattr(vault_lookup, "fetch_cad_children", fake_fetch_children)

    result = await vault_lookup.load_assembly(object(), "1", "CD-001659.iam")

    assert result["assembly"]["file_name"] == "CD-001659.iam"
    assert result["children"] == []
    assert "boom" in result["children_error"]


async def test_load_assembly_without_a_version_id_cannot_walk_the_bom(monkeypatch):
    async def fake_fetch_file(api, vault_id, file_name):
        return {"properties": _props(), "file_version_id": ""}

    monkeypatch.setattr(vault_lookup, "fetch_file", fake_fetch_file)

    result = await vault_lookup.load_assembly(object(), "1", "CD-001659.iam")

    assert result["children"] == []
    assert "file-version ID" in result["children_error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'formed_fiber_vault'`

- [ ] **Step 3: Write minimal implementation**

Create `formed_fiber_vault.py`:

```python
"""
Vault lookups for the Formed Fiber handoff tool.

A thin wrapper over ``scripts/check_file_properties.py``. The CAD BOM walk
and the property flattening already live there, are tested, and use the
``option[propDefIds]`` spelling that Vault's FILE endpoints require -- the
bare ``propDefIds`` that item endpoints accept returns 200 OK with the
properties silently missing. Calling the REST API directly from here would
mean rediscovering that the hard way.

This module only reshapes their output into the handful of fields the
handoff form needs.
"""
from __future__ import annotations

import os
import sys
from typing import Any

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from check_file_properties import fetch_cad_children, fetch_file  # noqa: E402


def _summarise(properties: dict[str, Any]) -> dict[str, str]:
    """The six fields the handoff cares about, as plain strings."""
    def text(key: str) -> str:
        return str(properties.get(key) or "").strip()

    return {
        "file_name": text("File Name"),
        "revision": text("Revision"),
        "state": text("State"),
        "material": text("Material"),
        "folder_path": text("Folder Path"),
        "category": text("Category Name"),
    }


async def load_assembly(api: Any, vault_id: str, file_name: str) -> dict[str, Any]:
    """The assembly's fields plus one summary row per CAD BOM child.

    Returns ``{"assembly": {...}, "children": [...], "children_error": str}``.

    A failed BOM walk is reported, not raised: the assembly's own filename
    and revision are half the document and are already in hand, so losing
    them because the child walk failed would be a poor trade.
    """
    info = await fetch_file(api, vault_id, file_name)
    assembly = _summarise(info.get("properties") or {})

    children: list[dict[str, str]] = []
    children_error = ""
    version_id = str(info.get("file_version_id") or "")

    if not version_id:
        children_error = (
            "Vault returned no file-version ID for this assembly, so its CAD "
            "BOM cannot be walked. Pick the pressed part by hand."
        )
    else:
        try:
            rows = await fetch_cad_children(api, vault_id, version_id)
        except RuntimeError as exc:
            children_error = str(exc)
        else:
            for row in rows:
                child = _summarise(row.get("properties") or {})
                if not child["file_name"]:
                    # fetch_cad_children carries the name outside properties
                    # when Vault answered without them.
                    child["file_name"] = str(row.get("file_name") or "").strip()
                if child["file_name"]:
                    children.append(child)

    return {
        "assembly": assembly,
        "children": children,
        "children_error": children_error,
    }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: PASS — 26 passed

- [ ] **Step 5: Commit**

```bash
git add formed_fiber_vault.py tests/test_formed_fiber_handoff.py
git commit -m "feat(handoff): Vault lookup for the assembly and its CAD BOM

Wraps the tested helpers in scripts/check_file_properties.py rather than
calling the REST API directly -- they already use the option[propDefIds]
spelling that Vault's file endpoints require, where the bare propDefIds
returns 200 OK with properties silently missing.

A failed BOM walk is reported, not raised: the assembly's filename and
revision are half the document and already in hand."
```

---

## Task 8: PDF renderer

**Files:**
- Create: `formed_fiber_pdf.py`
- Modify: `tests/test_formed_fiber_handoff.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_formed_fiber_handoff.py`:

```python
# ------------------------------------------------------------------- the PDF

def _filled_handoff():
    return engine.HandoffData(
        machine="Beckwood 150T",
        vacuum_pressure="-0.9 barg",
        press_pressure="120 bar",
        material="Cellulose Fibre",
        volume="512.50",
        dry_thickness=engine.Value("2.4", True),
        wet_thickness=engine.Value("6.1", False),
        wet_weight=engine.Value("410.0", False),
        bone_dry_weight=engine.Value("105.26", False),
        standard_dry_weight=engine.Value("110.80", False),
        dryness=engine.Value("", False),          # left blank on purpose
        ga_filename="CD-001659.iam (Rev 3)",
        part_filename="CD-001660.ipt (Rev 2)",
    )


def _pdf_text(path):
    from pypdf import PdfReader
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def test_render_writes_a_pdf(tmp_path):
    from formed_fiber_pdf import render_handoff_pdf
    out = tmp_path / "CD-001659-DesignToProcessHandoff.pdf"
    written = render_handoff_pdf(_filled_handoff(), out)
    assert written == out
    assert out.is_file()
    assert out.stat().st_size > 1000


def test_rendered_pdf_carries_the_three_section_headings(tmp_path):
    from formed_fiber_pdf import render_handoff_pdf
    out = tmp_path / "h.pdf"
    render_handoff_pdf(_filled_handoff(), out)
    text = _pdf_text(out)
    assert "Machine and Process Details" in text
    assert "Production Details" in text
    assert "File References" in text


def test_rendered_pdf_carries_every_entered_value(tmp_path):
    from formed_fiber_pdf import render_handoff_pdf
    out = tmp_path / "h.pdf"
    render_handoff_pdf(_filled_handoff(), out)
    text = _pdf_text(out)
    for expected in ("Beckwood 150T", "-0.9 barg", "120 bar", "Cellulose Fibre",
                     "512.50", "6.1", "410.0", "105.26", "110.80",
                     "CD-001659.iam", "CD-001660.ipt"):
        assert expected in text, f"{expected!r} missing from the PDF"


def test_rendered_pdf_marks_targets_and_only_targets(tmp_path):
    from formed_fiber_pdf import render_handoff_pdf
    out = tmp_path / "h.pdf"
    render_handoff_pdf(_filled_handoff(), out)
    text = _pdf_text(out)
    assert "2.4 (TARGET)" in text          # dry thickness is a target
    assert "6.1 (TARGET)" not in text      # wet thickness is not


def test_rendered_pdf_shows_the_new_volume_row(tmp_path):
    """Part Volume is not on the paper form -- it was added deliberately."""
    from formed_fiber_pdf import render_handoff_pdf
    out = tmp_path / "h.pdf"
    render_handoff_pdf(_filled_handoff(), out)
    assert "Part Volume" in _pdf_text(out)


def test_rendered_pdf_renders_a_blank_as_an_em_dash(tmp_path):
    from formed_fiber_pdf import render_handoff_pdf
    out = tmp_path / "h.pdf"
    render_handoff_pdf(_filled_handoff(), out)
    assert engine.EM_DASH in _pdf_text(out)


def test_render_survives_a_missing_logo(tmp_path, monkeypatch):
    """The header degrades to text, the way the GUIs already do."""
    import formed_fiber_pdf
    monkeypatch.setattr(formed_fiber_pdf, "_logo_path", lambda: tmp_path / "nope.png")
    out = tmp_path / "h.pdf"
    formed_fiber_pdf.render_handoff_pdf(_filled_handoff(), out)
    assert out.is_file()
    assert "SIMPLIFYBER" in _pdf_text(out)


def test_render_copes_with_a_completely_empty_handoff(tmp_path):
    """The form can generate before anything is picked."""
    from formed_fiber_pdf import render_handoff_pdf
    out = tmp_path / "h.pdf"
    render_handoff_pdf(engine.HandoffData(), out)
    assert out.is_file()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'formed_fiber_pdf'`

- [ ] **Step 3: Write minimal implementation**

Create `formed_fiber_pdf.py`:

```python
"""
Renders the Formed Fiber design-to-process handoff as a one-page PDF.

Purely visual: rows come in already rendered by ``formed_fiber_handoff`` --
target markers applied, blanks turned into em dashes -- so nothing in here
decides what a field says, only how it looks. Layout tweaks cannot change a
number.

Colours come from ``gui.theme``, which is the canonical palette. Note that
``bom_purchasing.py`` keeps its own copy without the leading '#' because
openpyxl demands that form; the two are kept in sync by hand.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from formed_fiber_handoff import (
    HandoffData, file_rows, machine_rows, production_rows,
)
from gui.theme import (
    DARK_BLUE, GRAY_BDR, DARK_GRAY, PALE_BLUE, PROJECT_ROOT,
)

BRAND_BLUE = colors.HexColor(DARK_BLUE)
BAND_BLUE = colors.HexColor(PALE_BLUE)
GRID_GRAY = colors.HexColor(GRAY_BDR)
NOTE_GRAY = colors.HexColor(DARK_GRAY)

PAGE_MARGIN = 0.62 * inch
CONTENT_WIDTH = letter[0] - (2 * PAGE_MARGIN)

INTRO = (
    "This document transfers the design data for a formed fiber part to the "
    "process team responsible for running the mold, so the parameters "
    "established during development carry forward to the press without loss. "
    "Complete every field at handoff, and mark any value that is a target "
    "rather than a measured result."
)

SECTIONS = (
    (
        "1. Machine and Process Details",
        "Identify the press the mold will run on. If the machine has not "
        "already been characterized, it must be characterized before the "
        "first production run.",
        machine_rows,
    ),
    (
        "2. Production Details",
        "Record the values established during development. For a new part, "
        "record the target values the process is to be set up against.",
        production_rows,
    ),
    (
        "3. File References",
        "Record filenames exactly as released, including revision, so the "
        "process team can pull the correct geometry.",
        file_rows,
    ),
)

CONFIDENTIALITY = (
    "The information contained in these documents is confidential, privileged "
    "and intended solely for the information of the intended recipient and may "
    "not be used, published or redistributed without the prior written consent "
    "of Simplifyber, Inc."
)


def _logo_path() -> Path:
    """Where the brand logo lives. A function so tests can point it away."""
    return PROJECT_ROOT / "Simplifyber_Logo.png"


# ----------------------------------------------------------------- styles

_TITLE = ParagraphStyle(
    "handoffTitle", fontName="Helvetica", fontSize=16, leading=20,
    textColor=BRAND_BLUE, spaceAfter=8,
)
_INTRO = ParagraphStyle(
    "handoffIntro", fontName="Helvetica", fontSize=9.5, leading=13.5,
    textColor=colors.black, spaceAfter=10,
)
_HEADING = ParagraphStyle(
    "handoffHeading", fontName="Helvetica-Bold", fontSize=12, leading=15,
    textColor=BRAND_BLUE, spaceBefore=6, spaceAfter=2,
)
_LEAD_IN = ParagraphStyle(
    "handoffLeadIn", fontName="Helvetica-Oblique", fontSize=8.5, leading=11.5,
    textColor=NOTE_GRAY, spaceBefore=4, spaceAfter=6,
)
_FOOTER = ParagraphStyle(
    "handoffFooter", fontName="Helvetica-Oblique", fontSize=6.5, leading=8.5,
    textColor=colors.black,
)


def _rule(width: float, thickness: float = 1.2, color=BRAND_BLUE) -> Table:
    """A horizontal rule, drawn as a one-cell table so it flows."""
    line = Table([[""]], colWidths=[width], rowHeights=[thickness])
    line.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return line


def _parameter_table(rows: list[tuple[str, str]]) -> Table:
    """The PARAMETER / VALUE table used by all three sections."""
    body = [["PARAMETER", "VALUE"]] + [[name, value] for name, value in rows]
    table = Table(body, colWidths=[CONTENT_WIDTH * 0.45, CONTENT_WIDTH * 0.55],
                  hAlign="LEFT")

    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("TEXTCOLOR", (0, 1), (0, -1), colors.black),
        ("GRID", (0, 0), (-1, -1), 0.5, GRID_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    # Band every other data row, matching the source document.
    for index in range(1, len(body)):
        if index % 2 == 0:
            style.append(("BACKGROUND", (0, index), (-1, index), BAND_BLUE))
    table.setStyle(TableStyle(style))
    return table


def _page_furniture(canvas, doc, data: HandoffData) -> None:
    """Header and footer -- page furniture, so drawn rather than flowed."""
    canvas.saveState()
    width, height = letter
    top = height - PAGE_MARGIN

    logo = _logo_path()
    drew_logo = False
    if logo.is_file():
        try:
            canvas.drawImage(str(logo), PAGE_MARGIN, top - 24,
                             width=138, height=26,
                             preserveAspectRatio=True, anchor="sw", mask="auto")
            drew_logo = True
        except Exception:  # noqa: BLE001
            drew_logo = False
    if not drew_logo:
        # Same degradation the GUIs use when Pillow or the asset is absent.
        canvas.setFont("Helvetica-Bold", 13)
        canvas.setFillColor(BRAND_BLUE)
        canvas.drawString(PAGE_MARGIN, top - 18, "SIMPLIFYBER")

    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(NOTE_GRAY)
    canvas.drawRightString(width - PAGE_MARGIN, top - 14, "Page 1 of 1")

    canvas.setStrokeColor(GRID_GRAY)
    canvas.setLineWidth(0.6)
    canvas.line(PAGE_MARGIN, top - 32, width - PAGE_MARGIN, top - 32)

    # Footer
    footer_top = PAGE_MARGIN + 46
    canvas.line(PAGE_MARGIN, footer_top, width - PAGE_MARGIN, footer_top)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(colors.black)
    # Built from the parts rather than strftime: the document's format is
    # 8/6/2026, and the strftime code for an unpadded number differs between
    # platforms (%-m on Linux, %#m on Windows). This works everywhere.
    stamp = data.generated_on
    canvas.drawString(PAGE_MARGIN, footer_top - 12,
                      f"Date: {stamp.month}/{stamp.day}/{stamp.year}")
    canvas.setFont("Helvetica", 8)
    canvas.drawString(PAGE_MARGIN, footer_top - 23, "CONFIDENTIAL")

    note = Paragraph(CONFIDENTIALITY, _FOOTER)
    note.wrapOn(canvas, CONTENT_WIDTH, 40)
    note.drawOn(canvas, PAGE_MARGIN, footer_top - 44)

    canvas.restoreState()


def render_handoff_pdf(data: HandoffData, output_path: str | Path) -> Path:
    """Write the handoff PDF and return the path written."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out),
        pagesize=letter,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN + 30,
        bottomMargin=PAGE_MARGIN + 52,
        title="Formed Fiber: Design-to-Process Handoff",
        author="Simplifyber",
    )

    story: list = [
        Paragraph(
            '<b>FORMED FIBER</b>: DESIGN-TO-PROCESS HANDOFF', _TITLE),
        Paragraph(INTRO, _INTRO),
    ]

    for heading, lead_in, build_rows in SECTIONS:
        story.append(KeepTogether([
            Paragraph(heading, _HEADING),
            _rule(CONTENT_WIDTH, 1.0),
            Paragraph(lead_in, _LEAD_IN),
            _parameter_table(build_rows(data)),
            Spacer(1, 12),
        ]))

    def _on_page(canvas, doc_):
        _page_furniture(canvas, doc_, data)

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return out
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_formed_fiber_handoff.py -v`
Expected: PASS — 34 passed

- [ ] **Step 5: Eyeball the output**

Run:

```bash
python -c "
import formed_fiber_handoff as e
from formed_fiber_pdf import render_handoff_pdf
d = e.HandoffData(machine='Beckwood 150T', vacuum_pressure='-0.9 barg',
                  press_pressure='120 bar', material='Cellulose Fibre',
                  volume='512.50', dry_thickness=e.Value('2.4', True),
                  wet_weight=e.Value('410.0'), bone_dry_weight=e.Value('105.26'),
                  standard_dry_weight=e.Value('110.80'),
                  ga_filename='CD-001659.iam (Rev 3)',
                  part_filename='CD-001660.ipt (Rev 2)')
print(render_handoff_pdf(d, 'sample-handoff.pdf'))
"
```

Open `sample-handoff.pdf` and compare against the source document. Adjust padding and font sizes in `formed_fiber_pdf.py` only — never the row content. Delete `sample-handoff.pdf` before committing.

- [ ] **Step 6: Commit**

```bash
rm -f sample-handoff.pdf
git add formed_fiber_pdf.py tests/test_formed_fiber_handoff.py
git commit -m "feat(handoff): render the handoff document as a PDF

Purely visual -- rows arrive already rendered by the engine, target
markers applied and blanks turned into em dashes, so a layout tweak
cannot change a number. Header degrades to text when the logo is
missing, the way the GUIs already do."
```

---

## Task 9: The form

**Files:**
- Create: `gui/formed_fiber_handoff.py`
- Create: `tests/test_formed_fiber_gui.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_formed_fiber_gui.py`:

```python
# tests/test_formed_fiber_gui.py
"""Form tests for the Formed Fiber handoff tool.

Constructed with api=None throughout: the form must open and be usable
without a Vault session, because a handoff written entirely by hand is a
legitimate thing to want.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

tk = pytest.importorskip("tkinter")


def _make_gui(**kwargs):
    from tests.tk_helpers import make_tk_root
    from gui.formed_fiber_handoff import HandoffGUI

    root = make_tk_root()
    gui = HandoffGUI(parent=root, api=None, vault_id="", cfg={}, **kwargs)
    root.update_idletasks()
    return root, gui


def test_form_opens_without_a_vault_session():
    root, gui = _make_gui()
    try:
        assert gui.win.winfo_exists()
    finally:
        root.destroy()


def test_every_document_field_has_an_entry():
    import formed_fiber_handoff as engine

    root, gui = _make_gui()
    try:
        for name, _ in engine.MACHINE_FIELDS:
            assert name in gui.vars, f"{name} has no entry"
        for name, _ in engine.PRODUCTION_FIELDS:
            assert name in gui.vars, f"{name} has no entry"
            assert name in gui.target_vars, f"{name} has no target checkbox"
        assert "material" in gui.vars
        assert "volume" in gui.vars
        # Material and volume are not measurements -- no target checkbox.
        assert "material" not in gui.target_vars
        assert "volume" not in gui.target_vars
    finally:
        root.destroy()


def test_standard_dry_weight_tracks_bone_dry_weight():
    root, gui = _make_gui()
    try:
        gui.vars["bone_dry_weight"].set("100")
        root.update_idletasks()
        assert gui.vars["standard_dry_weight"].get() == "105.26"
    finally:
        root.destroy()


def test_standard_dry_weight_stops_tracking_once_typed_in():
    root, gui = _make_gui()
    try:
        gui.vars["bone_dry_weight"].set("100")
        root.update_idletasks()
        gui.on_standard_dry_weight_edited()      # what the <Key> binding calls
        gui.vars["standard_dry_weight"].set("999.99")
        gui.vars["bone_dry_weight"].set("250")
        root.update_idletasks()
        assert gui.vars["standard_dry_weight"].get() == "999.99"
    finally:
        root.destroy()


def test_target_flag_mirrors_bone_dry_weight_while_tracking():
    """A value derived from a target is itself a target."""
    root, gui = _make_gui()
    try:
        gui.vars["bone_dry_weight"].set("100")
        gui.target_vars["bone_dry_weight"].set(True)
        root.update_idletasks()
        assert gui.target_vars["standard_dry_weight"].get() is True
    finally:
        root.destroy()


def test_collect_builds_handoff_data_with_target_markers():
    import formed_fiber_handoff as engine

    root, gui = _make_gui()
    try:
        gui.vars["machine"].set("Beckwood 150T")
        gui.vars["material"].set("Cellulose Fibre")
        gui.vars["volume"].set("512.50")
        gui.vars["dry_thickness"].set("2.4")
        gui.target_vars["dry_thickness"].set(True)
        gui.vars["wet_thickness"].set("6.1")

        data = gui.collect()

        assert data.machine == "Beckwood 150T"
        assert data.material == "Cellulose Fibre"
        assert data.volume == "512.50"
        assert data.dry_thickness == engine.Value("2.4", True)
        assert data.wet_thickness == engine.Value("6.1", False)
    finally:
        root.destroy()


def test_picking_a_machine_fills_both_pressures(tmp_path):
    import json

    library = tmp_path / "machines.json"
    library.write_text(json.dumps({"machines": [
        {"name": "Beckwood 150T", "vacuum_pressure": "-0.9 barg",
         "press_pressure": "120 bar", "characterized": True},
    ]}), encoding="utf-8")

    root, gui = _make_gui(machines_path=library)
    try:
        gui.vars["machine"].set("Beckwood 150T")
        gui.on_machine_selected()
        assert gui.vars["vacuum_pressure"].get() == "-0.9 barg"
        assert gui.vars["press_pressure"].get() == "120 bar"
        assert gui.machine_warning_var.get() == ""
    finally:
        root.destroy()


def test_uncharacterized_machine_warns_without_blocking(tmp_path):
    import json

    library = tmp_path / "machines.json"
    library.write_text(json.dumps({"machines": [
        {"name": "New Press", "characterized": False},
    ]}), encoding="utf-8")

    root, gui = _make_gui(machines_path=library)
    try:
        gui.vars["machine"].set("New Press")
        gui.on_machine_selected()
        assert "characterized" in gui.machine_warning_var.get().lower()
    finally:
        root.destroy()


def test_a_broken_machine_library_does_not_stop_the_form(tmp_path):
    library = tmp_path / "machines.json"
    library.write_text("{not json", encoding="utf-8")

    root, gui = _make_gui(machines_path=library)
    try:
        assert gui.machines == []
        assert gui.win.winfo_exists()
    finally:
        root.destroy()


def test_generate_writes_a_pdf(tmp_path, monkeypatch):
    root, gui = _make_gui()
    try:
        gui.vars["machine"].set("Beckwood 150T")
        gui.vars["material"].set("Cellulose Fibre")
        gui.out_dir_var.set(str(tmp_path))
        gui.out_name_var.set("CD-001659-DesignToProcessHandoff.pdf")
        # Skip the "some fields are blank" dialog.
        monkeypatch.setattr(gui, "confirm_blank_fields", lambda missing: True)

        written = gui.generate()

        assert written is not None
        assert written.is_file()
    finally:
        root.destroy()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_formed_fiber_gui.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gui.formed_fiber_handoff'`

- [ ] **Step 3: Write minimal implementation**

Create `gui/formed_fiber_handoff.py`:

```python
"""
GUI: build the Formed Fiber design-to-process handoff document.

Pick the general assembly from Vault, click the final pressed part in its CAD
BOM, choose the press, and type the four values nobody can look up. Material
and the filenames come from Vault; mass and volume come from the Inventor
model; both pressures come from the machine library.

Vault and Inventor work runs on a worker thread so the window stays
responsive, and results come back through a queue drained on the Tk thread.
No Tk call happens off the main thread.

The form works with no Vault session at all -- every pulled field stays
editable -- because a handoff written by hand is a legitimate thing to want.
"""
from __future__ import annotations

import asyncio
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from gui.theme import (  # noqa: E402
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY,
    WHITE, RUST_ORANGE, WARN_AMBER,
)

import formed_fiber_handoff as engine  # noqa: E402
import formed_fiber_vault as vault_lookup  # noqa: E402
from formed_fiber_pdf import render_handoff_pdf  # noqa: E402


def _card(parent, title: str):
    """A bordered panel with the brand's dark-blue caption bar. Returns body."""
    card = tk.Frame(parent, bg=WHITE, highlightthickness=1,
                    highlightbackground=GRAY_BDR)
    card.pack(fill="x", padx=16, pady=(0, 10))
    tk.Label(card, text=f"  {title}", bg=DARK_BLUE, fg=WHITE,
             font=("Arial", 10, "bold"), anchor="w", padx=10, pady=6).pack(fill="x")
    tk.Frame(card, bg=MID_BLUE, height=2).pack(fill="x")
    body = tk.Frame(card, bg=WHITE, padx=12, pady=10)
    body.pack(fill="both", expand=True)
    return body


class HandoffGUI:
    """The handoff form.

    Also satisfies ``FileSearchDialog``'s duck-typed contract -- ``root``,
    ``api``, ``vault_id``, ``top_file_var``, ``set_top_file``,
    ``_brand_button``, ``_ensure_signed_in`` -- so the wizard's file picker
    can be reused without modifying it. ``gui/search_dialog.py`` is NOT the
    one to use here: it is item-based and returns a part number, and both
    modules carry explicit "do not merge them" notes.
    """

    BOM_COLUMNS = [
        ("file_name", "File Name", 210),
        ("revision", "Rev", 45),
        ("state", "State", 110),
        ("material", "Material", 190),
    ]

    def __init__(
        self,
        *,
        parent=None,
        api: Any = None,
        vault_id: str = "",
        cfg: Optional[dict] = None,
        machines_path: Path | str = engine.MACHINES_PATH,
    ) -> None:
        self.api = api
        self.vault_id = vault_id or ""
        self.cfg = cfg or {}
        self.workspace_root = engine.workspace_root_from_config(self.cfg)
        self.machines = engine.load_machines(machines_path)

        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.assembly: dict[str, str] = {}
        self.part: dict[str, str] = {}
        self.children: list[dict[str, str]] = []
        self.busy = False
        self._sdw_tracking = True
        self._sdw_updating = False

        self.win = tk.Toplevel(parent) if parent is not None else tk.Tk()
        # FileSearchDialog reaches for parent.root, so expose the window there.
        self.root = self.win
        if parent is not None:
            self.win.transient(parent)
        self.win.title("Simplifyber — Formed Fiber Design-to-Process Handoff")
        self.win.geometry("880x900")
        self.win.minsize(760, 700)
        self.win.configure(bg=LIGHT_GRAY)

        self.vars: dict[str, tk.StringVar] = {}
        self.target_vars: dict[str, tk.BooleanVar] = {}
        self.top_file_var = tk.StringVar()          # FileSearchDialog contract
        self.status_var = tk.StringVar(value="Ready. Find the general assembly to start.")
        self.ga_detail_var = tk.StringVar(value="")
        self.part_detail_var = tk.StringVar(value="")
        self.state_warning_var = tk.StringVar(value="")
        self.machine_warning_var = tk.StringVar(value="")
        self.inventor_note_var = tk.StringVar(value="")
        self.out_dir_var = tk.StringVar(value="")
        self.out_name_var = tk.StringVar(value="")

        self._build_ui()
        self._wire_derived_fields()
        if not self.machines:
            self.status_var.set(
                "machines.json could not be read — the machine fields are free "
                "text for this session."
            )
        self.win.after(100, self._drain_queue)

    # ----- FileSearchDialog contract ---------------------------------------

    def _ensure_signed_in(self) -> bool:
        """Called from worker threads. The launcher hands us its session."""
        return bool(self.api is not None and self.vault_id)

    def _brand_button(self, parent, text, command, *, primary: bool) -> tk.Button:
        bg, fg = (DARK_BLUE, WHITE) if primary else (MID_BLUE, WHITE)
        active_bg = MID_BLUE if primary else DARK_BLUE
        return tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, activebackground=active_bg, activeforeground=WHITE,
            font=("Arial", 10, "bold" if primary else "normal"),
            relief="flat", bd=0, padx=12, pady=5, cursor="hand2",
        )

    def set_top_file(self, name: str) -> None:
        """FileSearchDialog hands the picked assembly here."""
        self.top_file_var.set(name)
        self._load_assembly(name)

    # ----- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        header = tk.Frame(self.win, bg=DARK_BLUE, height=46)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="  Formed Fiber: Design-to-Process Handoff",
                 bg=DARK_BLUE, fg=WHITE, font=("Arial", 13, "bold"),
                 anchor="w", padx=12).pack(side="left", fill="y")
        tk.Frame(self.win, bg=MID_BLUE, height=2).pack(fill="x")

        body = tk.Frame(self.win, bg=LIGHT_GRAY, pady=12)
        body.pack(fill="both", expand=True)

        self._build_assembly_card(body)
        self._build_bom_card(body)
        self._build_machine_card(body)
        self._build_production_card(body)
        self._build_output_card(body)

        bar = tk.Frame(self.win, bg=PALE_BLUE, highlightthickness=1,
                       highlightbackground=GRAY_BDR)
        bar.pack(fill="x", side="bottom")
        tk.Label(bar, textvariable=self.status_var, bg=PALE_BLUE, fg=DARK_BLUE,
                 font=("Arial", 9), anchor="w", padx=12, pady=4).pack(fill="x")

    def _build_assembly_card(self, parent) -> None:
        body = _card(parent, "GENERAL ASSEMBLY")
        row = tk.Frame(body, bg=WHITE)
        row.pack(fill="x")
        tk.Entry(row, textvariable=self.top_file_var, state="readonly",
                 font=("Consolas", 10), readonlybackground=LIGHT_GRAY,
                 relief="flat", highlightthickness=1,
                 highlightbackground=GRAY_BDR).pack(
            side="left", fill="x", expand=True, padx=(0, 8), ipady=3)
        self._brand_button(row, "  Find GA  ", self._on_find_ga,
                           primary=True).pack(side="left")
        tk.Label(body, textvariable=self.ga_detail_var, bg=WHITE, fg=DARK_GRAY,
                 font=("Arial", 9), anchor="w").pack(fill="x", pady=(6, 0))
        tk.Label(body, textvariable=self.state_warning_var, bg=WHITE,
                 fg=WARN_AMBER, font=("Arial", 9, "bold"), anchor="w",
                 wraplength=780, justify="left").pack(fill="x")

    def _build_bom_card(self, parent) -> None:
        body = _card(parent, "FINAL PRESSED PART — PICK FROM THE CAD BOM")
        columns = [c[0] for c in self.BOM_COLUMNS]
        self.bom_tree = ttk.Treeview(body, columns=columns, show="headings",
                                     height=6, selectmode="browse")
        for key, label, width in self.BOM_COLUMNS:
            self.bom_tree.heading(key, text=label)
            self.bom_tree.column(key, width=width, anchor="w")
        self.bom_tree.pack(fill="x")
        self.bom_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_pick_part())
        tk.Label(body, textvariable=self.part_detail_var, bg=WHITE, fg=DARK_GRAY,
                 font=("Arial", 9), anchor="w").pack(fill="x", pady=(6, 0))

    def _build_machine_card(self, parent) -> None:
        body = _card(parent, "1. MACHINE AND PROCESS DETAILS")
        for name, label in engine.MACHINE_FIELDS:
            row = tk.Frame(body, bg=WHITE)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg=WHITE, fg=DARK_BLUE, width=34,
                     font=("Arial", 9, "bold"), anchor="w").pack(side="left")
            var = tk.StringVar()
            self.vars[name] = var
            if name == "machine":
                widget = ttk.Combobox(row, textvariable=var, state="normal",
                                      values=[m.name for m in self.machines])
                widget.bind("<<ComboboxSelected>>",
                            lambda _e: self.on_machine_selected())
            else:
                widget = tk.Entry(row, textvariable=var, font=("Arial", 10),
                                  relief="flat", highlightthickness=1,
                                  highlightbackground=GRAY_BDR)
            widget.pack(side="left", fill="x", expand=True, ipady=2)
        tk.Label(body, textvariable=self.machine_warning_var, bg=WHITE,
                 fg=RUST_ORANGE, font=("Arial", 9, "bold"), anchor="w",
                 wraplength=780, justify="left").pack(fill="x", pady=(6, 0))

    def _build_production_card(self, parent) -> None:
        body = _card(parent, "2. PRODUCTION DETAILS")

        # Material and volume are pulled, not measured -- no target checkbox.
        for name, label in (("material", engine.MATERIAL_LABEL),
                            ("volume", engine.VOLUME_LABEL)):
            row = tk.Frame(body, bg=WHITE)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg=WHITE, fg=DARK_BLUE, width=34,
                     font=("Arial", 9, "bold"), anchor="w").pack(side="left")
            var = tk.StringVar()
            self.vars[name] = var
            tk.Entry(row, textvariable=var, font=("Arial", 10), relief="flat",
                     highlightthickness=1, highlightbackground=GRAY_BDR).pack(
                side="left", fill="x", expand=True, ipady=2)
            tk.Label(row, text="  ", bg=WHITE, width=8).pack(side="left")

        for name, label in engine.PRODUCTION_FIELDS:
            row = tk.Frame(body, bg=WHITE)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg=WHITE, fg=DARK_BLUE, width=34,
                     font=("Arial", 9, "bold"), anchor="w").pack(side="left")
            var = tk.StringVar()
            self.vars[name] = var
            entry = tk.Entry(row, textvariable=var, font=("Arial", 10),
                             relief="flat", highlightthickness=1,
                             highlightbackground=GRAY_BDR)
            entry.pack(side="left", fill="x", expand=True, ipady=2)
            if name == "standard_dry_weight":
                entry.bind("<Key>", lambda _e: self.on_standard_dry_weight_edited())
            target = tk.BooleanVar(value=False)
            self.target_vars[name] = target
            tk.Checkbutton(row, text="target", variable=target, bg=WHITE,
                           fg=DARK_GRAY, activebackground=WHITE,
                           selectcolor=PALE_BLUE, font=("Arial", 8)).pack(
                side="left", padx=(6, 0))

        tk.Label(body, textvariable=self.inventor_note_var, bg=WHITE,
                 fg=DARK_GRAY, font=("Arial", 8, "italic"), anchor="w",
                 wraplength=780, justify="left").pack(fill="x", pady=(6, 0))

    def _build_output_card(self, parent) -> None:
        body = _card(parent, "3. OUTPUT")
        row = tk.Frame(body, bg=WHITE)
        row.pack(fill="x")
        tk.Label(row, text="Folder", bg=WHITE, fg=DARK_BLUE, width=10,
                 font=("Arial", 9, "bold"), anchor="w").pack(side="left")
        tk.Entry(row, textvariable=self.out_dir_var, font=("Consolas", 9),
                 relief="flat", highlightthickness=1,
                 highlightbackground=GRAY_BDR).pack(
            side="left", fill="x", expand=True, ipady=2)

        row2 = tk.Frame(body, bg=WHITE)
        row2.pack(fill="x", pady=(4, 0))
        tk.Label(row2, text="File", bg=WHITE, fg=DARK_BLUE, width=10,
                 font=("Arial", 9, "bold"), anchor="w").pack(side="left")
        tk.Entry(row2, textvariable=self.out_name_var, font=("Consolas", 9),
                 relief="flat", highlightthickness=1,
                 highlightbackground=GRAY_BDR).pack(
            side="left", fill="x", expand=True, ipady=2)

        actions = tk.Frame(body, bg=WHITE)
        actions.pack(fill="x", pady=(10, 0))
        self._brand_button(actions, "  Generate Handoff PDF  ",
                           self._on_generate, primary=True).pack(side="left")
        self._brand_button(actions, "  Open Folder  ", self._on_open_folder,
                           primary=False).pack(side="left", padx=(8, 0))

    # ----- Derived fields ---------------------------------------------------

    def _wire_derived_fields(self) -> None:
        self.vars["bone_dry_weight"].trace_add(
            "write", lambda *_a: self._refresh_standard_dry_weight())
        self.target_vars["bone_dry_weight"].trace_add(
            "write", lambda *_a: self._refresh_standard_dry_weight())

    def _refresh_standard_dry_weight(self) -> None:
        """Recompute while the field is still tracking bone dry weight.

        A value derived from a target is itself a target, so the checkbox
        mirrors too -- until the user overrides the value, at which point both
        become independent.
        """
        if not self._sdw_tracking:
            return
        self._sdw_updating = True
        try:
            self.vars["standard_dry_weight"].set(
                engine.standard_dry_weight(self.vars["bone_dry_weight"].get()))
            self.target_vars["standard_dry_weight"].set(
                self.target_vars["bone_dry_weight"].get())
        finally:
            self._sdw_updating = False

    def on_standard_dry_weight_edited(self) -> None:
        """Typing in the field detaches it from the derivation for good."""
        if not self._sdw_updating:
            self._sdw_tracking = False

    def on_machine_selected(self) -> None:
        """Fill both pressures from the picked profile."""
        machine = engine.find_machine(self.machines, self.vars["machine"].get())
        if machine is None:
            self.machine_warning_var.set("")
            return
        self.vars["vacuum_pressure"].set(machine.vacuum_pressure)
        self.vars["press_pressure"].set(machine.press_pressure)
        if machine.characterized:
            self.machine_warning_var.set("")
        else:
            self.machine_warning_var.set(
                f"{machine.name} is not characterized. The document requires a "
                "machine to be characterized before the first production run."
            )

    # ----- Vault + Inventor -------------------------------------------------

    def _on_find_ga(self) -> None:
        if not self._ensure_signed_in():
            messagebox.showwarning(
                "Not signed in",
                "Finding an assembly needs a Vault session. Open this tool "
                "from the launcher, or click Reconnect there first.\n\n"
                "You can still fill the form in by hand.",
                parent=self.win)
            return
        from gui.release_workflow import FileSearchDialog
        FileSearchDialog(self)

    def _load_assembly(self, file_name: str) -> None:
        if self.busy or not self._ensure_signed_in():
            return
        self.busy = True
        self.status_var.set(f"Looking up {file_name} in Vault …")

        def worker() -> None:
            try:
                result = asyncio.run(
                    vault_lookup.load_assembly(self.api, self.vault_id, file_name))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("assembly_error", f"{type(exc).__name__}: {exc}"))
                return
            self.q.put(("assembly", result))

        threading.Thread(target=worker, daemon=True).start()

    def _on_pick_part(self) -> None:
        selection = self.bom_tree.selection()
        if not selection:
            return
        index = self.bom_tree.index(selection[0])
        if index >= len(self.children):
            return
        self.part = self.children[index]
        self.vars["material"].set(self.part.get("material", ""))
        self.part_detail_var.set(
            f"{self.part.get('file_name', '')} — Rev "
            f"{self.part.get('revision', '?')}, {self.part.get('state', '?')}")
        self._refresh_state_warning()
        self._read_physical_properties()

    def _read_physical_properties(self) -> None:
        """Pull mass and volume off the model, on the worker thread."""
        path = engine.part_local_path(
            self.part.get("folder_path", ""), self.part.get("file_name", ""),
            workspace_root=self.workspace_root)
        self.inventor_note_var.set("")
        self.status_var.set("Reading mass and volume from Inventor …")

        def worker() -> None:
            try:
                from inventor_automation import read_part_physical_properties
                props = read_part_physical_properties(path)
            except Exception as exc:  # noqa: BLE001
                self.q.put(("inventor_error", f"{exc}"))
                return
            self.q.put(("inventor", props))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_state_warning(self) -> None:
        """The document asks for filenames 'exactly as released'."""
        unreleased = [
            f"{row.get('file_name')} is {row.get('state') or 'in an unknown state'}"
            for row in (self.assembly, self.part)
            if row and row.get("state") and row.get("state") != "Released"
        ]
        self.state_warning_var.set(
            "Not released: " + "; ".join(unreleased) if unreleased else "")

    # ----- Queue drain ------------------------------------------------------

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self.win.after(150, self._drain_queue)

    def _handle(self, kind: str, payload: Any) -> None:
        if kind == "assembly":
            self.busy = False
            self.assembly = payload["assembly"]
            self.children = payload["children"]
            self._populate_bom(payload.get("children_error", ""))
        elif kind == "assembly_error":
            self.busy = False
            self.status_var.set(f"Vault lookup failed: {payload}")
        elif kind == "inventor":
            self.vars["bone_dry_weight"].set(f"{payload.mass_g:.2f}")
            self.vars["volume"].set(f"{payload.volume_cm3:.2f}")
            self.inventor_note_var.set(
                "Bone dry weight and volume read from the Inventor model. The "
                "mass is only the bone dry weight if the part's material "
                "density is the dried fibre density — check it."
            )
            self.status_var.set("Mass and volume read from the model.")
        elif kind == "inventor_error":
            self.inventor_note_var.set(
                f"Could not read mass and volume — type them in. ({payload})")
            self.status_var.set("Inventor read failed; the fields stay manual.")

    def _populate_bom(self, children_error: str) -> None:
        self.bom_tree.delete(*self.bom_tree.get_children())
        for child in self.children:
            self.bom_tree.insert("", "end", values=[
                child.get(key, "") for key, *_ in self.BOM_COLUMNS])

        self.ga_detail_var.set(
            f"{self.assembly.get('file_name', '')} — Rev "
            f"{self.assembly.get('revision', '?')}, "
            f"{self.assembly.get('state', '?')}")
        self._refresh_state_warning()

        directory, note = engine.resolve_output_dir(
            self.assembly.get("folder_path", ""),
            workspace_root=self.workspace_root)
        self.out_dir_var.set(str(directory))
        self.out_name_var.set(
            engine.handoff_filename(self.assembly.get("file_name", "")))

        if children_error:
            self.status_var.set(children_error)
        elif note:
            self.status_var.set(note)
        else:
            self.status_var.set(
                f"{len(self.children)} child files — pick the final pressed part.")

    # ----- Generate ---------------------------------------------------------

    def collect(self) -> engine.HandoffData:
        """The form's current contents as a HandoffData."""
        machine = engine.find_machine(self.machines, self.vars["machine"].get())
        values = {
            name: engine.Value(self.vars[name].get().strip(),
                               bool(self.target_vars[name].get()))
            for name, _ in engine.PRODUCTION_FIELDS
        }
        return engine.HandoffData(
            machine=self.vars["machine"].get().strip(),
            vacuum_pressure=self.vars["vacuum_pressure"].get().strip(),
            press_pressure=self.vars["press_pressure"].get().strip(),
            machine_characterized=(machine.characterized if machine else True),
            material=self.vars["material"].get().strip(),
            volume=self.vars["volume"].get().strip(),
            ga_filename=engine.format_file_reference(
                self.assembly.get("file_name", ""),
                self.assembly.get("revision", "")),
            part_filename=engine.format_file_reference(
                self.part.get("file_name", ""), self.part.get("revision", "")),
            **values,
        )

    def confirm_blank_fields(self, missing: list[str]) -> bool:
        """Ask before generating an incomplete document. Blocking is wrong --
        a partly-filled handoff is sometimes exactly what is wanted."""
        return messagebox.askyesno(
            "Some fields are blank",
            "These fields will print as an em dash:\n\n  "
            + "\n  ".join(missing)
            + "\n\nGenerate anyway?",
            parent=self.win)

    def generate(self) -> Optional[Path]:
        """Write the PDF. Returns the path, or None if nothing was written."""
        data = self.collect()
        missing = engine.missing_fields(data)
        if missing and not self.confirm_blank_fields(missing):
            return None

        directory = Path(self.out_dir_var.get().strip() or ".")
        name = self.out_name_var.get().strip() or engine.handoff_filename("")
        try:
            written = render_handoff_pdf(data, directory / name)
        except OSError as exc:
            messagebox.showerror(
                "Could not write the PDF",
                f"{directory / name}\n\n{exc}", parent=self.win)
            return None
        self.status_var.set(f"Wrote {written}")
        return written

    def _on_generate(self) -> None:
        written = self.generate()
        if written is not None:
            messagebox.showinfo("Handoff written", str(written), parent=self.win)

    def _on_open_folder(self) -> None:
        directory = self.out_dir_var.get().strip()
        if not directory or not os.path.isdir(directory):
            messagebox.showwarning(
                "No folder", f"{directory or '(blank)'} is not a folder.",
                parent=self.win)
            return
        try:
            os.startfile(directory)  # noqa: S606  (Windows-only, by design)
        except AttributeError:
            subprocess.Popen(["xdg-open", directory])


def launch_gui(*, api=None, vault_id: str = "", cfg=None, parent=None) -> HandoffGUI:
    """Entry point used by gui/launcher.py."""
    return HandoffGUI(parent=parent, api=api, vault_id=vault_id, cfg=cfg)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_formed_fiber_gui.py -v`
Expected: PASS — 10 passed

- [ ] **Step 5: Run the whole suite for regressions**

Run: `python -m pytest -q`
Expected: PASS — no new failures against the pre-task baseline.

- [ ] **Step 6: Commit**

```bash
git add gui/formed_fiber_handoff.py tests/test_formed_fiber_gui.py
git commit -m "feat(handoff): the form

Reuses release_workflow's FileSearchDialog by satisfying its duck-typed
contract rather than modifying it -- gui/search_dialog.py is item-based
and returns a part number, and both carry 'do not merge' notes.

Standard dry weight tracks bone dry weight, checkbox included, until the
field is typed in directly. Everything works with no Vault session: a
handoff written by hand is a legitimate thing to want."
```

---

## Task 10: Wire it into the launcher

**Files:**
- Modify: `gui/launcher.py:637-653` (tool rows), `gui/launcher.py:1025` (handlers)
- Modify: `gui/__init__.py:1-18`
- Modify: `tests/test_launcher_flags.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_launcher_flags.py`:

```python
def test_handoff_tool_is_on_the_dashboard():
    root, gui = _make_gui()
    try:
        btn = gui.tool_buttons["Formed Fiber Handoff"]
        assert str(btn["state"]) != "disabled"
    finally:
        root.destroy()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_launcher_flags.py -v`
Expected: FAIL — `KeyError: 'Formed Fiber Handoff'`

- [ ] **Step 3: Add the tool row**

In `gui/launcher.py`, insert this `_tool_row` call in `_build_tools_panel` immediately after the "BOM → Manufacturing Tasks" block and before the "Open Reports Folder" block (around line 637):

```python
        self._tool_row(
            body,
            "Formed Fiber Handoff",
            "Build the design-to-process handoff document for a formed fiber "
            "part. Pick the assembly and its pressed part; material, mass, "
            "volume, filenames and press pressures fill themselves in.",
            "Open Handoff",
            self._on_open_formed_fiber_handoff,
            primary=False,
        )
```

- [ ] **Step 4: Add the handler**

In `gui/launcher.py`, add this method immediately after `_on_open_wrike_mfg_tasks` ends and before `_on_open_logs`:

```python
    def _on_open_formed_fiber_handoff(self) -> None:
        # No Vault gate: the form works session-less, with every pulled field
        # editable, so a handoff can still be written by hand.
        try:
            from gui.formed_fiber_handoff import launch_gui as launch_handoff
        except ImportError as exc:
            messagebox.showerror(
                "Handoff tool unavailable", str(exc), parent=self.root,
            )
            return
        launch_handoff(
            api=self.api, vault_id=self.vault_id,
            cfg=self.cfg, parent=self.root,
        )
        self.status_var.set("Launching Formed Fiber Handoff…")
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_launcher_flags.py -v`
Expected: PASS — 5 passed

- [ ] **Step 6: Correct the stale package docstring**

`gui/__init__.py` claims four GUIs and lists four; there are now ten modules. Replace lines 1–18 with:

```python
"""GUI front-ends for the Vault MCP suite.

This package exposes the Tk-based desktop GUIs that ship with the project.
Each module defines a ``launch_*`` (or ``run_*``) entry point that ``app.py``
and ``gui.launcher`` call after signing in to Vault, so the GUIs share the
single authenticated session created at launch.

* ``gui.launcher``              — Vault Integration launcher dashboard
* ``gui.release_workflow``      — Release Workflow wizard (also owns FileSearchDialog)
* ``gui.purchasing``            — Purchasing-sheet generator
* ``gui.purchasing_list_sync``  — BOM → Purchased Parts SharePoint sync
* ``gui.publish_bom``           — BOM → published PDF / STEP deliverables
* ``gui.file_property_check``   — File property compliance check
* ``gui.wrike_mfg_tasks``       — BOM → Wrike manufacturing tasks
* ``gui.formed_fiber_handoff``  — Formed Fiber design-to-process handoff
* ``gui.mfg_package``           — Manufacturing package builder (item-based; off the dashboard)
* ``gui.search_dialog``         — Item search dialog used by gui.mfg_package only

The GUIs depend on root-level engine modules (``vault_rest_api``,
``mcp_server``, ``bom_purchasing``, ``mfg_package``, ``pdf_watermark``,
``formed_fiber_handoff``, ``formed_fiber_vault``, ``formed_fiber_pdf``) and on
helpers under ``scripts/`` (``check_file_properties``, ``check_item_properties``,
``inventor_automation``, ``release_workflow``). ``app.py`` adds the project root
and ``scripts/`` to ``sys.path`` before importing this package.
"""
```

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS — no new failures against the pre-task baseline.

- [ ] **Step 8: Launch it and check by hand**

Run: `python app.py --gui`

Confirm: the Formed Fiber Handoff row appears; Open Handoff opens the form; Find GA opens the Vault file search; picking an assembly populates the BOM table; picking a child fills Material and (with Inventor available and the part in the workspace) Bone Dry Weight and Part Volume; picking a machine fills both pressures; Generate writes the PDF into the assembly's workspace folder.

- [ ] **Step 9: Commit**

```bash
git add gui/launcher.py gui/__init__.py tests/test_launcher_flags.py
git commit -m "feat(handoff): add Formed Fiber Handoff to the launcher

No Vault gate on the row -- the form is usable session-less with every
pulled field editable.

Also corrects gui/__init__.py, which claimed four GUIs and listed four
while the package had eight."
```

---

## Verification

Before calling this done, run and paste the output:

```bash
python -m pytest -q
```

All three new test files must pass, and no existing test may newly fail. If `tests/test_launcher_flags.py` skips because `config.json` is absent, that is the pre-existing gitignore behaviour, not a new failure — but say so rather than reporting a clean run.

## Notes for the implementer

- **Never change `STANDARD_DRY_FIBRE_FRACTION` to `1.05`.** It looks like the textile moisture-regain convention and it is not. See the comment at the constant.
- **Never call `get_inventor_app(visible=False)`.** It sets `Visible` on the *application*, which hides the user's already-running Inventor. Open the *document* invisibly instead.
- **Do not repoint `gui/search_dialog.py` at `search_files`,** and do not merge it with `FileSearchDialog`. Both modules carry explicit notes explaining why.
- **Vault file endpoints need `option[propDefIds]`,** not the bare `propDefIds` that item endpoints take. The wrong spelling returns 200 OK with properties silently missing. This is why `formed_fiber_vault.py` wraps `check_file_properties` instead of calling the REST API.
