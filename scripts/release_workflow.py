"""
End-to-end release workflow for a Vault item with child components.

Walks the user through six sequential gates:

    1. Check item-property compliance for the top item + every BOM child
    2. Print a Markdown readiness report — STOP here if anything is non-compliant
    3. Synchronize file properties (Vault SyncProperties job per file)
    4. Pull every referenced file local (download via REST to a working folder)
    5. Open the top assembly in Inventor and rebuild it (Update2 + Save)
    6. Release the CAD files (SOAP UpdateFileLifeCycleStates)
    7. Release the engineering items (SOAP UpdateItemLifeCycleStates)

Each step prompts for confirmation. Steps can also be skipped with `--skip`.

Usage
-----
    python scripts/release_workflow.py SF-001702
    python scripts/release_workflow.py SF-001702 --target-state Released
    python scripts/release_workflow.py SF-001702 --skip 5,6
    python scripts/release_workflow.py SF-001702 --auto-approve
    python scripts/release_workflow.py SF-001702 --report-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

# Reconfigure stdout/stderr to UTF-8 *before* argparse runs — otherwise
# argparse's --help writer trips cp1252 on Windows when help text contains
# em-dashes / arrows. Module-level so it runs at import time, not in main().
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vault_rest_api import VaultRestAPI  # noqa: E402

from check_item_properties import (  # noqa: E402
    DEFAULT_RULES_PATH,
    check_part_number,
    format_markdown_report,
    load_json,
)
# vault_soap was the previous lifecycle-change path. It only worked when
# Vault exposed the legacy ASMX services directly — this server doesn't,
# so the workflow now goes through vault_sdk (the PowerShell ↔ .NET SDK
# bridge). vault_soap.py is kept for the access-token decoder and as a
# reference implementation.


CONFIG_PATH = PROJECT_ROOT / "config.json"
DEFAULT_WORKFOLDER = Path.home() / "Documents" / "Vault" / "Workspace"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tiny console helpers
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    return sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if _supports_color() else text


def banner(step_num: int, title: str) -> None:
    bar = "-" * 70
    print()
    print(_c("1;36", bar))
    print(_c("1;36", f"  STEP {step_num} -- {title}"))
    print(_c("1;36", bar))


def info(msg: str) -> None:
    print(f"  {msg}")


def success(msg: str) -> None:
    print(_c("32", f"  [OK]   {msg}"))


def warn(msg: str) -> None:
    print(_c("33", f"  [WARN] {msg}"))


def fail(msg: str) -> None:
    print(_c("31", f"  [FAIL] {msg}"))


def confirm(prompt: str, *, default_yes: bool = True, auto_approve: bool = False) -> bool:
    if auto_approve:
        print(_c("90", f"  (auto-approved) {prompt}"))
        return True
    suffix = " [Y/n] " if default_yes else " [y/N] "
    try:
        ans = input(f"  {prompt}{suffix}").strip().lower()
    except EOFError:
        return default_yes
    if not ans:
        return default_yes
    return ans in ("y", "yes")


# ---------------------------------------------------------------------------
# Vault REST helpers used across steps
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


async def _sign_in(cfg: dict) -> tuple[VaultRestAPI, str, str, str]:
    """Sign in and return (api, vault_id, access_token, user_id).

    ``user_id`` is required by the SOAP client's SecurityHeader — it's not
    embedded in the access token, so we capture it from the sign-in
    response separately.
    """
    vault_cfg = cfg.get("vault") or {}
    api = VaultRestAPI(servername=vault_cfg["servername"])
    sign_in = await api.create_session(
        database=vault_cfg["database"],
        username=vault_cfg["username"],
        password=vault_cfg["password"],
    )
    if sign_in["error"]:
        raise RuntimeError(f"Vault sign-in failed: {sign_in['data']}")
    data = sign_in["data"]
    vault_id = str(
        (data.get("vaultInformation") or {}).get("id", "")
        or data.get("vaultId", "")
        or ""
    )
    access_token = str(data.get("accessToken") or "")
    user_id = str((data.get("userInformation") or {}).get("id", "") or "")
    return api, vault_id, access_token, user_id


# ---------------------------------------------------------------------------
# Step 1+2 — compliance check & readiness report
# ---------------------------------------------------------------------------

async def step_check_compliance(part_number: str, *, rules_path: Path) -> dict[str, Any]:
    info(f"Walking BOM for {part_number} and checking property rules…")
    result = await check_part_number(
        part_number,
        config_path=CONFIG_PATH,
        rules_path=rules_path,
        recursive=True,
    )
    top_failed = bool((result.get("report") or {}).get("failed", 0))
    kids_failed = any(
        (c.get("error") is not None) or
        ((c.get("report") or {}).get("failed", 0) > 0)
        for c in (result.get("children") or [])
    )
    if top_failed or kids_failed:
        warn("One or more items did not pass the compliance rules.")
    else:
        success("All items pass compliance.")
    return result


def step_readiness_report(result: dict[str, Any], *, output_path: Optional[Path] = None) -> str:
    md = format_markdown_report(result)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md, encoding="utf-8")
        success(f"Report written to {output_path}")
    print()
    print(md)
    return md


# ---------------------------------------------------------------------------
# Step 3 — synchronize file properties
# ---------------------------------------------------------------------------

async def _associated_file_versions(
    api: VaultRestAPI,
    vault_id: str,
    item_version_id: str,
) -> list[dict[str, Any]]:
    """Return associated file-version dicts for an item version."""
    if not item_version_id:
        return []
    resp = await api.get_item_associated_files(
        vault_id=vault_id, item_version_id=item_version_id, limit=200
    )
    if resp["error"]:
        warn(f"associated-files lookup failed for {item_version_id}: {resp['data']}")
        return []
    return _extract_collection(resp.get("data"))


async def step_sync_properties(
    api: VaultRestAPI,
    vault_id: str,
    compliance: dict[str, Any],
    *,
    auto_approve: bool,
) -> list[dict[str, Any]]:
    """Submit Autodesk.Vault.SyncProperties for every CAD file in the BOM tree.

    Vault's SyncProperties job pushes UDP values from the file's link-mapped
    properties down to the file metadata. Running it across the whole tree
    before release is the standard 'flush dirty properties to Vault' step.
    """
    top_iv = (compliance.get("info") or {}).get("item_version_id") or ""
    file_versions: dict[str, dict[str, Any]] = {}

    info("Collecting associated files for top item and every child…")
    for fv in await _associated_file_versions(api, vault_id, top_iv):
        fid = str(fv.get("id") or "")
        if fid:
            file_versions[fid] = fv

    for child in compliance.get("children") or []:
        cv = child.get("item_version_id") or ""
        if not cv:
            continue
        for fv in await _associated_file_versions(api, vault_id, cv):
            fid = str(fv.get("id") or "")
            if fid and fid not in file_versions:
                file_versions[fid] = fv

    if not file_versions:
        warn("No associated CAD files found — nothing to sync.")
        return []

    info(f"Found {len(file_versions)} unique file version(s).")
    if not confirm(
        f"Submit Autodesk.Vault.SyncProperties for all {len(file_versions)} files?",
        auto_approve=auto_approve,
    ):
        warn("Skipped sync-properties step.")
        return []

    submitted: list[dict[str, Any]] = []
    for fid, fv in file_versions.items():
        name = fv.get("name") or "(file)"
        resp = await api.submit_job(
            vault_id=vault_id,
            job_type="Autodesk.Vault.SyncProperties",
            params={"FileVersionId": fid},
            description=f"SyncProperties: {name}",
            priority=10,
        )
        if resp["error"]:
            fail(f"SyncProperties submit failed for {name}: {resp['data']}")
            submitted.append({"file_version_id": fid, "name": name, "ok": False, "error": resp["data"]})
        else:
            job_id = str(((resp["data"] or {}).get("job") or {}).get("id") or resp["data"].get("id") or "?")
            success(f"Queued SyncProperties for {name}  (job {job_id})")
            submitted.append({"file_version_id": fid, "name": name, "ok": True, "job_id": job_id})

    info("Jobs are queued. The Vault Job Processor will run them; check status in Vault Explorer.")
    return submitted


# ---------------------------------------------------------------------------
# Step 4 — pull every file local
# ---------------------------------------------------------------------------

async def step_download_local(
    api: VaultRestAPI,
    vault_id: str,
    compliance: dict[str, Any],
    *,
    workfolder: Path,
    auto_approve: bool,
) -> list[dict[str, Any]]:
    """Download every associated file (top + children) to the workfolder.

    The Vault add-in restores reference paths better than this REST download
    can — for production releases you'll typically follow this with
    Inventor's Get Latest in step 5. This step is here so the workflow can
    operate even when Inventor is unavailable, and so you have a clean
    on-disk snapshot before opening anything.
    """
    workfolder = workfolder.expanduser().resolve()

    top_iv = (compliance.get("info") or {}).get("item_version_id") or ""
    files: dict[str, dict[str, Any]] = {}
    for fv in await _associated_file_versions(api, vault_id, top_iv):
        fid = str(fv.get("id") or "")
        if fid:
            files[fid] = fv
    for child in compliance.get("children") or []:
        cv = child.get("item_version_id") or ""
        if not cv:
            continue
        for fv in await _associated_file_versions(api, vault_id, cv):
            fid = str(fv.get("id") or "")
            if fid and fid not in files:
                files[fid] = fv

    if not files:
        warn("No associated files to download.")
        return []

    info(f"Will download {len(files)} file(s) into {workfolder}")
    if not confirm("Proceed with REST download?", auto_approve=auto_approve):
        warn("Skipped download step.")
        return []

    workfolder.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for fid, fv in files.items():
        name = fv.get("name") or f"file_{fid}"
        target = workfolder / name
        resp = await api.download_file_version_content(
            vault_id=vault_id, file_version_id=fid
        )
        if resp["error"]:
            fail(f"download failed for {name}: {resp['data']}")
            results.append({"name": name, "ok": False, "error": resp["data"]})
            continue
        target.write_bytes(resp["data"])
        success(f"{name}  ({len(resp['data']):,} bytes)")
        results.append({"name": name, "ok": True, "path": str(target), "size": len(resp["data"])})
    return results


# ---------------------------------------------------------------------------
# Step 5 — Inventor open + rebuild + save
# ---------------------------------------------------------------------------

def _guess_top_assembly_path(downloads: list[dict[str, Any]], part_number: str) -> Optional[Path]:
    """Pick the most likely top-level assembly path from the download manifest."""
    iams = [d for d in downloads if d.get("ok") and d.get("name", "").lower().endswith(".iam")]
    if not iams:
        return None
    pn_low = part_number.strip().lower()
    for d in iams:
        if pn_low in d["name"].lower():
            return Path(d["path"])
    # Fallback: largest .iam (most likely the top assembly)
    best = max(iams, key=lambda d: d.get("size", 0))
    return Path(best["path"])


def step_inventor_rebuild(
    downloads: list[dict[str, Any]],
    part_number: str,
    *,
    explicit_path: Optional[Path],
    auto_approve: bool,
) -> Optional[str]:
    """Open + Update2 + Save the top assembly via Inventor COM."""
    target = explicit_path or _guess_top_assembly_path(downloads, part_number)
    if not target:
        warn("Could not identify a top-level .iam to rebuild. Pass --top-assembly explicitly.")
        return None

    info(f"Top assembly: {target}")
    if not confirm("Open in Inventor, rebuild (Update2), and save?", auto_approve=auto_approve):
        warn("Skipped Inventor rebuild step.")
        return None

    try:
        from inventor_automation import (
            InventorUnavailableError,
            rebuild_and_save_assembly,
        )
    except ImportError as exc:
        fail(f"inventor_automation module unavailable: {exc}")
        return None

    try:
        path_used = rebuild_and_save_assembly(target)
    except InventorUnavailableError as exc:
        fail(str(exc))
        warn("Open the assembly manually in Inventor, rebuild it, and check it back in to Vault.")
        return None
    except Exception as exc:  # noqa: BLE001
        fail(f"Inventor rebuild failed: {exc}")
        return None

    success(f"Rebuilt and saved: {path_used}")
    warn("Use the Vault add-in inside Inventor to Check In the updated assembly before continuing.")
    if not confirm("Has the rebuild been checked in to Vault?", auto_approve=auto_approve):
        warn("Stopping before lifecycle release — check in first, then re-run with --start-step 6.")
        return None
    return path_used


# ---------------------------------------------------------------------------
# Steps 6 + 7 — release lifecycle (CAD files, then items)
# ---------------------------------------------------------------------------

def _collect_file_master_ids(
    file_versions: dict[str, dict[str, Any]],
) -> list[int]:
    """Pull file masterIds from a dict of file-version records.

    SOAP UpdateFileLifeCycleStates expects masterIds (not version ids).
    Vault's REST returns either ``masterId`` or nested ``file.id``.
    """
    masters: list[int] = []
    seen: set[int] = set()
    for fv in file_versions.values():
        mid = fv.get("masterId") or (fv.get("file") or {}).get("id")
        if mid is None:
            continue
        try:
            mid_int = int(mid)
        except (TypeError, ValueError):
            continue
        if mid_int not in seen:
            seen.add(mid_int)
            masters.append(mid_int)
    return masters


# NB: an earlier _resolve_target_state_id helper used the REST
# lifecycle-definitions endpoint — removed when we switched to vault_sdk
# for state resolution (REST v2 has no lifecycle endpoints on this server,
# and even when it does, REST can't disambiguate between identically-named
# states across multiple lifecycle definitions). The SDK's
# find_state_id_for_item / find_state_id_for_file do the right thing.


async def step_release_cad(
    api: VaultRestAPI,
    vault_id: str,
    access_token: str,
    user_id: str,
    compliance: dict[str, Any],
    *,
    target_state: str,
    target_state_id: Optional[int],
    soap_version: str,    # kept for back-compat; vault_sdk ignores it
    soap_servername: str, # kept for back-compat; vault_sdk uses config.json
    auto_approve: bool,
) -> list[int]:
    """Promote every CAD file in the BOM tree to ``target_state`` via the
    Vault SDK PowerShell bridge.

    The legacy SOAP path was deprecated when we discovered this server
    only exposes the WCF Filestore shell services; full SOAP lives behind
    the .NET SDK and is reachable via ``vault_sdk``.
    """
    top_iv = (compliance.get("info") or {}).get("item_version_id") or ""
    file_versions: dict[str, dict[str, Any]] = {}
    for fv in await _associated_file_versions(api, vault_id, top_iv):
        fid = str(fv.get("id") or "")
        if fid:
            file_versions[fid] = fv
    for child in compliance.get("children") or []:
        cv = child.get("item_version_id") or ""
        if not cv:
            continue
        for fv in await _associated_file_versions(api, vault_id, cv):
            fid = str(fv.get("id") or "")
            if fid and fid not in file_versions:
                file_versions[fid] = fv

    masters = _collect_file_master_ids(file_versions)
    if not masters:
        warn("No CAD files found to release.")
        return []

    try:
        from vault_sdk import (
            VaultSDKError, lookup_file, find_state_id_for_file,
            update_file_lifecycle_states,
        )
    except ImportError as exc:
        fail(f"vault_sdk unavailable: {exc}")
        return []

    state_id = target_state_id
    if state_id is None:
        try:
            first = lookup_file(masters[0])
        except VaultSDKError as exc:
            fail(f"file lookup failed: {exc}")
            return []
        if not first.get("found"):
            fail(f"could not look up file masterId={masters[0]}")
            return []
        state_id = find_state_id_for_file(first, target_state)
    if state_id is None:
        fail(
            f"Could not resolve lifecycle state id for {target_state!r} "
            "in the file's lifecycle. Pass --target-state-id explicitly."
        )
        return []

    info(f"Releasing {len(masters)} file(s) to state '{target_state}' (id={state_id}).")
    if not confirm("Submit UpdateFileLifeCycleStates via Vault SDK?", auto_approve=auto_approve):
        warn("Skipped CAD release step.")
        return []

    try:
        result = update_file_lifecycle_states(
            masters, state_id,
            comment=f"Released via release_workflow.py to {target_state}",
        )
    except VaultSDKError as exc:
        fail(str(exc))
        return []
    success(f"Released {result.get('updated', len(masters))} file(s).")
    return masters


async def step_release_items(
    api: VaultRestAPI,
    vault_id: str,
    access_token: str,
    user_id: str,
    compliance: dict[str, Any],
    *,
    target_state: str,
    target_state_id: Optional[int],
    soap_version: str,    # kept for back-compat
    soap_servername: str, # kept for back-compat
    auto_approve: bool,
) -> list[int]:
    """Promote the top item and every child item to ``target_state`` via
    the Vault SDK PowerShell bridge.

    Lifecycle change works on master IDs (not item-version IDs) — the
    item's lifecycle def is read from a ``lookup_item`` of the top so
    we resolve "Released" to the right state id (Item Release Process =
    state 29 on this vault, NOT Basic Release Process = state 3).
    """
    master_ids: list[int] = []
    seen: set[int] = set()

    def add(v: Any) -> None:
        if v is None:
            return
        try:
            mid = int(v)
        except (TypeError, ValueError):
            return
        if mid not in seen:
            seen.add(mid)
            master_ids.append(mid)

    top_master = ((compliance.get("info") or {}).get("master") or {}).get("id")
    add(top_master)
    for child in compliance.get("children") or []:
        child_master = ((child.get("properties") or {}).get("item") or {}).get("id")
        add(child_master)

    if not master_ids:
        warn("No item master IDs collected — nothing to release.")
        return []

    try:
        from vault_sdk import (
            VaultSDKError, lookup_item, find_state_id_for_item,
            update_item_lifecycle_states,
        )
    except ImportError as exc:
        fail(f"vault_sdk unavailable: {exc}")
        return []

    state_id = target_state_id
    if state_id is None:
        top_pn = (compliance.get("info") or {}).get("properties", {}).get("Number")
        if not top_pn:
            fail("could not determine top item part number")
            return []
        try:
            top_item = lookup_item(top_pn)
        except VaultSDKError as exc:
            fail(f"item lookup failed: {exc}")
            return []
        if not top_item.get("found"):
            fail(f"item {top_pn!r} not found via SDK")
            return []
        state_id = find_state_id_for_item(top_item, target_state)
    if state_id is None:
        fail(
            f"Could not resolve lifecycle state id for {target_state!r} "
            "in the item's lifecycle. Pass --target-state-id explicitly."
        )
        return []

    info(f"Releasing {len(master_ids)} item(s) to state '{target_state}' (id={state_id}).")
    if not confirm("Submit UpdateItemLifeCycleStates via Vault SDK?", auto_approve=auto_approve):
        warn("Skipped item release step.")
        return []

    try:
        result = update_item_lifecycle_states(
            master_ids, state_id,
            comment=f"Released via release_workflow.py to {target_state}",
        )
    except VaultSDKError as exc:
        fail(str(exc))
        return []
    success(f"Released {result.get('updated', len(master_ids))} item(s).")
    return master_ids


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

async def run_workflow(args: argparse.Namespace) -> int:
    cfg = load_json(args.config)
    rules_path = args.rules
    workfolder = Path(args.workfolder).expanduser()
    skip = {int(s) for s in (args.skip or "").replace(" ", "").split(",") if s}
    start_step = max(1, args.start_step)
    auto = args.auto_approve

    # Step 1 — compliance check
    if start_step <= 1 and 1 not in skip:
        banner(1, "Check item-property compliance")
        compliance = await step_check_compliance(args.part_number, rules_path=rules_path)
    else:
        info("Step 1 skipped — re-running compliance silently to populate context…")
        compliance = await check_part_number(
            args.part_number, config_path=args.config, rules_path=rules_path, recursive=True
        )

    # Step 2 — readiness report
    if start_step <= 2 and 2 not in skip:
        banner(2, "Release readiness report")
        report_path = (
            args.report_out if args.report_out
            else PROJECT_ROOT / "Log" / f"release_readiness_{args.part_number}.md"
        )
        step_readiness_report(compliance, output_path=report_path)
        if args.report_only:
            return 0
        # Hard gate: if anything failed, require explicit override
        top_failed = bool((compliance.get("report") or {}).get("failed", 0))
        kids_failed = any(
            (c.get("error") is not None) or
            ((c.get("report") or {}).get("failed", 0) > 0)
            for c in (compliance.get("children") or [])
        )
        if (top_failed or kids_failed) and not args.force:
            fail("Compliance gate failed. Fix the items above and re-run, or pass --force to continue anyway.")
            return 1

    # Sign in for the remaining steps (REST + SOAP)
    api, vault_id, access_token, user_id = await _sign_in(cfg)
    soap_servername = cfg["vault"]["servername"]

    # Step 3 — sync properties
    if start_step <= 3 and 3 not in skip:
        banner(3, "Synchronize file properties (Vault SyncProperties job)")
        await step_sync_properties(api, vault_id, compliance, auto_approve=auto)

    # Step 4 — get files local
    downloads: list[dict[str, Any]] = []
    if start_step <= 4 and 4 not in skip:
        banner(4, "Pull every referenced file local")
        downloads = await step_download_local(
            api, vault_id, compliance, workfolder=workfolder, auto_approve=auto
        )

    # Step 5 — Inventor open + rebuild
    if start_step <= 5 and 5 not in skip:
        banner(5, "Open in Inventor, rebuild assembly, save")
        explicit_top = Path(args.top_assembly) if args.top_assembly else None
        step_inventor_rebuild(
            downloads, args.part_number,
            explicit_path=explicit_top, auto_approve=auto,
        )

    # Step 6 — release CAD
    if start_step <= 6 and 6 not in skip:
        banner(6, "Release CAD files (lifecycle → " + args.target_state + ")")
        await step_release_cad(
            api, vault_id, access_token, user_id, compliance,
            target_state=args.target_state,
            target_state_id=args.target_state_id,
            soap_version=args.soap_version,
            soap_servername=soap_servername,
            auto_approve=auto,
        )

    # Step 7 — release items
    if start_step <= 7 and 7 not in skip:
        banner(7, "Release engineering items (lifecycle → " + args.target_state + ")")
        await step_release_items(
            api, vault_id, access_token, user_id, compliance,
            target_state=args.target_state,
            target_state_id=args.target_state_id,
            soap_version=args.soap_version,
            soap_servername=soap_servername,
            auto_approve=auto,
        )

    print()
    success("Release workflow complete.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="End-to-end Vault release workflow (compliance → sync → download → rebuild → release).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("part_number", nargs="?", default="",
                   help="Top-level part number to release (e.g. SF-001702). "
                        "Optional when --gui is passed.")
    p.add_argument("--gui", action="store_true",
                   help="Launch the Tkinter wizard instead of running on the console.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to config.json.")
    p.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH, help="Path to property rules JSON.")
    p.add_argument("--workfolder", default=str(DEFAULT_WORKFOLDER),
                   help="Local folder for the REST download in step 4.")
    p.add_argument("--top-assembly", default="",
                   help="Explicit path to the top .iam (overrides auto-detection in step 5).")
    p.add_argument("--target-state", default="Released",
                   help="Lifecycle state name to promote to.")
    p.add_argument("--target-state-id", type=int, default=None,
                   help="Override state lookup with an explicit lifecycle state ID.")
    p.add_argument("--soap-version", default="v26",
                   help="Vault SOAP version (v26 = Vault 2025; lower for older releases).")
    p.add_argument("--report-out", type=Path, default=None,
                   help="Where to write the markdown readiness report.")
    p.add_argument("--report-only", action="store_true",
                   help="Generate the readiness report (steps 1-2) and stop.")
    p.add_argument("--skip", default="", help="Comma-separated step numbers to skip.")
    p.add_argument("--start-step", type=int, default=1,
                   help="Resume mid-workflow at this step number.")
    p.add_argument("--auto-approve", action="store_true",
                   help="Skip every confirmation prompt.")
    p.add_argument("--force", action="store_true",
                   help="Continue past the compliance gate even when there are failures.")
    p.add_argument("--log-level", default="INFO",
                   help="Logging verbosity (DEBUG/INFO/WARNING).")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s -- %(message)s",
    )

    if args.gui:
        # ``gui`` package lives at the project root; add the parent to sys.path
        # so this CLI can pull in the GUI when launched as ``python scripts/release_workflow.py``.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        try:
            from gui.release_workflow import launch_gui
        except ImportError as exc:
            fail(f"GUI module unavailable: {exc}")
            return 1
        launch_gui(prefill_part_number=args.part_number or "")
        return 0

    if not args.part_number:
        fail("part_number is required (or pass --gui to launch the wizard).")
        return 2

    try:
        return asyncio.run(run_workflow(args))
    except KeyboardInterrupt:
        print()
        warn("Interrupted by user.")
        return 130
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        fail(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
