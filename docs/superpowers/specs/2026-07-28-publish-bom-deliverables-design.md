# Publish BOM Deliverables — Design

**Date:** 2026-07-28
**Status:** Approved, ready for implementation planning

## Problem

Before a manufacturing package can be assembled, every Make part needs a
published PDF drawing and a STEP file sitting in Vault. Today someone queues
those jobs by hand in Vault Explorer, one file at a time, and finds out which
parts are missing a drawing only when the package comes out short.

`mfg_package.py` is the downstream half of this workflow — it walks a BOM and
*downloads* PDFs and STEPs that already exist. It has no way to create them,
and it is currently disabled anyway (`broken=True` in the launcher, "Item
Master retired") because it resolves parts through Vault items rather than
files.

This tool is the upstream half: given an Inventor BOM export, queue the Vault
job-server jobs that produce those deliverables, and report which Make parts
have no drawing to publish.

## Scope

**In scope**

- PDF publish jobs for Make parts that have a drawing file in Vault
- STEP publish jobs for Make parts and for the top-level assembly
- A drawing (PDF) job for the top-level assembly as well, when it has one
- A scan report naming every Make part with no drawing and every part not
  found in Vault

**Out of scope**

- Authoring drawings. The Vault job server publishes a PDF *from* an existing
  `.idw`/`.dwg`; it cannot create one. Make parts without a drawing are
  reported as gaps, not fixed.
- Polling jobs to completion. Submission is fire-and-forget; the user watches
  the queue in Vault Explorer.
- Downloading the finished files. That is MFG Order Package's job.
- An MCP tool. GUI plus engine only.

## Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Missing drawing | Report, never create | Job server cannot author drawings; Inventor COM is a much larger scope |
| BOM input | Inventor BOM export file only | Matches how Purchasing and BOM Sync are fed; the item-driven path is retired |
| Drawing scope | Make parts + top assembly | Buy parts are ordered, not manufactured |
| STEP scope | Make parts + top assembly | Vendor needs the assembly geometry for context |
| Already published | Always re-publish | Guarantees freshness; simpler than staleness comparison |
| Completion | Fire and forget | Job queue is visible in Vault Explorer already |
| Preview | Two-step Scan then Submit | Nothing reaches the queue by accident, and gaps are visible first |
| File lookup | Require a `Filename` column | Direct, unambiguous stem match; no item-to-file hop |
| Top assembly | Derived from BOM file name, editable | Convention with an escape hatch |
| `Reference` rows | Excluded | Context geometry and fixtures, not manufactured |
| `Phantom` rows | Included | Still real manufactured geometry |
| Surface | GUI tool + engine module | Matches every other tool in this repo |

### Reference BOM

`C:\Vault Workspace\DESIGNS\PRODUCTION EQUIPMENT\CD-001608 BOM.xlsx`, copied
to `tests/fixtures/CD-001608-bom.xlsx`, is the worked example this tool is
built against. Its shape:

```
Item | Filename | Thumbnail | BOM Structure | Unit QTY | QTY
     | Description | REV | Material | Vendor | Web Link
```

22 rows: 13 `Purchased`, 9 `Normal`. There is **no `Part Number` column** —
`coerce_bom_dataframe` derives `Number` from the filename stem, which is
exactly the case the "Part Number optional" change added.

The 9 Make rows, and therefore the publish set:

```
CD-001613.iam   CD-001612.ipt   CD-001577.ipt
CD-001578.ipt   CD-001621.iam   CD-001623.ipt
CD-001620.ipt   CD-001660.ipt   CD-001364.ipt
```

Plus `CD-001608` as the top assembly, derived from the file name. Ten stems,
up to twenty jobs.

Two properties of this file drive requirements:

- `ISO 4762 - M6 x 20ISO Stainless Steel.ipt` appears on rows 2.3 and 12.
  Deduplication by stem is not hypothetical.
- Row 13 is `CD-001366.ipt` marked `Purchased` — an in-house-numbered part
  flagged as bought. It is excluded, with no special case. `BOM Structure` is
  authoritative; a part that needs deliverables but is marked `Purchased` is a
  BOM error to fix in Inventor, not something the tool second-guesses.

### Consequence of requiring `Filename`

The current export template carries `Filename`, so this requirement does not
break the workflow in practice. The older
`tests/fixtures/CD-001608-inventor-bom.txt` export has only `SF-######` item
numbers and no filename column; exports in that shape are rejected. The error
message must name the column and tell the engineer to re-export with it, so
the remedy is obvious without reading the code.

## Architecture

```
publish_bom.py           engine: parse, scan, submit. Async, no Tk.
gui/publish_bom.py       Tk Toplevel dialog
gui/launcher.py          + one _tool_row tile
tests/test_publish_bom.py
```

The engine never imports Tk and takes an `on_progress: Callable[[str], None]`
callback, so it is unit-testable headless. This is the same split as
`mfg_package.py` / `gui/mfg_package.py`.

### Data flow

```
BOM file
  -> read_bom_file()            (bom_purchasing — .xlsx/.xls/.csv/.txt)
  -> raw BOM Structure captured (before coercion flattens Reference to Make)
  -> coerce_bom_dataframe()     (bom_purchasing — header mapping, Source values)
  -> filter to Make, drop Reference, dedupe by file stem
  -> PublishRow[]
      -> scan: one search_files per stem, 8 concurrent
      -> ScanRow[] (model file, drawing file, status)
          -> submit: one job per resolved file, serial
          -> SubmitResult (job ids, failures)
```

## Component: BOM parsing

`load_publish_rows(bom_file_path) -> (rows, error)`

1. `bom_purchasing.read_bom_file()` — handles all four extensions with the
   string-dtype guards that keep `"2.10"` from collapsing to `2.1`.
2. Capture the raw `BOM Structure` column **before** coercion.
   `coerce_bom_dataframe` maps `Reference` to `Make` via `SOURCE_VALUE_MAP`,
   so the distinction is unrecoverable afterwards. The captured value is
   stashed as an extra column (`__bom_structure__`) rather than held as a
   positional list: `coerce_bom_dataframe` passes unknown columns through
   untouched, so the value travels with its own row. Indexing a separate list
   by row position would work today but would silently misassign every
   structure after any row coercion ever dropped — landing a `Purchased` flag
   on a `Make` part with nothing raising.
3. `bom_purchasing.coerce_bom_dataframe()` — header mapping and `Source`
   translation.
4. Locate the filename column using `bom_purchasing.FILE_NAME_HEADERS`
   (`filename`, `file name`, `file`, `document name`, `documentname`,
   case-insensitive). **Absent means return an error**, not an exception.
5. Keep rows where the part is manufactured:
   - When the raw `BOM Structure` column exists, keep rows whose value is not
     `Purchased` and not `Reference` (case-insensitive, stripped). `Normal`,
     `Phantom` and `Inseparable` are kept. A blank or unrecognized value is
     kept too — an unpublished deliverable is a visible gap in the scan
     table, whereas a silently dropped row is not.
   - Otherwise fall back to `Source == "Make"`, which is what a
     Vault-canonical BOM already carries.
6. Take `supplier_pricing.normalize.file_stem(Filename)` for each kept row and
   dedupe — a BOM lists the same part on many rows, and each file needs one
   job, not one per occurrence.

Rows with a blank or unparseable filename are dropped and logged. A Make row
with no file name cannot be published, but a blank cell is a BOM problem worth
noticing rather than swallowing.

## Component: Scan

`scan_rows(api, vault_id, rows, on_progress) -> ScanRow[]`

One `api.search_files(query=stem, latest_only=True, search_sub_folders=True,
limit=SEARCH_LIMIT)` per unique stem, at most 8 in flight behind an
`asyncio.Semaphore` — the cap `vault_state.MAX_CONCURRENCY` already uses.

Filtering each response:

- Keep only `entityType == "FileVersion"`, compared case-insensitively but
  still as an exact match. The looser `startswith("file")` that
  `vault_state.py` uses would be wrong here: that module only reads lifecycle
  state, whereas this one submits the id as a `FileVersionId`, and a master
  id would publish the wrong thing.
- Require the basename to **equal** the stem, case-insensitively. A substring
  match pulls in every assembly that references the part; this is the
  `_basename_matches` guard from `mfg_package.py:271-284`.

Classification by extension:

- `.ipt`, `.iam` → model (STEP source)
- `.idw`, `.dwg` → drawing (PDF source)
- anything else → ignored

When several files of one kind match a stem — an archived copy, a library
duplicate — ranking is deterministic rather than first-hit: assemblies outrank
parts, Inventor drawings outrank AutoCAD ones, then name breaks the tie. This
mirrors `vault_state._EXT_PRIORITY`. Taking whichever file the server happened
to list first would make the published deliverable non-reproducible. When more
than one candidate existed, `ScanRow.ambiguous` is set and the status carries a
`(multiple matches)` suffix, because the Scan step exists precisely so a human
can catch this before jobs are queued.

Each `ScanRow` carries: stem, description, model name + file-version id,
drawing name + file-version id, an `ambiguous` flag, and a status string:

| Status | Meaning |
| --- | --- |
| `2 jobs` | Model and drawing both found |
| `STEP only - no drawing` | Model found, no drawing. **The reported gap.** |
| `PDF only - no model` | Drawing found, no model |
| `not in Vault` | Stem matched nothing |
| `lookup failed` | The search errored, or raised, for this stem |
| `search truncated - refine` | A full page of hits came back without the stem's own files |

That last status matters more than it looks. A stem's keyword search also
matches its `.pdf`/`.stp`/`.dwf` siblings, its item, and anything carrying the
stem in a property, so the hit list is far longer than the two files wanted.
Search is capped at `SEARCH_LIMIT` (50). If the cap is hit and the files were
not among the results, saying `not in Vault` would be a lie that sends someone
to redraw a part that already exists — so truncation reports itself instead.

The top assembly is scanned the same way and appended as its own row. Its stem
comes from the editable field, prefilled by pulling the leading `CD-######`
(or the first whitespace-delimited token) out of the BOM file name. A blank
field skips it entirely.

## Component: Submit

`submit_jobs(api, vault_id, scan_rows, on_progress) -> SubmitResult`

Serial loop over the scan result. Job submission is cheap, and serial keeps
the log readable and the queue ordered.

Before the loop, call `api.get_job_queue_enabled()`. If the queue is off,
warn that jobs will sit unprocessed until a Job Processor agent comes online —
then submit anyway, since queuing ahead of the processor is valid. That check
is advisory, so a failure or exception from it must never block the submission
it was only meant to annotate.

Note that "queue enabled" and "a processor is running" are different things:
the queue can report enabled while no Job Processor is online, in which case
jobs sit at status `Ready` until one starts. That is normal and outside this
tool's control.

Param shapes, reused verbatim from the working MCP tools in
`mcp_server.py:998-1069`:

```
drawing -> JobType "Autodesk.Vault.PDF.Create.{idw|dwg}"
           Params  {FileVersionId, UpdateViewOption: "False"}

model   -> JobType "Autodesk.Vault.STEP.Create.{ipt|iam}"
           Params  {FileVersionId, UpdatePdfOption:  "False",
                                   UpdateViewOption: "False"}
```

Param keys are PascalCase — the job-processor constructor rejects the job
outright otherwise, and the REST response echoes them back camelCased, which
is misleading. STEP reads both `UpdatePdfOption` and `UpdateViewOption`
despite the names; `UpdateStpOption` is not used.

Description is auto-generated per job (`"PDF Create: CD-001578.idw"`).
`api.submit_job` rejects an empty description with Vault error 155.

A failed submit is logged with the Vault error and the loop continues. The
result carries submitted count, failed count, and the job ids.

## GUI

`tk.Toplevel` opened from the launcher with the live `api` and `vault_id`
attached, palette imported from `gui.release_workflow` — the arrangement
`gui/mfg_package.py:28-40` uses.

```
+- Publish BOM Deliverables ------------------------------+
| BOM file      [ CD-001608 BOM.xlsx        ] [Browse...] |
| Top assembly  [ CD-001608                 ]  blank=skip |
|                                     [ Scan ]  [ Submit ]|
+---------------------------------------------------------+
| Part      Description        Model        Drawing    Status  |
| CD-001578 bmw vacuum backer  ...578.ipt   ...578.idw 2 jobs  |
| CD-001601 bmw bladder plate  ...601.iam   --         STEP    |
| CD-001644 bmw deckle plate   --           --         missing |
+---------------------------------------------------------+
| 34 Make rows - 31 models - 28 drawings - 59 jobs - 3 gap|
| log...                                        [ Close ] |
+---------------------------------------------------------+
```

- Submit starts disabled, enables after a successful Scan, and disables again
  once submitted. A second run needs a fresh Scan — the guard against
  accidentally queueing sixty jobs twice.
- Results table is a `ttk.Treeview`; the summary line sits directly beneath it.
  The Description column comes from the BOM row — a bare stem like `CD-001613`
  is hard to sanity-check, and the whole point of the scan step is that a
  human can spot a wrong or missing part before jobs are queued. The BOM's
  part number is deliberately not shown: these exports derive it from the
  filename stem, so it would be the same string twice.
- Work runs on a worker thread pushing status strings to a `queue.Queue`,
  drained by `after()`, so the window never freezes. Same pattern as the other
  dialogs.
- The launcher gains one `_tool_row` tile: "BOM → Publish Deliverables",
  button "Open Publisher".

## Error handling

| Condition | Behavior |
| --- | --- |
| No `Filename` column | Scan refuses, message names the column and the remedy |
| Unreadable file / bad extension | `read_bom_file`'s `ValueError` shown in a messagebox |
| BOM parses to zero Make rows | Scan reports "no Make parts found", Submit stays disabled |
| `search_files` fails for one stem | Row shows `lookup failed`, scan continues |
| `search_files` *raises* for one stem | Same — caught per row, so one bad row cannot discard an otherwise good scan |
| Search came back full without the wanted files | Row shows `search truncated - refine`, never a false `not in Vault` |
| Queue check fails or raises | Warning skipped, submission proceeds — an advisory check must not block the work it annotates |
| Stem resolves to no files | Row shows `not in Vault`, contributes no jobs |
| Make part with model, no drawing | STEP queued, drawing counted as a gap |
| `submit_job` fails | Logged with the Vault error, loop continues, summary counts it |
| Job queue disabled | Warned before submitting; jobs still queued |
| No live Vault session | Tool refuses to open, same as the other launcher tools |

Only an unparseable BOM aborts the whole run.

## Testing

`tests/test_publish_bom.py`, engine only. The GUI stays untested, consistent
with the rest of the suite. No network: a fake `api` object returns canned
responses and records calls.

Two fixtures:

- `tests/fixtures/CD-001608-bom.xlsx` — the real export, for end-to-end
  parsing against a file that actually ships.
- A synthetic export built in-test from a DataFrame, covering the
  `Phantom`, `Inseparable`, `Reference` and blank-structure branches that the
  real BOM happens not to contain.

Parsing, against the real fixture:
- Exactly 9 rows survive, and their stems are the nine `CD-` names listed in
  the Reference BOM section
- All 13 `Purchased` rows excluded, including `CD-001366.ipt`
- The duplicated `ISO 4762 - M6 x 20ISO Stainless Steel.ipt` never reaches the
  output (it is `Purchased`), and a synthetic duplicate of a `Normal` row
  collapses to one stem

Parsing, against the synthetic fixture:
- `Reference` rows excluded even though coercion calls them Make
- `Phantom` and `Inseparable` rows kept
- A blank or unrecognized `BOM Structure` value is kept
- Missing `Filename` column returns an error, does not raise
- A BOM with no `BOM Structure` column falls back to `Source == "Make"`

Scan:
- Exact-basename matching rejects `CD-001578-BRACKET.ipt` for stem `CD-001578`
- `.ipt`/`.iam` classify as model, `.idw`/`.dwg` as drawing
- Non-`FileVersion` search hits ignored
- A search error degrades that one row to `lookup failed`

Submit:
- `JobType` matches the file extension for both kinds
- Param keys are PascalCase
- `UpdatePdfOption` present on STEP jobs, absent on PDF jobs
- Description is non-empty on every submitted job
- One failing submit does not stop the remaining ones

Top assembly:
- Stem parsed from `"CD-001608 BOM.xlsx"` and from `"CD-001608 MFG BOM.xlsx"`
  is `CD-001608` in both cases
- Blank field queues no top-level jobs
- Top assembly gets both a PDF and a STEP job when both files exist

## Live verification, 2026-07-28

Run against the production vault (`Simplifyber1`, vault id 1) with the real
`CD-001608 BOM.xlsx`.

Ground truth was established first by querying Vault directly, *not* through
this tool, so the check is independent rather than a restatement of the tool's
own output. The engine then reproduced it exactly:

```
rows=10  models=10  drawings=3  jobs=13
missing_drawing=7  not_found=0  failed=0
```

Seven of the nine Make parts have no drawing — CD-001613, CD-001577,
CD-001578, CD-001621, CD-001623, CD-001660, CD-001364. Only CD-001612,
CD-001620 and the CD-001608 top assembly have one. That gap list is the
tool's actual product.

Incidental confirmations from real data:

- `CD-001578` returns `CD-001578.SLDPRT` and `CD-001578_perf.stl` alongside
  its `.ipt`. Neither is picked up — the first is an unhandled extension, the
  second has a different stem. The exact-basename guard earns its keep.
- The same search returns ItemVersion `SF-001922`, correctly filtered by the
  `entityType == FileVersion` check.
- Maximum hits for any stem was 5, so `SEARCH_LIMIT = 50` has wide headroom
  and no truncation occurred.
- No stem returned two models or two drawings, so the ambiguity path was not
  exercised live and remains unit-test-only.

**Submission**, exercised for `CD-001612` alone (one PDF + one STEP, so both
param shapes were covered) rather than all thirteen jobs:

```
CD-001612.idw: PDF  queued (job 25206)  Autodesk.Vault.PDF.Create.idw
CD-001612.ipt: STEP queued (job 25207)  Autodesk.Vault.STEP.Create.ipt
submitted=2 failed=0
```

Both were accepted with no param error. They sat at status `Ready` overnight
while no Job Processor was online, then were consumed once one started.

**Confirmed the following morning: the publish worked end-to-end.** Both jobs
left the queue and two new files appeared in Vault:

```
CD-001612-R3.pdf   verId 124909
CD-001612-R3.stp   verId 124912
```

Attribution is unambiguous. `CD-001620` and `CD-001608` both have drawings
too, and neither has a published `.pdf` or `.stp` — because no job was
submitted for them. Only the one part that was submitted gained outputs.

### Published outputs carry a revision suffix

Note the names: `CD-001612-R3.pdf`, not `CD-001612.pdf`. Vault names a
published deliverable `<stem>-R<rev>.<ext>`.

This tool is unaffected — it scans *source* files (`.ipt`/`.iam`/`.idw`/
`.dwg`) and never looks for published outputs. But it is a trap for anything
downstream that assumes a deliverable is a same-stem sibling of its source.
`mfg_package.py:271-284` (`_basename_matches`) requires the basename to
*equal* the part number, so it would not match `CD-001612-R3.pdf` when
collecting deliverables for `CD-001612`. That module is already disabled for
unrelated reasons (retired Item Master), but the naming assumption should be
fixed whenever it is revived — and any future "skip if already published"
option here would need to match `<stem>-R*.pdf` rather than `<stem>.pdf`.

## Open questions

None.
