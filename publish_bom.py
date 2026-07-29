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

# When more than one file shares a stem — an archived copy, a library
# duplicate — rank deterministically instead of trusting server response
# order. Assemblies outrank parts, Inventor drawings outrank AutoCAD ones.
# Mirrors vault_state._EXT_PRIORITY.
_EXT_RANK = {"iam": 0, "ipt": 1, "idw": 0, "dwg": 1}

# BOM Structure values that are not manufactured in house. Everything else —
# Normal, Phantom, Inseparable, and anything unrecognized — is treated as Make,
# because an unexpected deliverable is a visible row in the scan table whereas
# a silently dropped part is not.
NON_MAKE_STRUCTURES = frozenset({"purchased", "reference"})

# Spellings an Inventor export might use for the BOM Structure column.
STRUCTURE_HEADERS = ("bom structure", "bomstructure", "structure")

# Where the raw BOM Structure value is stashed so it survives coercion on its
# own row. coerce_bom_dataframe maps Reference onto Make, so the raw value has
# to be captured first — carrying it as a column instead of a positional list
# means the parse cannot silently misalign if coercion ever drops a row.
STRUCTURE_STASH_COL = "__bom_structure__"

STATUS_BOTH = "2 jobs"
STATUS_MODEL_ONLY = "STEP only - no drawing"
STATUS_DRAWING_ONLY = "PDF only - no model"
STATUS_MISSING = "not in Vault"
STATUS_FAILED = "lookup failed"
STATUS_TRUNCATED = "search truncated - refine"

