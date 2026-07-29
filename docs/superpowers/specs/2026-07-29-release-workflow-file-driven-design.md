# Release Workflow — File-Driven Rewrite — Design

**Date:** 2026-07-29
**Status:** Approved, ready for implementation planning

## Problem

The Release Workflow wizard is disabled. Its launcher tile carries
`broken=True` with the label "Item Master retired" (`gui/launcher.py`), because
four of its seven steps resolve parts through Vault *items*:

- Step 1 Compliance check — `check_item_properties.check_part_number`
- Step 2 Readiness report — renders that item-based result
- Step 7 Release items — `update_item_lifecycle_states`
- Steps 3, 4 and 6 derive their file lists from `_associated_file_versions`,
  which walks an item version to reach its files

Meanwhile the tools an engineer actually uses to get an assembly out the door
all work today, and all of them are file- or BOM-driven. They are just six
separate windows launched by hand, in an order you have to remember, with no
single place that says how far along a release is.

This rewrite re-points the wizard at those six working tools, so the release
sequence is one window again.

## Scope

**In scope**

- Rewriting the wizard's seven item-driven steps as the six file-driven steps
  below
- Extracting the shared brand palette out of `gui/release_workflow.py`, which
  8 modules currently import it from
- A headless engine module so the step logic is testable without Tk
- Re-enabling the launcher tile
- Re-pointing the wizard's Vault search dialog from items to files

**Out of scope**

- MFG Order Package. It is still genuinely item-based and stays flagged
  broken.
- `item_property_rules.json` and `check_item_properties.py`. Untouched — the
  item path is retired, not deleted.
- `scripts/release_workflow.py`'s CLI, beyond updating its palette import.
- Polling submitted jobs to completion. Fire-and-forget, as
  `publish_bom` already is.
- Authoring anything missing. Gaps are reported, never fixed.

## Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Workflow input | Top file name **and** an exported BOM | Steps 1–3 need Vault file identity; steps 4–6 are built around BOM exports. One input cannot serve both without rewriting three working tools. |
| File list for steps 2–3 | Derived from step 1's result | `check_file_name(recursive=True)` already returns every child with both IDs. No second Vault walk, no item hop. |
| Steps 4–6 execution | Preview in the wizard, apply on a second click | Preserves each tool's existing write gate and gives the wizard a measured pass/fail instead of "the user closed a window". |
| `Run all remaining` | Halts at every pending apply | A release should never write to Vault or SharePoint unattended. |
| Step 6 gate | None — single click | It writes a local `.xlsx` only. A gate guarding nothing trains people to click through gates. |
| Property Check failure | Blocks steps 2 and 3 only | Releasing a non-compliant file is wrong; building a purchasing sheet while properties are still being fixed is useful. |
| Force override | Kept | Same escape hatch as today. |
| Dropped steps | Readiness report, Download local, Inventor rebuild, Release items | Two are item-based and dead; Property Check's own Excel export replaces the readiness report; download-and-rebuild is a separate concern from releasing. |
| Palette | Extracted to `gui/theme.py`, re-exported | 8 modules import it from the file being rewritten. |
| Engine location | `release_steps.py` at project root | Matches the existing `publish_bom.py` ↔ `gui/publish_bom.py` split. |
| Search dialog | Re-pointed to `api.search_files` | The input is now a file name, so searching items finds the wrong thing. |

## Architecture

```
gui/theme.py           NEW    palette + widget factories (pure extraction)
release_steps.py       NEW    six headless step engines, no Tk
gui/release_workflow.py REWRITE  wizard shell: inputs, step list, output, dispatch
gui/launcher.py        EDIT   un-break the tile
scripts/check_file_properties.py  EDIT  optional session reuse
```

`gui/release_workflow.py` re-exports the palette names from `gui.theme` so all
eight importers keep working unchanged.

### Data flow

```
Top file name  CD-001659.iam ──┬─► 1  Property Check
                                │       check_file_name(recursive=True)
                                │       └─► info + children, each carrying
                                │           file_version_id AND file_id
                                │
                                ├─► 2  Sync Properties
                                │       SyncProperties job per file_version_id
                                │
                                └─► 3  Release Files
                                        update_file_lifecycle_states(file_ids, state_id)

BOM export  CD-001659.xlsx ────┬─► 4  → Purchased Parts List   bom_list_sync
                                ├─► 5  → Publish Deliverables  publish_bom.scan_bom
                                └─► 6  → Purchasing Sheet      bom_purchasing
```

