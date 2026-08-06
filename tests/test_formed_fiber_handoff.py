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
