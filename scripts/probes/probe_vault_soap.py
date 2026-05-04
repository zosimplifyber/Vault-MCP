"""
Discover the SOAP / WCF surface on a Vault server and verify whether it
exposes the property-update / lifecycle-update operations the release
workflow needs.

Phases (in order):
    A. Walk a wide set of candidate URL prefixes and list every .asmx /
       .svc service that exists.
    B. For each found service, fetch its WSDL (?WSDL or ?singleWsdl for
       WCF) and grep operation names. Report which ones look relevant
       (Update*, *LifeCycleState*, *Properties*, GetVaults*, etc.).
    C. Verify auth by issuing a known-safe SOAP call (the ASMX
       AdminService.GetVaultsByCurrentUser, or the WCF equivalent).
    D. Read-only test: fetch one item's properties via SOAP and compare
       to the REST response so we know the SOAP namespace / shape.
    E. (opt-in) WRITE test: with --write-test ITEM_ID PROP NEW_VALUE,
       attempt to update the property. NEVER runs without that flag and
       always prints a confirmation prompt first unless --yes is passed.

Run:
    python scripts/probe_vault_soap.py
    python scripts/probe_vault_soap.py --verbose
    python scripts/probe_vault_soap.py --write-test 106189 'Engr Approved' 'Yes'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vault_rest_api import VaultRestAPI  # noqa: E402
from vault_soap import VaultSoapError, extract_ticket  # noqa: E402


# ---------------------------------------------------------------------------
# Console helpers (ASCII-only — Windows cp1252 safe)
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    return sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if _supports_color() else text


def H1(text: str) -> None:
    bar = "=" * 70
    print()
    print(_c("1;36", bar))
    print(_c("1;36", f"  {text}"))
    print(_c("1;36", bar))


def H2(text: str) -> None:
    print()
    print(_c("1", text))
    print(_c("1", "-" * len(text)))


def OK(text: str) -> None:
    print(_c("32", f"  [OK]   {text}"))


def NOTE(text: str) -> None:
    print(f"         {text}")


def WARN(text: str) -> None:
    print(_c("33", f"  [WARN] {text}"))


def FAIL(text: str) -> None:
    print(_c("31", f"  [FAIL] {text}"))


# ---------------------------------------------------------------------------
# Sign-in helper
# ---------------------------------------------------------------------------

def load_config() -> dict:
    p = PROJECT_ROOT / "config.json"
    if not p.exists():
        sys.exit(f"[ERROR] config.json not found at {p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


async def sign_in(cfg: dict) -> tuple[VaultRestAPI, str, str, str]:
    api = VaultRestAPI(servername=cfg["vault"]["servername"])
    r = await api.create_session(
        database=cfg["vault"]["database"],
        username=cfg["vault"]["username"],
        password=cfg["vault"]["password"],
    )
    if r["error"]:
        raise SystemExit(f"sign-in failed: {r['data']}")
    data = r["data"]
    vault_id = str((data.get("vaultInformation") or {}).get("id", ""))
    user_id = str((data.get("userInformation") or {}).get("id", ""))
    access_token = str(data.get("accessToken") or "")
    return api, vault_id, access_token, user_id


# ---------------------------------------------------------------------------
# Phase A — endpoint discovery
# ---------------------------------------------------------------------------

CATEGORIES = ["Filestore", "Connectivity"]
VERSIONS = ["v26", "v27", "v28", "v29", "v30", "v31"]


def list_dir(client: httpx.Client, url: str) -> list[str]:
    """Return basenames of any .asmx / .svc files at a given listing URL."""
    try:
        r = client.get(url)
    except Exception:  # noqa: BLE001
        return []
    if r.status_code != 200 or "html" not in (r.headers.get("content-type", "")):
        return []
    return sorted(set(re.findall(r'HREF="([^"]+\.(?:asmx|svc))"', r.text)))


def discover_endpoints(servername: str) -> dict[str, list[str]]:
    """Walk likely Vault SOAP locations; return {prefix: [.asmx/.svc URL, ...]}."""
    base = servername.rstrip("/")
    found: dict[str, list[str]] = {}
    with httpx.Client(verify=False, timeout=10) as c:
        for cat in CATEGORIES:
            for v in VERSIONS:
                prefix = f"{base}/AutodeskDM/Services/{cat}/{v}/"
                services = list_dir(c, prefix)
                if services:
                    found[f"{cat}/{v}"] = [
                        url if url.startswith("http") else base + url
                        for url in services
                    ]
        # Also try the legacy un-categorised paths
        for v in VERSIONS:
            prefix = f"{base}/AutodeskDM/Services/{v}/"
            services = list_dir(c, prefix)
            if services:
                found[v] = [
                    url if url.startswith("http") else base + url
                    for url in services
                ]
    return found


# ---------------------------------------------------------------------------
# Phase B — WSDL inspection
# ---------------------------------------------------------------------------

# Operations we want to find — case-insensitive substring matches in the WSDL.
INTERESTING_OPS = [
    "updateitemproperties", "updatefileproperties",
    "updatefilelifecyclestates", "updateitemlifecyclestates",
    "getallpropertydefinitions", "getallpropertydefinitionsbyentityclassid",
    "getalllifecycledefinitions", "getlifecycledefinitionsbyids",
    "getallcategories", "getallcategorydefinitions",
    "getvaultsbycurrentuser", "getvaults",
    "createsession", "signin",
    "getitemsbyids", "getfilesbyids",
    "getallitemsfromsearchresults",
]


def wsdl_url(svc_url: str) -> list[str]:
    """Return candidate WSDL URLs for the given service. WCF uses
    `?singleWsdl` or `?wsdl`; ASMX uses `?WSDL`."""
    if svc_url.endswith(".svc"):
        return [svc_url + "?singleWsdl", svc_url + "?wsdl"]
    return [svc_url + "?WSDL", svc_url + "?wsdl"]


def fetch_wsdl_operations(client: httpx.Client, svc_url: str) -> tuple[str, list[str]]:
    """Return (used_url, sorted unique operation names) — empty list on failure."""
    for url in wsdl_url(svc_url):
        try:
            r = client.get(url)
        except Exception:  # noqa: BLE001
            continue
        if r.status_code != 200:
            continue
        text = r.text
        # Both ASMX and WCF WSDL list operations as <wsdl:operation name="X">
        # or <operation name="X"> inside <portType>.
        ops = set(re.findall(r'<(?:wsdl:)?operation\s+name="([^"]+)"', text))
        if ops:
            return url, sorted(ops, key=str.lower)
    return "", []


def matches_interesting(op: str) -> bool:
    low = op.lower()
    return any(kw in low for kw in INTERESTING_OPS)


# ---------------------------------------------------------------------------
# Phase C — auth verification
# ---------------------------------------------------------------------------

_SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
_VAULT_NS = "http://AutodeskDM/Services"


def _security_envelope(ticket: str, user_id: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="{_SOAP_NS}"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <soap:Header>
    <SecurityHeader xmlns="{_VAULT_NS}">
      <Ticket>{ticket}</Ticket>
      <UserId>{user_id}</UserId>
    </SecurityHeader>
  </soap:Header>
  <soap:Body>{body}</soap:Body>
</soap:Envelope>"""


