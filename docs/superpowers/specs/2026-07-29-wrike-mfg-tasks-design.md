# BOM → Manufacturing Tasks (Wrike) — Design

**Date:** 2026-07-29
**Status:** Approved, ready for implementation planning

## Problem

A purchasing sheet tells you what to order and from whom. Turning it into work
that someone tracks is manual: an engineer reads the sheet, opens Wrike, and
hand-types a task for each supplier's order, then more tasks for the stages that
order passes through — issue the PO, wait for the shop to make it, wait for it
to arrive. Nothing ties those tasks back to the sheet, so a part added in a
later revision is remembered or it isn't.

This tool reads the generated purchasing workbook, reconciles each part's
supplier against Vault, groups the parts into orders, and creates the Wrike task
set for each order in one pass.

## Scope

**In scope**

- Reading the **generated purchasing workbook** (the output of BOM → Purchasing
  Sheet), not a raw Inventor export
- Reconciling each part's supplier between the sheet and the Vault `Vendor`
  property, resolved per row in the GUI
- Grouping parts into one order per supplier, Make and Buy alike
- Creating, per supplier: a parent task plus Purchasing / Manufacturing /
  Shipping subtasks, dependency-chained, dated, and assigned
- Detecting supplier orders that already have tasks and skipping them

**Out of scope**

- Creating Wrike projects or folders. You pick an existing one.
- Updating or deleting existing tasks. An existing order is skipped, not
  refreshed — you edit it in Wrike.
- PO documents, attachments, and file uploads.
- Polling task status back out of Wrike.
- A new MCP tool. The existing `wrike_*` server already covers the
  conversational path; this is a GUI plus engine.

## Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Granularity | One trio per supplier, not per part | One PO, one shipment, one set of tasks. Part count never changes task count. |
| Input | Generated purchasing workbook | It already carries Vendor, Source, Qty, Cost and Lead Time — the raw export carries none of them for Make rows. |
| Supplier source | Sheet **and** Vault, cross-checked | Both are maintained today; the tool's job is to make them agree before work is created. |
| Conflicts | Resolved per row in the GUI | Neither side is authoritative enough to win automatically. |
| Wrike target | Existing project picked in the GUI | Nothing auto-created; the folder tree stays yours. |
| Task links | Parent + subtasks + finish-to-start | The Gantt then shows the real sequence and a slip cascades. |
| Dates | Forward from a start date, lead time drives duration | The sheet already knows each part's lead time. |
| Assignees | A default per stage, remembered in config | Set once, not every run. |
| Descriptions | Tailored per stage | Each task reads as the instruction for its own stage. |
| Re-runs | Detect and skip, report | Safe by default; no hand-written note gets overwritten. |
| Buy parts | Included, with no Manufacturing task | Screws are ordered and shipped; nothing is made. |
| Mixed supplier | One trio, Manufacturing present if anything is made | One PO to that vendor means one set of tasks. |
| Sub-assemblies | Roll-ups excluded, children ordered | An assembly's Sub Total is a SUM of its children; ordering both double-counts. |
| Surface | Launcher GUI + engine module | Matches every other tool in this repo. |

## Context

Five findings from the existing code shape the design.

1. **The generated workbook puts column headers on row 3.** Row 1 is the
   assembly-number title bar, row 2 the generated date
   (`bom_purchasing.py:482-512`). `read_bom_file` reads header row 0, so it
   cannot read this workbook back. A dedicated reader is required.
2. **Cell A1 is the assembly number** (`bom_purchasing.py:484`) — a build
   identifier for task titles that needs no filename parsing.
3. **The Purchasing tab hides `Number` and renames `Title` to "Name"**
   (`bom_purchasing.py:95-105`). Part identity on the sheet is therefore
   `Title`, which carries the CD number — the same string the Vault file
   lookup needs as a stem.
4. **`Sub Total` is an Excel formula** (`bom_purchasing.py:579-588`), as is an
   assembly's `Cost Per`. Read back with `data_only=True` they return `None`
   unless Excel has opened and re-saved the file, so costs must be recomputed,
   never read.