Step 1 is the sole source of file identity for steps 2 and 3. Steps 4–6 read
only the BOM export and never consult step 1.

## Component: the step contract

Every engine in `release_steps.py` returns the same shape:

```python
@dataclass
class StepOutcome:
    ok: bool
    summary: str                       # one line, for the status bar
    lines: list[tuple[str, str]]       # (text, tag) for the OUTPUT panel
    pending_apply: Callable[[], StepOutcome] | None = None
    result: Any = None                 # step 1's compliance dict; None elsewhere

    @property
    def needs_review(self) -> bool:    # pending_apply is not None
        ...
```

`StepOutcome.needs_review` (a property, `pending_apply is not None`) is the
single named check the wizard uses. **It takes precedence over `ok`:** a preview
may legitimately report problems and still offer Apply — step 5 previewing
drawing gaps is exactly that case. Callers test `needs_review` before `ok`, and
`Run all remaining` halts on it. Giving this one name, rather than an
`is not None` test repeated at each call site, is what keeps the "nothing
reaches Vault unattended" invariant from resting on six engines each
independently getting an unwritten precedence rule right.

`result` is the channel step 1's compliance dict travels through to steps 2
and 3; it is `None` for every other step.

`pending_apply is None` means the step is finished. When it is set, the step
has computed a preview and staged a write. The wizard then:

1. renders `lines` into the output panel,
2. relabels the primary button to `Apply N …`,
3. sets the step status to a new `REVIEW` state, and
4. **stops a `Run all remaining` sequence** rather than continuing.

`REVIEW` is a new entry in `STATUS_TAGS` alongside the existing PENDING /
RUNNING / OK / SKIPPED / FAILED / BLOCKED, rendered in the amber warning
colour. Overloading `RUNNING` would make a step waiting on a human look
identical to one still talking to Vault.

Clicking Apply calls `pending_apply()` and uses the returned outcome as final.
Clicking Skip discards it, writing nothing.

This keeps every existing preview-before-write checkpoint intact while giving
the wizard a real result to report.

| Step | Preview | Apply |
| --- | --- | --- |
| 1 Property Check | full pass/fail report | none — read-only |
| 2 Sync Properties | files that will be synced | submit jobs |
| 3 Release Files | files + resolved target state id | lifecycle change |
| 4 → Purchased Parts List | dry run against the SharePoint list | write additions |
| 5 → Publish Deliverables | `scan_bom`, including drawing gaps | submit PDF/STEP jobs |
| 6 → Purchasing Sheet | none | writes `.xlsx` locally |

## Component: Property Check (step 1)

Calls `check_file_properties.check_file_name(file_name, recursive=True)`.

The result dict already carries everything downstream steps need:

- `info.file_version_id` / `info.file_id` — the top file
- `children[].file_version_id` / `children[].file_id` — every CAD BOM child,
  de-duplicated on the File master by `fetch_cad_children`

The wizard stores the whole result on the instance and derives two lists from
it on demand: version IDs for step 2, master IDs for step 3.

Output formatting mirrors the existing compliance summary — top item header,
children roll-up, then only the offenders with their failing properties.

### Session reuse

`check_file_name` builds its own `VaultRestAPI` and signs in from
`config.json`, ignoring any existing session. The wizard is handed an
authenticated session by the launcher, so this re-authenticates on every run
of step 1.

Add optional `api` and `vault_id` parameters. When both are supplied, skip the
sign-in block and use them; when neither is, behave exactly as now. Existing
callers — the CLI and `gui/file_property_check.py` — are unaffected.

**Supplying exactly one raises `ValueError`.** Silently falling back to a full
sign-in was the first draft, and review showed it hides a reachable bug rather
than tolerating a hypothetical one: `scripts/release_workflow.py` derives
`vault_id` as `str(... or ... or "")` without validating it, so a sign-in that
returns `error: False` with an unexpected payload yields a truthy `api` and an
empty `vault_id` — and `_ensure_signed_in` stores that pair and reports
success. In that state the lenient version would silently re-authenticate on
every call, so the optimisation quietly no-ops with nobody the wiser; and where
`config.json` is absent — a real deployment mode for the standalone exe — it
would report `config.json is missing vault.servername`, blaming config for what
is actually a session fault.

