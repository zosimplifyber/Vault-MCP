"""
Simplifyber brand palette and shared widget helpers.

Every GUI module in this package imports its colours from here. Dark blue is
the primary (headers, primary buttons), mid blue is the accent (hover,
secondary text), pale blue is for info cards and hover wells.

This used to live in ``gui/release_workflow.py``; it was extracted so the
workflow wizard could be rewritten without breaking the eight other modules
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
    return str(PROJECT_ROOT / filename)
