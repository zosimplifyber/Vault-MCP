# supplier_pricing — McMaster prices → "Engineering Purchased Parts" Microsoft List

Look up **McMaster-Carr** unit prices by exact part number and write them into the
**Engineering Purchased Parts** SharePoint/Microsoft List (`Cost Per`), setting
**Lead Time = 1 business day** for every McMaster row. Built on the repo's existing
`purchasing_reference.py` (Microsoft Graph + MSAL device-code auth).

> Scope note: MiSUMi and Excel-file output from the earlier draft were dropped per
> owner request. See `docs/superpowers/specs/2026-07-24-mcmaster-list-pricing-design.md`.

## What it does

- **Price a part** — McMaster **official API** when a client cert is configured,
  otherwise a **browser fallback** using your mcmaster.com login. Always reports
  `lead_time_days = 1` (McMaster ships same-day; the API exposes no lead time).
- **Update the List** — reads every row whose Vendor normalizes to *McMaster* and
  has a Vendor Number, prices it, and PATCHes `Cost Per` + `Lead Time`.
  **Dry-run by default** — nothing is written until you pass `--apply`.

## Modules

| File | Role |
|------|------|
| `normalize.py` | vendor-name + part-number matching keys |
| `models.py` | `PriceResult`, `PriceBreak`, `QuoteLineItem`, `UpdateOutcome` |
| `providers/mcmaster.py` | `McMasterApiProvider` (cert+token) / `McMasterBrowserProvider` |
| `../purchasing_update.py` | Graph write path + `update_mcmaster_prices` orchestrator |
| `cli.py` / `mcp_tools.py` | CLI and MCP tool wiring |

## CLI

```bash
python -m supplier_pricing price 1078A331            # one lookup (JSON)
python -m supplier_pricing probe                     # sign in (write scope) + list columns
python -m supplier_pricing update-list               # DRY RUN — plan only
python -m supplier_pricing update-list --only-missing --limit 5   # first cautious pass
python -m supplier_pricing update-list --apply       # actually write to the List
python -m supplier_pricing login-mcmaster            # browser-fallback sign-in
```

## MCP tools (registered automatically in `mcp_server.py`)

- `purchasing_get_mcmaster_price(part_number, qty=1)`
- `purchasing_update_mcmaster_prices(dry_run=True, only_missing=False, limit=None)`

## Config (`config.json`, optional `supplier_pricing` block)

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
- Empty McMaster `api_cert` ⇒ browser fallback (the expected default).
- Empty `write_field_map` values ⇒ the tool auto-detects the columns; fill them in
  from `probe` output if auto-detect picks the wrong column.

---

## ⚠️ Go-live checklist (needs the owner — one time)

1. **Grant write scope.** The current Entra app registration only has
   `Sites.Read.All`. Add **delegated `Sites.ReadWrite.All`** + admin consent.
2. **Sign in with write scope + confirm columns:**
   ```bash
   python -m supplier_pricing probe
   ```
   Sign in at `microsoft.com/devicelogin` with the code shown. It prints the
   "Engineering Purchased Parts" columns (display → internal) and the detected
   field roles. If `Cost Per` / `Lead Time` weren't detected correctly, set them
   in `write_field_map` (display → internal name from the probe output).
3. **Enable McMaster pricing** — the **browser fallback works out of the box**:
   McMaster shows list prices **without a login**, and the price selector is
   confirmed live (`price 1078A331` -> $7.08). Just make sure Playwright + its
   browser are installed:
   ```bash
   python -m pip install playwright && python -m playwright install chromium
   ```
   No `login-mcmaster` needed for list pricing (it only helps if you want
   contract pricing tied to your account).
   - **Official API (optional):** obtain API access (email
     eprocurement@mcmaster.com — approval-gated) and put the cert/creds in
     `config.json` for contract pricing. A `.pfx` cert may need PEM conversion —
     ping me to finalize.
4. **Dry-run, then apply:**
   ```bash
   python -m supplier_pricing update-list --only-missing --limit 5   # review
   python -m supplier_pricing update-list --apply                    # write
   ```

## Tests

```bash
python -m pytest tests/supplier_pricing/ -q
```
No network: Graph and the McMaster API are mocked. Live end-to-end is the go-live
steps above.

## Not done yet (planned follow-on)

Dropping supplier **quote files** (PDF/Excel) to auto-fill prices+lead times into
the same List. The value objects (`QuoteLineItem`) exist; the parser is the next
piece.
```
