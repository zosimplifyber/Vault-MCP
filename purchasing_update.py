"""
Write McMaster-Carr prices back into the "Engineering Purchased Parts" Microsoft
List.

This is the **write** counterpart to ``purchasing_reference.py`` (which is
read-only by design). It reuses that module's MSAL auth + Graph plumbing, adds
the ``Sites.ReadWrite.All`` scope, and PATCHes list-item fields.

Design
------
* ``build_field_index`` and ``update_mcmaster_prices`` are pure orchestration and
  are unit-tested with a fake client (no network) — mirroring how the read module
  mocks Graph.
* ``GraphListClient`` is the live network object; ``connect`` resolves a
  write-scoped token + the target site/list. It reuses ``purchasing_reference``'s
  private cache/resolve helpers rather than re-editing that (actively developed)
  module.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Iterator

import httpx

import purchasing_reference as pref
from supplier_pricing.normalize import vendor_family, normalize_part_number

GRAPH = pref.GRAPH
SCOPES_WRITE = ["Sites.ReadWrite.All"]
DEFAULT_UPDATE_LIST = "Engineering Purchased Parts"

# Role -> set of accepted display names (lower-cased) for auto-detecting the
# list's columns.  Overridable per install via config `write_field_map`.
_ROLE_ALIASES: dict[str, set[str]] = {
    "number": {"number", "part number", "partnumber", "title", "item number"},
    "vendor": {"vendor", "supplier"},
    "vendor_number": {"vendor number", "vendor #", "vendor no", "vendor no.",
                      "supplier number", "supplier #", "mfr part number"},
    "cost": {"cost per", "cost", "unit cost", "price", "unit price"},
    "lead": {"lead time (business days)", "lead time", "lead", "lead time days",
             "lead time (days)"},
}


def _role_for_display(display: str) -> str | None:
    key = (display or "").strip().lower()
    for role, aliases in _ROLE_ALIASES.items():
        if key in aliases:
            return role
    return None


def build_field_index(display_map: dict[str, str],
                      overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Map canonical roles -> the list's *internal* field names.

    ``display_map`` is Graph's {internal name -> display name}.  ``overrides`` is
    {display name -> internal name}, letting an install pin a column exactly.
    """
    index: dict[str, str] = {}
    for internal, display in (display_map or {}).items():
        role = _role_for_display(display)
        # First match wins, except "number" must not shadow a real vendor_number.
        if role and role not in index:
            index[role] = internal
    for display, internal in (overrides or {}).items():
        role = _role_for_display(display)
        if role:
            index[role] = internal
    return index


def _has_value(v: Any) -> bool:
    return v is not None and str(v).strip() != ""


def update_mcmaster_prices(client, provider, *, dry_run: bool = True,
                           only_missing: bool = False, limit: int | None = None,
                           field_overrides: dict[str, str] | None = None) -> dict:
    """Plan (and optionally apply) McMaster price writes to the list.

    ``client`` implements ``column_display_map()``, ``iter_rows()`` (yielding
    ``(item_id, fields)``), and ``patch_fields(item_id, fields)``.
    ``provider`` implements ``get_price(part_number) -> PriceResult``.
    Returns a structured report; with ``dry_run`` nothing is written.
    """
    disp = client.column_display_map()
    idx = build_field_index(disp, field_overrides)

    warnings: list[str] = []
    for required in ("vendor", "vendor_number", "cost"):
        if required not in idx:
            warnings.append(f"Could not find the '{required}' column in the list.")
    if "lead" not in idx:
        warnings.append("No lead-time column found; lead time will not be written.")

    rows: list[dict] = []
    counts = {"scanned": 0, "mcmaster": 0, "priced": 0, "not_found": 0,
              "no_vendor_number": 0, "skipped_has_price": 0, "skipped_limit": 0}
    applied = 0

    # Can't proceed without the essential columns.
    if any(r not in idx for r in ("vendor", "vendor_number", "cost")):
        return {"rows": rows, "counts": counts, "applied": applied,
                "warnings": warnings, "field_index": idx, "dry_run": dry_run}

    priced = 0
    for item_id, fields in client.iter_rows():
        counts["scanned"] += 1
        vendor_val = fields.get(idx["vendor"])
        if vendor_family(vendor_val) != "mcmaster":
            continue
        counts["mcmaster"] += 1
        vnum = fields.get(idx["vendor_number"])
        row = {
            "item_id": item_id,
            "number": fields.get(idx.get("number", ""), None),
            "vendor": vendor_val,
            "vendor_number": normalize_part_number(vnum) if _has_value(vnum) else None,
            "current_price": fields.get(idx["cost"]),
            "new_price": None,
            "lead_time_days": None,
            "status": None,
        }

        if not _has_value(vnum):
            row["status"] = "no_vendor_number"
            counts["no_vendor_number"] += 1
            rows.append(row)
            continue

        if only_missing and _has_value(fields.get(idx["cost"])):
            row["status"] = "skipped_has_price"
            counts["skipped_has_price"] += 1
            rows.append(row)
            continue

        if limit is not None and priced >= limit:
            row["status"] = "skipped_limit"
            counts["skipped_limit"] += 1
            rows.append(row)
            continue

        result = provider.get_price(row["vendor_number"])
        priced += 1
        if not result.ok():
            row["status"] = "not_found"
            counts["not_found"] += 1
            rows.append(row)
            continue

        lead = result.lead_time_days if result.lead_time_days is not None else 1
        row["new_price"] = result.unit_price
        row["lead_time_days"] = lead
        row["status"] = "priced"
        counts["priced"] += 1

        if not dry_run:
            patch: dict[str, Any] = {idx["cost"]: result.unit_price}
            if "lead" in idx:
                patch[idx["lead"]] = lead
            client.patch_fields(item_id, patch)
            applied += 1

        rows.append(row)

    return {"rows": rows, "counts": counts, "applied": applied,
            "warnings": warnings, "field_index": idx, "dry_run": dry_run}


