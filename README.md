# Vault MCP Server

An MCP (Model Context Protocol) server that exposes Autodesk Vault REST API operations as tools, enabling AI assistants like Claude to browse, search, generate purchasing sheets from, and submit jobs against an Autodesk Vault server.

## Prerequisites

- Python 3.10+
- An Autodesk Vault server with REST API access enabled
- Vault user credentials with appropriate permissions
- Autodesk Vault Job Processor 2025+ on a reachable machine if you plan to submit publish/sync jobs
- **Autodesk Vault Client (with an activated license) installed on any machine running the SDK / SOAP scripts** — `scripts/vault_sdk.ps1`, the diagnostic probes under `scripts/probes/` (e.g. `probe_edit_items.ps1`, `probe_vault_sdk.ps1`), and any GUI mode that performs writes. These scripts request a `Client` (per-machine) Vault license seat at sign-in and will fail with `VaultLicenseException` if no Vault Client is installed, no seat is available, or the same user is already signed in to Vault Explorer. The core REST server (`app.py` in `sse` / `stdio` mode) does not require this.
- **`AdskLicensingSDK_8.dll` reachable from PowerShell's DLL search path.** The Vault SDK assemblies P/Invoke into this native library to acquire a license seat; without it on `$env:PATH`, every writable login flow fails with `"Failed to acquire a license"` even though a seat is available. The SDK scripts now prepend `C:\Program Files\Autodesk\Autodesk Vault 2025 SDK\bin\x64` (and the matching Vault Client `Explorer\` folder) to `$env:PATH` automatically — but if you set `$env:VAULT_SDK_BIN` to a non-default location, make sure that folder contains `AdskLicensingSDK_8.dll`. (For Vault 2020-era installs the file is named `AdskLicensingSDK_2.dll`; the scripts probe both names.)
- **A Vault user account with the Item Editor role assigned** if you intend to use SDK writes (`update_item_properties`, `update_item_lifecycle_states`, etc.). Read-only operations are unaffected. Have a Vault admin assign the role in **ADMS Console → Users → Roles**.

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Copy the template, then edit it with your real credentials:

```bash
cp config.json.example config.json
```

`config.json` is gitignored — keep your credentials local. Edit before running:

```json
{
    "vault": {
        "servername": "http://VaultServer",
        "username": "Administrator",
        "password": "your-password-here",
        "database": "Vault"
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8765
    },
    "logging": {
        "level": "INFO",
        "file": "Log/mcp_server.log"
    }
}
```

| Field | Description |
|---|---|
| `vault.servername` | Hostname or IP of your Vault server (include scheme, e.g. `http://`) |
| `vault.username` | Vault login username |
| `vault.password` | Vault login password |
| `vault.database` | Vault database name (e.g. `Vault`, `Inventor`) |
| `server.host` | Bind address for SSE mode (`0.0.0.0` = all interfaces) |
| `server.port` | Port for the SSE HTTP server (default `8765`). Note: avoid `8080` — Autodesk Desktop Connector and other agents squat on it and spam the log with `403` WebSocket-handshake retries. |
| `logging.level` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `logging.file` | Path to the rotating log file (relative to project root) |

### Wrike configuration (used by BOM → Manufacturing Tasks)

