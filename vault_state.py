"""
Vault lifecycle state for BOM part numbers — read-only and best effort.

The purchasing sheet shows each part's Vault state ("Released", "Work in
Progress", …). Every Vault file-search hit already carries that state, so one
keyword search per part number is enough; nothing is written and no CAD file is
touched.

Design notes
------------
* **Best effort, never fatal.** No ``config.json``, no network, bad credentials,
  a Vault outage — every failure returns an empty map plus a warning, and the
  caller leaves the State column blank. Sheet generation must never depend on
  Vault being reachable: the standalone ``.exe`` ships without ``config.json``
  and takes the no-config path on every run.
* ``vault_rest_api`` is imported **lazily**, so the Excel-only / offline path
  costs nothing (same pattern ``purchasing_reference`` uses for ``msal``).
* Vault's search is keyword-based and matches properties as well as names —
  querying "CD-001582" returns "CD-001582.iam" *and* an unrelated "SF-001915".
  ``pick_state_file`` therefore trusts a hit only when the file's name IS the
  part number. Showing no state beats showing someone else's.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable

from supplier_pricing.normalize import normalize_part_number

# Vault caps concurrent work anyway; this keeps a 300-row BOM from opening 300
# sockets at once while still finishing a big BOM in a few seconds.
MAX_CONCURRENCY = 8

# When several files share a part number, the model is the one whose state the
# BOM means. Anything not listed sorts last (files stored without an extension).
_EXT_PRIORITY = (".iam", ".ipt", ".idw", ".dwg")


# --------------------------------------------------------------------------- config

def _project_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(os.path.abspath(__file__))


def vault_config(config_path: str = "") -> dict | None:
    """The `vault` block of config.json, or None when it is absent/incomplete."""
    path = config_path or os.path.join(_project_dir(), "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = (json.load(f) or {}).get("vault") or {}
    except Exception:      # noqa: BLE001 — missing/unreadable config is normal
        return None
    if not all(cfg.get(k) for k in ("servername", "username", "password", "database")):
        return None
    return cfg


# --------------------------------------------------------------------------- matching

def _stem(name: Any) -> str:
    base = os.path.basename(str(name or "")).strip()
    return normalize_part_number(os.path.splitext(base)[0])


def _ext_rank(name: Any) -> int:
    ext = os.path.splitext(str(name or ""))[1].lower()
    return _EXT_PRIORITY.index(ext) if ext in _EXT_PRIORITY else len(_EXT_PRIORITY)


def pick_state_file(number: Any, files: Iterable[dict]) -> dict | None:
    """The search hit whose file NAME is this part number, model files first.

    Returns None when nothing matches by name — a keyword-only hit (Vault also
    searches properties) is not this part and must not lend it a state.
    """
    key = normalize_part_number(number)
    if not key:
        return None
    matches = [f for f in files if _stem(f.get("name")) == key]
    if not matches:
        return None
    return sorted(matches, key=lambda f: _ext_rank(f.get("name")))[0]


def state_of(file_row: dict) -> str:
    """Lifecycle state name from a file-search hit ('Released', …)."""
    lifecycle = file_row.get("lifecycleState") or {}
    return str(lifecycle.get("name") or file_row.get("state") or "").strip()


# --------------------------------------------------------------------------- fetch

async def _fetch_states(cfg: dict, numbers: list[str]) -> dict[str, str]:
    """Sign in and resolve one state per part number. Raises on sign-in failure."""
    from vault_rest_api import VaultRestAPI      # lazy — see module docstring

    api = VaultRestAPI(servername=cfg["servername"])
    sign_in = await api.create_session(
        database=cfg["database"], username=cfg["username"], password=cfg["password"],
    )
    if sign_in["error"]:
        raise RuntimeError(f"Vault sign-in failed: {str(sign_in['data'])[:200]}")
    data = sign_in["data"] or {}
    vault_id = str((data.get("vaultInformation") or {}).get("id", "")
                   or data.get("vaultId", "") or "")

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(number: str) -> tuple[str, str]:
        async with sem:
            result = await api.search_files(vault_id, number, limit=20)
        if result["error"]:
            return number, ""
        payload = result["data"] or {}
        rows = payload.get("results") or payload.get("value") or []
        hit = pick_state_file(number, rows)
        return number, state_of(hit) if hit else ""

    out: dict[str, str] = {}
    for res in await asyncio.gather(*(one(n) for n in numbers), return_exceptions=True):
        if isinstance(res, BaseException):
            continue          # one bad lookup must not sink the rest
        number, state = res
        if state:
            out[normalize_part_number(number)] = state
    return out


def _run(factory: Callable[[], Any]) -> Any:
    """Run a coroutine from sync code, including from inside a running loop.

    The GUI and CLI have no loop; the MCP tools are `async def` and call sheet
    generation directly, where `asyncio.run` would raise. Takes a factory so the
    coroutine is only created on the loop that will run it.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(factory())).result()


# --------------------------------------------------------------------------- public

def lookup_file_states(
    numbers: Iterable[Any], *, config_path: str = "",
) -> tuple[dict[str, str], list[str]]:
    """Map normalized part number -> Vault state. Returns (states, warnings)."""
    unique: list[str] = []
    seen: set[str] = set()
    for raw in numbers:
        key = normalize_part_number(raw)
        if key and key not in seen:
            seen.add(key)
            unique.append(str(raw).strip())
    if not unique:
        return {}, []

    cfg = vault_config(config_path)
    if not cfg:
        return {}, ["No Vault connection configured — the State column is left blank."]

    try:
        return _run(lambda: _fetch_states(cfg, unique)), []
    except Exception as exc:      # noqa: BLE001 — Vault must never break the sheet
        return {}, [f"Could not read Vault states ({exc}); State is left blank."]
