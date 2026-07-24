# Design — Purchasing reference source: Microsoft List (via Graph) instead of the Excel file

**Date:** 2026-07-24
**Status:** Approved (self-approved per owner delegation — owner out of office). Live
verification pending the owner's Entra app registration + a one-time sign-in.

## Background & goal

The purchasing sheet auto-fills Vendor / Cost / Material / lead-time by matching
BOM part numbers against a reference of purchased items. Today that reference is
an Excel file (`purchased items.xlsx`) discovered on OneDrive. The owner wants the
reference to come from a **Microsoft List** in SharePoint instead:

`https://netorgft6579427.sharepoint.com/sites/Simplifyber` — a List (share link
provided). Tenant host: `netorgft6579427.sharepoint.com`; site path `/sites/Simplifyber`.

A Microsoft List is only reachable through **Microsoft Graph**, which requires an
OAuth token. Per the owner's decision, the tool uses **delegated device-code
sign-in** (each user signs in with their Simplifyber M365 account; no shared secret
— safe for the distributed `.exe`).

## Scope

- **In:** a pluggable reference-data loader with an Excel backend (today) and a
  Microsoft List backend (MSAL device-code → Graph); config + non-secret bundled
  defaults; `msal` dependency; graceful fallback to Excel; mock tests; docs (app
  registration + go-live). Benefits both the MCP tool and the standalone `.exe`.
- **Out:** writing back to the List (read-only `Sites.Read.All`); any Vault change;
  a GUI sign-in dialog (device-code prints a code + URL to the console/log for now).

## Hard external prerequisite (owner-supplied)

An **Entra app registration** (single-tenant, public client):
- Delegated permission **`Sites.Read.All`** with admin consent.
- **"Allow public client flows" = Yes** (enables device code).
- The owner provides the **Application (client) ID** and **Directory (tenant) ID**.
  These are **not secrets** (public-client id) — safe to bundle in the `.exe`.

Until a client id is configured, the tool **stays on Excel** (no behavior change),
so this ships safely before the app registration exists.

---

## Architecture

New module **`purchasing_reference.py`** owns reference loading. It returns a
pandas DataFrame whose columns use the **existing canonical names** so the
downstream `lookup_purchased_data(bom_df, ref_df)` is unchanged: a key column
named `Number` (or `Part Number`), plus the `LOOKUP_COLUMNS`
(`Material, Vendor, Vendor Number, Cost Per, HS/HTS Code, Shipping, Tax/Tariff,
Lead Time (Business Days)`).

### Public API
```
load_reference_dataframe(config: dict | None = None,
                         excel_path: str = "") -> tuple[pd.DataFrame | None, str, list[str]]
    # returns (df_or_None, source_label, warnings)
```
Selection logic:
1. If `excel_path` is a real file → **Excel** from that path (explicit override wins).
2. Else if the MS List is configured (a non-placeholder `client_id`) → **MS List**;
   on any failure, log a warning and fall through to step 3.
3. Else → **Excel** auto-discovery (`find_purchased_items_file`).

### MS List backend
- **Auth:** `msal.PublicClientApplication(client_id, authority=https://login.microsoftonline.com/{tenant_id})`.
  Try `acquire_token_silent` against a persisted `SerializableTokenCache`
  (`%LOCALAPPDATA%/Simplifyber/purchasing_msal_cache.bin`); if none, run
  `initiate_device_flow` + `acquire_token_by_device_flow` (prints the
  "go to microsoft.com/devicelogin, enter CODE" message). Scope: `["Sites.Read.All"]`.
- **Graph (httpx):**
  1. Site id: `GET /v1.0/sites/{host}:{site_path}` → `site.id`.
  2. List id: `GET /v1.0/sites/{site-id}/lists?$filter=displayName eq '{list_name}'`
     (or configured `list_id`).
  3. Columns: `GET /v1.0/sites/{site-id}/lists/{list-id}/columns` → build a
     {internal `name` → displayName} map (Graph item `fields` are keyed by internal name).
  4. Items: `GET /v1.0/sites/{site-id}/lists/{list-id}/items?$expand=fields&$top=999`,
     following `@odata.nextLink` for paging. Each `item.fields` → a row.
