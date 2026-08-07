"""
Simplifyber brand palette and brand-asset helpers, for the GUIs.

Every GUI module in this package imports its colours from here. Dark blue is
the primary (headers, primary buttons), mid blue is the accent (hover,
secondary text), pale blue is for info cards and hover wells.

The palette and ``resource_path`` themselves now live in the root-level
``branding`` module and are re-exported below. They moved because non-GUI code
needs them too -- ``formed_fiber_pdf`` renders a branded document and was
importing this module to get them, which ran the layering backwards and set up
a circular import through ``gui/__init__.py``. Importing them from here still
works and is still correct for anything inside ``gui/``.

What genuinely belongs here is the Tk-adjacent part: the optional Pillow
import the GUIs use for the logo and window icon.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from branding import (  # noqa: E402,F401  (re-exported for the GUIs)
    PROJECT_ROOT,
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY,
    WHITE, OLIVE_GREEN, RUST_ORANGE, WARN_AMBER,
    resource_path,
)

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


# Six GUIs import this name. Kept as an alias rather than renamed at every
# call site, so this extraction stays a no-op for them.
_resource_path = resource_path