Raising is safe for the wizard because `run_property_check` turns any exception
into `StepOutcome(ok=False, summary=str(exc))`, so a partial session surfaces as
a cleanly failed step 1 with an accurate message.

## Component: Sync Properties (step 2)

Unchanged in substance from today's step 3, except the file list comes from
step 1 rather than from `_associated_file_versions`.

Preview lists the files. Apply submits one
`Autodesk.Vault.SyncProperties` job per file version, with
`params={"FileVersionId": fid}`.

Job params must be PascalCase — the REST layer is case-sensitive here even
though responses echo camelCase.

## Component: Release Files (step 3)

Unchanged in substance from today's step 6. Master IDs come from step 1's
`file_id` values.

State resolution keeps the existing two-tier approach: an explicit
**State ID (override)** wins; otherwise look up the first file via
`vault_sdk.lookup_file` and resolve the target state name within *that file's*
lifecycle definition, since the state must live in the file lifecycle rather
than the item one.

Preview shows the file count and the resolved state id before anything moves.

## Component: the three BOM steps (4–6)

Each wraps an existing engine. The wizard supplies the BOM path from its own
input field, so none of these tools needs a prefill parameter — the GUI
wrappers are bypassed entirely and the engines are called directly.

- **4 Purchased Parts List** — `bom_list_sync` dry run, then apply. The
  reference list is *Engineering Purchased Parts*, keyed on `SF-######`
  numbers; ISO/DIN fasteners in Inventor BOMs will not auto-match and show as
  additions. Preview must make that visible so they are not added blindly.
- **5 Publish Deliverables** — `publish_bom.scan_bom`, then submit. The scan
  already reports Make parts with no drawing as gaps; those surface in the
  preview and are never created.
- **6 Purchasing Sheet** — `bom_purchasing` builds and writes the workbook in
  one click, reporting the output path.

## Gate

The wizard's `_compliance_blocked()` method is replaced by
`release_steps.property_check_blocked(compliance, *, force)` — a module-level
function, not a GUI method, so it is testable without a window. It is consulted
by **steps 2 and 3 only**.

It returns a reason string when blocked and `None` when clear. It blocks when
step 1 has not run, or when step 1 found failures and **Force past compliance
gate** is unticked. Steps 4–6 never consult it and are runnable from the moment
a BOM path is set.

`force` covers failing properties but deliberately does **not** cover a missing
step 1 result: steps 2 and 3 take their file list from step 1, so with no step 1
there is nothing to force past.

### The gate must distinguish "checked and passed" from "not checked"

Code review found the first draft resolving absent data to the permissive
answer in three places, each ending in a release that reports success while
files stay behind. The producer's own contract makes this explicit —
`evaluate_against_rules` sets `report = None` when no rule set matches
"which reports as SKIP, never as a pass"
(`scripts/check_file_properties.py:420`), and `result_exit_code` returns a
distinct code **2** for that case rather than 0.

Reading `report` with `(x or {}).get("failed", 0)` turns `None` into `0` into
"clear". The gate was therefore strictly more permissive than the CLI on
identical input. Three rules, decided:

| Condition | Blocks? | `force` overrides? | Why |
| --- | --- | --- | --- |
| No step 1 result | Yes | **No** | Steps 2/3 have no file list at all. Nothing to force past. |
| Top file has no rule set (`category_resolved` falsy, `report is None`) | Yes | **Yes** | Nothing was evaluated. A category may legitimately have no rules, so an un-forceable block would make the wizard unusable for that work. The gate exists to stop *unknowing* release of unchecked files; ticking Force is informed consent. The message must say "nothing was checked", never imply failure. |
| `children_error` set (the CAD BOM walk failed) | Yes | **No** | The child list is silently incomplete. The user cannot consent to a partial release because they cannot know what is missing — step 3 would move the top assembly and whatever children happened to resolve, leaving the rest in WIP and reporting success. Re-running step 1 is cheap. |

Children with no rule set are **not** blocked — they stay SKIP, matching
`child_status`, which already treats an unresolved child category as SKIP
rather than FAIL. Only the top file escalates, matching `result_exit_code`.

### Dropped files must be visible

