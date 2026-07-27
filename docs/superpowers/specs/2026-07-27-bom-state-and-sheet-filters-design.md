# Design — Vault state on the purchasing sheet, outsourced Make parts, sheet filters

**Date:** 2026-07-27
**Status:** Approved by owner (2026-07-27).

## Background & goal

Three owner requests against the BOM → Purchasing Sheet output:

1. Show each part's **Vault release state** on the sheet.
2. Include **Make parts on the By Vendor tab** when they are outsourced.
3. Put an **auto-filter on every column** of the exported workbook.

### What the owner originally asked for, and what was decided

The original request was to have Inventor's **"Design State" iProperty** carry the
Vault state, so the state rides along in any BOM. Two findings changed the shape:

- Their Vault has **no "Design State" property definition**. The state-ish
  property definitions are `Designer`, `Design Reference File`, `Archive Status`,
  `Fusion Manage Sync Status`, `Fusion Manage Link State`. "Design State" is the
  **Inventor** iProperty (Status tab), which Vault only writes if an admin creates
  a Behaviors → Properties mapping with write-back enabled. That standard Inventor
  field is also an **enum** (Work in Progress / Pending / Released / Obsolete), so
  arbitrary Vault state names may not be writable into it — a custom iProperty
  would be the safer target.
- **Every Vault file search result already carries the state.** A `search_files`
  hit includes `state`, plus a full `lifecycleState` object (`name`,
  `isReleasedState`, `displayOrder`, `color`). No per-file round trip is needed.

The owner chose the **live Vault lookup at export time**. The iProperty
write-back mapping is explicitly **out of scope** here and remains available as a
follow-up; this design does not modify any Inventor file.

## Scope

- **In:** a Vault state lookup module; restoring the `State` column on the
  purchasing sheet; By Vendor including vendor-bearing Make rows; an auto-filter
  on the Purchasing tab; tests for all three.
- **Out:** Vault Behaviors property mapping / iProperty write-back; any write to
  Vault or to CAD files; Inventor COM automation; changing the Assembly Costs tab.

---

## 1. Vault state on the sheet

### New module `vault_state.py`

```python
lookup_file_states(numbers: list[str]) -> tuple[dict[str, str], list[str]]
    # ({normalized part number: state name}, warnings)
```

- Reads the `vault` block of `config.json`. **Missing config → return immediately**
  with an empty map and one warning. The standalone `.exe` ships without
  `config.json`, so this is its normal path: no import cost, no network, no delay.
- `vault_rest_api` is imported **lazily inside the function**, matching how
  `purchasing_reference` defers `msal`.
- One `search_files` call per unique part number, run concurrently with a small
  cap (8) so a 300-row BOM does not open 300 sockets.
- **Result filtering is the important part.** Vault's search is keyword-based and
  matches properties, not just names: searching `CD-001582` returns
  `CD-001582 BOM.xlsx`, `CD-001582.iam`, **and `SF-001915`** — an unrelated file.
  A hit only counts when its name matches the key (see "Which file" below).
  Among the survivors, prefer `.iam`/`.ipt`, then `.idw`, then anything else.
  No match → no state (blank), never a guess.

### Which key, and which file (revised 2026-07-27 after live testing)

The first cut searched the BOM's Part Number and matched on an exact name stem.
Live runs found three ways that goes wrong, all fixed:

1. **The Part Number is the item number, not the file.** `SF-001922` matches the
   *ItemVersion*, which is Work in Progress, while its CAD file `CD-001578.ipt`
   is Released — so the sheet reported the wrong state for every Make row. The
   export now carries a **`Filename`** column; that is searched first, then the
   **`Title`** column (which holds the CD- number), then the part number.
   Accepted headers: `File Name`, `Filename`, `File`, `Document Name`.
2. **Items and folders must be excluded.** `search_files` returns ItemVersions
   and folders; a folder named exactly `ISO 4762 - M6 x 16 - Stainless Steel`
   outranked the real `.ipt`. Only entities that are files *and* carry a state
   qualify.
3. **File names are not part numbers.** `DIN 934 - M5` is stored as
   `DIN 934 - M5 x 0.8.ipt`; `ISO 4762 - M6 x 50 - Stainless Steel` as
   `ISO 4762 - M6 x 50 Stainless Steel.ipt` (no dash). Matching runs in three
   passes, each tried only if the previous found nothing: **exact** name/stem,
   **loose** (punctuation- and case-insensitive), then **separated prefix**,
   restricted to CAD extensions so `CD-001582` cannot match
   `CD-001582 BOM.xlsx`. The boundary requirement stops `M6 x 1` from matching
   `M6 x 10`.