5. **`search_file_versions` already spells the property selector
   `option[propDefIds]`** (`vault_rest_api.py:587`) — the spelling file
   endpoints honour. The bare `propDefIds` that item endpoints use returns 200
   with the properties silently absent.

The reference BOM `tests/fixtures/CD-001608-bom.xlsx` supplies the live cases:
Make rows carry no Vendor, Buy rows spell it `McMASTER-CARR` (so comparison
must be case-insensitive), `CD-001613.iam` is a Make sub-assembly with
children, and `CD-001366.ipt` is an in-house number marked `Purchased` against
vendor MiSUMi — the mixed-supplier case.

## Architecture

```
bom_purchasing.py          + HDR_ROW constant, + read_purchasing_sheet()
wrike_mfg_tasks.py         NEW engine: reconcile, group, schedule, render, create
gui/wrike_mfg_tasks.py     NEW Tk dialog
gui/launcher.py            + one _tool_row tile
wrike_rest_api.py          + super_task_ids on create_task, + add_dependency
tests/test_wrike_mfg_tasks.py
```

The reader lives beside the writer so both halves share `HDR_ROW`,
`HEADER_LABELS` and `SHEET_COLUMNS`. Change a column and they move together;
a copy of the layout in the new module would rot silently.

The engine never imports Tk and takes an `on_progress: Callable[[str], None]`
callback, so it is unit-testable headless. Same split as
`publish_bom.py` / `gui/publish_bom.py`.

### Data flow

```
Purchasing workbook (.xlsx)
  -> read_purchasing_sheet()      header row 3, A1 = assembly number
  -> drop roll-up rows (rows with children)
  -> OrderPart[]   title, description, kind (Make/Buy), qty, material, rev,
                   unit cost, shipping, tax, lead time, sheet vendor
      -> reconcile: one file-version lookup per title, 8 concurrent
      -> ReconcileRow[]   sheet vendor | Vault vendor | status | proposal
          -> [GUI resolves reds, accepts proposals]
          -> group by chosen supplier
          -> schedule from start date + durations + lead time
          -> SupplierOrder[]  -> preview  -> create
```

## Component: reading the sheet

`read_purchasing_sheet(path) -> (df, assembly_number, error)`, added to
`bom_purchasing.py`.

- `load_workbook(path, data_only=True)`, worksheet **by name** (`"Purchasing"`).
  A workbook with no such tab returns an error naming it — that is exactly what
  a raw Inventor export looks like, and the message must say so rather than
  mis-parse it.
- `assembly_number = ws["A1"].value`, the merged title bar. Blank falls back to
  the file-name stem; the GUI's Build field stays editable either way.
- Header row: start at the shared `HDR_ROW` constant and **verify** the row
  carries the expected labels. If it does not, scan rows 1–10 for one that does
  and error if none matches. A workbook generated before a future layout change
  still reads.
- Invert `HEADER_LABELS` on the way in: `"Name"` → `Title`, `"Description"` →
  `Description (Item,CO)`.
- Part identity is `Title`. `Number` is deliberately absent from the sheet. A
  row with a blank `Title` is dropped and logged.
- **Never read `Sub Total`.** Line total is recomputed as
  `Cost Per × Item Qty + Shipping + Tax/Tariff`, all of which are literal
  values.
- Roll-ups are identified with the existing `build_children_map(df)`, which
  keys on `Row Order` — a column the sheet does carry. Any row with children is
  excluded and reported; its children are ordered individually.
- A non-numeric or blank `Item Qty` counts as 1 and is flagged, mirroring the
  sheet's own `ISNUMBER` guard at `bom_purchasing.py:587`.

## Component: supplier reconcile

`reconcile_vendors(api, vault_id, parts, on_progress) -> ReconcileRow[]`

One `api.search_file_versions(query=title, prop_def_ids="all",
latest_only=True, limit=SEARCH_LIMIT)` per unique title, at most 8 in flight
behind an `asyncio.Semaphore` — the cap `publish_bom` uses. `SEARCH_LIMIT` is
50, matching `publish_bom`.

Filtering each response:

- Keep only `entityType == "FileVersion"`, compared case-insensitively but as
  an exact match.
