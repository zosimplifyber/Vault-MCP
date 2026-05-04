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

## Running the Server

### SSE Mode (Claude Code / remote clients)

This is the default mode. It starts an HTTP server that MCP clients connect to over the network.

```bash
python app.py
```

The server will be available at:
- Web server: `http://localhost:8765`
- SSE endpoint: `http://localhost:8765/sse`
- Messages endpoint: `http://localhost:8765/messages`

To use a custom config file:

```bash
python app.py --config path/to/my_config.json
```

### stdio Mode (Claude Desktop)

For Claude Desktop, the server runs as a subprocess communicating over stdin/stdout.

```bash
python app.py --transport stdio
```

## Connecting to Claude Code

Add the server to your Claude Code MCP configuration (`.claude/settings.json` or via `claude mcp add`):

```json
{
  "mcpServers": {
    "vault": {
      "type": "sse",
      "url": "http://localhost:8765/sse"
    }
  }
}
```

Or use the CLI:

```bash
claude mcp add --transport sse vault http://localhost:8765/sse
```

## Connecting to Claude Desktop

Add the following to your Claude Desktop configuration file (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "vault": {
      "command": "python",
      "args": [
        "C:/path/to/Vault-MCP/app.py",
        "--transport", "stdio"
      ]
    }
  }
}
```

Replace `C:/path/to/Vault-MCP/` with the actual path to this project directory. Claude Desktop will launch the server automatically as a subprocess.

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
├── bom_purchasing.py           # Purchasing-sheet generation engine
├── mfg_package.py              # Manufacturing-order package builder engine (PDF + STEP + Excel BOM)
├── pdf_watermark.py            # PDF watermark helper (RELEASED / FOR REVIEW overlays)
├── item_property_rules.json    # Property-compliance rules used by readiness reports
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
│   └── mfg_package.py          # Manufacturing Package builder GUI
│
└── scripts/                    # Helpers and CLI tools used by the GUIs / for one-offs
    ├── vault_sdk.py            # Python wrapper around the Vault .NET SDK (via PowerShell bridge)
    ├── vault_sdk.ps1           # PowerShell .NET SDK bridge (sign-in, lifecycle, property writes)
    ├── vault_soap.py           # Direct legacy SOAP client (used by some workflow steps)
    ├── check_item_properties.py # Property-compliance checker (per part-number)
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