# A stem's keyword search also matches its .pdf/.stp/.dwf siblings, its item,
# and anything with the stem in a property, so the hit list is much longer
# than the two files we want.
SEARCH_LIMIT = 50

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
    ambiguous: bool = False

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
    on_progress: Optional[ProgressFn] = None,
) -> tuple[list[PublishRow], Optional[str]]:
    """Parse a BOM export into the unique CAD file stems to publish.

    Returns ``(rows, error)``. ``error`` is None on success; on failure it is
    a message meant to be shown to the user verbatim, and ``rows`` is empty.

    Manufactured rows are kept: everything except ``Purchased`` and
    ``Reference``. The raw ``BOM Structure`` column is read *before*
    ``coerce_bom_dataframe`` runs, because that function maps ``Reference``
    onto ``Make`` and the distinction cannot be recovered afterwards.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)

    if not os.path.isfile(bom_file_path):
        return [], f"BOM file not found: {bom_file_path}"

    try:
        raw = bom_purchasing.read_bom_file(bom_file_path)
    except ValueError as exc:
        return [], str(exc)
    except Exception as exc:  # noqa: BLE001 — unreadable file, bad sheet, etc.
        name = os.path.basename(bom_file_path)
        return [], f"Could not read {name}: {exc}"

    structure_col = _find_column(raw.columns, STRUCTURE_HEADERS)
    if structure_col is not None:
        raw = raw.copy()
        raw[STRUCTURE_STASH_COL] = [_norm(v).lower() for v in raw[structure_col]]

    df, error = bom_purchasing.coerce_bom_dataframe(raw)
    if error:
        return [], error

    file_col = _find_column(df.columns, bom_purchasing.FILE_NAME_HEADERS)
    if file_col is None:
        return [], MISSING_FILENAME_ERROR

    rows: list[PublishRow] = []
    seen: set[str] = set()
    has_structure = STRUCTURE_STASH_COL in df.columns

    for _idx, rec in df.iterrows():
        if has_structure:
            if _norm(rec.get(STRUCTURE_STASH_COL)) in NON_MAKE_STRUCTURES:
                continue
        elif _norm(rec.get("Source")).lower() != "make":
            continue

        stem = file_stem(rec.get(file_col))
        if not stem:
            # A Make row with no file name cannot be published. Surface it in
            # the dialog's log, not just the logger — a blank Filename cell is
            # a BOM problem worth noticing, and nobody reads the log file.
            label = _norm(rec.get("Row Order")) or "(unnumbered)"
            logger.info("Skipping BOM row %s: no file name", label)
            progress(f"  BOM row {label} has no file name; skipped.")
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
        search_sub_folders=True, latest_only=True, limit=SEARCH_LIMIT,
    )
    if resp.get("error"):
        out.status = STATUS_FAILED
        progress(f"  {row.stem}: lookup failed - {_error_text(resp)}")
        return out

    hits = _search_results(resp.get("data"))
    models: list[tuple[int, str, str]] = []
    drawings: list[tuple[int, str, str]] = []

    for rec in hits:
        # Case-tolerant, but still strictly the *version* entity: we submit
        # this id as a FileVersionId, and a master id would publish the wrong
        # thing. (vault_state can be looser — it only reads state.)
        if _norm(rec.get("entityType")).lower() != "fileversion":
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
        rank = _EXT_RANK.get(ext, 9)
        if ext in MODEL_EXTS:
            models.append((rank, name, fvid))
        elif ext in DRAWING_EXTS:
            drawings.append((rank, name, fvid))

    # Sort by extension rank then name so the same vault state always yields
    # the same job.
    models.sort(key=lambda t: (t[0], t[1].lower()))
    drawings.sort(key=lambda t: (t[0], t[1].lower()))
    if models:
        _, out.model_name, out.model_version_id = models[0]
    if drawings:
        _, out.drawing_name, out.drawing_version_id = drawings[0]
    out.ambiguous = len(models) > 1 or len(drawings) > 1

    out.status = _status_for(out)
    if out.status == STATUS_MISSING and len(hits) >= SEARCH_LIMIT:
        out.status = STATUS_TRUNCATED
    if out.ambiguous:
        # Worth a human's eye during Scan — that is what the Scan step is for.
        out.status += " (multiple matches)"
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
            try:
                return await _scan_one(api, vault_id, row, progress)
            except Exception as exc:  # noqa: BLE001 — one bad row must not sink the scan
                logger.exception("Scan failed for %s", row.stem)
                progress(f"  {row.stem}: lookup failed - {exc}")
                return ScanRow(stem=row.stem, description=row.description,
                               is_top=row.is_top, status=STATUS_FAILED)

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


def _planned_jobs(
    row: ScanRow,
    *,
    include_pdf: bool = True,
    include_step: bool = True,
) -> list[tuple[str, str, str]]:
    """(kind, file name, file version id) for each job this row implies.

    The single place that decides what a row means. ``count_planned_jobs``
    calls this rather than re-deriving the rule, so a displayed count cannot
    drift from what is actually submitted.

    The flags are the user's output-type choice. They default to permissive so
    that every caller predating the selection feature behaves unchanged.
    """
    jobs: list[tuple[str, str, str]] = []
    if include_pdf and row.drawing_version_id:
        jobs.append(("PDF", row.drawing_name, row.drawing_version_id))
    if include_step and row.model_version_id:
        jobs.append(("STEP", row.model_name, row.model_version_id))
    return jobs


def count_planned_jobs(
    rows: list[ScanRow],
    *,
    include_pdf: bool = True,
    include_step: bool = True,
) -> dict[str, int]:
    """How many jobs ``submit_jobs`` would queue for ``rows``.

    Implemented by calling ``_planned_jobs`` rather than re-deriving the rule,
    so the number shown to the user cannot drift from the number submitted.
    """
    counts = {"pdf": 0, "step": 0, "total": 0}
    for row in rows:
        for kind, _name, _fvid in _planned_jobs(
            row, include_pdf=include_pdf, include_step=include_step
        ):
            counts["pdf" if kind == "PDF" else "step"] += 1
            counts["total"] += 1
    return counts


def merge_selection(
    previous: set[str],
    previous_stems: set[str],
    new_stems: set[str],
) -> set[str]:
    """Carry a user's part selection across a re-scan.

    A stem the user already saw keeps whatever state they left it in. A stem
    that is new this scan arrives selected, because a part that just appeared
    should not be silently excluded from the run. A stem that has gone is
    dropped.

    Pure set logic over stems, kept here rather than in a widget callback so
    it can be tested without Tk. A first scan has no prior intent to respect,
    so everything comes back selected.
    """
    return {s for s in new_stems if s not in previous_stems or s in previous}


def _job_spec(kind: str, name: str, fvid: str) -> Optional[tuple[str, dict[str, str]]]:
    """JobType and Params for one job, or None if the extension doesn't fit.

    Param keys are PascalCase because the job processor's constructor rejects
    the job otherwise. STEP reads both UpdatePdfOption and UpdateViewOption
    despite the names; there is no UpdateStpOption.

    ``_planned_jobs`` only ever proposes a PDF job for a drawing extension and
    a STEP job for a model extension, so a mismatch should not happen — but
    this is checked rather than assumed, because submitting a job type built
    from a bad extension (``"Autodesk.Vault.PDF.Create."``) is worse than
    skipping it.
    """
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if kind == "PDF":
        if ext not in DRAWING_EXTS:
            return None
        return (
            f"Autodesk.Vault.PDF.Create.{ext}",
            {"FileVersionId": fvid, "UpdateViewOption": "False"},
        )
    if ext not in MODEL_EXTS:
        return None
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
    include_pdf: bool = True,
    include_step: bool = True,
) -> dict[str, Any]:
    """Queue one job per resolved file. Fire and forget — nothing is polled.

    Submits serially: job submission is cheap, and serial keeps the log
    readable and the queue ordered. A failed submit is logged and counted; the
    loop continues.

    ``include_pdf`` / ``include_step`` are the user's output-type choice.
    Both default to True, which is the pre-selection behavior.

    Returns ``{"submitted": int, "failed": int, "jobs": [...]}``.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)

    # Nothing to do, so do not even run the advisory queue check — a warning
    # about a queue we are not going to use is noise.
    if not (include_pdf or include_step):
        return {"submitted": 0, "failed": 0, "jobs": []}

    try:
        queue_resp = await api.get_job_queue_enabled(vault_id=vault_id)
    except Exception as exc:  # noqa: BLE001 — advisory only, never block the work
        logger.info("Job queue check failed: %s", exc)
        queue_resp = {"error": True}
    if not queue_resp.get("error") and _queue_is_disabled(queue_resp.get("data")):
        progress(
            "WARNING: the Vault job queue is disabled. Jobs will be queued but "
            "sit unprocessed until a Job Processor agent comes online."
        )

    submitted = 0
    failed = 0
    jobs: list[dict[str, str]] = []

    for row in scan_rows_in:
        for kind, name, fvid in _planned_jobs(
            row, include_pdf=include_pdf, include_step=include_step
        ):
            spec = _job_spec(kind, name, fvid)
            if spec is None:
                logger.warning(
                    "Skipping %s job for %r: extension does not match a %s file",
                    kind, name, kind)
                progress(f"  {name}: skipped - not a valid {kind} source file")
                continue
            job_type, params = spec
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


