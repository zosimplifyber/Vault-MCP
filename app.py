"""
Vault MCP Server — Entry Point
Reads config.json, authenticates with Vault, and starts the MCP server.

Transports
----------
sse   (default) — HTTP WebServer using Server-Sent Events.
                  Connect from Claude Code or any remote MCP client.
stdio           — stdin/stdout transport, launched as a subprocess.
                  Used by Claude Desktop (command-based MCP entry).

Usage:
    python app.py                          # SSE WebServer (default)
    python app.py --transport stdio        # stdio for Claude Desktop
    python app.py --config my_config.json  # custom config path
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from vault_rest_api import VaultRestAPI
from mcp_server import create_mcp_server


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

CONFIG_FILE = Path(__file__).parent / "config.json"


def load_config(path: Path) -> dict:
    """Load and validate the JSON configuration file."""
    if not path.exists():
        sys.exit(f"[ERROR] Config file not found: {path}")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    vault = cfg.get("vault", {})
    for key in ("servername", "username", "password", "database"):
        if not vault.get(key):
            sys.exit(f"[ERROR] config.json is missing vault.{key}")

    return cfg


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(cfg: dict, stdio_mode: bool = False) -> None:
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file", "Log/mcp_server.log")

    # Anchor relative log paths to this script's directory so the path
    # doesn't depend on the launcher's working directory (e.g. Claude Desktop).
    log_path = Path(log_file)
    if not log_path.is_absolute():
        log_path = Path(__file__).parent / log_path
    log_file = str(log_path)

    log_dir = log_path.parent
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    handlers: list = []
    # In stdio mode stdout is used for the MCP protocol — log to file only
    if not stdio_mode:
        handlers.append(logging.StreamHandler(sys.stdout))
    if log_file:
        handlers.append(
            RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5)
        )

    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        handlers=handlers,
    )


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

async def authenticate(api: VaultRestAPI, vault_cfg: dict) -> str:
    """
    Sign in with credentials from config.json.
    Returns the resolved vault ID. Exits on failure.
    """
    logger = logging.getLogger(__name__)
    logger.info(
        "Signing in to %s as %s (database: %s) …",
        vault_cfg["servername"],
        vault_cfg["username"],
        vault_cfg["database"],
    )

    result = await api.create_session(
        database=vault_cfg["database"],
        username=vault_cfg["username"],
        password=vault_cfg["password"],
    )

    if result["error"]:
        logger.error("Authentication failed: %s", result["data"])
        sys.exit(f"[ERROR] Could not authenticate with Vault: {result['data']}")

    data = result["data"]
    vault_id = str(
        (data.get("vaultInformation") or {}).get("id", "")
        or data.get("vaultId", "")
        or ""
    )
    logger.info("Authenticated successfully. vault_id=%s", vault_id or "(unknown)")
    return vault_id


# ---------------------------------------------------------------------------
# SSE mode (WebServer)
# ---------------------------------------------------------------------------

async def run_sse(cfg: dict) -> None:
    logger = logging.getLogger(__name__)

    vault_cfg = cfg["vault"]
    server_cfg = cfg.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = int(server_cfg.get("port", 8080))

    api = VaultRestAPI(servername=vault_cfg["servername"])
    vault_id = await authenticate(api, vault_cfg)
    mcp = create_mcp_server(api=api, vault_id=vault_id)

    display_host = "localhost" if host == "0.0.0.0" else host
    logger.info("Starting Vault MCP Server  (SSE)")
    logger.info("  WebServer  : http://%s:%d", display_host, port)
    logger.info("  SSE endpoint  : http://%s:%d/sse", display_host, port)
    logger.info("  Messages      : http://%s:%d/messages", display_host, port)
    logger.info("  Vault database: %s", vault_cfg["database"])
    logger.info("  Vault server  : %s", vault_cfg["servername"])

    sse_app = mcp.sse_app()
    config = uvicorn.Config(
        app=sse_app,
        host=host,
        port=port,
        log_level=cfg.get("logging", {}).get("level", "INFO").lower(),
        access_log=True,
    )
    server = uvicorn.Server(config)
    await server.serve()


# ---------------------------------------------------------------------------
# stdio mode (Claude Desktop subprocess)
# ---------------------------------------------------------------------------

async def _setup_for_stdio(cfg: dict):
    """Authenticate and build the MCP server object (async step)."""
    logger = logging.getLogger(__name__)
    vault_cfg = cfg["vault"]

    api = VaultRestAPI(servername=vault_cfg["servername"])
    vault_id = await authenticate(api, vault_cfg)
    mcp = create_mcp_server(api=api, vault_id=vault_id)

    logger.info("Vault MCP Server ready (stdio)")
    logger.info("  Vault database: %s", vault_cfg["database"])
    logger.info("  Vault server  : %s", vault_cfg["servername"])
    return mcp


def run_stdio(cfg: dict) -> None:
    """
    Authenticate (async), then hand control to FastMCP's synchronous stdio runner.
    Two separate event loops are used intentionally:
      1. First loop — async authentication with the Vault REST API.
      2. Second loop — FastMCP's own stdio event loop for the MCP protocol.
    The VaultRestAPI client uses stateless httpx calls per request, so it works
    across loops with no issues.
    """
    mcp = asyncio.run(_setup_for_stdio(cfg))
    # FastMCP.run(transport="stdio") manages its own event loop internally
    mcp.run(transport="stdio")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Autodesk Vault MCP Server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_FILE,
        help="Path to config.json",
    )
    parser.add_argument(
        "--transport",
        choices=["sse", "stdio"],
        default="sse",
        help=(
            "Transport mode: "
            "'sse' starts an HTTP WebServer (default, for Claude Code / remote clients); "
            "'stdio' uses stdin/stdout (for Claude Desktop command-based MCP entry)."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    cfg = load_config(args.config)
    setup_logging(cfg, stdio_mode=(args.transport == "stdio"))

    if args.transport == "stdio":
        run_stdio(cfg)          # manages its own event loop internally
    else:
        asyncio.run(run_sse(cfg))
