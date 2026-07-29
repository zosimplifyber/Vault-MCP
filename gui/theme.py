"""
Simplifyber brand palette and brand-asset helpers.

Every GUI module in this package imports its colours from here. Dark blue is
the primary (headers, primary buttons), mid blue is the accent (hover,
secondary text), pale blue is for info cards and hover wells.

This used to live in ``gui/release_workflow.py``; it was extracted so the
workflow wizard could be rewritten without breaking the six other GUI
modules — gui.launcher, gui.purchasing, gui.mfg_package, gui.publish_bom,
gui.file_property_check, gui.purchasing_list_sync — that import the palette
(via ``gui.release_workflow``'s re-export, see there).

The same colours are duplicated, without the leading ``#``, in
``bom_purchasing.py`` (openpyxl wants ``"1F3864"``, not ``"#1F3864"``), so
they can't share these constants directly — keep the two in sync by hand.
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
# still work without PIL — they fall back to a text-only header. PILImage /
# ImageTk are always bound (to None when PIL is absent) so callers can do
# `from gui.theme import PILImage, ImageTk` unconditionally; guard on
# `_pil_available` before actually using them.
try:
    from PIL import Image as PILImage, ImageTk  # noqa: F401
    _pil_available = True
except ImportError:
    PILImage = None
    ImageTk = None
    _pil_available = False


def _resource_path(filename: str) -> str:
    """Return the absolute path to a bundled brand asset (logo, icon)."""
    # NOTE: not frozen-aware (no sys.frozen / sys._MEIPASS check), unlike the
    # equivalent helpers in bom_purchasing.py:435, purchasing_reference.py:92,
    # supplier_pricing/config.py:15, vault_state.py:53. Inherited verbatim
    # from the pre-extraction code; nothing here is packaged today, but this
    # is now the canonical asset-path resolver for six GUIs, so don't
    # rediscover this the hard way under deadline.
    return str(PROJECT_ROOT / filename)
