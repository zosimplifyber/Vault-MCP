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
    """One unique CAD file stem to publish deliverables for."""
    stem: str
    part_number: str = ""
    description: str = ""
    is_top: bool = False


@dataclass
class ScanRow:
    """A PublishRow after Vault lookup, carrying whatever files were found."""
    stem: str
    part_number: str = ""
    is_top: bool = False
    model_name: str = ""
    model_version_id: str = ""
    drawing_name: str = ""
    drawing_version_id: str = ""
    status: str = ""

    @property
    def job_count(self) -> int:
        return bool(self.model_version_id) + bool(self.drawing_version_id)