# ---------------------------------------------------------------------------
# Top-level entry points
# ---------------------------------------------------------------------------

async def scan_bom(
    api,
    vault_id: str,
    bom_file_path: str,
    *,
    top_assembly: str = "",
    on_progress: Optional[ProgressFn] = None,
) -> tuple[list[ScanRow], Optional[str]]:
    """Parse a BOM, append the top assembly, and resolve everything in Vault.

    Returns ``(scan_rows, error)``. On a parse error the list is empty, the
    message is meant to be shown verbatim, and Vault is never called.

    ``top_assembly`` is a stem such as ``"CD-001608"``; blank skips the
    top-level row. A top assembly that already appears in the BOM is not
    duplicated.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)

    rows, error = load_publish_rows(bom_file_path, progress)
    if error:
        return [], error

    # Check for zero before announcing a count: the only way rows can still be
    # empty after the top-assembly block below is an empty BOM with no top
    # assembly given, so there is no point logging "0 Make part(s)" right
    # before bailing out with an error that says the same thing.
    top = _norm(top_assembly)
    if not rows and not top:
        return [], "No Make parts found in this BOM."

    progress(f"{len(rows)} Make part(s) in the BOM.")

    if top:
        if any(r.stem.lower() == top.lower() for r in rows):
            progress(f"Top assembly {top} is already a BOM row; not repeating it.")
        else:
            rows.append(PublishRow(stem=top, is_top=True))
            progress(f"Top assembly: {top}")

    progress("Resolving files in Vault...")
    return await scan_rows(api, vault_id, rows, progress), None


def summarize(rows: list[ScanRow]) -> dict[str, int]:
    """Counts for the GUI's summary line.

    Counts off the resolved ids and a status *prefix*, never off the whole
    status string. ``status`` is display text and carries a
    "(multiple matches)" suffix when a stem was ambiguous — comparing it by
    equality silently reported zero missing drawings for exactly the rows a
    human most needs to look at, which is the one number this tool exists to
    produce.
    """
    def _is(row: ScanRow, status: str) -> bool:
        return row.status.startswith(status)

    return {
        "rows": len(rows),
        "models": sum(1 for r in rows if r.model_version_id),
        "drawings": sum(1 for r in rows if r.drawing_version_id),
        "jobs": sum(r.job_count for r in rows),
        "missing_drawing": sum(1 for r in rows
                               if r.model_version_id and not r.drawing_version_id),
        # A confirmed miss only — a search that failed or came back truncated
        # is an unknown, not an answer, and must not read as "go draw this".
        "not_found": sum(1 for r in rows
                         if not r.model_version_id and not r.drawing_version_id
                         and not _is(r, STATUS_FAILED)
                         and not _is(r, STATUS_TRUNCATED)),
        "failed": sum(1 for r in rows if _is(r, STATUS_FAILED)),
        "truncated": sum(1 for r in rows if _is(r, STATUS_TRUNCATED)),
        "ambiguous": sum(1 for r in rows if r.ambiguous),
    }
