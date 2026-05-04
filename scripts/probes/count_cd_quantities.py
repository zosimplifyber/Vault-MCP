"""
Walk MFG-00044-OrderFiles.iam recursively and count how many times each
target CD-XXXXXX leaf appears across the entire assembly tree. The CAD BOM
is one level deep per file, so we DFS using /file-versions/{id}/uses.

Writes a JSON map {CD: quantity} to ./scripts/cd_quantities.json so other
scripts can reuse it without re-walking the tree.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, Set

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((PROJECT_ROOT / "config.json").read_text("utf-8"))["vault"]
SERVER = CONFIG["servername"].rstrip("/")
BASE = f"{SERVER}/AutodeskDM/Services/api/vault/v2"
VAULT_ID = "1"
ROOT_FILE_VERSION_ID = "111495"  # MFG-00044-OrderFiles.iam latest

TARGET_CDS = {
    "CD-001107", "CD-001141", "CD-001277", "CD-001308", "CD-001328",
    "CD-001331", "CD-001358", "CD-001360", "CD-001361", "CD-001365",
    "CD-001369", "CD-001370", "CD-001386",
}

CD_PATTERN = re.compile(r"^(CD-\d+)\.", re.IGNORECASE)
OUT_PATH = Path(__file__).parent / "cd_quantities.json"


def sign_in(client: httpx.Client) -> dict:
    r = client.post(
        f"{BASE}/sessions",
        json={"input": {"vault": CONFIG["database"], "userName": CONFIG["username"],
                        "password": CONFIG["password"], "appCode": ""}},
    )
    r.raise_for_status()
    token = r.json()["accessToken"]
    if not token.startswith("Bearer "):
        token = f"Bearer {token}"
    return {"Authorization": token}


def get_uses(client, headers, file_version_id: str) -> list[dict]:
    r = client.get(
        f"{BASE}/vaults/{VAULT_ID}/file-versions/{file_version_id}/uses",
        headers=headers,
        params={"limit": 200},
        timeout=60.0,
    )
    if r.status_code != 200:
        return []
    return r.json().get("results", [])


def walk(client, headers, fv_id: str, counts: Dict[str, int],
         visited_assemblies: Set[str], depth: int = 0):
    """DFS through CAD assembly tree; tally CD-leaf occurrences in counts."""
    if fv_id in visited_assemblies:
        return  # avoid revisiting the same sub-assembly version (skip duplicate sub-tree)
    visited_assemblies.add(fv_id)

    children = get_uses(client, headers, fv_id)
    indent = "  " * depth
    for c in children:
        child = c.get("childFile") or {}
        name = child.get("name") or ""
        cid = child.get("id")
        if not name or not cid:
            continue
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        m = CD_PATTERN.match(name)
        if m:
            cd = m.group(1).upper()
            # Tally every occurrence — sub-assemblies count as containers,
            # actual hardware is .ipt; assemblies (.iam) we still walk into.
            if cd in TARGET_CDS and ext == "ipt":
                counts[cd] = counts.get(cd, 0) + 1
                print(f"{indent}- {name} (LEAF, +1, total={counts[cd]})")
            elif cd in TARGET_CDS and ext == "iam":
                # Target CD is itself an assembly — count as 1 occurrence in BOM
                counts[cd] = counts.get(cd, 0) + 1
                print(f"{indent}- {name} (TARGET ASM, +1, total={counts[cd]})")
                walk(client, headers, str(cid), counts, visited_assemblies, depth + 1)
                continue
            else:
                print(f"{indent}- {name} ({ext})")
        if ext == "iam":
            walk(client, headers, str(cid), counts, visited_assemblies, depth + 1)


def main():
    counts: Dict[str, int] = {cd: 0 for cd in TARGET_CDS}
    visited: Set[str] = set()

    with httpx.Client(verify=False, timeout=60.0) as client:
        headers = sign_in(client)
        print(f"Walking MFG-00044-OrderFiles.iam (file-version {ROOT_FILE_VERSION_ID})...")
        walk(client, headers, ROOT_FILE_VERSION_ID, counts, visited)

    print("\nQuantities:")
    for cd in sorted(TARGET_CDS):
        print(f"  {cd}: {counts[cd]}")

    OUT_PATH.write_text(json.dumps(counts, indent=2))
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    sys.exit(main())