`file_version_ids` / `file_master_ids` drop entries carrying no usable Vault ID
— correct, since one bad ID fails an entire lifecycle batch. But dropping them
at `logger.debug` is invisible: no GUI module configures a logging handler and
`app.py` defaults to INFO, so those lines go nowhere. Step 3 would release 37 of
40 files and report "37 moved" with no trace of the 3 left behind.

`unresolved_files(compliance) -> list[tuple[str, str]]` names them as
`(name, missing)`, where `missing` is `"version"`, `"master"` or `"both"`.
Steps 2 and 3 must surface it in their preview, each filtering to the kind it
actually drops.

The attribution matters. A file can carry a valid version ID and no master ID:
step 2 syncs it perfectly well, step 3 silently drops it. Returning bare names
would make step 2's preview announce "will be skipped" about a file it handles
fine — and a preview that cries wolf trains people to click through it, which
is the same failure this exists to prevent. Returning the *kind* lets each step
say something true.

It returns `[]` for a falsy `compliance`. That is unreachable in practice — the
gate blocks a missing step 1 result un-forceably — but it means the function is
not a standalone health check: `if not unresolved_files(c)` reads "all clear"
when step 1 never ran.

## Search dialog

The wizard gets a **new** `FileSearchDialog`: `api.search_files`, columns File
Name / Revision / State / Category / Folder, and selecting a result fills the
**Top file name** field.

The existing item-based `SearchDialog` is **not** re-pointed. It is moved
verbatim to `gui/search_dialog.py`, because `gui/mfg_package.py` imports it and
satisfies a six-member duck-typed contract for it — `root`, `api`, `vault_id`,
`_brand_button`, `_ensure_signed_in`, `set_part_number`. MFG Order Package is
out of scope for this rewrite and genuinely wants item search, so mutating the
shared dialog would silently change a tool we said we were not touching.

This also means the wizard's `set_part_number` hook is deleted rather than
renamed; `set_top_file` is a new hook belonging to the new dialog.

The threading structure — worker thread, `queue.Queue`, Tk-thread drain — is
correct as written and `FileSearchDialog` copies its shape.

## Launcher

Remove `broken=True` from the Release Workflow tile and rewrite its
description to name the six steps. MFG Order Package keeps its broken flag.

## Error handling

- A step that raises is caught by the existing `thread_main` wrapper, logged to
  the output panel, and marked `FAILED`. A failure never auto-continues a
  `Run all remaining` sequence.
- Missing top file name blocks steps 1–3 with a clear message; missing BOM path
  blocks steps 4–6 the same way. Neither blocks the other group.
- Changing either input resets dependent step statuses, so a stale step 1
  result cannot silently feed steps 2 and 3 after the file name changes. This
  mirrors `publish_bom`'s `_invalidate_scan` trace.
- `vault_sdk` and Vault sign-in failures are reported at the step that needs
  them, not at window construction.

## Testing

New `tests/test_release_steps.py`, against the engines rather than the GUI:

- file-version and master-ID lists are derived correctly from a compliance
  result, including de-duplication and children with `error` set
- `_property_check_blocked` truth table: no result / clean / failures / failures
  with force
- every `pending_apply` step performs no writes during preview — asserted with
  a fake session that fails the test if a write method is called
- `pending_apply()` returns an outcome whose `ok` reflects the underlying
  engine result
- a step whose engine raises produces `ok=False` rather than propagating

Existing `tests/test_launcher_flags.py:31` asserts Release Workflow is
disabled. That assertion inverts; MFG Order Package stays in the broken list.

`tests/test_check_file_properties.py` gains a case for the new optional
session parameters — supplied, and omitted.

## Open questions

Both resolved while writing the implementation plan:

1. **SharePoint auth works in-process.** `supplier_pricing.cli._connect_client`
   calls `GraphListClient.connect(..., interactive=False)`, so step 4 needs no
   interactive sign-in. When the token cache is empty it raises "not signed
   in"; the step surfaces the existing one-off fix, `python -m supplier_pricing
   probe`, rather than a bare error.
2. **The sheet writer takes an explicit directory.**
   `bom_purchasing.generate_from_file(bom_file_path, assembly_number,
   output_dir="", reference_path="")` writes
   `{assembly_number}-PurchasingExport.xlsx` into `output_dir`, defaulting to
   the BOM's own folder, and returns `output_path`. Nothing targets Downloads.
