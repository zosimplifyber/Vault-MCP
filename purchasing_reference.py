"""
Purchasing reference source — Microsoft List (via Graph) with an Excel fallback.

The purchasing sheet auto-fills Vendor / Cost / Material / lead-time by matching
BOM part numbers against a reference of purchased items. Historically that
reference was an Excel file (``purchased items.xlsx``). This module can instead
read it from a Microsoft List in SharePoint, reached through Microsoft Graph with
a delegated **device-code** sign-in (each user signs in with their own M365
account; no shared secret — safe to bundle in the standalone .exe).

Design notes
------------
* This module owns ONLY the Microsoft-List path + config resolution. The Excel
  path stays in ``bom_purchasing`` (``find_purchased_items_file`` /
  ``load_reference_file``) and this module never imports ``bom_purchasing`` — so
  there is no import cycle.
* ``msal`` and ``pandas`` are imported lazily, so importing this module (and
  therefore ``bom_purchasing``) never hard-requires ``msal``; the Excel-only path
  works even if ``msal`` is missing.
* Safe by default: the non-secret app-registration IDs are bundled, so the List
  is "configured", but reading it still requires a per-user device-code sign-in.
  Until a user signs in (no token cache), the loader short-circuits instantly and
  callers fall back to Excel — so existing users see no change until they opt in.
* The returned DataFrame uses the SAME canonical column names the Excel path
  produces (``Number`` + the LOOKUP columns), so ``lookup_purchased_data`` is
  unchanged.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Sites.Read.All"]

# Non-secret defaults. The client/tenant IDs come from an Entra app registration
# (public client) and are safe to bundle (a public-client id is not a secret).
DEFAULT_CONFIG: dict[str, Any] = {
    "source": "mslist",                      # "mslist" (default) | "auto" | "excel"
    "mslist": {
        # Non-secret Entra app-registration IDs (public client). Safe to bundle.
        "tenant_id": "66328e6d-6557-413a-9061-8797e292ea89",   # Directory (tenant) ID
        "client_id": "df058d52-922d-4d5a-b6d1-27f4aeeb9e1b",   # Application (client) ID
        "site_hostname": "netorgft6579427.sharepoint.com",
        "site_path": "/sites/Simplifyber",
        "list_name": "Purchased Items",      # or set "list_id"
        "list_id": "",
    },
    "column_map": {},                        # display-name -> canonical override
}

# Microsoft List column DISPLAY name -> our canonical reference column.
# lookup_purchased_data matches on "Number" and fills the rest.
MSLIST_FIELD_MAP: dict[str, str] = {
    "Number": "Number",
    "Part Number": "Number",
    "PartNumber": "Number",
    "Title": "Number",                       # Lists default the key column to "Title"
    "Item Number": "Number",
    "Vendor": "Vendor",
    "Supplier": "Vendor",
    "Vendor Number": "Vendor Number",
    "Vendor #": "Vendor Number",
    "Cost": "Cost Per",
    "Cost Per": "Cost Per",
    "Unit Cost": "Cost Per",
    "Price": "Cost Per",
    "Material": "Material",
    "HS Code": "HS/HTS Code",
    "HS/HTS Code": "HS/HTS Code",
    "HTS Code": "HS/HTS Code",
    "Shipping": "Shipping",
    "Tax": "Tax/Tariff",
    "Tax/Tariff": "Tax/Tariff",
    "Tariff": "Tax/Tariff",
    "Lead Time": "Lead Time (Business Days)",
    "Lead Time (Business Days)": "Lead Time (Business Days)",
}


# --------------------------------------------------------------------------- config

def _project_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(os.path.abspath(__file__))


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _config_json_block() -> dict:
    """Best-effort read of config.json's 'purchasing_reference' block (server side).
    Returns {} when config.json is absent/unreadable (e.g. the standalone .exe)."""
    path = os.path.join(_project_dir(), "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            return (json.load(f) or {}).get("purchasing_reference", {}) or {}
    except Exception:
        return {}


def resolve_reference_config(override: dict | None = None) -> dict:
    """DEFAULT_CONFIG <- config.json purchasing_reference <- explicit override."""
    cfg = _deep_merge(DEFAULT_CONFIG, _config_json_block())
    if override:
        cfg = _deep_merge(cfg, override)
    return cfg


def mslist_is_configured(cfg: dict) -> bool:
    ml = (cfg or {}).get("mslist", {})
    return bool(ml.get("client_id")) and bool(ml.get("tenant_id"))


def has_cached_login() -> bool:
    """Cheap hint for the GUI: True if a device-code sign-in was completed before
    (a token cache exists). Not a guarantee the token is still valid."""
    return os.path.isfile(_token_cache_path())


# --------------------------------------------------------------------------- auth

def _token_cache_path() -> str:
    # Pure path computation (no side effects) — callers that only read the path
    # (has_cached_login, the offline short-circuit) must not create directories.
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Simplifyber", "purchasing_msal_cache.bin")


def _load_cache():
    import msal
    cache = msal.SerializableTokenCache()
    p = _token_cache_path()
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                cache.deserialize(f.read())
        except Exception:
            pass
    return cache


def _save_cache(cache) -> None:
    if getattr(cache, "has_state_changed", False):
        try:
            p = _token_cache_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(cache.serialize())
        except Exception:
            pass


def acquire_token(mslist_cfg: dict, *, interactive: bool = False, printer=print) -> str:
    """Get a Graph token via delegated device-code, reusing the cached token when
    possible. With interactive=False, uses only the cache and raises if none —
    safe for automated contexts (they fall back to Excel). Interactive sign-in
    (CLI / a GUI button) populates the shared per-user cache."""
    # Fast, offline short-circuit: no cache file means definitely not signed in.
    # Keeps the Excel-fallback path instant (no msal import / no network) for
    # automated contexts and first runs.
    if not interactive and not os.path.isfile(_token_cache_path()):
        raise RuntimeError(
            "Not signed in to the Microsoft List. Run: python -m purchasing_reference --login"
        )
    import msal
    cache = _load_cache()
    app = msal.PublicClientApplication(
        mslist_cfg["client_id"],
        authority=f"https://login.microsoftonline.com/{mslist_cfg['tenant_id']}",
        token_cache=cache,
    )
    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result:
        if not interactive:
            raise RuntimeError(
                "Not signed in to the Microsoft List. Run: python -m purchasing_reference --login"
            )
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(
                f"Could not start device-code sign-in: {flow.get('error_description', flow)}"
            )
        printer(flow["message"])   # "To sign in, open https://microsoft.com/devicelogin and enter CODE"
        result = app.acquire_token_by_device_flow(flow)
    _save_cache(cache)
    if "access_token" not in result:
        raise RuntimeError(f"Sign-in failed: {result.get('error_description', result)}")
    return result["access_token"]


# --------------------------------------------------------------------------- graph

def _graph_get(token: str, url: str, params: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=headers, params=params)
    if resp.status_code >= 400:
        raise RuntimeError(f"Graph {resp.status_code} for {url}: {resp.text[:200]}")
    return resp.json()


def _resolve_site_id(token: str, hostname: str, site_path: str) -> str:
    data = _graph_get(token, f"{GRAPH}/sites/{hostname}:{site_path}")
    sid = data.get("id")
    if not sid:
        raise RuntimeError(f"Could not resolve site {hostname}:{site_path}")
    return sid


def _resolve_list_id(token: str, site_id: str, list_name: str, list_id: str = "") -> str:
    if list_id:
        return list_id
    data = _graph_get(token, f"{GRAPH}/sites/{site_id}/lists",
                      params={"$select": "id,displayName,name"})
    want = (list_name or "").strip().lower()
    for lst in data.get("value", []):
        if want in (str(lst.get("displayName", "")).lower(),
                    str(lst.get("name", "")).lower()):
            return lst["id"]
    raise RuntimeError(f"List '{list_name}' not found on the site.")


def _column_display_map(token: str, site_id: str, list_id: str) -> dict[str, str]:
    """internal field name -> display name."""
    data = _graph_get(token, f"{GRAPH}/sites/{site_id}/lists/{list_id}/columns",
                      params={"$select": "name,displayName"})
    out: dict[str, str] = {}
    for col in data.get("value", []):
        name = col.get("name")
        if name:
            out[name] = col.get("displayName", name)
    return out


def _iter_items(token: str, site_id: str, list_id: str):
    url: str | None = f"{GRAPH}/sites/{site_id}/lists/{list_id}/items"
    # 200 is Graph's documented max page size for list items; nextLink paging
    # below collects the rest. (A larger $top can 400 on some tenants.)
    params: dict | None = {"$expand": "fields", "$top": "200"}
    while url:
        data = _graph_get(token, url, params=params)
        for item in data.get("value", []):
            yield item.get("fields", {}) or {}
        url = data.get("@odata.nextLink")
        params = None   # nextLink already carries the query string


def load_mslist_dataframe(mslist_cfg: dict, column_map: dict | None = None,
                          *, token: str | None = None):
    """Read the Microsoft List into a reference DataFrame with canonical columns.
    Raises RuntimeError on any auth/Graph failure (callers fall back to Excel)."""
    import pandas as pd
    if token is None:
        token = acquire_token(mslist_cfg, interactive=False)
    site_id = _resolve_site_id(token, mslist_cfg["site_hostname"], mslist_cfg["site_path"])
    list_id = _resolve_list_id(token, site_id, mslist_cfg.get("list_name", ""),
                               mslist_cfg.get("list_id", ""))
    disp = _column_display_map(token, site_id, list_id)
    fmap = dict(MSLIST_FIELD_MAP)
    fmap.update(column_map or {})

    rows: list[dict[str, Any]] = []
    for fields in _iter_items(token, site_id, list_id):
        row: dict[str, Any] = {}
        for internal, value in fields.items():
            display = disp.get(internal, internal)
            canon = fmap.get(display) or fmap.get(internal)
            if canon and (canon not in row or row[canon] in (None, "")):
                row[canon] = value
        if row.get("Number"):
            rows.append(row)
    if not rows:
        raise RuntimeError("Microsoft List returned no rows with a mappable part number.")
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- CLI (go-live)

def _login_and_probe(override: dict | None = None) -> int:
    cfg = resolve_reference_config(override)
    ml = cfg["mslist"]
    if not (ml.get("client_id") and ml.get("tenant_id")):
        print("client_id / tenant_id are not set. Put them in config.json under "
              "purchasing_reference.mslist, or in purchasing_reference.DEFAULT_CONFIG.")
        return 2
    token = acquire_token(ml, interactive=True)
    site_id = _resolve_site_id(token, ml["site_hostname"], ml["site_path"])
    list_id = _resolve_list_id(token, site_id, ml.get("list_name", ""), ml.get("list_id", ""))
    disp = _column_display_map(token, site_id, list_id)
    print("\nSite + List resolved. List columns (display names):")
    for d in disp.values():
        print(f"   - {d}")
    df = load_mslist_dataframe(ml, cfg.get("column_map"), token=token)
    print(f"\nMapped {len(df)} row(s). Canonical columns: {list(df.columns)}")
    print(df.head(5).to_string())
    return 0


if __name__ == "__main__":
    if "--login" in sys.argv:
        raise SystemExit(_login_and_probe())
    print("usage: python -m purchasing_reference --login")
