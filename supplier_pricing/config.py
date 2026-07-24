"""Config loading for supplier pricing.

Reads the ``supplier_pricing`` block from the project ``config.json`` (same file
the rest of the server uses). Everything is optional; sensible defaults keep the
tool usable before the owner fills anything in.
"""
from __future__ import annotations

import json
import os
import sys


def _project_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    # supplier_pricing/ -> repo root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config() -> dict:
    """Full config.json as a dict, or {} if absent/unreadable."""
    path = os.path.join(_project_dir(), "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def supplier_pricing_block(cfg: dict | None = None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    return (cfg or {}).get("supplier_pricing", {}) or {}


def update_list_name(sp_block: dict) -> str:
    from purchasing_update import DEFAULT_UPDATE_LIST
    return (sp_block.get("update", {}) or {}).get("list_name") or DEFAULT_UPDATE_LIST


def write_field_overrides(sp_block: dict) -> dict:
    """Non-empty {display -> internal} overrides from config write_field_map."""
    raw = sp_block.get("write_field_map", {}) or {}
    return {k: v for k, v in raw.items() if v}
