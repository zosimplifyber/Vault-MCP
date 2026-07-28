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

Properties arrive as `[{propertyDefinitionId, value}]` with no names, so
resolving them needs a second call to `/property-definitions` to build an
`id → displayName` map.

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
| `scripts/check_file_properties.py` | **New.** File-name in, compliance report out. CLI + GUI. |
| `file_property_rules.json` | **New.** Rule sets keyed by file Category Name. |
| `vault_rest_api.py` | **New method** `search_file_versions()`. Existing methods untouched. |
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

**One judgment call:** `Title` gets a `forbidden_patterns` entry rejecting a
value that is only the part number (`^(CD|SF|MFG|DT)-\d+$`). Mirroring the
standard means a Title repeating the file number is not a title. One line to
delete if unwanted.

### Expected output for `CD-001659.iam`

```
Assembly - Engineering                              9/12 passed

[FAIL]  Title              CD-001659
        > contains forbidden pattern — Title is just the file number
[FAIL]  Description (File) bmw kft90 hot a adapter plate assembly
        > does not match pattern /^[a-z][a-z \-]*[a-z]$/
[FAIL]  CAD Category       (empty)
        > missing (required)
```

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
  fields, and prefers populated values over empty ones.
- File-name resolution: exact match wins over a prefix match; ambiguity sets
  `note`; no match raises.
- Rules applied to the recorded fixture produce exactly the three expected
  failures (`Title`, `Description (File)`, `CAD Category`) and nine passes.
- Every category in `file_property_rules.json` parses and its rules are
  well-formed (regexes compile).
- Unknown category → SKIP, not a pass.

Plus a live smoke run of the CLI against `CD-001659.iam` before completion.

## Out of scope

- Any change to the release workflow, its readiness gate, or its MCP tool.
- Writing properties back to Vault (read-only tool).
- An MCP tool wrapping the file check — possible follow-up.
- BOM quantities (the known `/uses` gap; irrelevant to property compliance).
