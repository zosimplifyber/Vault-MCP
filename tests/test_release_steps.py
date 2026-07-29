# tests/test_release_steps.py

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