When several equally-ranked files disagree about the state — the duplicated
library fasteners, e.g. `ISO 4762 - M6 x 10 Stainless Steel.ipt` (WIP) beside
`ISO 4762 - M6 x 10ISO Stainless Steel.ipt` (Released) — the lookup returns
nothing. No rule can pick correctly there; only de-duplicating the library can.

**Consequence:** a part whose only Vault entity is an item now shows a blank
State rather than the item's state. That is deliberate — the column reports the
file's state.
- Keys on `normalize_part_number`, the same key the reference lookup uses.
- **Never raises.** Offline, bad credentials, Vault down, timeout → empty map plus
  a warning that flows into the existing `warnings` list on the result.

### Sync/async boundary

`vault_rest_api` is async; `generate_from_file` is sync and is called from both a
Tk worker thread and from inside a running event loop (the MCP tool
`vault_generate_purchasing_sheet` is `async def`). A bare `asyncio.run` would
raise `RuntimeError: asyncio.run() cannot be called from a running event loop`.

The module wraps its coroutine: if no loop is running, `asyncio.run`; if one is,
run it in a one-shot worker thread with its own loop. The wrapper takes a
**coroutine factory**, not a coroutine object, so nothing is ever created against
the wrong loop.

### Wiring into `bom_purchasing.py`

- Restore `State` to `BOM_COLUMNS`, `COLUMN_WIDTHS`, and `VAULT_FIELD_MAP` — a
  revert of `65f9d05`. A Vault-sourced BOM then fills the column natively, and the
  column returns to its original slot after `Revision`.
- After reference enrichment, fill `State` **only where blank**, the same
  precedence rule `Material` already uses: data that came with the BOM wins.
- Non-Released rows get **no special styling**. The sheet already colors
  assemblies olive and unpriced Buy rows orange; a third color is noise. Trivial
  to add later.

## 2. By Vendor tab: outsourced Make parts

`_build_vendor_tab` filters to `Source in {"Buy", "Other"}` **and** a non-blank
Vendor. Dropping the Source filter and keeping the Vendor filter is the whole
change: a Make part with a vendor (machine shop, laser cutter) appears under that
vendor; a Make part with no vendor is excluded automatically. Grouping, quantity
roll-up, and the Line Total formula are untouched.

## 3. Auto-filter

The By Vendor tab already sets `ws.auto_filter.ref`. The Purchasing tab does not —
add it from the header row (row 3) through the last data row, so the
"Unmatched (n)" note below stays outside the range.

The Assembly Costs tab is deliberately skipped: it is a four-column summary ending
in a merged GRAND TOTAL row, which a filter range would either swallow or
awkwardly exclude.

**Known consequence:** filtering is safe; **sorting is not**. The Purchasing tab is
hierarchical and its assembly roll-ups (`=SUM(P7,P9,P12)`) reference absolute row
numbers. Sorting through a filter dropdown reorders rows without rewriting
formulas, silently attaching totals to the wrong parts.

---

## Testing

Test-first for each change; no test touches the network.

| Area | Test |
|---|---|
| State lookup | name-stem match wins over a keyword-only hit (`SF-001915` for query `CD-001582`) |
| State lookup | `.iam` preferred over `.xlsx` for the same number |
| State lookup | an ItemVersion never lends its state to a file |
| State lookup | a folder does not mask the real `.ipt` |
| State lookup | `Filename` beats `Title` beats Part Number as the search key |
| State lookup | interior punctuation ignored; a typo'd duplicate still excluded |
| State lookup | `M6 x 1` does not match `M6 x 10`; equal-rank disagreement → blank |
| State lookup | no `vault` config → empty map + warning, no import of `vault_rest_api` |
| State lookup | a raised error inside the lookup degrades to a warning |
| Sheet wiring | a BOM that already carries State keeps its own value |
| Sheet wiring | blank State is filled from the lookup (monkeypatched) |
| By Vendor | a Make row with a Vendor appears; a Make row without one does not |
| By Vendor | Buy rows are unchanged |
| Auto-filter | Purchasing tab `auto_filter.ref` covers header → last data row, excludes the note row |

## Verification

Reference BOM: **`C:\Vault Workspace\DESIGNS\PRODUCTION EQUIPMENT\CD-001582 MFG BOM.xlsx`**
— a Structured/All-Levels Inventor export carrying `Filename`, so it exercises
the whole key chain. Regenerate it against live Vault and confirm each row's
state matches the file the REST API reports it resolved to (`SF-001922` →
`CD-001578.ipt` → Released), then rebuild the standalone `.exe` and confirm it
still runs with no `config.json` (blank State column, warning, no hang).
