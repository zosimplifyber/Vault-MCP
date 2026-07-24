# Design — McMaster price updates into the "Engineering Purchased Parts" Microsoft List

**Date:** 2026-07-24
**Status:** Approved-by-delegation (owner out of office, asked to complete
autonomously). Live steps (write scope, sign-in, field map, McMaster creds)
deferred to owner — see §9.
**Supersedes:** the Excel-file / MiSUMi parts of the earlier
`2026-07-23-supplier-pricing-tool-design.md` (kept for history).

## 1. Goal

Given an **exact McMaster-Carr part number**, fetch its current **unit price** and
write it back into the **"Engineering Purchased Parts"** Microsoft List
(`netorgft6579427.sharepoint.com/sites/Simplifyber`), setting **Lead Time = 1
business day** for every McMaster row. Built into the existing tool (MCP server +
CLI), reusing the repo's `purchasing_reference.py` Graph/auth plumbing.

Scope was narrowed by the owner from the original: **MiSUMi dropped**, **lead time
is a constant 1**, **datasource is the SharePoint List** (read the part numbers to
price *and* the write target) instead of `purchased items.xlsx`.

## 2. What we build on (existing)

`purchasing_reference.py` already does delegated **device-code** MSAL auth
(`PublicClientApplication`, per-user token cache under `%LOCALAPPDATA%/Simplifyber`),
and Graph helpers: `_resolve_site_id`, `_resolve_list_id`, `_column_display_map`
(internal→display), `_iter_items` (paged `items?$expand=fields`). Config lives in
`DEFAULT_CONFIG` / `config.json["purchasing_reference"]` with site host + path.
It is **read-only** (`SCOPES = ["Sites.Read.All"]`).

## 3. New pieces

```
supplier_pricing/                     # McMaster-only pricing
  models.py        # PriceResult, PriceBreak, QuoteLineItem, UpdateOutcome  [done]
  normalize.py     # vendor_family(), normalize_part_number(), loose_part_key() [done]
  providers/
    base.py        # PriceProvider ABC: get_price(part_number) -> PriceResult
    mcmaster.py    # McMasterApiProvider (cert+token, subscribe->price)
                   #  + McMasterBrowserProvider (Playwright fallback, owner login)
                   #  both set lead_time_days = 1
purchasing_update.py                  # the WRITE path (separate from read module)
  # reuses purchasing_reference auth/graph; adds Sites.ReadWrite.All + PATCH
  update_list_item_fields(site_id, list_id, item_id, fields, token)
  iter_list_rows_with_ids(...)        # like _iter_items but yields (item_id, fields)
  resolve_write_field_names(...)      # display "Cost Per"/"Lead Time" -> internal name
  update_mcmaster_prices(cfg, *, dry_run=True, only_missing=False, limit=None)
mcp_server.py (additive tool)         # purchasing_update_mcmaster_prices(...)
supplier_pricing/__main__.py / cli    # price / update-list / probe / login
```

### 3.1 McMaster provider
- **API (preferred):** httpx client with PKCS#12 client cert; `POST /v1/login`
  → 24h bearer; `PUT /v1/products` (subscribe) then `GET /v1/products/{pn}/price`
  → `[{Amount, MinimumQuantity, UnitOfMeasure}]`. Map to `unit_price` (qty-1
  amount) + `price_breaks`. Handle already-subscribed + cap errors. `source="mcmaster:api"`.
