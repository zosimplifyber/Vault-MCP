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


def test_release_workflow_still_exposes_the_pil_names():
    """_build_header and _set_window_icon reference these; without them the
    logo and window icon fail inside a bare except and vanish silently."""
    from gui import release_workflow
    for name in ("PILImage", "ImageTk", "_pil_available"):
        assert hasattr(release_workflow, name), f"release_workflow lost {name}"
