# File-based Property Check — Design

**Date:** 2026-07-28
**Status:** Approved

## Problem

The Property Check tool checks Vault **items**. The Item Master is being
retired, so the tool has been flagged `BROKEN — Item Master retired` in the
launcher since 2026-07-23. The
[deferred CAD/iProperty rewrite](2026-07-23-purchasing-inventor-import-and-broken-flags-design.md)
called for it to "be rewritten to validate file iProperties or retired."

This spec rewrites it onto files. You type a file name — `CD-001659.iam` — and
the tool pulls that file's properties from Vault and reports which ones are out
of compliance.

## Key finding: the file-side propDefIds parameter is spelled differently

Vault REST v2 returns only system fields unless you ask for property
definitions by ID. For **items** the parameter is `propDefIds` (already
recorded in project memory). For **files** the OpenAPI spec defines it as
`option[propDefIds]`, and it accepts the literal value `all`:

```
components.parameters.propDefIds:
  in: query
  name: option[propDefIds]
  description: The properties that need to be returned. property ids separated
               by ',', e.g. '1,2,3' 'all' means return all properties.
```

`VaultRestAPI.search_files()` and `get_folder_contents()` send `propDefIds`,
which Vault silently ignores on file endpoints — no error, no properties. This
is why file properties have never come back. Verified against
`CD-001659.iam`: `propDefIds=<csv>` returns 0 properties, `option[propDefIds]=all`
returns 74.

Properties arrive as `[{propertyDefinitionId, value}]` with no names — but the
same response embeds an `included.propertyDefinition` map naming every property
it returned, so a single request yields both the values and their display
names. `/property-definitions` is only needed as a fallback when that block is
absent.

The same applies to `/file-versions/{id}/uses`: passing the selector enriches
the parent **and every child** in one call, so walking a CAD BOM costs one
request regardless of child count (unlike the item-side BOM walk, which
hydrates per child).

### Ground truth — `CD-001659.iam`

| Property | Value |
|---|---|
| Category Name | `Assembly - Engineering` |
| Revision / State | `4` / `Released` |
| Title | `CD-001659` |
| Description (File) | `bmw kft90 hot a adapter plate assembly` |
| Source | `Make` |
| Engineer / Designer | `Alan Y.` / `Alan Y.` |
| Engr Approved By | `Zak O.` |
| Project | `BMW` |
| Vendor | `Yaodi` |
| Material Finish | `As Finished` |
| CAD Category | *(empty)* |

## Scope

Replace the tool the user opens. Leave the release workflow alone.

`scripts/check_item_properties.py` is imported by `scripts/release_workflow.py`,
`gui/release_workflow.py`, and the `vault_release_readiness_report` MCP tool. It
stays untouched and becomes a library rather than something you open. The new
file tool imports its rule engine rather than duplicating it.

| File | Change |
|---|---|
| `scripts/check_file_properties.py` | **New.** File-name in, compliance report out. Fetch + rules + CLI. |
| `gui/file_property_check.py` | **New.** The GUI, styled from the shared brand palette. |
| `file_property_rules.json` | **New.** Rule sets keyed by file Category Name. |
| `vault_rest_api.py` | **New method** `search_file_versions()`; `get_file_uses()` gains a `prop_def_ids` passthrough. Existing behavior unchanged. |
| `gui/launcher.py` | Property Check row re-points at the new tool; `broken=True` removed. |
| `tests/test_check_file_properties.py` | **New.** Unit tests over a recorded fixture. |
| `scripts/check_item_properties.py` | **Untouched.** |
| `scripts/release_workflow.py`, `gui/release_workflow.py`, `mcp_server.py` | **Untouched.** |

## Architecture

### Data flow

```
"CD-001659.iam"
  → GET /vaults/{id}/file-versions?q=CD-001659.iam&option[propDefIds]=all
  → properties as [{propertyDefinitionId, value}]
  → GET /vaults/{id}/property-definitions   (cached per vault)
      → {id: (displayName, systemName)}
  → flatten to {display name: value}, merged with record system fields
  → Category Name → rule set → evaluate_rule()   (imported from the item tool)
  → report (text / Markdown / JSON)
```

`--recursive` additionally walks `/vaults/{id}/file-versions/{id}/uses` for
child files and runs the same rules against each.

### Components — `scripts/check_file_properties.py`

Each unit is independently testable; only `fetch_*` touch the network.