- **Browser fallback:** Playwright persistent context (owner's login) →
  `mcmaster.com/<pn>/`, read list price from DOM. `source="mcmaster:web"`.
- Both: `vendor="McMaster-Carr"`, `lead_time_days = 1`.
- Selection: use API when a cert is configured (env/config), else browser.

### 3.2 Write path (`purchasing_update.py`)
- `SCOPES_WRITE = ["Sites.ReadWrite.All"]` (needs app-registration update + consent).
- `update_list_item_fields`: `PATCH {GRAPH}/sites/{site}/lists/{list}/items/{id}/fields`
  with a JSON body of `{internalName: value}`; 200/204 = success.
- Field mapping: introspect columns (`_column_display_map` gives internal→display);
  invert + fuzzy-match to find the internal names for the price and lead-time
  columns. Config override `write_field_map` (display→internal) for certainty.
- Target list defaults to **"Engineering Purchased Parts"** (config
  `update.list_name`), on the same site as the read reference.

### 3.3 Orchestrator `update_mcmaster_prices`
1. Acquire a write-scoped token; resolve site + target list id.
2. Page the list rows *with item ids*; for each row whose **Vendor** normalizes to
   `mcmaster` and has a non-empty **Vendor Number**:
   - (optional) skip if `only_missing` and a price already present.
   - `provider.get_price(vendor_number)`.
3. Build a per-row plan: `{item_id, part, current, new_price, lead_time=1, status}`.
4. **dry_run (default):** return the plan, write nothing. **apply:** PATCH each
   matched row's price + lead-time fields; collect successes/failures.
5. Return a structured report (counts + per-row rows + warnings). Vendor rows with
   no price found or ambiguous fields are reported, never guessed.

## 4. Interfaces

- **MCP tool** (`mcp_server.py`, additive): `purchasing_update_mcmaster_prices(dry_run=True, only_missing=False, limit=None)` → report dict. Default dry-run; the
  human confirms before an `apply` call.
- **CLI:** `python -m supplier_pricing price <pn>` (print a PriceResult);
  `... update-list [--apply] [--only-missing] [--limit N]`;
  `... probe` (device-code sign-in, print the list's display+internal columns);
  `... login-mcmaster` (browser fallback session).

## 5. Config additions (`config.json["purchasing_reference"]` reuse + new block)

```json
"supplier_pricing": {
  "update": { "list_name": "Engineering Purchased Parts" },
  "write_field_map": { "Cost Per": "", "Lead Time (Business Days)": "" },
  "mcmaster": {
    "api_cert": "", "api_cert_password": "",
    "api_user": "", "api_password": "",
    "user_data_dir": ""
  }
}
```
Empty McMaster cert ⇒ browser fallback. Empty `write_field_map` ⇒ auto-detect.

## 6. Safety

- **Dry-run by default** everywhere; writing requires explicit `--apply`/`dry_run=False`.
- Writes touch **only** the price + lead-time fields of matched McMaster rows.
- Isolated on branch `feat/mcmaster-list-pricing`; does not modify files the
  parallel `main` work is editing (e.g. `gui/purchasing.py`). `mcp_server.py`
  wiring is additive and kept minimal.

## 7. Testing (no live network)

- **normalize / models:** done (21 tests green).
- **McMaster provider:** mock httpx — assert login→subscribe→price sequence, price
  + breaks parsing, error paths; assert `lead_time_days == 1`. Browser provider
  tested against a saved product-page HTML fixture via a fake page object.
- **purchasing_update:** monkeypatch token + httpx Graph; assert PATCH body targets
  the right internal field names and item ids; assert dry-run writes nothing;
  assert only McMaster rows with a Vendor Number are planned; assert paging.

## 8. Out of scope (for now)

MiSUMi; lead-time scraping; quote-file ingestion (planned follow-on, same List
sink); a GUI sign-in dialog (device-code prints to console, per existing pattern).

## 9. Owner go-live checklist

1. Add **`Sites.ReadWrite.All`** (delegated) to the Entra app registration + admin
   consent; run `python -m supplier_pricing probe` to sign in with write scope.
2. `probe` prints the "Engineering Purchased Parts" columns → we confirm/set
   `write_field_map` for the price + lead-time columns.
3. Provide McMaster API `.pfx` cert + password (official API), **or** run
   `login-mcmaster` once (browser fallback).
4. Run `update-list` (dry-run) to review, then `--apply`.