The standalone Wrike MCP server (the `wrike_*` tools) has moved to its own repo, [SF-WrikeConnector](https://github.com/zosimplifyber/SF-WrikeConnector) — it's an independent service and no longer runs inside this project.

This project still talks to Wrike directly for one feature, **BOM → Manufacturing Tasks** (see below), which needs its own `wrike` block in `config.json`:

```json
{
    "wrike": {
        "token": "your-wrike-permanent-access-token-here",
        "base_url": "https://www.wrike.com/api/v4",
        "allowed_folders": []
    }
}
```

| Field | Description |
|---|---|
| `wrike.token` | Wrike **permanent access token**. Create one at Wrike → **Apps & Integrations → API** → *Permanent access token* → **Create token**. |
| `wrike.base_url` | API base URL. Default `https://www.wrike.com/api/v4` (US data center). EU-hosted accounts use `https://app-eu.wrike.com/api/v4`. |
| `wrike.allowed_folders` | Optional **folder allowlist** (list of folder IDs). When set, task creation refuses to write into a folder located *exclusively outside* these folders and their subfolders — the safe zone. Empty `[]` (or absent) disables the guard. |
| `wrike.mfg_tasks` | Picks remembered by **BOM → Manufacturing Tasks** — last-used `project_id`, per-stage `owners` (contact IDs), and the three `_days` stage durations. Written back to `config.json` automatically each time you create tasks; there's nothing to hand-edit here. |

If no `wrike` block (or token) is present, **BOM → Manufacturing Tasks** shows a warning when opened; everything else in this project runs normally.

## Running the Server

### Recommended: launcher dashboard (default)

```bash
python app.py
```

This opens the **Vault Integration launcher** (Tk dashboard) and auto-starts the SSE MCP server on `http://127.0.0.1:8765/sse` inside the same process. From the dashboard you can also launch the Release Workflow wizard, BOM → Purchasing sheet, Property Check, BOM → Publish Deliverables, and BOM → Manufacturing Tasks — all sharing the same Vault session as the MCP server. One sign-in, one audit trail.

The server endpoints:
- Dashboard: opens automatically (no URL — it's a desktop window)
- SSE endpoint for MCP clients: `http://127.0.0.1:8765/sse`
- Messages endpoint: `http://127.0.0.1:8765/messages`

**The launcher must stay open while any MCP client is using the server.** Closing it prompts to confirm because it would disconnect Claude Desktop / Claude Code mid-session.

### Daily startup (Windows)

A startup shortcut at
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Vault MCP Launcher.lnk`
runs `pythonw.exe app.py` at login so the launcher is already up before you open Claude Desktop. To create or recreate it:

```powershell
$startup = [Environment]::GetFolderPath('Startup')
$sc = (New-Object -ComObject WScript.Shell).CreateShortcut("$startup\Vault MCP Launcher.lnk")
$sc.TargetPath = 'C:\Users\<you>\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe'
$sc.Arguments = '"C:\path\to\Vault-MCP\app.py"'
$sc.WorkingDirectory = 'C:\path\to\Vault-MCP'
$sc.Save()
```

`pythonw.exe` (rather than `python.exe`) keeps the console window from appearing — only the Tk dashboard is visible. Logs still go to `Log/mcp_server.log`.

### Other run modes

```bash
python app.py --headless              # bare SSE WebServer, no GUI (for unattended hosts)
python app.py --gui                   # launcher dashboard, MCP server NOT auto-started (manual Start button)
python app.py --transport stdio       # stdin/stdout MCP transport (used directly only when bridging proxies)
python app.py --workflow --part-number SF-001717   # skip launcher, open Release Workflow wizard pre-filled
python app.py --config path/to/my_config.json      # custom config path
```

### Property Check

Type a Vault file name and get back every property that is out of compliance. Open it from the launcher dashboard, or run it directly:

```bash
python scripts/check_file_properties.py CD-001659.iam              # one file
python scripts/check_file_properties.py CD-001659.iam --recursive  # + every child in the CAD BOM
python scripts/check_file_properties.py CD-001659.iam --markdown   # Markdown report
python scripts/check_file_properties.py CD-001659.iam --excel      # also write an .xlsx to Log/
python scripts/check_file_properties.py CD-001659.iam -r -x out.xlsx   # BOM walk → named workbook
python scripts/check_file_properties.py                            # no argument → GUI
```

Exit codes: `0` everything passed, `1` at least one failure, `2` no rule set matched the file's category.

`--recursive` grades each child at its **latest** version, not the version the parent assembly pins. A parent often references an older revision of a child, so grading the pinned version would keep reporting failures you have already fixed. If a child's latest version can't be read it reports ERROR rather than being graded on stale data.

**Excel export.** `--excel` (bare) drops a timestamped workbook in your **Downloads** folder (same place the MFG package builder and purchasing sheet land); give it a path to choose the name. In the GUI, **Export to Excel** unlocks once a check succeeds. The workbook has two sheets — **Summary** (one row per file: status and which properties failed) and **Detail** (one row per property checked) — both filterable with frozen headers and colour-coded PASS / FAIL / SKIP rows.

Rules live in [`file_property_rules.json`](file_property_rules.json), keyed by the file's Category Name, and are re-read on every run — edit and re-check, no restart. What's gated:

Categories split into **in-house work** (`Assembly - Engineering`, `Part - Engineering`, `Drawing - Engineering`) and **bought parts** (`Part - Purchased`, `Part - Content Center`) — catalogue hardware and Inventor library files nobody in-house designs, engineers, approves, or bills to a project.

| Property | Required in |
|---|---|
| State | every category |
| Source | every category except `Part - Content Center` |
| Revision | every category except `Part - Purchased` |
| Engineer, Engr Approved By, Project | in-house categories only |
| Designer | in-house categories except `Assembly - Engineering` |
| Vendor | every part and assembly |
| Title, CAD Category, Description (File) | **nowhere** — reported for reference only |

`Engr Approved By` rejects `NOT REVIEWED` on in-house work, where it means the review hasn't happened; bought parts allow it. A category with no rule set at all reports SKIP rather than a misleading pass.

`Description (File)` was gated on the *Vault PDM – Item Description* standard (lowercase keyword nouns; no dimensions, materials, ISO/DIN numbers, or project/customer names) and is currently switched off. Its rule in the JSON carries the exact snippet needed to turn it back on.

Note this checks **files** (iProperties). The item-side equivalent, `scripts/check_item_properties.py`, still backs the Release Workflow's readiness report and uses `item_property_rules.json`.

### BOM → Manufacturing Tasks

Reads a **generated purchasing workbook** — the output of BOM → Purchasing Sheet, after suppliers have been filled in — and creates the Wrike tasks for it. Open it from the launcher dashboard (**BOM → Manufacturing Tasks** → **Open Task Builder**); there is no CLI entry point.

Every supplier on the sheet gets **one order**: a parent task plus stage subtasks. Part count never changes task count — eleven screws from McMaster-Carr are one order with eleven line items, not eleven tasks. A supplier with both made and bought parts still gets one order, because that is still one PO; Manufacturing then lists only the made parts. Sub-assembly roll-up rows are excluded and their children ordered individually — an assembly's Sub Total is a SUM of its children, so ordering both would double-count.

Stages are **Purchasing → Manufacturing → Shipping**, chained finish-to-start so a slip cascades on the Gantt. An order with nothing to make skips Manufacturing — a catalogue supplier ships from stock — and its lead time drives Shipping instead. An order for `In House` or `Inhouse` skips Purchasing instead — there is no PO to issue to your own shop — so an in-house Make order is Manufacturing → Shipping and takes its Manufacturing owner as the parent's owner.

**Supplier reconciliation** is the heart of it. Each part's supplier is recorded twice — the sheet's Vendor column and the Vault file's Vendor property — and the tool checks them against each other before anything can be previewed:

| Outcome | What happens |
|---|---|
| Both agree | Accepted automatically |
| One side blank | The populated value is proposed — accept it individually, or **Accept all proposals** takes every proposal at once |
| They disagree | Blocked — you pick a side |
| Neither has one | Blocked — you type a supplier, or exclude the part |
| Not in Vault, bought part | Sheet value proposed — a catalogue screw was probably never checked in |
| Not in Vault, made part | Blocked — a missing CD-numbered part means a wrong name or an un-checked-in file |

Comparison ignores case, whitespace and punctuation, so `McMASTER-CARR` matches `McMaster-Carr` and `In-house` matches `In House` — a real sheet spelled the same shop both ways and, before punctuation was folded in, that split one shop into two one-part orders.

Three gated steps guard against writing a board twice: **Load & Reconcile** → **Preview** (enabled only once nothing is unresolved) → **Create Tasks** (enabled only after a Preview, and disabled once used — a second run needs a fresh Preview).

Dates run forward from a start date you set, in business days, weekends skipped, no holiday calendar.

- **Purchasing** always runs its editable default length.
- **Manufacturing**, on an order with Make parts, uses the longest `Lead Time (Business Days)` among that order's made parts, falling back to its editable default when the column is blank.
- **Shipping** uses its own editable default on an order with Make parts; on a Buy-only order it takes Manufacturing's place instead, using the longest lead time among the order's parts and the same kind of fallback.

**Re-runs are safe.** Before creating, the tool looks for a task with exactly the order's title in the target project and skips suppliers that already have one — Wrike's own title filter is a substring match, so the exact comparison happens locally. If the existence check itself errors, the order is skipped as a precaution rather than risk a duplicate board — Wrike has no rollback — and that is reported as its own outcome rather than as "already exists," so a run where the API failed can't read back as a clean no-op.

One caveat, confirmed live: **Wrike's task search does not index a new task immediately.** Creating an order and then re-running within the same minute can duplicate it, because the search that backs the guard cannot see tasks that are seconds old. A few minutes later the same check finds them and skips correctly. In normal use — re-running after a BOM revision — the window has long since passed.

**Folder safe zone.** If `wrike.allowed_folders` is configured, picking a project outside it raises a confirmation naming the project and the zone before anything is created. All projects stay selectable; nothing is created without an explicit yes.

Requires a live Vault session (for the supplier check) and a configured `wrike` block.

The tool remembers your picks in a `wrike.mfg_tasks` block, written back to `config.json` each time you create tasks — and to the config file actually in use, so `--config other.json` is honoured:

```json
{
    "wrike": {
        "mfg_tasks": {
            "project_id": "IEAF...",
            "owners": {"Purchasing": "KUAA...", "Manufacturing": "KUAA...",
                       "Shipping": "KUAA..."},
            "purchasing_days": 2,
            "manufacturing_days": 10,
            "shipping_days": 3
        }
    }
}
```

## Connecting MCP clients

All clients connect to the same SSE endpoint exposed by the running launcher: `http://127.0.0.1:8765/sse`. The launcher must be running first.

### Claude Code

In `~/.claude.json` (user-level) or `.claude/settings.json` (project-level):

```json
{
  "mcpServers": {
    "vault": {
      "type": "sse",
      "url": "http://127.0.0.1:8765/sse"
    }
  }
}
```

For the Wrike `wrike_*` tools, add the [SF-WrikeConnector](https://github.com/zosimplifyber/SF-WrikeConnector) server (its own independent process) instead.

Or via CLI:

```bash
claude mcp add --transport sse vault http://127.0.0.1:8765/sse
```

### Claude Desktop

Claude Desktop's connector currently launches stdio subprocesses, so connecting it to a long-running SSE server requires the [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) bridge. Add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "vault-mcp": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://127.0.0.1:8765/sse"]
    }
  }
}
```

Requires Node.js installed (for `npx`). First launch downloads `mcp-remote` from npm (~5–10 s); subsequent launches use the cache.

After editing, fully quit Claude Desktop from the system tray — not just close the window — and reopen.

### Daily startup checklist

1. Launcher running (auto from the Startup-folder shortcut, or run `python app.py`). Confirm the MCP status dot is green.
2. Open Claude Desktop / Claude Code. They reconnect to the running SSE server automatically.
3. Smoke test: any tool call (e.g. `vault_search_items` for a known part number) should succeed.

If the launcher isn't running when a client tries to connect, you'll see "connection refused" or a red connector status. Start the launcher and re-toggle the connector.

## Session expiration / auto re-authentication

Vault Server times out idle REST sessions (default ~30 minutes), after which every call returns `HTTP 401` with the misleading message _"You currently do not have permissions to download this file."_ — this is **not** an ACL problem; it's the Bearer token having been invalidated.

`VaultRestAPI` in `vault_rest_api.py` handles this transparently: it caches the credentials from the last successful `create_session()` call and, when any subsequent `_request()` returns 401, re-authenticates once with the cached credentials and retries the call. The retry is serialized by an `asyncio.Lock` so concurrent 401s only cause one re-sign-in. If the retry also returns 401 (genuine ACL denial or rotated password), the error propagates normally.

In `Log/mcp_server.log` you'll see this on session expiry:

```
... API error 401: {... 'detail': 'You currently do not have permissions...'}
... Session likely expired — re-authenticating as zolech (database: Simplifyber)
... POST .../sessions  ... 200 OK
... Re-authenticated; retrying GET .../items?q=SF-001717
... Response 200
```

The single visible-to-the-user 401 entry above is harmless — the retry on the next line succeeds.

## Available Tools

| Tool | Description |
|---|---|
| **Server / auth** | |
| `vault_get_server_info` | Get Vault server version and metadata |
| `vault_sign_in` | Authenticate with different credentials |
| `vault_sign_out` | Invalidate the current session |
| **Vaults / folders** | |
| `vault_list_vaults` | List all accessible vaults |
| `vault_get_vault` | Get details for a specific vault |
| `vault_get_folder_contents` | List files and sub-folders in a folder |
| `vault_get_folder` | Get metadata for a specific folder |
| **Files** | |
| `vault_get_file` | Get metadata for a specific file |
| `vault_get_file_versions` | List all versions of a file |
| `vault_get_file_download_url` | Get the download URL for a file |
| `vault_search_files` | Keyword search across vault files |
| `vault_advanced_search` | Structured search using property criteria |
| **Users / groups** | |
| `vault_list_groups` | List all groups in the vault |
| `vault_get_group` | Get details for a specific group |
| `vault_list_users` | List all users in the vault |
| `vault_get_user` | Get details for a specific user |
| **Properties / categories / lifecycles** | |
| `vault_list_property_definitions` | List user-defined property definitions |
| `vault_get_property_definition` | Get a specific property definition |
| `vault_list_lifecycle_definitions` | List lifecycle definitions |
| `vault_list_category_definitions` | List category definitions |
| **Items (engineering BOM)** | |
| `vault_search_items` | Search for engineering/BOM items |
| `vault_get_item` | Get details for a specific engineering item |
| `vault_get_item_version_history` | List all versions of a master item |
| `vault_get_item_change_orders` | List change orders linked to an item |
| `vault_list_item_versions` | List item versions, optionally filtered by query |
| `vault_get_item_version` | Get details for a specific item version |
| `vault_get_item_bom` | Get the Bill of Materials for an item version |
| `vault_get_item_parents` | Get parent items (where-used) for an item version |
| `vault_get_item_associated_files` | Get files associated with an item version |
| `vault_get_bom_by_part_number` | **One-call lookup: part number → item → BOM** |
| `vault_get_cad_bom_by_part_number` | **One-call lookup: part number → CAD assembly BOM** |
| **Purchasing sheets** | |
| `vault_generate_purchasing_sheet` | **End-to-end: part number → BOM → enriched .xlsx** |
| `vault_generate_purchasing_sheet_from_vault_bom` | Build a sheet from an already-fetched Vault BOM payload |
| `vault_generate_purchasing_sheet_from_file` | Build a sheet from a manually-exported BOM file (.xls/.xlsx/.csv) |
| `vault_lookup_purchased_part` | Look up vendor / cost / lead-time for one part number |
| `vault_get_purchased_items_reference_status` | Check the SharePoint reference file is reachable |
| **Jobs** | |
| `vault_get_job_queue_enabled` | Check whether the Vault job queue is enabled |
| `vault_submit_job` | Submit a job to the Vault job queue (see caveats below) |
| `vault_get_job` | Get a job's status and metadata by ID |
| **Files / utilities** | |
| `vault_watermark_pdfs_in_folder` | Download every PDF in a Vault folder, watermark it, save locally |

The `wrike_*` MCP tools themselves (search/create/update tasks, folders, comments, timelogs, metadata) now live in [SF-WrikeConnector](https://github.com/zosimplifyber/SF-WrikeConnector) — see that repo's README for the tool list. The `WrikeRestAPI` client vendored in this repo (`wrike_rest_api.py`) is used directly by **BOM → Manufacturing Tasks** for task/subtask creation and dependency chaining; it is not exposed as MCP tools here.

## Known issues / caveats

### `vault_submit_job` and the `*.create.*` job family

Submitting `autodesk.vault.pdf.create.idw`, `autodesk.vault.dwf.create.iam`, etc. via the Vault REST API is **structurally limited**: the server normalizes the first character of every `Params` key to lowercase on receive (`FileMasterId` → `fileMasterId`). The stock Inventor JP handlers do exact-match lookups on PascalCase keys, so REST-submitted jobs in this family typically fail with a wrapped `Job param error` (visible only in the JP machine's Application event log under provider `Autodesk Job Processor`).

`vault_submit_job` works reliably for handlers that case-fold their lookups — confirmed working for:
- `autodesk.vault.syncproperties` (with `FileVersionId` / `FileVersionIds`)
- `autodesk.vault.updaterevisionblock.idw`

For PDF / DWF / DXF / STEP publish jobs, prefer one of:
1. **Manual:** right-click the file in Vault Explorer → Update Visualization Attachment, or transition lifecycle state to one whose entry trigger is `*.create.*`.
2. **Programmatic:** PowerShell using the Vault .NET SDK on a JP-host machine — see `scripts/` (work in progress) or call `DocumentService.UpdateFileLifeCycleStates` directly.

The relevant docstring on `vault_submit_job` in `mcp_server.py` documents this in detail.

### Inspecting JP failures

Job Processor errors are not exposed via REST — by default the JP deletes failed jobs from the queue, which surfaces as a 404 `QueuedEventDoesntExist` on `vault_get_job`. To see the real `InnerException`, query the Windows Application event log on the JP machine:

```powershell
Get-WinEvent -FilterHashtable @{
  LogName='Application'; ProviderName='Autodesk Job Processor';
  StartTime=(Get-Date).AddMinutes(-30); Level=2
} | ForEach-Object { ($_.Properties | ForEach-Object { $_.Value }) -join "`n" }
```

## Project layout

```
Vault-MCP/
├── app.py                      # Entry point — config, logging, mode dispatch (sse / stdio / gui / workflow)
├── mcp_server.py               # FastMCP tool definitions (REST tools + SOAP write paths)
├── vault_rest_api.py           # Async Vault REST client
├── wrike_rest_api.py           # Async Wrike API v4 client (used by the BOM → Manufacturing Tasks engine)
├── wrike_fields.py             # Pure helpers resolving Wrike custom-field names/values for the client above
├── bom_purchasing.py           # Purchasing-sheet generation engine
├── mfg_package.py              # Manufacturing-order package builder engine (PDF + STEP + Excel BOM)
├── publish_bom.py              # BOM → Vault publish-job engine (queues PDF/STEP for Make parts)
├── wrike_mfg_tasks.py          # Purchasing sheet → Wrike manufacturing-task engine (reconcile, group, schedule, create)
├── pdf_watermark.py            # PDF watermark helper (RELEASED / FOR REVIEW overlays)
├── file_property_rules.json    # Property-compliance rules for FILES (used by Property Check)
├── item_property_rules.json    # Property-compliance rules for ITEMS (used by readiness reports)
├── config.json.example         # Template (committed) — copy to config.json
├── config.json                 # Live credentials (gitignored)
├── requirements.txt
├── vault_openapi.yml           # Reference: Vault REST API v2 OpenAPI spec (~180 KB)
│
├── gui/                        # Tk GUI front-ends (driven by ``app.py --gui`` / ``--workflow``)
│   ├── __init__.py
│   ├── launcher.py             # Vault Integration launcher dashboard
│   ├── release_workflow.py     # Release Workflow wizard (compliance → sync → release)
│   ├── purchasing.py           # Purchasing-sheet GUI
│   ├── file_property_check.py  # Property Check GUI (file name → compliance report)
│   ├── mfg_package.py          # Manufacturing Package builder GUI
│   ├── publish_bom.py          # BOM → Publish Deliverables GUI (scan, then queue jobs)
│   └── wrike_mfg_tasks.py      # BOM → Manufacturing Tasks GUI (reconcile suppliers, preview, create)
│
└── scripts/                    # Helpers and CLI tools used by the GUIs / for one-offs
    ├── vault_sdk.py            # Python wrapper around the Vault .NET SDK (via PowerShell bridge)
    ├── vault_sdk.ps1           # PowerShell .NET SDK bridge (sign-in, lifecycle, property writes)
    ├── vault_soap.py           # Direct legacy SOAP client (used by some workflow steps)
    ├── check_file_properties.py # Property Check — file name in, compliance report out
    ├── check_item_properties.py # Item-side compliance engine (backs the readiness report)
    ├── inventor_automation.py  # Inventor COM automation (open + rebuild + save)
    ├── release_workflow.py     # CLI release workflow (also reachable via ``--gui`` / ``--workflow``)
    └── probes/                 # One-off diagnostic / probe scripts (not part of the server)
        ├── probe_edit_items.ps1            # Reproduce + diagnose EditItems failures
        ├── probe_edit_items_authflags.ps1  # Sweep AuthenticationFlags combos against EditItems
        ├── probe_create_item.ps1           # Create a fresh test item via SDK
        ├── probe_delete_item.ps1           # Delete a test item via SDK
        ├── probe_jobs.py                   # Probe REST job-submission shapes
        ├── probe_pdf_job.py                # Probe PDF publish-job param keys
        ├── probe_vault_sdk.{py,ps1}        # SDK sign-in / read-only probes
        ├── probe_vault_soap.py             # Legacy SOAP probes (ticket extract, lifecycle)
        ├── setup_explorer_trace.ps1        # Capture Vault Explorer SOAP traffic via WCF tracing
        ├── test_license.ps1                # Verify licensing DLL discovery + sign-in
        └── ... (other one-offs)
```

CLI workflows and probes each read `config.json` from the project root, so run them from anywhere:

```bash
python scripts/release_workflow.py SF-001702
python scripts/probes/probe_jobs.py
powershell scripts/probes/setup_explorer_trace.ps1 -Mode Enable
```

## Logs

Runtime logs are written to `Log/mcp_server.log` (rotating, max 5 MB × 5 files). In SSE mode, logs also print to the console. In stdio mode, only the file is written (stdout is reserved for the MCP protocol). The `Log/` directory is gitignored.
