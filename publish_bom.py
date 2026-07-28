"""
BOM-driven deliverable publisher.

Given an Inventor BOM export, queue the Vault job-server jobs that publish a
PDF drawing and a STEP file for every Make part, plus the top-level assembly.

This is the upstream half of the manufacturing workflow: it makes sure the
deliverables *exist* in Vault. ``mfg_package.py`` is the downstream half — it
collects deliverables that already exist.

The job server publishes; it cannot author. A Make part with no drawing file
in Vault is reported as a gap, never created. See
``docs/superpowers/specs/2026-07-28-publish-bom-deliverables-design.md``.

This module is the engine. The GUI wrapper lives in ``gui/publish_bom.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

import bom_purchasing
from supplier_pricing.normalize import file_stem

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]

# Vault caps concurrent work anyway; this keeps a 200-row BOM from opening 200
# sockets at once. Same cap vault_state.py uses.
MAX_CONCURRENCY = 8

MODEL_EXTS = ("ipt", "iam")
DRAWING_EXTS = ("idw", "dwg")

# BOM Structure values that are not manufactured in house. Everything else —
# Normal, Phantom, Inseparable, and anything unrecognized — is treated as Make,
# because an unexpected deliverable is a visible row in the scan table whereas
# a silently dropped part is not.
NON_MAKE_STRUCTURES = frozenset({"purchased", "reference"})

# Spellings an Inventor export might use for the BOM Structure column.
STRUCTURE_HEADERS = ("bom structure", "bomstructure", "structure")

STATUS_BOTH = "2 jobs"
STATUS_MODEL_ONLY = "STEP only - no drawing"
STATUS_DRAWING_ONLY = "PDF only - no model"
STATUS_MISSING = "not in Vault"
STATUS_FAILED = "lookup failed"

MISSING_FILENAME_ERROR = (
    "This BOM has no file-name column, so there is no way to tell which CAD "
    "file each row refers to. Re-export the BOM from Inventor with the "
    "'Filename' column included."
)


@dataclass
class PublishRow:
    """One unique CAD file stem to publish deliverables for.

    ``stem`` is the identity — it names the CAD file. The BOM's part number is
    deliberately not carried: these exports derive Number from the filename
    stem, so it would be the same string twice.
    """
    stem: str
    description: str = ""
    is_top: bool = False


@dataclass
class ScanRow:
    """A PublishRow after Vault lookup, carrying whatever files were found."""
    stem: str
    description: str = ""
    is_top: bool = False
    model_name: str = ""
    model_version_id: str = ""
    drawing_name: str = ""
    drawing_version_id: str = ""
    status: str = ""

    @property
    def job_count(self) -> int:
        return bool(self.model_version_id) + bool(self.drawing_version_id)


# ---------------------------------------------------------------------------
# Stage 1: BOM -> publish rows
# ---------------------------------------------------------------------------

def _norm(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:            # NaN
            return ""
    except Exception:                 # noqa: BLE001
        pass
    return str(value).strip()


def _find_column(columns, candidates) -> Optional[str]:
    """First column whose lower-cased, stripped name is in ``candidates``."""
    lower = {str(c).strip().lower(): c for c in columns}
    for name in candidates:
        if name in lower:
            return lower[name]
    return None


def load_publish_rows(
    bom_file_path: str,
) -> tuple[list[PublishRow], Optional[str]]:
    """Parse a BOM export into the unique CAD file stems to publish.

    Returns ``(rows, error)``. ``error`` is None on success; on failure it is
    a message meant to be shown to the user verbatim, and ``rows`` is empty.

    Manufactured rows are kept: everything except ``Purchased`` and
    ``Reference``. The raw ``BOM Structure`` column is read *before*
    ``coerce_bom_dataframe`` runs, because that function maps ``Reference``
    onto ``Make`` and the distinction cannot be recovered afterwards.
    """
    try:
        raw = bom_purchasing.read_bom_file(bom_file_path)
    except ValueError as exc:
        return [], str(exc)
    except Exception as exc:  # noqa: BLE001 — unreadable file, bad sheet, etc.
        name = os.path.basename(bom_file_path)
        return [], f"Could not read {name}: {exc}"

    structure_col = _find_column(raw.columns, STRUCTURE_HEADERS)
    structures = (
        [_norm(v).lower() for v in raw[structure_col]]
        if structure_col is not None else None
    )

    df, error = bom_purchasing.coerce_bom_dataframe(raw)
    if error:
        return [], error

    file_col = _find_column(df.columns, bom_purchasing.FILE_NAME_HEADERS)
    if file_col is None:
        return [], MISSING_FILENAME_ERROR

    rows: list[PublishRow] = []
    seen: set[str] = set()

    # coerce_bom_dataframe renames and reindexes but never drops rows, so
    # positions still line up with the structures list read off the raw frame.
    for pos, (_idx, rec) in enumerate(df.iterrows()):
        if structures is not None:
            if structures[pos] in NON_MAKE_STRUCTURES:
                continue
        elif _norm(rec.get("Source")).lower() != "make":
            continue

        stem = file_stem(rec.get(file_col))
        if not stem:
            # A Make row with no file name cannot be published. Log it rather
            # than dropping it silently — a blank Filename cell is a BOM
            # problem worth noticing.
            logger.info(
                "Skipping BOM row %s: no file name", _norm(rec.get("Row Order"))
            )
            continue
        key = stem.lower()
        if key in seen:
            continue
        seen.add(key)

        rows.append(PublishRow(
            stem=stem,
            description=_norm(rec.get("Description (Item,CO)")),
        ))

    return rows, None