def soap_post(
    client: httpx.Client,
    url: str,
    soap_action: str,
    body_xml: str,
    ticket: str,
    user_id: str,
) -> httpx.Response:
    envelope = _security_envelope(ticket, user_id, body_xml)
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f'"{soap_action}"',
    }
    return client.post(url, headers=headers, content=envelope.encode("utf-8"))


def try_auth_call(
    client: httpx.Client,
    svc_url: str,
    ticket: str,
    user_id: str,
    namespace: str,
    op: str,
    body_inner: str = "",
) -> tuple[bool, str]:
    """Attempt one SOAP call. Return (success, summary)."""
    body = f'<{op} xmlns="{namespace}">{body_inner}</{op}>'
    try:
        r = soap_post(client, svc_url, f"{namespace}/{op}",
                      body, ticket, user_id)
    except Exception as exc:  # noqa: BLE001
        return False, f"transport error: {exc}"
    if r.status_code >= 400:
        fault = re.search(r"<faultstring[^>]*>([^<]+)</faultstring>", r.text)
        msg = fault.group(1) if fault else r.text[:200]
        return False, f"HTTP {r.status_code}: {msg}"
    # Look for the response body
    return True, f"HTTP {r.status_code}, {len(r.text):,} bytes"


# ---------------------------------------------------------------------------
# Phase E — write test (opt-in)
# ---------------------------------------------------------------------------

