"""
Manufacturing Order Package builder.

Given a top-level part number, walks its BOM tree, locates the published
PDF drawings and STEP files for every item, watermarks each PDF with
"RELEASED" or "FOR REVIEW" based on the source item's lifecycle state,
and drops the whole package — plus a Simplifyber-branded MFG BOM Excel
sheet — into a single clean folder (under ~/Downloads by default).

This module is the engine. The GUI wrapper lives in
``gui/mfg_package.py``.
"""

from __future__ import annotations

import datetime
import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import bom_purchasing
from pdf_watermark import apply_watermark


logger = logging.getLogger(__name__)


# Match what mcp_server._safe_filename strips so we get the same
# Windows-safe filenames here.
_INVALID_FN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Watermark colors that read well on top of typical drawing backgrounds.
_WM_RELEASED_COLOR = "#1F6B2E"   # forest green
_WM_REVIEW_COLOR   = "#C0504D"   # rust orange (matches workflow GUI accent)


def _safe(name: str) -> str:
    cleaned = _INVALID_FN.sub("_", str(name)).strip(" .")
    return cleaned or "file"


def _state_to_watermark(state: str) -> str:
    """Map a Vault lifecycle-state name onto our two watermark options."""
    s = (state or "").lower()
    if "release" in s:
        return "RELEASED"
    return "FOR REVIEW"


def default_output_dir(part_number: str) -> Path:
    """``~/Downloads/<pn>_MFG_<YYYY-MM-DD_HHMM>`` — a fresh folder per run."""
    downloads = Path.home() / "Downloads"
    if not downloads.exists():
        downloads = Path.home()
    pn = _safe(part_number) or "MFG"
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    return downloads / f"{pn}_MFG_{ts}"


# ---------------------------------------------------------------------------
# Generic Vault response helpers (mirrored from mcp_server.py — kept local so
# this module stays usable from a launcher even if mcp_server isn't imported).
# ---------------------------------------------------------------------------

