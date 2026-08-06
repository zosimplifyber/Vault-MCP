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
