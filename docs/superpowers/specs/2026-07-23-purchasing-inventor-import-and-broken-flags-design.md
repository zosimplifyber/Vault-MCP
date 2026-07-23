# Design — Inventor BOM → Purchasing (with assembly costing) + flag Item-Master tools

**Date:** 2026-07-23
**Status:** Approved (design); pending spec review before implementation planning

## Background & motivation

Simplifyber is retiring the Vault **Item Master** and moving to file-based
tracking via **CAD BOM + file iProperties**. Investigation this session
confirmed the current item-based BOM → Purchasing export is effectively broken
and the item path is a dead end for purchasing:

- `vault_generate_purchasing_sheet` runs `search_items → get_item_bom →
  generate_from_vault_bom`. For `CD-001434` it produced a sheet with
  **0 matched parts** and warnings that `Number` and `Item Qty` did not map.
- Root cause: the item BOM returns child fields as lowercase system names
  (`number`, `title`) plus a `properties[]` **list** of
  `{definition, propertyDefinitionId, value}` objects. `VAULT_FIELD_MAP` keys
  are Title-Case (`"Number"`, `"Item Qty"`), and `vault_bom_to_dataframe`
  only flattens one level of top-level **dicts** — it never reads the
  `properties[]` list. So nothing mapped.
- Critically, **the item BOM response carries no quantity or position field
  at all** (verified: 22 top-level keys + 49 property entries, none a
  quantity). The CAD file-uses endpoint (`get_file_uses`) also returns **no
  quantity/position/row-order**. So neither Vault REST path yields BOM
  quantities.

An **Inventor BOM export** does include Part Number **and** quantity, so it is
the pragmatic, file-based source that gets purchasing working now without the
Item Master. This design uses it. The full CAD/iProperty rewrite of the other
tools is deferred (see below).

## Scope

Two deliverables now; one explicitly deferred.

1. **Inventor BOM import → purchasing sheet with per-assembly costing.**
2. **Flag the Item-Master-dependent tools as broken in the launcher GUI.**
3. **Deferred (separate spec):** CAD/iProperty rewrite of Release Workflow and
   MFG Order Package, and disposition of Property Check.

---

## Deliverable 1 — Inventor BOM import + assembly costing

### 1.1 Import & header auto-detection

Enhance the existing file-import path (`bom_purchasing.generate_from_file`,
surfaced by `vault_generate_purchasing_sheet_from_file` and the purchasing GUI
file mode) to accept an Inventor BOM export.

- **File types:** `.xlsx`, `.xls`, `.csv`, and **tab-delimited `.txt`**
  (the current importer rejects `.txt`; the sample export is tab-delimited).
  Read by extension: `.csv` → comma; `.txt` → tab; `.xls/.xlsx` → Excel.
- **Auto-detection:** after reading, strip/normalize headers.
  - If all canonical `BOM_COLUMNS` are present → treat as a Vault export
    (existing behavior, unchanged).
  - Otherwise → apply the Inventor header map (below). Any canonical column
    still absent is filled with `None` instead of erroring (today the importer
    hard-errors on any missing column).
  - After mapping, require the two **critical** columns `Number` and
    `Item Qty`; if either is missing, return a clear error naming what was
    found vs expected.

- **Inventor → canonical header map** (`INVENTOR_FIELD_MAP`, case-insensitive):

  | Inventor export column | Canonical column |
  |---|---|
  | `Item` | `Row Order` |
  | `Part Number` | `Number` |
  | `QTY` | `Item Qty` |
  | `Unit QTY` | `Units` |
  | `BOM Structure` | `Source` |
  | `Description` | `Description (Item,CO)` |
  | `REV` | `Revision` |
  | `Material` | `Material` |
  | `Material Finish` | `Material Finish` (new column) |
  | `Thumbnail`, `Stock Number` | ignored |
  | (`Category Name`, `State`, `Title (Item,CO)`, `Position Number`) | blank if absent |

- **`Material` precedence:** when the export supplies `Material`, it wins;
  fall back to the reference file's Material only when the export value is
  blank. `Material Finish` is a **new** display column (added after `Material`).

### 1.2 Assembly costing

Requires the **Structured / All-Levels** export (dotted `Item`, e.g. `2`,
`2.1`, `2.8`, `14`, `14.1`). Parsing reuses the dotted-`Row Order` hierarchy
that `build_children_map` already understands.

- **Extended quantity:** for each row, `extended_qty = QTY × extended_qty(parent)`,
  where a row's parent is its dotted prefix (`2.8`→`2`, `14.3`→`14`), and
  top-level rows use `extended_qty(root) = 1`. This yields true build counts.
- **Per-assembly cost ("cost to make one"):** for each sub-assembly (a row that
  has children), cost = Σ over its leaf descendants of
  `(qty relative to this assembly) × unit_cost`, where the relative qty is the
  product of QTYs from the assembly down to the leaf.
- **Grand total:** cost to make one top-level assembly = Σ over all leaves of
  `absolute extended_qty × unit_cost` (top qty = 1).
