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


# A CAD or item number: two-or-more letters, a hyphen, four-or-more digits.
# Matches CD-001608 and SF-001922 but not "M6" or "4762".
_TOP_STEM_RE = re.compile(r"[A-Za-z]{2,}-\d{4,}")


def top_assembly_stem(bom_file_path: str) -> str:
    """Pull the top-level part number out of a BOM file name.

    ``"CD-001608 BOM.xlsx"`` -> ``"CD-001608"``. Returns "" when the name
    carries no recognizable number, in which case the GUI leaves the top
    assembly field blank and no top-level jobs are queued.
    """
    if not bom_file_path:
        return ""
    base = os.path.splitext(os.path.basename(bom_file_path))[0]
    match = _TOP_STEM_RE.search(base)
    return match.group(0) if match else ""


# ---------------------------------------------------------------------------
# Stage 2: scan Vault for each stem's model and drawing
# ---------------------------------------------------------------------------

def _search_results(data: Any) -> list[dict[str, Any]]:
    """Pull the hit list out of a search-results response body."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("results", "items", "data", "value"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []


def _error_text(resp: dict[str, Any]) -> str:
    data = resp.get("data")
    if isinstance(data, dict):
        for key in ("message", "detail", "error"):
            v = data.get(key)
            if isinstance(v, str) and v:
                return v
    return f"HTTP {resp.get('status_code', '?')}"


def _status_for(row: ScanRow) -> str:
    if row.model_version_id and row.drawing_version_id:
        return STATUS_BOTH
    if row.model_version_id:
        return STATUS_MODEL_ONLY
    if row.drawing_version_id:
        return STATUS_DRAWING_ONLY
    return STATUS_MISSING


async def _scan_one(api, vault_id: str, row: PublishRow,
                    progress: ProgressFn) -> ScanRow:
    out = ScanRow(stem=row.stem, description=row.description,
                  is_top=row.is_top)

    resp = await api.search_files(
        vault_id=vault_id, query=row.stem,
        search_sub_folders=True, latest_only=True, limit=20,
    )
    if resp.get("error"):
        out.status = STATUS_FAILED
        progress(f"  {row.stem}: lookup failed - {_error_text(resp)}")
        return out

    for rec in _search_results(resp.get("data")):
        if rec.get("entityType") != "FileVersion":
            continue
        name = _norm(rec.get("name"))
        # Require the basename to EQUAL the stem. A loose containment check
        # pulls in every assembly that references this part.
        if file_stem(name).lower() != row.stem.lower():
            continue
        fvid = _norm(rec.get("id"))
        if not fvid:
            continue
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in MODEL_EXTS and not out.model_version_id:
            out.model_name, out.model_version_id = name, fvid
        elif ext in DRAWING_EXTS and not out.drawing_version_id:
            out.drawing_name, out.drawing_version_id = name, fvid

    out.status = _status_for(out)
    progress(f"  {row.stem}: {out.status}")
    return out


async def scan_rows(
    api,
    vault_id: str,
    rows: list[PublishRow],
    on_progress: Optional[ProgressFn] = None,
) -> list[ScanRow]:
    """Resolve every PublishRow to its model and drawing file versions.

    Runs at most ``MAX_CONCURRENCY`` searches at once. Output order matches
    input order so the GUI table is stable. A search failure degrades that one
    row to ``STATUS_FAILED``; it never aborts the scan.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def guarded(row: PublishRow) -> ScanRow:
        async with sem:
            return await _scan_one(api, vault_id, row, progress)

    return list(await asyncio.gather(*(guarded(r) for r in rows)))


# ---------------------------------------------------------------------------
# Stage 3: submit the jobs
# ---------------------------------------------------------------------------

def _queue_is_disabled(data: Any) -> bool:
    """True only when the response clearly says the queue is off."""
    if isinstance(data, bool):
        return not data
    if isinstance(data, dict):
        for key in ("value", "enabled", "isEnabled", "jobQueueEnabled"):
            v = data.get(key)
            if isinstance(v, bool):
                return not v
            if isinstance(v, str) and v.strip().lower() in ("true", "false"):
                return v.strip().lower() == "false"
    return False


def _planned_jobs(row: ScanRow) -> list[tuple[str, str, str]]:
    """(kind, file name, file version id) for each job this row implies."""
    jobs: list[tuple[str, str, str]] = []
    if row.drawing_version_id:
        jobs.append(("PDF", row.drawing_name, row.drawing_version_id))
    if row.model_version_id:
        jobs.append(("STEP", row.model_name, row.model_version_id))
    return jobs


def _job_spec(kind: str, name: str, fvid: str) -> tuple[str, dict[str, str]]:
    """JobType and Params for one job.

    Param keys are PascalCase because the job processor's constructor rejects
    the job otherwise. STEP reads both UpdatePdfOption and UpdateViewOption
    despite the names; there is no UpdateStpOption.
    """
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if kind == "PDF":
        return (
            f"Autodesk.Vault.PDF.Create.{ext}",
            {"FileVersionId": fvid, "UpdateViewOption": "False"},
        )
    return (
        f"Autodesk.Vault.STEP.Create.{ext}",
        {
            "FileVersionId": fvid,
            "UpdatePdfOption": "False",
            "UpdateViewOption": "False",
        },
    )


async def submit_jobs(
    api,
    vault_id: str,
    scan_rows_in: list[ScanRow],
    on_progress: Optional[ProgressFn] = None,
    *,
    priority: int = 10,
) -> dict[str, Any]:
    """Queue one job per resolved file. Fire and forget — nothing is polled.

    Submits serially: job submission is cheap, and serial keeps the log
    readable and the queue ordered. A failed submit is logged and counted; the
    loop continues.

    Returns ``{"submitted": int, "failed": int, "jobs": [...]}``.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)

    queue_resp = await api.get_job_queue_enabled(vault_id=vault_id)
    if not queue_resp.get("error") and _queue_is_disabled(queue_resp.get("data")):
        progress(
            "WARNING: the Vault job queue is disabled. Jobs will be queued but "
            "sit unprocessed until a Job Processor agent comes online."
        )

    submitted = 0
    failed = 0
    jobs: list[dict[str, str]] = []

    for row in scan_rows_in:
        for kind, name, fvid in _planned_jobs(row):
            job_type, params = _job_spec(kind, name, fvid)
            resp = await api.submit_job(
                vault_id=vault_id,
                job_type=job_type,
                params=params,
                description=f"{kind} Create: {name}",
                priority=priority,
            )
            if resp.get("error"):
                failed += 1
                progress(f"  {name}: {kind} submit FAILED - {_error_text(resp)}")
                continue

            data = resp.get("data")
            job_id = _norm(data.get("id")) if isinstance(data, dict) else ""
            submitted += 1
            jobs.append({"file": name, "kind": kind, "job_id": job_id})
            progress(f"  {name}: {kind} queued (job {job_id or '?'})")

    return {"submitted": submitted, "failed": failed, "jobs": jobs}