- Require the basename to **equal** `<title>.ipt` or `<title>.iam`. A substring
  match pulls in every assembly that references the part — the
  `_basename_matches` guard from `mfg_package.py:271-284`.
- Resolve `Vendor` through `included.propertyDefinition` by display name,
  case-insensitively.

Comparison normalizes case, surrounding whitespace, and runs of internal
whitespace. Not optional: the reference BOM spells it `McMASTER-CARR`.

| Status | Proposal | GUI |
| --- | --- | --- |
| `matched` | the agreed value | green, nothing to do |
| `sheet only` | the sheet value | amber, one click to accept |
| `Vault only` | the Vault value | amber, one click to accept |
| `mismatch` | none | **red — pick a side** |
| `both blank` | none | **red — type a supplier, or exclude** |
| `not in Vault`, Buy row | the sheet value if present, else none | amber, or red when the sheet is also blank |
| `not in Vault`, Make row | none | **red** |
| `lookup failed` | the sheet value if present, else none | amber, or red when the sheet is also blank |
| `search truncated` | same as `lookup failed` | never reported as `not in Vault` |

`not in Vault` splits by kind deliberately. A catalogue screw that was never
checked into Vault is routine, and its sheet vendor came from the Engineering
Purchased Parts list, which is the authority for bought items — blocking on it
would mean confirming eleven fasteners by hand every run. A CD-numbered Make
part missing from Vault is not routine: either the title is wrong or the part
was never checked in, and both are worth stopping for.

`lookup failed` proposes the sheet value for either kind, because a transient
search error is not evidence about the part.

`search truncated` matters more than it looks: a title's keyword search also
matches its `.pdf`/`.stp` siblings and its item, so the hit list is longer than
the one file wanted. Reporting `not in Vault` when the cap was hit would be a
lie that sends someone to fix data that is already correct.

An **Accept all proposals** button resolves every amber row at once. Eleven
screws must not mean eleven clicks. Reds are never auto-resolved.

## Component: grouping

Group key is the **chosen supplier alone**, normalized the same way the
reconcile comparison normalizes it — case, surrounding whitespace, and runs of
internal whitespace. A supplier typed as `xometry` on one row and `Xometry` on
another is one order, not two. The group's display name is the spelling from
its first row, which is what reaches the task title.

A supplier with both Make and Buy parts gets one order, because that is one PO.

- The Manufacturing task exists when the group contains at least one Make part,
  and lists only those parts.
- A Buy-only group is Purchasing → Shipping, two subtasks.
- Excluded rows contribute to no group.

## Component: scheduling

Forward from a start date set in the GUI. Lead time lands on the stage that
consumes it:

- **Purchasing duration** — the GUI default (default 2 business days).
- **Manufacturing duration** — the longest `Lead Time (Business Days)` among
  the group's *Make* parts, falling back to the GUI default (default 10) when
  the column is blank.
- **Shipping duration** — the GUI default (default 3), except in a group with
  no Manufacturing task, where it is the longest lead time among the group's
  parts. A McMaster order's lead time *is* its ship time; putting it on a stage
  that does not exist would lose it.

Durations are business days, weekends skipped. There is no holiday calendar.
Dates are sent to Wrike as plain `YYYY-MM-DD` strings.

```
Xometry   (Make, lead 15)   Purchasing     08-03 -> 08-04
                            Manufacturing  08-05 -> 08-25
                            Shipping       08-26 -> 08-28
McMaster  (Buy,  lead 3)    Purchasing     08-03 -> 08-04
                            Shipping       08-05 -> 08-07
```

## Component: task shape

Titles carry the build and supplier, because a subtask appears detached from
its parent in list views and in an assignee's My Work queue:

```
CD-001608 - Xometry                          (parent)
  CD-001608 Xometry - 1. Purchasing
  CD-001608 Xometry - 2. Manufacturing
  CD-001608 Xometry - 3. Shipping
```

The build prefix comes from cell A1, editable in the GUI. The separator is a
plain hyphen surrounded by single spaces (`" - "`), not an em dash — the
re-run check compares parent titles literally, so the separator has to be a
character that survives a round trip through the API unchanged.

