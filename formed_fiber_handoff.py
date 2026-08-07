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
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
MACHINES_PATH = PROJECT_ROOT / "machines.json"

# The root of the local Vault working folder. Overridable via
# config.json -> handoff.workspace_root.
DEFAULT_WORKSPACE_ROOT = r"C:\Vault Workspace"

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
    # isfinite as well as > 0: float() happily accepts "nan" and "inf", and
    # neither is caught by a positivity test -- NaN compares false against
    # everything, and +inf is not <= 0. Both would otherwise format straight
    # into the document as a weight of "nan" or "inf".
    if not math.isfinite(value) or value <= 0:
        return ""
    return f"{value / STANDARD_DRY_FIBRE_FRACTION:.2f}"


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
    # The moisture content is part of the definition, not a footnote: a
    # standard dry weight is meaningless without saying what it is standard
    # AT. Carrying it in the label means it prints on the document and shows
    # on the form from this one place.
    ("standard_dry_weight", "Standard Dry Weight [g] – at 5% moisture"),
    ("dryness", "Dryness [%]"),
)

FILE_FIELDS: tuple[tuple[str, str], ...] = (
    ("ga_filename", "General Assembly Filename"),
    ("part_filename", "Final Pressed Part Filename"),
)


def render_text(text: str) -> str:
    """A plain string as it prints -- em dash when there is nothing.

    Typed ``str`` because that is what every caller has: a HandoffData field
    or a Tk StringVar's value. None is tolerated defensively, but the obvious
    ``str(text or "")`` shorthand is avoided on purpose -- it treats every
    falsy value as blank, so a legitimate 0 or False would print as an em
    dash. Same falsy-value trap ``standard_dry_weight`` guards against above.
    """
    value = "" if text is None else str(text).strip()
    return value or EM_DASH


def render_value(value: Value) -> str:
    """A production value as it prints, with its target marker."""
    text = str(value.text or "").strip()
    if not text:
        return EM_DASH
    return f"{text} (TARGET)" if value.is_target else text


def machine_rows(data: HandoffData) -> list[tuple[str, str]]:
    """Section 1 rows, in document order."""
    return [(label, render_text(getattr(data, name)))
            for name, label in MACHINE_FIELDS]


def production_rows(data: HandoffData) -> list[tuple[str, str]]:
    """Section 2 rows, in document order: material, volume, then the six
    markable values."""
    rows = [
        (MATERIAL_LABEL, render_text(data.material)),
        (VOLUME_LABEL, render_text(data.volume)),
    ]
    rows.extend((label, render_value(getattr(data, name)))
                for name, label in PRODUCTION_FIELDS)
    return rows


def file_rows(data: HandoffData) -> list[tuple[str, str]]:
    """Section 3 rows, in document order."""
    return [(label, render_text(getattr(data, name)))
            for name, label in FILE_FIELDS]


def missing_fields(data: HandoffData) -> list[str]:
    """Labels of every row that would print as an em dash.

    The document says to complete every field, so the form warns before
    generating -- but does not block. A partly-filled handoff is sometimes
    exactly what is wanted.

    Blankness is detected by rendering and comparing to EM_DASH rather than
    by a separate emptiness test, so this can never disagree with what
    actually prints. The trade is that a field holding a literal em dash as
    real content would read as blank -- not a value any of these fields
    (numbers, filenames, material names) plausibly takes.
    """
    rows = machine_rows(data) + production_rows(data) + file_rows(data)
    return [label for label, value in rows if value == EM_DASH]


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


# Text a hand editor might reasonably write for "no". machines.json is
# maintained in a text editor with no schema check, so `"characterized":
# "false"` -- quoted, an easy JSON slip -- is a realistic entry. bool("false")
# is True, which would silently suppress the uncharacterized-press warning:
# a safety notice disappearing because of a pair of quotes. Compare the text
# instead of coercing it.
_NOT_CHARACTERIZED_TEXT = {"false", "no", "0", "off", "n"}


def _read_characterized(raw: Any) -> bool:
    """The characterized flag from one hand-edited row. Absent means True."""
    if isinstance(raw, str):
        return raw.strip().lower() not in _NOT_CHARACTERIZED_TEXT
    # Absent means characterized -- warning on every unflagged entry would
    # cry wolf across a library nobody has annotated yet.
    return True if raw is None else bool(raw)


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
            characterized=_read_characterized(row.get("characterized")),
        ))
    return machines


def find_machine(machines: list[Machine], name: str) -> Machine | None:
    """The profile with this exact name, or None."""
    wanted = str(name or "").strip()
    for machine in machines:
        if machine.name == wanted:
            return machine
    return None


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
    has_folder = bool(str(folder_path or "").strip())
    mapped = vault_folder_to_local(folder_path, workspace_root)
    if has_folder and mapped.is_dir():
        return mapped, ""

    if fallback is None:
        # Lazy: bom_purchasing pulls in pandas and openpyxl, which this
        # dependency-light engine should not import unless the fallback is
        # actually taken. Same pattern as vault_state.py's lazy imports.
        import bom_purchasing
        fallback = bom_purchasing.default_output_dir()

    # Two different reasons land here and the status bar shows this verbatim,
    # so say which one it was. Claiming a folder "is not on this machine" when
    # no folder was ever supplied -- the no-Vault-session path -- sends the
    # reader looking for a missing directory that was never named.
    reason = (
        f"{mapped} is not on this machine"
        if has_folder else
        "No Vault folder for this assembly"
    )
    return Path(fallback), f"{reason} — saving to {fallback} instead."


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