| Unit | Responsibility |
|---|---|
| `get_property_definition_index(api, vault_id)` | One `/property-definitions` call → `{id: {displayName, systemName, isSystem}}`. Cached per vault for the process lifetime. |
| `flatten_file_properties(record, defs)` | `{propertyDefinitionId, value}` list + record system fields → `{display name: value}`. Populated values win over empty ones. |
| `fetch_file(api, vault_id, file_name)` | Resolve a file name to one file version. Exact case-insensitive name match preferred; notes ambiguity when several match. Raises `RuntimeError` when nothing matches. |
| `fetch_cad_children(api, vault_id, file_version_id)` | Child file versions with properties, hydrating per child if `/uses` does not enrich. |
| `check_file_name(...)` | Orchestrates sign-in → fetch → evaluate. Returns the same result shape as the item tool's `check_part_number` so report renderers stay near-identical. |
| `format_markdown_report(result)` | Markdown report, file flavor. |
| `run_cli(args)` / `run_gui()` | Presentation only. |

Rule evaluation is **imported** from `check_item_properties`:
`evaluate_rule`, `resolve_category`, `check_properties`, `load_json`. One rule
engine, one place to fix it. No circular import — the item module does not
import the file module.

### Rule set — `file_property_rules.json`

The item standard ported to file property names. Same JSON schema, so the same
engine and the same `Edit Property Rules` button work unchanged.

| Item property | File property |
|---|---|
| `Number` | `File Name` |
| `Title (Item,CO)` | `Title` |
| `Description (Item,CO)` | `Description (File)` |
| `Units` | *(dropped — no file equivalent)* |
| — | `CAD Category` *(file-only, added)* |
| — | `Material Finish` *(file-only, added)* |

`Revision`, `State`, `Source`, `Material`, `Vendor`, `Vendor Number`,
`Engineer`, `Engr Approved By`, `Designer`, `Project`, `Category Name` keep
their names.

Categories covered: `Assembly - Engineering`, `Part - Engineering`,
`Part - Purchased`, `Drawing - Engineering`, `Part - Content Center`. Any other
category (`Documents`, `Design Representation`, `Unclassified`, `DT Data`)
reports **SKIP — no rule set** rather than a false pass.

`CAD Category` is gated with per-rule-set `allowed_values` (e.g. under
`Assembly - Engineering`, `allowed_values: ["Assembly - Engineering"]`), which
enforces "CAD Category matches Category Name" without adding a cross-property
rule type to the engine.

**Strictness:** mirror the item standard as-is. Expect failures on first run
against today's data — purchased files carry `Source = N/A` and 38/38
engineering parts carry `Engr Approved By = NOT REVIEWED`. That drift is the
thing worth surfacing. The rules are JSON and reloaded every run, so they are
tunable without a code change.

**What is gated:**

| Property | Required in |
|---|---|
| `State`, `Revision`, `Source` | every category |
| `Engineer`, `Engr Approved By`, `Project` | every category except `Part - Content Center` |
| `Designer` | every category except `Assembly - Engineering` and `Part - Content Center` |
| `Vendor` | every part and assembly, no exemption |
| `Vendor Number` | parts, with a published-standard exemption |

`Engr Approved By` forbids the literal `NOT REVIEWED` in **every** category —
including the ones where it isn't required — since that string means the review
has not happened. `Vendor Number` keeps its published-standard exemption
because a generic ISO screw has a supplier but no single supplier SKU.
`Designer` is exempt on assemblies because the design credit lives on the child
parts, and on Content Center because nobody in-house designs a library
fastener — the same reasoning drops `Engineer`, `Engr Approved By` and
`Project` there too.

**Declared but never gated:** `Title`, `CAD Category` and `Description (File)`
carry `required: false` and no other checks, so their values still appear in
the report but can never fail a file. Two of these stay declared for a
second reason: the `Vendor Number` exemption reads `Title` on
`Part - Purchased` and `Description (File)` on `Part - Content Center`, so
deleting the keys would silently break those exemptions.

`Description (File)` was gated on the Vault PDM – Item Description standard and
was ungated on request; its rule carries the exact JSON needed to restore it.

The test suite parametrises over every category and asserts each of these,
so dropping one from a rule set fails the build rather than silently weakening
the gate.

**One judgment call:** `Title` gets a `forbidden_patterns` entry rejecting a
value that is only the part number (`^(CD|SF|MFG|DT)-\d+$`). Mirroring the
standard means a Title repeating the file number is not a title. One line to
delete if unwanted.

### Actual output for `CD-001659.iam`

```
Assembly - Engineering                            16/16 properties passed
```

The file passes every gated property. Its odd-looking `Title` (`CD-001659`),
empty `CAD Category` and customer-name-carrying description are all reported
but ungated.