The **parent** task spans the order: its start is the first stage's start, its
due the last stage's due, and its responsible is the Purchasing owner, who
initiates the order. Its dates are set explicitly rather than left to Wrike's
roll-up, so the order still reads correctly if a subtask is later deleted.

Descriptions are tailored per stage:

- **Parent** — source workbook name, supplier, part count, piece count, order
  total; the full part table.
- **Purchasing** — part, qty, unit cost, line total, order total; a checklist
  (PO issued / acknowledgement received).
- **Manufacturing** — the Make parts only: part, qty, rev, material. Omitted
  entirely for a Buy-only group.
- **Shipping** — the full expected receipt: every part and qty, piece count.

Each stage task is assigned to that stage's default owner, chosen once in the
GUI and remembered in config.

## Component: creation

Serial per supplier, and serial within a supplier. Creation is cheap, and
serial keeps the log readable and the Wrike API gentle.

Per order: create the parent, then each subtask with `super_task_ids=[parent]`,
then the finish-to-start dependencies between consecutive stages.

There is no rollback — Wrike has no transaction. A trio that fails halfway is
reported with the ids that *were* created, so it can be cleaned up by hand, and
the loop continues to the next supplier.

### Wrike client additions

Two gaps in `wrike_rest_api.py`:

- `create_task(..., super_task_ids: Optional[List[str]] = None)` — adds
  `superTasks` to the POST body.
- `add_dependency(task_id, predecessor_id, relation_type="FinishToStart")` —
  `POST /tasks/{taskId}/dependencies`.

The dependency request body is the one shape not confirmed from the codebase.
The **first implementation step is a live probe** against a throwaway task,
with the confirmed shape written into the method's docstring — the same way
this repo pinned down the Vault job param casing. It is wrapped in one method,
so a wrong guess changes one place.

## Component: re-run detection

Before creating, query the target project for a parent task whose title equals
`<build> - <supplier>` exactly. Wrike's title filter is a substring match, so
the exact comparison happens locally on the returned set.

The query must **not** filter by status. Wrike's task search returns active
tasks by default, and a completed order filtered out of the result would be
recreated on the next run.

Matches are reported `already exists - skipped`; new suppliers are created.

## GUI

`tk.Toplevel` opened from the launcher with the live `api`, `vault_id` and the
Wrike client attached, palette imported from `gui.release_workflow` — the
arrangement `gui/mfg_package.py:28-40` uses. Work runs on a worker thread
pushing status strings to a `queue.Queue` drained by `after()`, so the window
never freezes.

```
+- BOM -> Manufacturing Tasks ---------------------------------------+
| Purchasing sheet [ CD-001608 Purchasing Sheet.xlsx  ] [ Browse... ] |
| Build [ CD-001608 ]        Wrike project [ BMW Hot Press Tooling v] |
| Start [ 2026-08-03 ]  Purch [2]d  Ship [3]d  MFG fallback [10]d     |
| Owners  Purchasing [ Zak v ]  Mfg [ Zak v ]  Shipping [ Zak v ]     |
|              [ Load & Reconcile ]   [ Preview ]   [ Create Tasks ]  |
+---------------------------------------------------------------------+
| ( Parts )  ( Task plan )                                            |
| Part       Description       Kind  Sheet         Vault      Supplier|
| CD-001612  bmw vacuum backer Make  Xometry       Xometry    Xometry |
| CD-001578  bmw backer        Make  Protolabs     Xometry    -- pick |
| ISO 4762.. M6 x 20 steel     Buy   McMaster-Carr --         McMaster|
+---------------------------------------------------------------------+
| [ Accept all proposals ]      18 rows - 3 suppliers - 1 unresolved   |
| log...                                                    [ Close ] |
+---------------------------------------------------------------------+
```

A `ttk.Notebook` holds two `ttk.Treeview` tables:

- **Parts** — the reconcile grid above. Supplier is editable per row; a row can
  be excluded.
- **Task plan** — one row per task: supplier, stage, start, due, owner, and
  `new` or `already exists`.

Flipping back to Parts after a preview is free.

Button gating, the guard against creating a board twice:

