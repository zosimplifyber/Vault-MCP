# Vault MCP Server

An MCP (Model Context Protocol) server that exposes Autodesk Vault REST API operations as tools, enabling AI assistants like Claude to browse, search, and read data from an Autodesk Vault server.

## Prerequisites

- Python 3.10+
- An Autodesk Vault server with REST API access enabled
- Vault user credentials with appropriate permissions

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Edit `config.json` before running the server. All fields are required.

```json
{
    "vault": {
        "servername": "VaultServer",
        "username": "Administrator",
        "password": "your-password-here",
        "database": "Inventor"
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8080
    },
    "logging": {
        "level": "INFO",
        "file": "Log/mcp_server.log"
    }
}
```

| Field | Description |
|---|---|
| `vault.servername` | Hostname or IP of your Vault server |
| `vault.username` | Vault login username |
| `vault.password` | Vault login password |
| `vault.database` | Vault database name (e.g. `Inventor`, `Vault`) |
| `server.host` | Bind address for SSE mode (`0.0.0.0` = all interfaces) |
| `server.port` | Port for the SSE HTTP server (default `8080`) |
| `logging.level` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `logging.file` | Path to the rotating log file |

## Running the Server

### SSE Mode (Claude Code / remote clients)

This is the default mode. It starts an HTTP server that MCP clients connect to over the network.

```bash
python app.py
```

The server will be available at:
- WebServer: `http://localhost:8080`
- SSE endpoint: `http://localhost:8080/sse`
- Messages endpoint: `http://localhost:8080/messages`

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
      "url": "http://localhost:8080/sse"
    }
  }
}
```

Or use the CLI:

```bash
claude mcp add --transport sse vault http://localhost:8080/sse
```

## Connecting to Claude Desktop

Add the following to your Claude Desktop configuration file (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "vault": {
      "command": "python",
      "args": [
        "C:/path/to/Vault MCP/app.py",
        "--transport", "stdio"
      ]
    }
  }
}
```

Replace `C:/path/to/Vault MCP/` with the actual path to this project directory. Claude Desktop will launch the server automatically as a subprocess.

## Available Tools

Once connected, the following tools are exposed to the AI assistant:

| Tool | Description |
|---|---|
| `vault_get_server_info` | Get Vault server version and metadata |
| `vault_sign_in` | Authenticate with different credentials |
| `vault_sign_out` | Invalidate the current session |
| `vault_list_vaults` | List all accessible vaults |
| `vault_get_vault` | Get details for a specific vault |
| `vault_get_folder_contents` | List files and sub-folders in a folder |
| `vault_get_folder` | Get metadata for a specific folder |
| `vault_get_file` | Get metadata for a specific file |
| `vault_get_file_versions` | List all versions of a file |
| `vault_get_file_download_url` | Get the download URL for a file |
| `vault_search_files` | Keyword search across vault files |
| `vault_advanced_search` | Structured search using property criteria |
| `vault_list_groups` | List all groups in the vault |
| `vault_get_group` | Get details for a specific group |
| `vault_list_users` | List all users in the vault |
| `vault_get_user` | Get details for a specific user |
| `vault_list_property_definitions` | List user-defined property definitions |
| `vault_get_property_definition` | Get a specific property definition |
| `vault_search_items` | Search for engineering/BOM items |
| `vault_get_item` | Get details for a specific engineering item |
| `vault_list_lifecycle_definitions` | List lifecycle definitions |
| `vault_list_category_definitions` | List category definitions |

## Logs

Logs are written to `Log/mcp_server.log` (rotating, max 5 MB × 5 files). In SSE mode, logs also print to the console. In stdio mode, only the file is written to (stdout is reserved for the MCP protocol).
