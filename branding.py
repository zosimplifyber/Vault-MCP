"""
Simplifyber brand palette and asset paths. No toolkit, no dependencies.

Lives at the repo root rather than inside ``gui/`` because non-GUI code needs
it too: ``formed_fiber_pdf`` renders a branded document with reportlab and has
no business importing the ``gui`` package to find out what colour the headers
are. That import also ran the layering backwards -- engine modules are below
the GUI, not above it -- and set up a circular-import trap, since
``gui.formed_fiber_handoff`` imports ``formed_fiber_pdf``, which would have
imported ``gui.theme``, which runs ``gui/__init__.py``.

``gui.theme`` re-exports everything here, so the six GUIs that already do
``from gui.theme import DARK_BLUE, _resource_path`` keep working unchanged.

The same colours are duplicated, without the leading ``#``, in
``bom_purchasing.py`` (openpyxl wants ``"1F3864"``, not ``"#1F3864"``), so
they can't share these constants directly -- keep the two in sync by hand.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

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


def resource_path(filename: str) -> str:
    """Return the absolute path to a bundled brand asset (logo, icon).

    NOTE: not frozen-aware (no sys.frozen / sys._MEIPASS check), unlike the
    equivalent helpers in bom_purchasing.py:435, purchasing_reference.py:92,
    supplier_pricing/config.py:15, vault_state.py:53. Inherited verbatim from
    the pre-extraction code; nothing here is packaged today, but this is the
    canonical asset-path resolver for every GUI and for the handoff PDF, so
    don't rediscover this the hard way under deadline.
    """
    return str(PROJECT_ROOT / filename)