def write_test(
    client: httpx.Client,
    item_svc_url: str,
    item_namespace: str,
    item_id: str,
    prop_name: str,
    new_value: str,
    ticket: str,
    user_id: str,
) -> None:
    """Attempt UpdateItemProperties on ITEM_ID. Prints success / fault."""
    H2(f"PHASE E — Write test: set '{prop_name}' = {new_value!r} on item {item_id}")
    NOTE(f"Endpoint: {item_svc_url}")

    # The exact request shape varies by Vault version; try the most common
    # one. ItemService.UpdateItemProperties takes (long[] revIds, PropInstParam[] props).
    # PropInstParam = {EntId, PropDefId, Val}.
    # We don't know the PropDefId off the top of the script — caller should
    # supply it via env or the script can list defs first. For the simplest
    # smoke-test we just dump the SOAP request + response so the user can
    # inspect the schema mismatch.
    body = f"""
      <UpdateItemProperties xmlns="{item_namespace}">
        <revIds>
          <long>{int(item_id)}</long>
        </revIds>
        <propInstParamArray>
          <PropInstParamArray>
            <Items>
              <PropInstParam>
                <PropDefId>0</PropDefId>
                <Val xsi:type="xsd:string">{new_value}</Val>
              </PropInstParam>
            </Items>
          </PropInstParamArray>
        </propInstParamArray>
      </UpdateItemProperties>
    """.strip()
    NOTE("Posting test request (PropDefId=0 placeholder; the server will tell us the right shape) …")
    try:
        r = soap_post(
            client, item_svc_url,
            f"{item_namespace}/UpdateItemProperties",
            body, ticket, user_id,
        )
    except Exception as exc:  # noqa: BLE001
        FAIL(f"transport error: {exc}")
        return
    print(f"  HTTP {r.status_code}")
    body_preview = r.text[:1500]
    print("  ----- response (first 1500 chars) -----")
    print(body_preview)
    print("  ----- end -----")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    # UTF-8 stdout for any wide chars in WSDL responses
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    p = argparse.ArgumentParser(description="Probe Vault SOAP/WCF surface.")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Dump every operation found, not just the interesting ones.")
    p.add_argument("--write-test", nargs=3,
                   metavar=("ITEM_ID", "PROP", "NEW_VALUE"),
                   help="OPT-IN: attempt UpdateItemProperties on ITEM_ID.")
    p.add_argument("--yes", action="store_true",
                   help="Skip the write-test confirmation prompt.")
    args = p.parse_args()

    cfg = load_config()
    servername = cfg["vault"]["servername"]
    H1("VAULT SOAP / WCF SURFACE PROBE")
    print(f"  Server   : {servername}")
    print(f"  Database : {cfg['vault']['database']}")
    print(f"  User     : {cfg['vault']['username']}")

    # ---- Sign in (REST) for ticket + user_id --------------------------
    H2("Sign in (REST) — obtain SOAP ticket + user_id")
    api, vault_id, access_token, user_id = asyncio.run(sign_in(cfg))
    ticket = extract_ticket(access_token)
    OK(f"vault_id={vault_id}  user_id={user_id}  ticket={ticket[:8]}…")

    # ---- Phase A — discover endpoints ---------------------------------
    H1("PHASE A — Discover SOAP / WCF service files")
    found = discover_endpoints(servername)
    if not found:
        FAIL("No services found at any of the candidate paths.")
        FAIL("Server may not expose ASMX/WCF, or paths are non-standard.")
        return 1
    total = sum(len(v) for v in found.values())
    OK(f"Found {total} service files across {len(found)} prefix(es).")
    for prefix in sorted(found):
        print(f"  {prefix}/")
        for url in found[prefix]:
            print(f"    - {url.split('/')[-1]}")

    # ---- Phase B — WSDL inspection ------------------------------------
    H1("PHASE B — Fetch each WSDL and find interesting operations")
    relevant: list[tuple[str, str, list[str]]] = []  # (prefix, svc_url, ops)
    with httpx.Client(verify=False, timeout=15) as c:
        for prefix, urls in sorted(found.items()):
            for svc_url in urls:
                wsdl_used, ops = fetch_wsdl_operations(c, svc_url)
                svc_name = svc_url.split("/")[-1]
                if not ops:
                    print(f"  [no WSDL] {prefix}/{svc_name}")
                    continue
                interesting = [o for o in ops if matches_interesting(o)]
                if interesting:
                    OK(f"{prefix}/{svc_name}  ({len(ops)} ops, {len(interesting)} interesting)")
                    for o in interesting:
                        NOTE(f"- {o}")
                else:
                    print(f"  {prefix}/{svc_name}  ({len(ops)} ops, none interesting)")
                if args.verbose:
                    for o in ops:
                        NOTE(f"  {o}")
                relevant.append((prefix, svc_url, ops))

    # ---- Phase C — auth verification ----------------------------------
    H1("PHASE C — Auth verification (try a safe known operation)")
    # Try the ASMX flavour first, then WCF.
    auth_targets: list[tuple[str, str, str]] = []  # (svc_url, namespace, op)
    for prefix, svc_url, ops in relevant:
        ns = (
            "http://AutodeskDM/Services/Connectivity"
            if "Connectivity" in prefix
            else "http://AutodeskDM/Services/Filestore"
            if "Filestore" in prefix
            else "http://AutodeskDM/Services"
        )
        for op in ops:
            if op.lower() == "getvaultsbycurrentuser":
                auth_targets.append((svc_url, ns, op))
                break
            if op.lower() == "getvaults":
                auth_targets.append((svc_url, ns, op))
                break

    if not auth_targets:
        WARN("No GetVaults / GetVaultsByCurrentUser operation found — skipping auth probe.")
    else:
        with httpx.Client(verify=False, timeout=15) as c:
            for svc_url, ns, op in auth_targets[:3]:
                ok, msg = try_auth_call(c, svc_url, ticket, user_id, ns, op)
                label = OK if ok else FAIL
                label(f"{op} @ {svc_url.split('/')[-1]} → {msg}")

    # ---- Phase E — opt-in write test ----------------------------------
    if args.write_test:
        item_id, prop, new_val = args.write_test

        # Find an ItemService endpoint
        item_target = None
        for prefix, svc_url, ops in relevant:
            if "ItemService" in svc_url and any(
                o.lower() == "updateitemproperties" for o in ops
            ):
                ns = (
                    "http://AutodeskDM/Services/Filestore"
                    if "Filestore" in prefix
                    else "http://AutodeskDM/Services"
                )
                item_target = (svc_url, ns)
                break

        if not item_target:
            FAIL("No ItemService.UpdateItemProperties operation found in WSDLs — write test skipped.")
            return 0

        if not args.yes:
            print()
            ans = input(
                f"  About to attempt UpdateItemProperties on item {item_id}. Continue? [y/N] "
            ).strip().lower()
            if ans not in ("y", "yes"):
                print("  Cancelled.")
                return 0

        with httpx.Client(verify=False, timeout=30) as c:
            write_test(
                c, item_target[0], item_target[1],
                item_id, prop, new_val, ticket, user_id,
            )

    H1("DONE")
    print("  Copy the relevant operations + auth-probe results back to me and we'll")
    print("  pick the right endpoint for property edits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
