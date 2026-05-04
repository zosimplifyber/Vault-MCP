"""
Quick yes/no test that the Vault .NET SDK works from Python via pythonnet.

If this script prints "ALL CHECKS PASSED" we know:
    1. pythonnet is installed and can host .NET Framework 4.x
    2. The Vault SDK assemblies load
    3. Sign-in via WebServiceManager works against your server
    4. The full SOAP API (LifeCycleService, PropertyService, ItemService, …)
       is reachable through the SDK — i.e. the path our hand-rolled SOAP
       client couldn't find is actually there, just behind the SDK's URL
       discovery
    5. We can read live data back (lifecycle states + a property def)

If any step fails the script tells you exactly which one and why, so we
know whether to fix the install, the path, the credentials, or pivot to
a different approach.

Run:
    pip install pythonnet              # one-time
    python scripts/probe_vault_sdk.py  # uses config.json for credentials
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Default Vault 2025 SDK install path. Override via env var if yours is elsewhere.
SDK_BIN = Path(
    r"C:\Program Files\Autodesk\Autodesk Vault 2025 SDK\bin\x64"
)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    return sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if _supports_color() else text


def step(n: int, title: str) -> None:
    print()
    print(_c("1;36", f"  STEP {n}: {title}"))


def ok(msg: str) -> None:
    print(_c("32", f"    [OK]   {msg}"))


def fail(msg: str) -> None:
    print(_c("31", f"    [FAIL] {msg}"))


def info(msg: str) -> None:
    print(f"           {msg}")


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

def main() -> int:
    # Force UTF-8 console so we don't trip on em dashes etc on Windows
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    print(_c("1;36", "Vault SDK + pythonnet probe"))
    print(_c("1;36", "============================"))

    # ----- 1. Read config -------------------------------------------------
    step(1, "Read config.json")
    cfg_path = PROJECT_ROOT / "config.json"
    if not cfg_path.exists():
        fail(f"config.json not found at {cfg_path}")
        return 1
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    vault_cfg = cfg.get("vault") or {}
    server   = vault_cfg.get("servername", "")
    db       = vault_cfg.get("database", "")
    user     = vault_cfg.get("username", "")
    password = vault_cfg.get("password", "")
    if not all([server, db, user, password]):
        fail("config.json is missing one of vault.servername/database/username/password")
        return 1
    ok(f"server={server}  database={db}  user={user}")

    # The SDK's UserPasswordCredentials wants the bare host (no scheme).
    # Strip http://… so it doesn't double-prefix.
    if server.startswith("http://"):
        host = server[len("http://"):]
    elif server.startswith("https://"):
        host = server[len("https://"):]
    else:
        host = server
    host = host.rstrip("/")
    info(f"using host={host!r} for UserPasswordCredentials")

    # ----- 2. Import pythonnet -------------------------------------------
    step(2, "Load pythonnet")
    try:
        import pythonnet  # type: ignore
    except ImportError:
        fail("pythonnet is not installed. Run:  pip install pythonnet")
        return 1
    try:
        # The SDK is built against .NET Framework 4.x, so we need the
        # netfx runtime, not coreclr.
        pythonnet.load("netfx")
    except Exception as exc:  # noqa: BLE001
        fail(f"pythonnet.load('netfx') failed: {exc}")
        info("Likely cause: .NET Framework 4.x runtime missing.")
        return 1
    import clr  # noqa: F401  -- now valid after pythonnet.load
    ok("pythonnet loaded the .NET Framework 4.x runtime")

    # ----- 3. Locate the SDK ---------------------------------------------
    step(3, "Locate the Vault SDK assemblies")
    if not SDK_BIN.exists():
        fail(f"SDK folder not found: {SDK_BIN}")
        info("Edit SDK_BIN at the top of this script if your install is elsewhere.")
        return 1
    needed = [
        "Autodesk.Connectivity.WebServices.dll",
        "Autodesk.Connectivity.WebServicesTools.dll",
        "Autodesk.DataManagement.Client.Framework.dll",
        "Autodesk.DataManagement.Client.Framework.Vault.dll",
    ]
    missing = [n for n in needed if not (SDK_BIN / n).exists()]
    if missing:
        fail(f"Missing assemblies in {SDK_BIN}: {missing}")
        return 1
    ok(f"All {len(needed)} required assemblies present at {SDK_BIN}")

    # ----- 4. Reference the assemblies -----------------------------------
    step(4, "AddReference each assembly")
    import clr  # noqa: F811
    # Make the bin folder discoverable for transitive dependencies
    sys.path.insert(0, str(SDK_BIN))
    try:
        for n in needed:
            clr.AddReference(str(SDK_BIN / n))
    except Exception as exc:  # noqa: BLE001
        fail(f"clr.AddReference failed: {exc}")
        return 1
    ok("All assemblies loaded into the .NET runtime")

    # ----- 5. Sign in via WebServiceManager ------------------------------
    step(5, "Sign in via UserPasswordCredentials + WebServiceManager")
    try:
        from Autodesk.Connectivity.WebServicesTools import (  # type: ignore
            UserPasswordCredentials, WebServiceManager,
        )
    except ImportError as exc:
        fail(f"Could not import WebServicesTools: {exc}")
        return 1

    try:
        # Args: serverName, vaultName, userName, password, useSecureConn
        cred = UserPasswordCredentials(host, db, user, password, False)
        mgr = WebServiceManager(cred)
    except Exception as exc:  # noqa: BLE001
        fail(f"Sign-in / WebServiceManager construction failed: {exc}")
        info("If the password is wrong you'll see VaultLoginException here.")
        return 1
    ok("WebServiceManager constructed (sign-in OK)")

    # ----- 6. Read lifecycle definitions ---------------------------------
    step(6, "Call LifeCycleService.GetAllLifeCycleDefinitions()")
    try:
        defs = mgr.LifeCycleService.GetAllLifeCycleDefinitions()
    except Exception as exc:  # noqa: BLE001
        fail(f"GetAllLifeCycleDefinitions failed: {exc}")
        return 1
    n_defs = len(defs) if defs is not None else 0
    ok(f"Returned {n_defs} lifecycle definition(s)")
    state_names: set[str] = set()
    for d in defs or []:
        for s in (d.StateArray or []):
            name = getattr(s, "DispName", None) or getattr(s, "Name", None)
            if name:
                state_names.add(str(name))
    info(f"Distinct state names: {sorted(state_names)}")

    # ----- 7. Read a property definition (sanity-check PropertyService) --
    step(7, "Call PropertyService.GetPropertyDefinitionInfosByEntityClassId('ITEM')")
    try:
        prop_infos = mgr.PropertyService.GetPropertyDefinitionInfosByEntityClassId(
            "ITEM", None,
        )
    except Exception as exc:  # noqa: BLE001
        # Older SDKs may use a different signature — fall back to GetPropertyDefinitionsByEntityClassId
        try:
            prop_infos = mgr.PropertyService.GetPropertyDefinitionsByEntityClassId("ITEM")
        except Exception as exc2:  # noqa: BLE001
            fail(f"PropertyService call failed: {exc!s}  /  fallback: {exc2!s}")
            return 1
    n_props = len(prop_infos) if prop_infos is not None else 0
    ok(f"Returned {n_props} property definition(s) for ITEM entity")
    sample = []
    for p in (prop_infos or [])[:8]:
        # Some shapes wrap the actual PropDef inside a Property attribute
        pdef = getattr(p, "PropDef", None) or p
        disp = getattr(pdef, "DispName", None) or getattr(pdef, "Name", "?")
        sysn = getattr(pdef, "SysName", "?")
        sample.append(f"{disp} ({sysn})")
    info(f"First 8: {sample}")

    # ----- 8. Final summary ----------------------------------------------
    step(8, "Summary")
    ok("ALL CHECKS PASSED — pythonnet + Vault SDK route is viable.")
    info("Next step: build scripts/vault_sdk.py thin wrapper exposing")
    info("  - get_lifecycle_definitions()")
    info("  - update_item_lifecycle_states(master_ids, state_id, comment)")
    info("  - update_file_lifecycle_states(master_ids, state_id, comment)")
    info("  - update_item_properties(item_id, {prop_def_id: value, ...})")
    info("Then swap the workflow's lifecycle / property paths over to it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
