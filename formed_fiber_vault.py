"""
Vault lookups for the Formed Fiber handoff tool.

A thin wrapper over ``scripts/check_file_properties.py``. The CAD BOM walk
and the property flattening already live there, are tested, and use the
``option[propDefIds]`` spelling that Vault's FILE endpoints require -- the
bare ``propDefIds`` that item endpoints accept returns 200 OK with the
properties silently missing. Calling the REST API directly from here would
mean rediscovering that the hard way.

This module only reshapes their output into the handful of fields the
handoff form needs.
"""
from __future__ import annotations

import os
import sys
from typing import Any

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from check_file_properties import fetch_cad_children, fetch_file  # noqa: E402


def _summarise(properties: dict[str, Any]) -> dict[str, str]:
    """The fields the handoff cares about, as plain strings."""
    def text(key: str) -> str:
        return str(properties.get(key) or "").strip()

    return {
        "file_name": text("File Name"),
        "revision": text("Revision"),
        "state": text("State"),
        "material": text("Material"),
        # "Description (File)", not "Description" -- that is the display name
        # Vault gives property definition 49, and the flattened properties are
        # keyed by display name. It is also what file_property_rules.json
        # calls it, so the two stay greppable together.
        "description": text("Description (File)"),
        "folder_path": text("Folder Path"),
        "category": text("Category Name"),
    }


async def load_assembly(api: Any, vault_id: str, file_name: str) -> dict[str, Any]:
    """The assembly's fields plus one summary row per CAD BOM child.

    Returns ``{"assembly": {...}, "children": [...], "children_error": str}``.

    A failed BOM walk is reported, not raised: the assembly's own filename
    and revision are half the document and are already in hand, so losing
    them because the child walk failed would be a poor trade.
    """
    info = await fetch_file(api, vault_id, file_name)
    assembly = _summarise(info.get("properties") or {})

    children: list[dict[str, str]] = []
    children_error = ""
    version_id = str(info.get("file_version_id") or "")

    if not version_id:
        children_error = (
            "Vault returned no file-version ID for this assembly, so its CAD "
            "BOM cannot be walked. Pick the pressed part by hand."
        )
    else:
        try:
            rows = await fetch_cad_children(api, vault_id, version_id)
        except RuntimeError as exc:
            children_error = str(exc)
        else:
            for row in rows:
                child = _summarise(row.get("properties") or {})
                if not child["file_name"]:
                    # fetch_cad_children carries the name outside properties
                    # when Vault answered without them.
                    child["file_name"] = str(row.get("file_name") or "").strip()
                if child["file_name"]:
                    children.append(child)

    return {
        "assembly": assembly,
        "children": children,
        "children_error": children_error,
    }