- **Load & Reconcile** enables once a file is chosen.
- **Preview** enables only when reconcile leaves zero unresolved rows.
- **Create Tasks** enables only after a successful preview, and disables once
  used. A second run needs a fresh preview.

The Wrike project dropdown is populated from `list_projects`; the owner
dropdowns from `list_contacts`.

The launcher gains one `_tool_row` tile: "BOM → Manufacturing Tasks", button
"Open Task Builder".

### Config

A `wrike.mfg_tasks` block remembers the three stage-owner contact IDs, the
three default durations, and the last-used project id, so they are set once
rather than every run. Absent block means empty defaults, not an error.

## Error handling

| Condition | Behavior |
| --- | --- |
| No `Purchasing` tab | Refuses; message names the tab and says this is the generated workbook, not a raw export |
| Header row not found | Refuses; message names the labels it looked for |
| `Sub Total` reads as a formula or blank | Expected — never read; totals recomputed |
| Blank `Title` on a row | Row dropped, logged |
| Non-numeric `Item Qty` | Counts as 1, flagged |
| Zero orderable rows | Reports "no orderable parts", Preview stays disabled |
| Vault lookup fails or raises for one part | That row shows `lookup failed`, reconcile continues |
| Search filled a page without the file | Row shows `search truncated`, never a false `not in Vault` |
| Unresolved rows remain | Preview disabled, unresolved count shown |
| Supplier already has a trio | Reported `already exists - skipped`; other suppliers still created |
| A task in a trio fails to create | Partial trio reported with the ids that were created; loop continues |
| Dependency call fails | Tasks kept, dependency reported missing — the tasks are the product, the link is the garnish |
| Wrike `429` | Surfaced as a rate-limit message; creation is serial to keep the API gentle |
| No live Vault session, or no Wrike config | Tool refuses to open, same as the other launcher tools |

Only an unreadable workbook aborts the whole run.

## Testing

`tests/test_wrike_mfg_tasks.py`, engine only. The GUI stays untested,
consistent with the rest of the suite. No network: a fake `api` object returns
canned responses and records calls.

The headline test is a **round trip**: feed the existing
`tests/fixtures/CD-001608-bom.xlsx` through `build_purchasing_sheet`, read it
back with `read_purchasing_sheet`, and assert the rows survive with their
Source, Vendor, Qty and Lead Time intact. That is the whole reason the reader
lives beside the writer — it fails the day a column moves.

Reading:
- Header row located when it is at `HDR_ROW`, and when it is not
- A workbook with no `Purchasing` tab returns an error, does not raise
- `Sub Total` is never read; line totals match `cost × qty + shipping + tax`
- `CD-001613.iam` is excluded as a roll-up and its children survive
- Blank `Title` dropped; non-numeric `Item Qty` counts as 1

Reconcile:
- Each status in the table above
- `not in Vault` is amber on a Buy row with a sheet vendor, red on a Make row
- `McMASTER-CARR` matches `McMaster-Carr`
- Exact-basename matching rejects `CD-001578-BRACKET.ipt` for title `CD-001578`
- Non-`FileVersion` hits ignored
- A search error degrades that one row to `lookup failed`, others unaffected

Grouping and scheduling:
- Mixed supplier (MiSUMi, one Make + fasteners) yields one order with a
  Manufacturing task listing only the Make part
- `xometry` and `Xometry` on different rows collapse to one order titled with
  the first row's spelling
- A Buy-only supplier yields an order with no Manufacturing task
- Lead time drives Manufacturing for a Make group, Shipping for a Buy-only
  group
- Blank lead time falls back to the GUI default
- Business-day arithmetic skips a weekend

Creation:
- Subtasks carry `superTasks` = the parent id
- Dependencies chain consecutive stages, and a Buy-only order chains
  Purchasing → Shipping directly
- Each stage task carries its stage's owner
- A mid-trio failure reports the created ids and does not stop the next
  supplier
- A dependency failure leaves the tasks in place

Re-runs:
- An existing parent title is skipped and reported
- An existing parent that is **completed** is still detected — the status
  filter regression

## Open questions

None. The Wrike dependency request body is unconfirmed but is resolved by the
first implementation step (a live probe), not by a design decision.
