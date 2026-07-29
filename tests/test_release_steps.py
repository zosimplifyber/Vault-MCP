# tests/test_release_steps.py
import release_steps

# Exact hex values, not just "starts with #" — a mistyped hex during a pure
# copy/paste move is the single most likely defect here, and the palette is
# the one thing this file most needs to catch.
PALETTE_HEX = {
    "DARK_BLUE":   "#1F3864",
    "MID_BLUE":    "#2E75B6",
    "PALE_BLUE":   "#EAF3FB",
    "LIGHT_GRAY":  "#F2F2F2",
    "GRAY_BDR":    "#CCCCCC",
    "DARK_GRAY":   "#888888",
    "WHITE":       "#FFFFFF",
    "OLIVE_GREEN": "#D8E4BC",
    "RUST_ORANGE": "#C0504D",
    "WARN_AMBER":  "#B7791F",
}
PALETTE = list(PALETTE_HEX)


def test_theme_exports_the_exact_palette_hex_values():
    from gui import theme
    for name, expected in PALETTE_HEX.items():
        assert hasattr(theme, name), f"theme is missing {name}"
        assert getattr(theme, name) == expected, (
            f"theme.{name} == {getattr(theme, name)!r}, expected {expected!r}"
        )


def test_theme_exports_the_shared_helpers():
    from gui import theme
    for name in ("_resource_path", "_pil_available"):
        assert hasattr(theme, name), f"theme is missing {name}"


def test_release_workflow_still_re_exports_the_palette():
    """Six GUI modules import these from gui.release_workflow. Keep that working."""
    from gui import release_workflow, theme
    for name in PALETTE + ["_resource_path", "_pil_available"]:
        assert getattr(release_workflow, name) == getattr(theme, name)


def test_release_workflow_still_exposes_the_pil_names():
    """_build_header and _set_window_icon reference these; without them the
    logo and window icon fail inside a bare except and vanish silently."""
    from gui import release_workflow
    for name in ("PILImage", "ImageTk", "_pil_available"):
        assert hasattr(release_workflow, name), f"release_workflow lost {name}"


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