- **Cost source & limitation:** unit costs come from the SharePoint
  `purchased items.xlsx` reference, matched on `Number` (unchanged enrichment).
  **Purchased** parts get real costs; **Normal/Make** parts and library parts
  with no reference entry (e.g. `ISO 4762 - M6 x 55 - Stainless Steel`,
  `Hotpress_Heating_Plate_92034699`) show **$0 / unmatched** — there is no
  fabrication-cost source. This is expected and must be visible (unmatched list
  + `$0`), not hidden.

### 1.3 Output — "Assembly Costs" summary sheet (Option A)

The workbook keeps its existing line-item sheet (with per-line `Item Qty ×
Cost Per = Sub Total`) and existing vendor summary, and **adds** a dedicated
sheet:

```
Assembly Costs
Item   Part #      Description                    Rolled-up Cost
2      SF-001803   kft90 generic bladder tool     $   412.50
14     CD-001621   bmw kft90 bladder assembly     $    88.20
...
────────────────────────────────────────────────────────────
GRAND TOTAL — <assembly_number>                   $  3,140.00
```

- One row per sub-assembly (rows that have children), showing its "cost to make
  one." A `GRAND TOTAL` row for the top assembly.
- `<assembly_number>` is the value already passed into the generator (drives the
  `{assembly_number}-PurchasingExport.xlsx` filename); not read from the BOM.

### 1.4 Export reminder (shown in the tool)

Surface this guidance in the purchasing GUI (a dialog before file selection)
and in the tool/CLI description:

> **Before exporting the BOM from Inventor**
> 1. Sort the BOM by **Description** (descending), then **renumber** the items.
> 2. Use a **Structured / All-Levels** BOM view (needed for per-assembly costs).
> 3. Include columns — **Required:** `Item`, `Part Number`, `QTY`;
>    **Recommended:** `Description`, `Unit QTY`, `BOM Structure`, `REV`,
>    `Material`, `Material Finish`. (`Thumbnail` / `Stock Number` are ignored.)
> 4. Export as **`.xlsx`** (preferred), or tab-delimited **`.txt`**, or **`.csv`**.

### 1.5 Touch points

- `bom_purchasing.py`: `INVENTOR_FIELD_MAP`; header-detection + normalization
  helper; `.txt`/tab reader; extended-qty + per-assembly costing; the
  "Assembly Costs" sheet writer; `Material Finish` column; `Material`
  precedence. Keep the Vault-canonical path working unchanged.
- `mcp_server.py`: update `vault_generate_purchasing_sheet_from_file` docstring
  to describe Inventor exports + the reminder.
- `gui/purchasing.py`: accept `.txt`; show the reminder dialog before file
  pick; surface the Assembly Costs result.

---

## Deliverable 2 — Flag Item-Master tools as broken (launcher GUI)

Per "flag now, rewrite later," mark the tools whose data source is being
retired so no one runs them and gets bad output.

- **Tools flagged:** Release Workflow, MFG Order Package, Property Check (Look
  Up). (BOM → Purchasing is **not** flagged — Deliverable 1 keeps it working.)
- **Treatment:** in `gui/launcher.py`, `_tool_row` gains a `broken=False`
  parameter. When `True`: disable the action button, and mark the row with a
  clear badge — title suffixed/labeled **"BROKEN — Item Master retired"** in the
  rust/warning color, with the description replaced by a short "disabled;
  depends on the retired Item Master — rewrite planned" note.
- No behavior change to the tools themselves; this is GUI-only.

---

## Deferred (separate future spec) — CAD/iProperty rewrite

Not built now; captured so findings aren't lost.

- Rewrite Release Workflow and MFG Order Package to source structure/part data
  from files (CAD BOM + iProperties) instead of items. Property Check would be
  rewritten to validate **file iProperties** or retired.
- **Quantity gap:** neither `get_item_bom` nor `get_file_uses` returns BOM
  quantities. The Inventor export is how Deliverable 1 sidesteps this; a future
  rewrite needs a quantity source (Inventor BOM export, occurrence counting, or
  a different endpoint).
- **iProperty retrieval:** `search_files` and `get_folder_contents` already
  accept `prop_def_ids`; `get_file_uses` supports `propDefIds`/`recurse` at the
  spec level but the client does not yet pass them. A file-iProperty helper
  analogous to the item `get_all_item_propdef_ids` path would be needed.

---

## Testing / verification

- End-to-end against the provided **`CD-001608`** Structured export
  (tab-delimited `.txt`): import → column mapping → extended quantities →
  Assembly Costs sheet + grand total. Assert: quantities populate; library /
  non-SF parts land in the unmatched list; the workbook writes and opens.
- Confirm the Vault-canonical file path still imports unchanged (regression).
- Reference-file enrichment: exercise with `purchased items.xlsx` reachable;
  degrade gracefully (warn, costs blank) when it is not.
- GUI: launch the launcher headless (withdrawn root) and assert the three rows
  build in the disabled/badged state and the button is disabled.

## Out of scope

- Any rewrite of Release Workflow / MFG / Property Check onto CAD/iProperties
  (deferred spec).
- Fabrication-cost modeling for Make parts (no data source).
- Parent×child roll-up beyond the extended-quantity rule defined in 1.2.