# --------------------------------------------------------------------------- live

def acquire_write_token(mslist_cfg: dict, *, interactive: bool = False,
                        printer=print) -> str:
    """Graph token with WRITE scope, reusing purchasing_reference's token cache."""
    if not interactive and not os.path.isfile(pref._token_cache_path()):
        raise RuntimeError(
            "Not signed in with write scope. Run: python -m supplier_pricing probe"
        )
    import msal
    cache = pref._load_cache()
    app = msal.PublicClientApplication(
        mslist_cfg["client_id"],
        authority=f"https://login.microsoftonline.com/{mslist_cfg['tenant_id']}",
        token_cache=cache,
    )
    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES_WRITE, account=accounts[0])
    if not result:
        if not interactive:
            raise RuntimeError(
                "Not signed in with write scope. Run: python -m supplier_pricing probe"
            )
        flow = app.initiate_device_flow(scopes=SCOPES_WRITE)
        if "user_code" not in flow:
            raise RuntimeError(
                f"Could not start device-code sign-in: {flow.get('error_description', flow)}"
            )
        printer(flow["message"])
        result = app.acquire_token_by_device_flow(flow)
    pref._save_cache(cache)
    if "access_token" not in result:
        raise RuntimeError(f"Sign-in failed: {result.get('error_description', result)}")
    return result["access_token"]


class GraphListClient:
    """Live Graph client for one SharePoint list (read rows + PATCH fields)."""

    def __init__(self, token: str, site_id: str, list_id: str):
        self.token = token
        self.site_id = site_id
        self.list_id = list_id

    @classmethod
    def connect(cls, cfg: dict | None = None, *, list_name: str | None = None,
                interactive: bool = False, token: str | None = None) -> "GraphListClient":
        cfg = pref.resolve_reference_config(cfg)
        ml = cfg["mslist"]
        target = list_name or DEFAULT_UPDATE_LIST
        token = token or acquire_write_token(ml, interactive=interactive)
        site_id = pref._resolve_site_id(token, ml["site_hostname"], ml["site_path"])
        list_id = pref._resolve_list_id(token, site_id, target, "")
        return cls(token, site_id, list_id)

    def column_display_map(self) -> dict[str, str]:
        return pref._column_display_map(self.token, self.site_id, self.list_id)

    def iter_rows(self) -> Iterator[tuple[str, dict]]:
        url: str | None = f"{GRAPH}/sites/{self.site_id}/lists/{self.list_id}/items"
        params: dict | None = {"$expand": "fields", "$top": "200"}
        while url:
            data = pref._graph_get(self.token, url, params=params)
            for item in data.get("value", []):
                yield item.get("id"), (item.get("fields") or {})
            url = data.get("@odata.nextLink")
            params = None

    def patch_fields(self, item_id: str, fields: dict) -> None:
        url = f"{GRAPH}/sites/{self.site_id}/lists/{self.list_id}/items/{item_id}/fields"
        headers = {"Authorization": f"Bearer {self.token}",
                   "Content-Type": "application/json"}
        with httpx.Client(timeout=30) as client:
            resp = client.patch(url, headers=headers, json=fields)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Graph PATCH {resp.status_code} for item {item_id}: {resp.text[:200]}"
            )

    def create_list_item(self, fields: dict) -> dict:
        """POST a new list item. `fields` is keyed by internal field name."""
        url = f"{GRAPH}/sites/{self.site_id}/lists/{self.list_id}/items"
        headers = {"Authorization": f"Bearer {self.token}",
                   "Content-Type": "application/json"}
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, headers=headers, json={"fields": fields})
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Graph POST item {resp.status_code}: {resp.text[:200]}")
        return resp.json()


def probe(printer=print) -> int:
    """Device-code sign-in (write scope) + print the target list's columns."""
    cfg = pref.resolve_reference_config(None)
    ml = cfg["mslist"]
    token = acquire_write_token(ml, interactive=True, printer=printer)
    client = GraphListClient.connect(cfg, interactive=False, token=token)
    disp = client.column_display_map()
    printer(f"\nList '{DEFAULT_UPDATE_LIST}' columns (display -> internal):")
    for internal, display in disp.items():
        printer(f"   - {display!r:40} -> {internal}")
    printer(f"\nDetected field roles: {build_field_index(disp)}")
    return 0


if __name__ == "__main__":
    if "--probe" in sys.argv:
        raise SystemExit(probe())
    print("usage: python -m purchasing_update --probe")