With `--recursive`, all three CAD BOM children still fail — two are categorised
`Part - Engineering` but carry `Source = Buy`, and `CD-001624.ipt` adds a blank
`Vendor` and `Engr Approved By = NOT REVIEWED`.

### Excel export

`export_to_excel(result, path)` writes a two-sheet workbook using the same
palette as `bom_purchasing.py`, so it sits alongside the purchasing sheets:

- **Summary** — one row per file: status and which properties failed. This is
  the sheet you hand to someone; the score columns were dropped because the
  named failures are what you act on.
- **Detail** — one row per property checked, with its current value and the
  reason it failed.

Both sheets get an autofilter and frozen headers so a long BOM walk stays
navigable, and rows are colour-coded PASS / FAIL / SKIP. A file with no rule
set contributes exactly one SKIP row — it must never read as compliant.

Reached by `--excel [PATH]` on the CLI (bare flag → timestamped file in `Log/`)
and the **Export to Excel** button in the GUI, which unlocks only after a
successful check and re-uses the in-memory result rather than re-querying
Vault. Writing over a workbook that's open in Excel raises a `RuntimeError`
that says so.

### Surface

**CLI**

```
python scripts/check_file_properties.py CD-001659.iam
python scripts/check_file_properties.py CD-001659.iam --recursive
python scripts/check_file_properties.py CD-001659.iam --json
```

Flags: `--recursive/-r`, `--json`, `--show-all-props`, `--category`, `--rules`,
`--config`, `--gui`, `--no-gui`. Exit codes: `0` all pass, `1` one or more
failures, `2` no rule set matched.

**GUI** — File Name entry (hint `e.g. CD-001659.iam`), Check button, category
override dropdown, and checkboxes for *show all Vault properties*, *check
children (walk CAD BOM)*, *show passing child details*. Styled with the brand
palette imported from `gui/release_workflow` so it matches the rest of the
suite, replacing the old tool's dark-terminal look.

## Error handling

| Condition | Behavior |
|---|---|
| File name not found | `RuntimeError` → CLI exits with a message; GUI shows it in the results pane. |
| Several files match | Prefer exact case-insensitive name match; otherwise use the first and attach a `note` shown in the report. |
| Category has no rule set | Report SKIP, exit 2. Never a silent pass. |
| `/property-definitions` fails | Property names cannot be resolved — fail loudly rather than report a file with no properties as compliant. |
| Child fetch fails | That child reports ERROR with the message; siblings still evaluate. |
| Vault sign-in / config missing | `RuntimeError` naming the missing `config.json` key. |

## Testing

`tests/test_check_file_properties.py`, over a fixture recorded from the live
probe of `CD-001659.iam` (`tests/fixtures/`) — no network in the test suite:

- `flatten_file_properties` resolves ids to display names, merges system
  fields, prefers populated values over empty ones, keeps historical twins from
  shadowing live values, and skips values whose definition did not come back
  rather than inventing a key for them.
- File-name resolution: exact match wins over a prefix match, ignores case,
  sets `note` on ambiguity, raises on no match.
- The recorded fixture passes every gated property; blanking one gated column
  produces exactly one failure, proving the rule set hasn't been hollowed out.
- Parametrised over every gated column × every category: it is required exactly
  where it should be, and blanking it is reported in gated categories and
  ignored in exempt ones. `Engr Approved By` rejects `NOT REVIEWED` even where
  it isn't required. Every part category requires `Vendor` with no exemption.
  `Title`, `CAD Category` and `Description (File)` appear in the report but
  pass on every value including blank.
- A fully-populated Content Center part passes with all four sign-off fields
  empty.
- Excel export: two sheets with filters and frozen headers; one summary row per
  file and one detail row per property; a no-rule-set file exports as SKIP, not
  PASS; a locked target raises a message naming Excel; missing directories are
  created. The GUI's Export button unlocks only after a successful check and
  re-locks after a failed one.
- Every category in `file_property_rules.json` parses, its regexes compile, and
  each `required_unless` names a property the same rule set checks.
- No rule set uses item-only property names (guards against copy-paste drift).
- CAD BOM children arrive enriched and are deduplicated by file version; a
  failed walk raises with the Vault message.
- Exit codes: 0 / 1 (file or child failure) / 2 (no rule set); a SKIP child is
  not a failure.
- The GUI builds headless and renders a fixture-derived report.

Plus a live run of the CLI against `CD-001659.iam` before completion.

## Out of scope

- Any change to the release workflow, its readiness gate, or its MCP tool.
- Writing properties back to Vault (read-only tool).
- An MCP tool wrapping the file check — possible follow-up.
- BOM quantities (the known `/uses` gap; irrelevant to property compliance).
