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
    # "nan", "inf" and "Infinity" are the interesting ones: float() accepts
    # them all, and a positivity test alone lets them through -- NaN compares
    # false against everything and +inf is not <= 0 -- so they would print as
    # a weight of "nan" or "inf" on the document.
    for bad in ("", "   ", "abc", "0", "-5", None,
                "nan", "inf", "Infinity", "-inf"):
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


def test_machine_and_file_labels_are_pinned():
    """Sections 1 and 3 pin their literal text the way section 2 does.

    These strings print on a customer-facing document, and the whole point of
    building rows in the engine is that the wording is provable without
    opening a PDF. Asserting only row [0] and a count would let a typo in the
    other labels ship silently.
    """
    data = engine.HandoffData()
    assert [label for label, _ in engine.machine_rows(data)] == [
        "Machine – Brand and Model",
        "Vacuum Pressure [bar or barg]",
        "Hot Press Pressing Pressure [bar]",
    ]
    assert [label for label, _ in engine.file_rows(data)] == [
        "General Assembly Filename",
        "Final Pressed Part Filename",
    ]


def test_render_text_does_not_treat_a_legitimate_zero_as_blank():
    """The `str(text or "")` shorthand would render 0 as an em dash."""
    assert engine.render_text("0") == "0"
    assert engine.render_text(0) == "0"
    assert engine.render_text(None) == engine.EM_DASH


def test_missing_fields_lists_every_blank_row_by_label():
    data = engine.HandoffData(machine="Beckwood 150T")
    missing = engine.missing_fields(data)
    assert "Machine – Brand and Model" not in missing
    assert "Part Volume [cm³]" in missing
    assert "Dryness [%]" in missing
    # 3 machine + 8 production + 2 file = 13 rows, one of which is filled.
    assert len(missing) == 12
