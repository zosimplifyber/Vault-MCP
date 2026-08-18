"""
Vault MCP Server — Entry Point
Reads config.json, authenticates once against Vault, and dispatches to the
selected run mode. All modes share the single authenticated session created
here, so credentials live in only one place (config.json) and Vault sees
one audit trail per launch.

Run modes (--transport)
-----------------------
sse      (default) — Opens the Vault Integration launcher dashboard and
                     auto-starts the SSE MCP server inside it. Connect
                     remote MCP clients (Claude Code etc.) at
                     http://<host>:<port>/sse. Pass ``--headless`` to skip
                     the GUI and run bare uvicorn instead.
stdio              — stdin/stdout transport for Claude Desktop's
                     command-based MCP entry. Logs go to file only (stdout
                     is reserved for the MCP protocol).
gui                — Opens the launcher dashboard with the live session
                     pre-attached but does NOT auto-start the MCP server
                     (the user clicks Start when they want it). Same as
                     SSE mode without the auto-start.
workflow           — Skips the launcher and opens the Release Workflow
                     wizard (``gui.release_workflow``) directly. Use
                     when the wizard is the only thing needed.

Convenience flags
-----------------
--gui              Shortcut for --transport gui.
--workflow         Shortcut for --transport workflow.
--headless         In SSE mode, skip the launcher GUI and run bare uvicorn.
--part-number STR  Pre-fill the part number for --gui / --workflow.
--config PATH      Override the default config.json location.

Usage:
    python app.py                                 # launcher + auto-started MCP (default)
    python app.py --headless                      # bare SSE WebServer (old default)
    python app.py --transport stdio               # stdio for Claude Desktop
    python app.py --gui                           # launcher only, manual MCP start
    python app.py --workflow --part-number 12345  # wizard, pre-filled
    python app.py --config my_config.json         # custom config path

Notes
-----
* GUI / workflow modes import the ``gui`` package at the project root and
  also need ``scripts/`` on ``sys.path`` for helper modules (``check_item_properties``,
  ``release_workflow``); the latter is handled by ``_ensure_scripts_on_path``.
* SOAP-based actions (lifecycle changes etc.) need both ``access_token``
  and ``user_id`` from the sign-in response — the GUI sign-in helper
  returns both; the MCP-only path does not collect ``user_id`` because
  the REST tools don't need it.
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

    # Record where this came from so tools that persist a preference write
    # back to the file actually in use, not whichever config.json happens to
    # sit next to the code. app.py --config <other> must not silently update
    # the default.
    cfg["__path__"] = os.path.abspath(path)

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

async def run_sse_headless(cfg: dict) -> None:
    """Bare SSE server — no GUI. Used when --headless is passed or when
    running on a box with no display (Tk unavailable)."""
    logger = logging.getLogger(__name__)

    vault_cfg = cfg["vault"]
    server_cfg = cfg.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = int(server_cfg.get("port", 8765))

    api = VaultRestAPI(servername=vault_cfg["servername"])
    vault_id = await authenticate(api, vault_cfg)
    mcp = create_mcp_server(api=api, vault_id=vault_id)

    display_host = "localhost" if host == "0.0.0.0" else host
    logger.info("Starting Vault MCP Server  (SSE, headless)")
    logger.info("  WebServer  : http://%s:%d", display_host, port)
    logger.info("  SSE endpoint  : http://%s:%d/sse", display_host, port)
    logger.info("  Messages      : http://%s:%d/messages", display_host, port)
    logger.info("  Vault database: %s", vault_cfg["database"])
    logger.info("  Vault server  : %s", vault_cfg["servername"])

    log_level = cfg.get("logging", {}).get("level", "INFO").lower()
    server = uvicorn.Server(uvicorn.Config(
        app=mcp.sse_app(), host=host, port=port,
        log_level=log_level, access_log=True,
        log_config=None,  # use parent's logging setup; see launcher.py for context
    ))

    await server.serve()


def run_sse(cfg: dict) -> None:
    """Default ``python app.py`` mode. Opens the launcher dashboard and
    auto-starts the MCP server inside it, so the user gets a live status
    panel and the Engineering Tools as soon as the server comes up. Falls
    back to bare uvicorn if Tk fails to initialise (e.g. headless box)."""
    logger = logging.getLogger(__name__)
    api, vault_id, access_token, user_id = asyncio.run(_sign_in_for_gui(cfg))

    _ensure_scripts_on_path()

    try:
        from gui.launcher import launch_launcher
    except ImportError as exc:
        logger.warning("GUI unavailable (%s) — falling back to headless SSE", exc)
        asyncio.run(run_sse_headless(cfg))
        return

    logger.info("Launching Vault Integration dashboard with auto-started MCP server")
    logger.info("  Vault database: %s", cfg["vault"]["database"])
    logger.info("  Vault server  : %s", cfg["vault"]["servername"])

    try:
        launch_launcher(
            api=api, vault_id=vault_id,
            access_token=access_token, user_id=user_id, cfg=cfg,
            auto_start_mcp=True,
        )
    except tk_init_error_types() as exc:
        logger.warning("Tk init failed (%s) — falling back to headless SSE", exc)
        asyncio.run(run_sse_headless(cfg))


def tk_init_error_types() -> tuple:
    """Return the tuple of exception types that mean Tk couldn't open a
    display. Imported lazily so app.py still loads on a Tk-less Python."""
    try:
        import tkinter
        return (tkinter.TclError,)
    except ImportError:
        return (Exception,)


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
# GUI mode (Tkinter Release Workflow wizard)
# ---------------------------------------------------------------------------

async def _sign_in_for_gui(cfg: dict) -> tuple[VaultRestAPI, str, str, str]:
    """Sign in and return (api, vault_id, access_token, user_id).

    Used by ``run_gui`` so the wizard inherits the same session the MCP server
    would have created — single config, single audit trail, no second login.

    ``user_id`` is the Vault user-account ID — required separately because
    every SOAP call needs it in the ``SecurityHeader`` (the access token is
    just ``V:<ticket-guid>`` with no embedded UserId).
    """
    logger = logging.getLogger(__name__)
    vault_cfg = cfg["vault"]

    api = VaultRestAPI(servername=vault_cfg["servername"])
    logger.info(
        "Signing in to %s as %s (database: %s) for GUI session …",
        vault_cfg["servername"], vault_cfg["username"], vault_cfg["database"],
    )
    result = await api.create_session(
        database=vault_cfg["database"],
        username=vault_cfg["username"],
        password=vault_cfg["password"],
    )
    if result["error"]:
        sys.exit(f"[ERROR] Could not authenticate with Vault: {result['data']}")

    data = result["data"]
    vault_id = str(
        (data.get("vaultInformation") or {}).get("id", "")
        or data.get("vaultId", "")
        or ""
    )
    access_token = str(data.get("accessToken") or "")
    user_id = str((data.get("userInformation") or {}).get("id", "") or "")
    logger.info(
        "Authenticated successfully. vault_id=%s user_id=%s",
        vault_id or "(unknown)", user_id or "(unknown)",
    )
    return api, vault_id, access_token, user_id


def _ensure_scripts_on_path() -> None:
    """Make ``scripts/`` importable so the GUIs can pull in helper modules
    that live there (``check_item_properties``, ``release_workflow``).
    The ``gui`` package itself is at the project root and imports
    naturally without a path tweak."""
    scripts_dir = Path(__file__).resolve().parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


def run_gui(cfg: dict, *, prefill_part_number: str = "") -> None:
    """
    Sign in once with credentials from ``config.json``, then open the
    Vault Integration launcher dashboard. From there the user can start
    the MCP server, launch the Release Workflow wizard, and access the
    other engineering tools — all sharing the same authenticated session.
    """
    logger = logging.getLogger(__name__)
    api, vault_id, access_token, user_id = asyncio.run(_sign_in_for_gui(cfg))

    _ensure_scripts_on_path()

    try:
        from gui.launcher import launch_launcher
    except ImportError as exc:
        sys.exit(f"[ERROR] Could not import gui.launcher: {exc}")

    logger.info("Launching Vault Integration launcher")
    logger.info("  Vault database: %s", cfg["vault"]["database"])
    logger.info("  Vault server  : %s", cfg["vault"]["servername"])

    # ``prefill_part_number`` flows through to the workflow window the user
    # opens from the launcher. (Stash it on the cfg dict for now — minor
    # state, single-use.)
    if prefill_part_number:
        cfg.setdefault("_runtime", {})["prefill_part_number"] = prefill_part_number

    launch_launcher(
        api=api, vault_id=vault_id,
        access_token=access_token, user_id=user_id, cfg=cfg,
    )


def run_workflow_direct(cfg: dict, *, prefill_part_number: str = "") -> None:
    """Skip the launcher dashboard and open the Release Workflow wizard
    directly — same single-sign-in path as ``run_gui``. Use this when the
    only thing the user wants is the wizard."""
    logger = logging.getLogger(__name__)
    api, vault_id, access_token, user_id = asyncio.run(_sign_in_for_gui(cfg))

    _ensure_scripts_on_path()

    try:
        from gui.release_workflow import launch_gui
    except ImportError as exc:
        sys.exit(f"[ERROR] Could not import gui.release_workflow: {exc}")

    logger.info("Launching Release Workflow wizard directly")
    launch_gui(
        prefill_part_number=prefill_part_number,
        api=api, vault_id=vault_id,
        access_token=access_token, user_id=user_id, cfg=cfg,
    )


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
        choices=["sse", "stdio", "gui", "workflow"],
        default="sse",
        help=(
            "Run mode: "
            "'sse' starts an HTTP WebServer (default, for Claude Code / remote clients); "
            "'stdio' uses stdin/stdout (for Claude Desktop command-based MCP entry); "
            "'gui' opens the Vault Integration launcher dashboard with the "
            "live Vault session pre-attached; "
            "'workflow' skips the launcher and opens the Release Workflow "
            "wizard directly."
        ),
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Shortcut for --transport gui (launcher dashboard).",
    )
    parser.add_argument(
        "--workflow",
        action="store_true",
        help="Shortcut for --transport workflow (skip launcher, open wizard).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="In SSE mode, skip the launcher GUI and run uvicorn directly.",
    )
    parser.add_argument(
        "--part-number",
        default="",
        help="Pre-fill the part number when launching --gui / --workflow.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    # CLI shortcut flags override --transport
    if args.workflow:
        args.transport = "workflow"
    elif args.gui:
        args.transport = "gui"

    cfg = load_config(args.config)
    setup_logging(cfg, stdio_mode=(args.transport == "stdio"))

    if args.transport == "stdio":
        run_stdio(cfg)          # manages its own event loop internally
    elif args.transport == "gui":
        run_gui(cfg, prefill_part_number=args.part_number)
    elif args.transport == "workflow":
        run_workflow_direct(cfg, prefill_part_number=args.part_number)
    elif args.headless:
        asyncio.run(run_sse_headless(cfg))
    else:
        run_sse(cfg)            # opens launcher GUI + auto-starts MCP server