def _extract_collection(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("results", "items", "itemVersions", "data", "value", "records"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
        if data.get("id") or data.get("masterId"):
            return [data]
    return []


def _extract_id(record: Optional[dict[str, Any]]) -> str:
    if not isinstance(record, dict):
        return ""
    for key in ("id", "itemVersionId", "fileVersionId", "masterId", "itemId", "fileId"):
        v = record.get(key)
        if v:
            return str(v)
    return ""


def _pick_latest_version(item: dict[str, Any]) -> tuple[str, Optional[dict[str, Any]]]:
    if not isinstance(item, dict):
        return "", None
    for key in ("latestItemVersion", "latestVersion", "latest"):
        v = item.get(key)
        if isinstance(v, dict):
            vid = _extract_id(v)
            if vid:
                return vid, v
    for key in ("latestItemVersionId", "latestVersionId"):
        v = item.get(key)
        if v:
            return str(v), None
    return "", None


def _item_part_number(rec: dict[str, Any]) -> str:
    """Pull a part-number-like value out of an item / item-version record."""
    if not isinstance(rec, dict):
        return ""
    for k in ("itemNumber", "number", "Number", "Part Number", "itemNum"):
        v = rec.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    props = rec.get("properties")
    if isinstance(props, dict):
        for k in ("Number", "Part Number", "number"):
            v = props.get(k)
            if isinstance(v, dict):
                v = v.get("value") or v.get("displayValue")
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _item_state(rec: dict[str, Any]) -> str:
    """Pull the lifecycle state name out of a Vault item / item-version record.

    Vault's REST returns the state in different shapes depending on the
    endpoint and whether ``propDefIds`` was supplied — this scans the common
    locations so the caller doesn't have to.
    """
    if not isinstance(rec, dict):
        return ""
    direct_keys = (
        "State", "Lifecycle State", "LifecycleState",
        "state", "lifecycleState", "stateName",
    )
    for k in direct_keys:
        v = rec.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            n = v.get("name") or v.get("displayName") or v.get("value")
            if isinstance(n, str) and n:
                return n

    props = rec.get("properties")
    if isinstance(props, dict):
        for k in direct_keys:
            v = props.get(k)
            if isinstance(v, dict):
                v = v.get("value") or v.get("displayValue")
            if isinstance(v, str) and v:
                return v

    lc = rec.get("lifeCycle") or rec.get("lifecycle")
    if isinstance(lc, dict):
        st = lc.get("state")
        if isinstance(st, dict):
            n = st.get("name") or st.get("displayName")
            if isinstance(n, str) and n:
                return n
        n = lc.get("stateName")
        if isinstance(n, str) and n:
            return n
    return ""


# ---------------------------------------------------------------------------
# BOM walk
# ---------------------------------------------------------------------------

ProgressFn = Callable[[str], None]


async def _resolve_top_item(api, vault_id: str, part_number: str) -> Optional[dict[str, Any]]:
    """Search for the top item by part number and return the first hit."""
    search = await api.search_items(vault_id=vault_id, query=part_number, limit=10)
    if search.get("error"):
        return None
    items = _extract_collection(search.get("data"))
    return items[0] if items else None


async def _resolve_item_version_id(
    api, vault_id: str, item: dict[str, Any]
) -> tuple[str, Optional[dict[str, Any]]]:
    """Best-effort: get the latest item-version id for a master item."""
    iv_id, iv = _pick_latest_version(item)
    if iv_id:
        return iv_id, iv
    item_id = _extract_id(item)
    if not item_id:
        return "", None
    hist = await api.get_item_version_history(
        vault_id=vault_id, item_id=item_id, limit=50
    )
    versions = _extract_collection(hist.get("data"))
    if not versions:
        return "", None
    last = versions[-1]
    return _extract_id(last), last


async def _gather_bom_tree(
    api, vault_id: str, top_item: dict[str, Any], on_progress: ProgressFn
) -> list[dict[str, Any]]:
    """BFS the BOM. Returns one row per unique item-version visited.

    Each row carries: ``part_number``, ``item_version_id``, ``state``,
    ``record`` (the underlying Vault dict).
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    iv_id, iv = await _resolve_item_version_id(api, vault_id, top_item)
    if not iv_id:
        on_progress(f"Could not resolve a version for the top item.")
        return rows

    queue: list[tuple[str, dict[str, Any]]] = [(iv_id, iv or top_item)]
    seen.add(iv_id)

    while queue:
        cur_iv_id, cur_iv = queue.pop(0)
        rows.append({
            "part_number": _item_part_number(cur_iv),
            "item_version_id": cur_iv_id,
            "state": _item_state(cur_iv),
            "record": cur_iv,
        })

        bom = await api.get_item_bom(
            vault_id=vault_id, item_version_id=cur_iv_id, limit=500
        )
        if bom.get("error"):
            on_progress(
                f"  BOM lookup failed for "
                f"{rows[-1]['part_number'] or cur_iv_id}: {bom.get('data')}"
            )
            continue
        for child in _extract_collection(bom.get("data")):
            child_iv_id = _extract_id(child)
            if child_iv_id and child_iv_id not in seen:
                seen.add(child_iv_id)
                queue.append((child_iv_id, child))

    return rows


# ---------------------------------------------------------------------------
# File discovery (PDFs + STEPs)
# ---------------------------------------------------------------------------

_PDF_EXT = "pdf"
_STEP_EXTS = {"stp", "step"}


def _classify(name: str) -> Optional[str]:
    if "." not in name:
        return None
    ext = name.rsplit(".", 1)[-1].lower()
    if ext == _PDF_EXT:
        return "pdf"
    if ext in _STEP_EXTS:
        return "step"
    return None


def _basename_matches(file_name: str, part_number: str) -> bool:
    """Check whether ``file_name`` is a deliverable for ``part_number``.

    Vault stores published PDFs / STEP files alongside the source CAD
    file with the same stem (``SF-001234.idw`` → ``SF-001234.pdf`` /
    ``.stp``). A loose ``part_number in file_name`` check would pull in
    every assembly that *uses* this part — much too greedy. So we require
    the basename (without extension) to equal the part number.
    """
    if not file_name or not part_number:
        return False
    stem = file_name.rsplit(".", 1)[0].strip().lower()
    pn = part_number.strip().lower()
    return stem == pn


async def _collect_deliverable_files(
    api, vault_id: str, rows: list[dict[str, Any]], on_progress: ProgressFn
) -> dict[str, dict[str, Any]]:
    """Walk every BOM row and gather a {file_version_id: info} map of
    PDF + STEP deliverables. ``info`` carries name, kind, lifecycle state
    (from the source item — used to pick the watermark text) and the
    part number that contributed it.

    Two passes per item:
      1. Item's own ``associated-files`` — picks up PDFs/STEPs that have
         been linked to the engineering item directly.
      2. ``search-files`` by part number — catches the common case where
         the published PDF lives next to the .idw on disk but isn't
         attached to the item.
    """
    files: dict[str, dict[str, Any]] = {}

    for row in rows:
        iv_id = row["item_version_id"]
        pn = row["part_number"]
        state = row["state"]

        # Pass 1: associated files
        af = await api.get_item_associated_files(
            vault_id=vault_id, item_version_id=iv_id, limit=200
        )
        if af.get("error"):
            on_progress(f"  associated-files failed for {pn or iv_id}: {af.get('data')}")
        else:
            for fv in _extract_collection(af.get("data")):
                name = str(fv.get("name") or "")
                kind = _classify(name)
                if not kind:
                    continue
                fv_id = _extract_id(fv)
                if not fv_id:
                    continue
                files.setdefault(fv_id, {
                    "name": name, "kind": kind, "state": state, "pn": pn,
                })

        # Pass 2: search by part number for sibling deliverables
        if pn:
            sr = await api.search_files(
                vault_id=vault_id, query=pn,
                search_sub_folders=True, latest_only=True, limit=20,
            )
            if not sr.get("error"):
                for f in _extract_collection(sr.get("data")):
                    name = str(f.get("name") or "")
                    if not _basename_matches(name, pn):
                        continue
                    kind = _classify(name)
                    if not kind:
                        continue
                    fv_id, _embedded = _pick_latest_version(f)
                    if not fv_id:
                        fv_id = _extract_id(f)
                    if not fv_id:
                        continue
                    files.setdefault(fv_id, {
                        "name": name, "kind": kind, "state": state, "pn": pn,
                    })

    return files


# ---------------------------------------------------------------------------
# Download + watermark
# ---------------------------------------------------------------------------

def _unique_target(target_dir: Path, name: str) -> Path:
    target = target_dir / name
    if not target.exists():
        return target
    stem = Path(name).stem
    suffix = Path(name).suffix
    i = 2
    while True:
        candidate = target_dir / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


async def _download_and_collate(
    api,
    vault_id: str,
    files: dict[str, dict[str, Any]],
    output_dir: Path,
    on_progress: ProgressFn,
    watermark_override: Optional[str],
) -> dict[str, list[dict[str, Any]]]:
    pdfs_dir = output_dir / "PDFs"
    steps_dir = output_dir / "STEP"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    steps_dir.mkdir(parents=True, exist_ok=True)

    pdf_results: list[dict[str, Any]] = []
    step_results: list[dict[str, Any]] = []

    for fv_id, info in files.items():
        name = info["name"]
        kind = info["kind"]
        state = info["state"]
        pn = info["pn"]

        on_progress(f"  ↓ {name}")
        dl = await api.download_file_version_content(
            vault_id=vault_id, file_version_id=fv_id
        )
        if dl.get("error"):
            on_progress(f"     download failed: {dl.get('data')}")
            continue
        data: bytes = dl["data"]

        if kind == "pdf":
            wm_text = watermark_override or _state_to_watermark(state)
            wm_color = (
                _WM_RELEASED_COLOR if wm_text == "RELEASED" else _WM_REVIEW_COLOR
            )
            try:
                data = apply_watermark(
                    data, wm_text,
                    font_size=80, color=wm_color, opacity=0.25, rotation=45.0,
                )
            except Exception as exc:  # noqa: BLE001 — fail open, keep plain
                on_progress(f"     watermark failed ({exc}); saving plain copy")
                wm_text = ""

            target = _unique_target(pdfs_dir, _safe(name))
            target.write_bytes(data)
            pdf_results.append({
                "name": name, "path": str(target),
                "file_version_id": fv_id, "part_number": pn,
                "state": state, "watermark": wm_text,
            })
        else:  # step
            target = _unique_target(steps_dir, _safe(name))
            target.write_bytes(data)
            step_results.append({
                "name": name, "path": str(target),
                "file_version_id": fv_id, "part_number": pn,
                "state": state,
            })

    return {"pdfs": pdf_results, "steps": step_results}


# ---------------------------------------------------------------------------
# MFG BOM Excel
# ---------------------------------------------------------------------------

async def _generate_mfg_bom_xlsx(
    api,
    vault_id: str,
    top_pn: str,
    top_iv_id: str,
    output_dir: Path,
    on_progress: ProgressFn,
) -> Optional[str]:
    """Pull the top item's BOM and emit a Simplifyber-branded MFG BOM xlsx.

    Reuses ``bom_purchasing.generate_from_vault_bom`` so the formatting
    matches every other purchasing/MFG sheet we ship. Output is renamed
    to ``<pn>-MFG-BOM.xlsx`` so it's distinct from the purchasing export.
    """
    bom = await api.get_item_bom(
        vault_id=vault_id, item_version_id=top_iv_id, limit=500
    )
    if bom.get("error"):
        on_progress(f"  BOM lookup for sheet failed: {bom.get('data')}")
        return None

    on_progress("  rendering Excel…")
    result = bom_purchasing.generate_from_vault_bom(
        {"bom": bom}, top_pn, str(output_dir),
    )
    if result.get("error"):
        on_progress(f"  BOM Excel error: {result.get('message')}")
        return None

    src = Path(result["output_path"])
    target = output_dir / f"{_safe(top_pn)}-MFG-BOM.xlsx"
    if src.exists() and src != target:
        try:
            if target.exists():
                target.unlink()
            src.replace(target)
        except OSError as exc:
            on_progress(f"  rename to MFG-BOM.xlsx failed ({exc}); kept {src.name}")
            target = src
    return str(target)


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

async def build_mfg_package(
    api,
    vault_id: str,
    part_number: str,
    *,
    output_dir: Optional[Path] = None,
    on_progress: Optional[ProgressFn] = None,
    watermark_override: Optional[str] = None,
) -> dict[str, Any]:
    """Build the full manufacturing-order package.

    Args:
        api: An authenticated ``VaultRestAPI`` instance.
        vault_id: Resolved vault id from sign-in.
        part_number: Top-level assembly / part number to package.
        output_dir: Destination folder (created if missing). Defaults to
            ``~/Downloads/<pn>_MFG_<timestamp>``.
        on_progress: Optional callback invoked with status strings as
            work progresses — wire this to the GUI log.
        watermark_override: ``"RELEASED"`` or ``"FOR REVIEW"`` to force
            every PDF to the same stamp. ``None`` (default) picks per file
            based on the item's lifecycle state.

    Returns a result dict with:
        ``error`` (bool), ``message`` (when error),
        ``output_dir``, ``items_collected``, ``pdfs``, ``steps``,
        ``bom_path``.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)

    output_dir = (output_dir or default_output_dir(part_number)).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    progress(f"Output folder: {output_dir}")

    progress(f"Resolving top item: {part_number}")
    top_item = await _resolve_top_item(api, vault_id, part_number)
    if not top_item:
        return {
            "error": True,
            "message": f"No item found for {part_number!r}.",
            "output_dir": str(output_dir),
        }

    top_iv_id, _top_iv = await _resolve_item_version_id(api, vault_id, top_item)
    if not top_iv_id:
        return {
            "error": True,
            "message": f"Could not resolve a version for {part_number!r}.",
            "output_dir": str(output_dir),
        }

    progress("Walking BOM tree…")
    rows = await _gather_bom_tree(api, vault_id, top_item, progress)
    if not rows:
        return {
            "error": True,
            "message": "BOM walk returned no items.",
            "output_dir": str(output_dir),
        }
    progress(f"Collected {len(rows)} item version(s) in the BOM tree.")

    progress("Locating PDF and STEP deliverables…")
    files = await _collect_deliverable_files(api, vault_id, rows, progress)
    progress(
        f"Found {len(files)} unique deliverable file(s) "
        f"({sum(1 for f in files.values() if f['kind']=='pdf')} PDF, "
        f"{sum(1 for f in files.values() if f['kind']=='step')} STEP)."
    )

    progress("Downloading and collating files…")
    download_summary = await _download_and_collate(
        api, vault_id, files, output_dir, progress, watermark_override,
    )

    progress("Generating MFG BOM Excel…")
    bom_path = await _generate_mfg_bom_xlsx(
        api, vault_id, part_number, top_iv_id, output_dir, progress,
    )

    return {
        "error": False,
        "output_dir": str(output_dir),
        "items_collected": len(rows),
        "pdfs": download_summary["pdfs"],
        "steps": download_summary["steps"],
        "bom_path": bom_path,
    }