- **Mapping:** translate each field's display name through `MSLIST_FIELD_MAP`
  (display name → canonical ref column) into the reference DataFrame. Defaults cover
  likely names (`Part Number`/`Title` → `Number`, `Vendor`, `Cost`/`Cost Per` →
  `Cost Per`, `Material`, `Vendor Number`, `Lead Time` → `Lead Time (Business Days)`,
  `HS Code`/`HS/HTS Code`, `Shipping`, `Tax`/`Tax/Tariff`). Overridable via
  `config['purchasing_reference']['column_map']`. The exact map is **confirmed live**
  once we can introspect the real list (see Go-live).

### Excel backend
Unchanged: `find_purchased_items_file()` + `load_reference_file()`.

### Integration point
`_enrich_with_reference(df, reference_path="")` (in `bom_purchasing.py`) delegates
to `purchasing_reference.load_reference_dataframe(config, excel_path=reference_path)`
and keeps the existing `lookup_purchased_data` + unmatched logic. It threads any
loaded config (see below). A `None` DataFrame (no reference at all) preserves
today's "reference not found → warning, columns blank" behavior.

## Configuration

`config.json` gains an optional block; the standalone bundles the same values as
**code defaults** (non-secret) so the `.exe` works without config.json:
```json
"purchasing_reference": {
  "source": "auto",                        // "auto" | "excel" | "mslist"
  "mslist": {
    "tenant_id": "",                       // Directory (tenant) ID (owner supplies)
    "client_id": "",                       // Application (client) ID (owner supplies)
    "site_hostname": "netorgft6579427.sharepoint.com",
    "site_path": "/sites/Simplifyber",
    "list_name": "Purchased Items"         // or "list_id"
  },
  "column_map": {}                          // optional display-name -> canonical overrides
}
```
- `source: "auto"` (default) = List when `client_id` is set, else Excel.
- Empty `client_id` ⇒ Excel (safe default before the app reg exists).
- Defaults live in `purchasing_reference.DEFAULT_CONFIG`; `app.py`/`load_config`
  passes `config` through to the purchasing tools; the standalone imports the
  defaults directly.

## Requirements / packaging

- Add `msal>=1.30.0` to `requirements.txt`; add `msal` to the PyInstaller install
  list in `build_purchasing_exe.bat` (so the `.exe` bundles it).

## Testing / verification

- **Mock unit tests** (no network): monkeypatch the token acquisition and the
  httpx Graph calls to return canned site/list/columns/items JSON; assert the
  resulting DataFrame has canonical columns and correct values; assert paging
  (`@odata.nextLink`) is followed; assert selection + **fallback to Excel** on a
  Graph error and on missing `client_id`.
- **Regression:** existing `lookup_purchased_data` / `generate_from_file` tests stay
  green; with no List configured, behavior is byte-for-byte the Excel path.
- **Live verification (owner):** a small CLI — `python -m purchasing_reference --login`
  — runs the device-code sign-in, resolves the site/list, prints the discovered
  columns + first few rows. The owner runs it once after creating the app reg and
  filling in the IDs; we finalize `MSLIST_FIELD_MAP` from the printed columns.

## Rollout / safety

- Ships **inert** (Excel path unchanged) until a `client_id` is configured — so this
  merge changes nothing for current users/the `.exe` until go-live.
- List failures never break sheet generation — they downgrade to Excel + a warning
  surfaced in the result `warnings`.

## Out of scope / notes

- No token/secret is committed; only the non-secret client/tenant IDs (once provided)
  live in config/defaults. The MSAL token cache is per-user under `%LOCALAPPDATA%`.
- A polished in-GUI sign-in (vs console device-code text) is a possible follow-up.
