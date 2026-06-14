"""
Startup launcher / dashboard for the Vault integration.

This is what `app.py --gui` opens. From here the user can:

  * see the live Vault connection status (server / database / user / vault id)
  * see the MCP server status and start/stop it (SSE on the configured port)
  * launch the Release Workflow wizard with the live session pre-attached
  * launch the standalone Property Check GUI (the original lookup tool)
  * open the Log folder where readiness reports get saved

Same Simplifyber palette as the workflow wizard. The dashboard owns the Tk
root; child tools open as ``tk.Toplevel`` windows so the launcher stays
visible underneath and one Vault session is shared across everything.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Optional

import tkinter as tk
from tkinter import messagebox

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from vault_rest_api import VaultRestAPI  # noqa: E402

# Reuse the brand palette + helpers + button factory from the workflow GUI
# so the two windows look like one tool.
from gui.release_workflow import (  # noqa: E402
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY,
    WHITE, OLIVE_GREEN, RUST_ORANGE,
    _pil_available, _resource_path,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCP server controller — runs uvicorn on a worker thread we can stop later
# ---------------------------------------------------------------------------

class MCPServerController:
    """Start / stop one SSE MCP server in-process on a background thread.

    Parametrized by ``server_factory`` — a zero-arg callable returning a built
    FastMCP instance — so the same controller drives both the Vault server and
    the Wrike server. ``stop()`` flips uvicorn's ``should_exit`` flag and joins
    the worker thread; uvicorn will let in-flight requests finish first.
    """

    def __init__(
        self,
        server_factory,
        host: str,
        port: int,
        *,
        name: str = "MCP",
        log_level: str = "INFO",
    ) -> None:
        self.server_factory = server_factory
        self.host = host
        self.port = int(port)
        self.name = name
        self.log_level = log_level
        self._server = None      # uvicorn.Server when running
        self._thread: Optional[threading.Thread] = None
        self._last_error: Optional[str] = None

    # ----- Lifecycle -------------------------------------------------------

    def start(self) -> bool:
        if self.is_running():
            return True
        self._last_error = None
        try:
            import uvicorn
        except ImportError as exc:
            self._last_error = f"import failed: {exc}"
            return False

        try:
            mcp = self.server_factory()
            sse_app = mcp.sse_app()
            config = uvicorn.Config(
                app=sse_app, host=self.host, port=self.port,
                log_level=self.log_level.lower(),
                access_log=True,
                # Don't let uvicorn re-run logging.dictConfig — app.py already
                # set up the root logger via basicConfig, and uvicorn's default
                # config can fail with "Unable to configure formatter 'default'"
                # when invoked after another logging setup. Use the parent
                # logger setup; access_log entries still propagate.
                log_config=None,
            )
            self._server = uvicorn.Server(config)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"server build failed: {exc}"
            return False

        def runner() -> None:
            try:
                asyncio.run(self._server.serve())
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"server crashed: {exc}"
                logger.exception("%s MCP server crashed", self.name)

        self._thread = threading.Thread(
            target=runner, daemon=True, name=f"{self.name.lower()}-sse")
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._server = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def url(self) -> str:
        host = "localhost" if self.host == "0.0.0.0" else self.host
        return f"http://{host}:{self.port}"

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error


# ---------------------------------------------------------------------------
# Launcher window
# ---------------------------------------------------------------------------

class LauncherGUI:
    """Top-level dashboard. Single instance — the Tk root."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        api: Optional[VaultRestAPI] = None,
        vault_id: str = "",
        access_token: str = "",
        user_id: str = "",
        cfg: Optional[dict[str, Any]] = None,
        auto_start_mcp: bool = False,
    ) -> None:
        self.root = root
        self.root.title("Simplifyber — Vault Integration")
        self.root.geometry("680x640")
        self.root.minsize(620, 580)
        self.root.configure(bg=LIGHT_GRAY)

        self.api = api
        self.vault_id = vault_id
        self.access_token = access_token
        self.user_id = user_id
        self.cfg: dict[str, Any] = cfg or {}

        # Cross-thread queue for status updates from worker threads
        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()

        # Persistent references for Tk PhotoImage objects
        self._logo_img = None
        self._icon_img = None

        # MCP server controllers (created when their config is present)
        self.mcp_ctrl: Optional[MCPServerController] = self._build_vault_ctrl()
        # Wrike controller — independent of the Vault session.
        self.wrike_ctrl: Optional[MCPServerController] = self._build_wrike_ctrl()

        self._set_window_icon()
        self._build_ui()
        self._refresh_vault_panel()
        self._refresh_mcp_panel()
        # Drain the cross-thread queue and re-poll status periodically
        self.root.after(100, self._drain_queue)
        self.root.after(2000, self._periodic_status_refresh)

        # Auto-start the MCP server once the window is up so the user sees
        # the running state immediately. Delay slightly so the launcher
        # finishes drawing before uvicorn starts spamming the log.
        if auto_start_mcp and self.mcp_ctrl is not None:
            self.root.after(300, self._on_mcp_start)

        # Closing the X button while MCP is running would drop any connected
        # MCP clients (Claude Desktop, Claude Code) mid-session. Confirm first.
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- Controller builders ---------------------------------------------

    def _build_vault_ctrl(self) -> Optional["MCPServerController"]:
        """Build the Vault MCP controller from the live session + cfg['server'].
        Returns None until a Vault session is attached."""
        if not (self.api and self.vault_id):
            return None
        server_cfg = self.cfg.get("server", {})
        log_level = self.cfg.get("logging", {}).get("level", "INFO")

        def _vault_factory(api=self.api, vault_id=self.vault_id):
            from mcp_server import create_mcp_server
            return create_mcp_server(api=api, vault_id=vault_id)

        return MCPServerController(
            _vault_factory,
            server_cfg.get("host", "0.0.0.0"),
            server_cfg.get("port", 8765),
            name="Vault",
            log_level=log_level,
        )

    def _build_wrike_ctrl(self) -> Optional["MCPServerController"]:
        """Build the Wrike MCP controller from cfg['wrike'] if a token is set.
        Independent of the Vault session — returns None when unconfigured."""
        wrike_cfg = (self.cfg.get("wrike") or {})
        token = wrike_cfg.get("token")
        if not token or token.startswith("your-wrike"):
            return None
        log_level = self.cfg.get("logging", {}).get("level", "INFO")

        def _wrike_factory(wcfg=wrike_cfg):
            from wrike_rest_api import WrikeRestAPI, DEFAULT_BASE_URL
            from wrike_mcp_server import create_wrike_mcp_server
            wapi = WrikeRestAPI(
                token=wcfg["token"],
                base_url=wcfg.get("base_url", DEFAULT_BASE_URL),
            )
            return create_wrike_mcp_server(
                wapi, readonly=bool(wcfg.get("readonly", False)))

        return MCPServerController(
            _wrike_factory,
            wrike_cfg.get("host", "0.0.0.0"),
            wrike_cfg.get("port", 8766),
            name="Wrike",
            log_level=log_level,
        )

    def _on_close(self) -> None:
        if self.mcp_ctrl is not None and self.mcp_ctrl.is_running():
            confirm = messagebox.askyesno(
                "Stop MCP server?",
                "The MCP server is running. Closing this window will disconnect "
                "any active MCP clients (Claude Desktop, Claude Code).\n\n"
                "Quit anyway?",
                parent=self.root,
                default="no",
            )
            if not confirm:
                return
            self.mcp_ctrl.stop()
        self.root.destroy()

    # ----- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        self._build_header()
        self._build_vault_panel()
        self._build_mcp_panel()
        self._build_tools_panel()
        self._build_status_bar()

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=DARK_BLUE, height=72)
        header.pack(fill="x")
        header.pack_propagate(False)

        if _pil_available:
            try:
                from PIL import Image as PILImage, ImageTk
                logo_path = _resource_path("Simplifyber_Logo_White.png")
                if os.path.isfile(logo_path):
                    img = PILImage.open(logo_path).convert("RGBA")
                    target_h = 40
                    target_w = int(target_h * img.width / img.height)
                    img = img.resize((target_w, target_h), PILImage.LANCZOS)
                    self._logo_img = ImageTk.PhotoImage(img)
                    tk.Label(header, image=self._logo_img,
                             bg=DARK_BLUE).pack(side="left", padx=18)
            except Exception:  # noqa: BLE001
                pass

        title_box = tk.Frame(header, bg=DARK_BLUE)
        title_box.pack(side="left", expand=True, fill="both")
        tk.Label(
            title_box, text="Vault Integration",
            font=("Arial", 14, "bold"),
            fg=WHITE, bg=DARK_BLUE,
        ).pack(side="top", anchor="w", pady=(14, 0))
        tk.Label(
            title_box, text="Engineering Tools Dashboard",
            font=("Arial", 9), fg=PALE_BLUE, bg=DARK_BLUE,
        ).pack(side="top", anchor="w")

        tk.Frame(self.root, bg=MID_BLUE, height=3).pack(fill="x")

    # -- Vault panel ---------------------------------------------------------

    def _build_vault_panel(self) -> None:
        card = tk.Frame(self.root, bg=PALE_BLUE,
                        highlightthickness=1, highlightbackground=GRAY_BDR)
        card.pack(fill="x", padx=18, pady=(14, 8))

        tk.Label(
            card, text="  VAULT CONNECTION",
            bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 10, "bold"),
            anchor="w", padx=10, pady=6,
        ).pack(fill="x")
        tk.Frame(card, bg=MID_BLUE, height=2).pack(fill="x")

        body = tk.Frame(card, bg=PALE_BLUE, padx=14, pady=10)
        body.pack(fill="x")

        # Status indicator + label row
        status_row = tk.Frame(body, bg=PALE_BLUE)
        status_row.pack(fill="x")
        self.vault_status_dot = tk.Label(
            status_row, text="●", bg=PALE_BLUE,
            fg=DARK_GRAY, font=("Arial", 16),
        )
        self.vault_status_dot.pack(side="left", padx=(0, 6))
        self.vault_status_text = tk.Label(
            status_row, text="Disconnected",
            bg=PALE_BLUE, fg=DARK_BLUE,
            font=("Arial", 11, "bold"),
        )
        self.vault_status_text.pack(side="left")

        self._brand_button(
            status_row, "Reconnect", self._on_reconnect, primary=False,
        ).pack(side="right")

        # Two-column key/value grid below
        grid = tk.Frame(body, bg=PALE_BLUE)
        grid.pack(fill="x", pady=(8, 0))
        self.vault_grid_vars: dict[str, tk.StringVar] = {}
        for i, key in enumerate(["Server", "Database", "User", "Vault ID"]):
            tk.Label(
                grid, text=f"{key}:",
                bg=PALE_BLUE, fg=DARK_GRAY,
                font=("Arial", 9, "bold"), anchor="w", width=12,
            ).grid(row=i, column=0, sticky="w", pady=1)
            var = tk.StringVar(value="—")
            tk.Label(
                grid, textvariable=var,
                bg=PALE_BLUE, fg=DARK_BLUE,
                font=("Arial", 9), anchor="w",
            ).grid(row=i, column=1, sticky="w", pady=1)
            self.vault_grid_vars[key] = var

    # -- MCP panel -----------------------------------------------------------

    def _build_mcp_panel(self) -> None:
        card = tk.Frame(self.root, bg=PALE_BLUE,
                        highlightthickness=1, highlightbackground=GRAY_BDR)
        card.pack(fill="x", padx=18, pady=8)

        tk.Label(
            card, text="  MCP SERVER",
            bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 10, "bold"),
            anchor="w", padx=10, pady=6,
        ).pack(fill="x")
        tk.Frame(card, bg=MID_BLUE, height=2).pack(fill="x")

        body = tk.Frame(card, bg=PALE_BLUE, padx=14, pady=10)
        body.pack(fill="x")

        status_row = tk.Frame(body, bg=PALE_BLUE)
        status_row.pack(fill="x")
        self.mcp_status_dot = tk.Label(
            status_row, text="●", bg=PALE_BLUE,
            fg=DARK_GRAY, font=("Arial", 16),
        )
        self.mcp_status_dot.pack(side="left", padx=(0, 6))
        self.mcp_status_text = tk.Label(
            status_row, text="Stopped",
            bg=PALE_BLUE, fg=DARK_BLUE,
            font=("Arial", 11, "bold"),
        )
        self.mcp_status_text.pack(side="left")

        self.mcp_open_btn = self._brand_button(
            status_row, "Open in browser", self._on_mcp_open_browser,
            primary=False,
        )
        self.mcp_open_btn.pack(side="right", padx=(6, 0))
        self.mcp_open_btn.configure(state="disabled")

        self.mcp_stop_btn = self._brand_button(
            status_row, "Stop", self._on_mcp_stop, primary=False,
        )
        self.mcp_stop_btn.pack(side="right", padx=(6, 0))
        self.mcp_stop_btn.configure(state="disabled")

        self.mcp_start_btn = self._brand_button(
            status_row, "Start", self._on_mcp_start, primary=True,
        )
        self.mcp_start_btn.pack(side="right")

        # URL / endpoint info
        info = tk.Frame(body, bg=PALE_BLUE)
        info.pack(fill="x", pady=(8, 0))
        tk.Label(
            info, text="Endpoint:",
            bg=PALE_BLUE, fg=DARK_GRAY,
            font=("Arial", 9, "bold"), anchor="w", width=12,
        ).grid(row=0, column=0, sticky="w")
        self.mcp_url_var = tk.StringVar(value="—")
        tk.Label(
            info, textvariable=self.mcp_url_var,
            bg=PALE_BLUE, fg=DARK_BLUE,
            font=("Consolas", 9), anchor="w",
        ).grid(row=0, column=1, sticky="w")

        tk.Label(
            info, text="SSE:",
            bg=PALE_BLUE, fg=DARK_GRAY,
            font=("Arial", 9, "bold"), anchor="w", width=12,
        ).grid(row=1, column=0, sticky="w")
        self.mcp_sse_var = tk.StringVar(value="—")
        tk.Label(
            info, textvariable=self.mcp_sse_var,
            bg=PALE_BLUE, fg=DARK_BLUE,
            font=("Consolas", 9), anchor="w",
        ).grid(row=1, column=1, sticky="w")

    # -- Tools panel ---------------------------------------------------------

    def _build_tools_panel(self) -> None:
        card = tk.Frame(self.root, bg=WHITE,
                        highlightthickness=1, highlightbackground=GRAY_BDR)
        card.pack(fill="both", expand=True, padx=18, pady=(8, 14))

        tk.Label(
            card, text="  TOOLS",
            bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 10, "bold"),
            anchor="w", padx=10, pady=6,
        ).pack(fill="x")
        tk.Frame(card, bg=MID_BLUE, height=2).pack(fill="x")

        body = tk.Frame(card, bg=WHITE, padx=14, pady=12)
        body.pack(fill="both", expand=True)

        self._tool_row(
            body,
            "Release Workflow",
            "Walk through compliance, sync properties, get files local, "
            "rebuild in Inventor, and release CAD + items.",
            "Open Workflow",
            self._on_open_workflow,
            primary=True,
        )
        self._tool_row(
            body,
            "BOM → Purchasing Sheet",
            "Generate a Simplifyber-branded purchasing workbook for "
            "budgeting and buying — by part number from Vault, or from "
            "an exported BOM file.",
            "Open Purchasing",
            self._on_open_purchasing,
            primary=False,
        )
        self._tool_row(
            body,
            "MFG Order Package",
            "Build a manufacturing-order folder: MFG BOM, watermarked "
            "PDFs (RELEASED / FOR REVIEW), and STEP files — all in one "
            "clean folder under Downloads.",
            "Open Builder",
            self._on_open_mfg_package,
            primary=False,
        )
        self._tool_row(
            body,
            "Property Check (Lookup)",
            "Quick standalone tool for checking a single item's properties "
            "against the rules without running the full release workflow.",
            "Open Lookup",
            self._on_open_property_check,
            primary=False,
        )
        self._tool_row(
            body,
            "Open Reports Folder",
            "Browse saved Markdown readiness reports and MCP server logs.",
            "Open Folder",
            self._on_open_logs,
            primary=False,
        )
        self._tool_row(
            body,
            "Edit Property Rules",
            "Open item_property_rules.json in your default editor to tune "
            "what gets enforced per category.",
            "Edit Rules",
            self._on_edit_rules,
            primary=False,
        )

    def _tool_row(self, parent, title, desc, btn_text, command, *, primary):
        row = tk.Frame(parent, bg=WHITE, pady=8)
        row.pack(fill="x")
        text = tk.Frame(row, bg=WHITE)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(
            text, text=title, bg=WHITE, fg=DARK_BLUE,
            font=("Arial", 11, "bold"), anchor="w",
        ).pack(fill="x")
        tk.Label(
            text, text=desc, bg=WHITE, fg=DARK_GRAY,
            font=("Arial", 9), anchor="w", justify="left",
            wraplength=400,
        ).pack(fill="x", pady=(2, 0))
        self._brand_button(
            row, f"  {btn_text}  ", command, primary=primary,
        ).pack(side="right", padx=(12, 0))
        # Subtle separator
        tk.Frame(parent, bg=GRAY_BDR, height=1).pack(fill="x", pady=(4, 0))

    # -- Status bar ----------------------------------------------------------

    def _build_status_bar(self) -> None:
        self.status_var = tk.StringVar(value="Ready.")
        bar = tk.Frame(self.root, bg=PALE_BLUE,
                       highlightthickness=1, highlightbackground=GRAY_BDR)
        bar.pack(fill="x", side="bottom")
        tk.Label(
            bar, textvariable=self.status_var,
            bg=PALE_BLUE, fg=DARK_BLUE,
            font=("Arial", 9), anchor="w",
            padx=12, pady=4,
        ).pack(fill="x", side="left", expand=True)

    # -- Brand button factory (mirror of the workflow GUI) ------------------

    def _brand_button(self, parent, text, command, *, primary: bool) -> tk.Button:
        if primary:
            bg, fg = DARK_BLUE, WHITE
            active_bg, active_fg = MID_BLUE, WHITE
            font = ("Arial", 10, "bold")
        else:
            bg, fg = MID_BLUE, WHITE
            active_bg, active_fg = DARK_BLUE, WHITE
            font = ("Arial", 9, "bold")
        return tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, font=font,
            relief="flat",
            padx=14 if primary else 10,
            pady=6 if primary else 4,
            cursor="hand2",
            activebackground=active_bg, activeforeground=active_fg,
            disabledforeground="#DDDDDD",
            borderwidth=0, highlightthickness=0,
        )

    # -- Window icon ---------------------------------------------------------

    def _set_window_icon(self) -> None:
        if not _pil_available:
            return
        try:
            from PIL import Image as PILImage, ImageTk
            icon_path = _resource_path("Simplifyber_Logo.png")
            if not os.path.isfile(icon_path):
                return
            ico = PILImage.open(icon_path).convert("RGBA")
            size = max(ico.width, ico.height)
            square = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
            square.paste(ico, ((size - ico.width) // 2,
                               (size - ico.height) // 2))
            square = square.resize((64, 64), PILImage.LANCZOS)
            self._icon_img = ImageTk.PhotoImage(square)
            self.root.iconphoto(True, self._icon_img)
        except Exception:  # noqa: BLE001 — icon is cosmetic
            pass

    # ----- Status refresh ---------------------------------------------------

    def _refresh_vault_panel(self) -> None:
        connected = bool(self.api and self.vault_id and self.access_token)
        if connected:
            self.vault_status_dot.configure(fg="#1F6B2E")  # forest green
            self.vault_status_text.configure(text="Connected", fg="#1F6B2E")
        else:
            self.vault_status_dot.configure(fg=DARK_GRAY)
            self.vault_status_text.configure(text="Not signed in", fg=DARK_GRAY)

        vc = (self.cfg.get("vault") or {})
        self.vault_grid_vars["Server"].set(vc.get("servername", "—"))
        self.vault_grid_vars["Database"].set(vc.get("database", "—"))
        self.vault_grid_vars["User"].set(vc.get("username", "—"))
        self.vault_grid_vars["Vault ID"].set(self.vault_id or "—")

    def _refresh_mcp_panel(self) -> None:
        if not self.mcp_ctrl:
            self.mcp_status_dot.configure(fg=DARK_GRAY)
            self.mcp_status_text.configure(text="No Vault session", fg=DARK_GRAY)
            self.mcp_url_var.set("—")
            self.mcp_sse_var.set("—")
            self.mcp_start_btn.configure(state="disabled")
            self.mcp_stop_btn.configure(state="disabled")
            self.mcp_open_btn.configure(state="disabled")
            return

        self.mcp_url_var.set(self.mcp_ctrl.url)
        self.mcp_sse_var.set(f"{self.mcp_ctrl.url}/sse")

        if self.mcp_ctrl.is_running():
            self.mcp_status_dot.configure(fg="#1F6B2E")
            self.mcp_status_text.configure(text="Running", fg="#1F6B2E")
            self.mcp_start_btn.configure(state="disabled")
            self.mcp_stop_btn.configure(state="normal")
            self.mcp_open_btn.configure(state="normal")
        else:
            err = self.mcp_ctrl.last_error
            label = "Stopped" if not err else f"Error — {err}"
            color = DARK_GRAY if not err else RUST_ORANGE
            self.mcp_status_dot.configure(fg=color)
            self.mcp_status_text.configure(text=label, fg=color)
            self.mcp_start_btn.configure(state="normal")
            self.mcp_stop_btn.configure(state="disabled")
            self.mcp_open_btn.configure(state="disabled")

    def _periodic_status_refresh(self) -> None:
        # Cheap — just re-evaluates state already in memory; no Vault calls.
        self._refresh_mcp_panel()
        self.root.after(2000, self._periodic_status_refresh)

    # ----- Cross-thread queue drain ----------------------------------------

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                self._handle_signal(kind, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def _handle_signal(self, kind: str, payload: Any) -> None:
        if kind == "status":
            self.status_var.set(str(payload))
        elif kind == "reconnect_done":
            ok, err = payload
            if ok:
                self.status_var.set("Reconnected to Vault.")
                self._refresh_vault_panel()
                # Rebuild MCP controller around the fresh session
                if self.api and self.vault_id:
                    if self.mcp_ctrl and self.mcp_ctrl.is_running():
                        self.mcp_ctrl.stop()
                    self.mcp_ctrl = self._build_vault_ctrl()
                self._refresh_mcp_panel()
            else:
                self.status_var.set(f"Reconnect failed: {err}")
                messagebox.showerror("Reconnect failed", err, parent=self.root)

    # ----- Action handlers --------------------------------------------------

    def _on_reconnect(self) -> None:
        self.status_var.set("Signing in…")

        def worker() -> None:
            try:
                # Import here so the launcher works even when app.py isn't
                # importable (e.g. running ``python -m gui.launcher`` directly).
                from app import _sign_in_for_gui, load_config
                cfg = load_config(Path(PROJECT_ROOT) / "config.json")
                api, vault_id, access_token, user_id = asyncio.run(
                    _sign_in_for_gui(cfg)
                )
            except Exception as exc:  # noqa: BLE001
                self.q.put(("reconnect_done", (False, str(exc))))
                return
            self.cfg = cfg
            self.api = api
            self.vault_id = vault_id
            self.access_token = access_token
            self.user_id = user_id
            self.q.put(("reconnect_done", (True, "")))

        threading.Thread(target=worker, daemon=True).start()

    def _on_mcp_start(self) -> None:
        if not self.mcp_ctrl:
            messagebox.showwarning(
                "Not signed in",
                "Click Reconnect first to sign in to Vault.",
                parent=self.root,
            )
            return
        ok = self.mcp_ctrl.start()
        if ok:
            self.status_var.set(f"MCP server starting on {self.mcp_ctrl.url}")
        else:
            err = self.mcp_ctrl.last_error or "unknown error"
            self.status_var.set(f"MCP start failed: {err}")
            messagebox.showerror(
                "MCP start failed", err, parent=self.root,
            )
        # Give uvicorn a moment to bind, then refresh
        self.root.after(400, self._refresh_mcp_panel)

    def _on_mcp_stop(self) -> None:
        if not self.mcp_ctrl:
            return
        self.status_var.set("Stopping MCP server…")
        self.mcp_ctrl.stop()
        self.status_var.set("MCP server stopped.")
        self._refresh_mcp_panel()

    def _on_mcp_open_browser(self) -> None:
        if not self.mcp_ctrl or not self.mcp_ctrl.is_running():
            return
        webbrowser.open(self.mcp_ctrl.url)

    def _on_open_workflow(self) -> None:
        if not (self.api and self.vault_id and self.access_token):
            messagebox.showwarning(
                "Not signed in",
                "Click Reconnect first — the Release Workflow needs an "
                "authenticated Vault session.",
                parent=self.root,
            )
            return
        try:
            from gui.release_workflow import launch_gui as launch_workflow
        except ImportError as exc:
            messagebox.showerror(
                "Workflow unavailable", str(exc), parent=self.root,
            )
            return
        # Open as a Toplevel child so the launcher remains visible
        launch_workflow(
            api=self.api, vault_id=self.vault_id,
            access_token=self.access_token, user_id=self.user_id,
            cfg=self.cfg, parent=self.root,
        )

    def _on_open_purchasing(self) -> None:
        try:
            from gui.purchasing import launch_purchasing_gui
        except ImportError as exc:
            messagebox.showerror(
                "Purchasing tool unavailable", str(exc), parent=self.root,
            )
            return
        # Vault session is optional — the tool still supports the file-import
        # flow when no session is attached, so we don't gate on it here.
        launch_purchasing_gui(
            api=self.api, vault_id=self.vault_id,
            cfg=self.cfg, parent=self.root,
        )
        self.status_var.set("Launching Purchasing Sheet…")

    def _on_open_mfg_package(self) -> None:
        if not (self.api and self.vault_id):
            messagebox.showwarning(
                "Not signed in",
                "Click Reconnect first — the MFG Package builder needs an "
                "authenticated Vault session.",
                parent=self.root,
            )
            return
        try:
            from gui.mfg_package import launch_gui as launch_mfg_gui
        except ImportError as exc:
            messagebox.showerror(
                "MFG Package tool unavailable", str(exc), parent=self.root,
            )
            return
        launch_mfg_gui(
            api=self.api, vault_id=self.vault_id,
            cfg=self.cfg, parent=self.root,
        )
        self.status_var.set("Launching MFG Package Builder…")

    def _on_open_property_check(self) -> None:
        # The original tool sits in check_item_properties.py — it has its own
        # GUI (a single-item lookup). It signs in lazily; runs in its own
        # window. We launch it on a worker thread because run_gui calls
        # mainloop internally — we don't want to block the launcher.
        def worker() -> None:
            try:
                from check_item_properties import run_gui as run_lookup_gui
                run_lookup_gui()
            except Exception as exc:  # noqa: BLE001
                self.q.put(("status", f"Property Check failed: {exc}"))

        threading.Thread(target=worker, daemon=True).start()
        self.status_var.set("Launching Property Check…")

    def _on_open_logs(self) -> None:
        log_dir = PROJECT_ROOT / "Log"
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(log_dir))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(log_dir)])
            else:
                subprocess.Popen(["xdg-open", str(log_dir)])
            self.status_var.set(f"Opened {log_dir}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Could not open folder", str(exc), parent=self.root,
            )

    def _on_edit_rules(self) -> None:
        rules_path = PROJECT_ROOT / "item_property_rules.json"
        if not rules_path.exists():
            messagebox.showerror(
                "Rules file missing",
                f"Could not find {rules_path}", parent=self.root,
            )
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(rules_path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(rules_path)])
            else:
                subprocess.Popen(["xdg-open", str(rules_path)])
            self.status_var.set(f"Opened {rules_path.name}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Could not open file", str(exc), parent=self.root,
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def launch_launcher(
    *,
    api: Optional[VaultRestAPI] = None,
    vault_id: str = "",
    access_token: str = "",
    user_id: str = "",
    cfg: Optional[dict[str, Any]] = None,
    auto_start_mcp: bool = False,
) -> None:
    """Open the dashboard. Pass an authenticated session to skip first-time
    sign-in (the user can still hit Reconnect to refresh).

    ``auto_start_mcp`` makes the dashboard kick off the SSE MCP server as
    soon as the window appears — used when the launcher is the front-end
    for ``python app.py`` (default SSE mode)."""
    root = tk.Tk()
    LauncherGUI(
        root,
        api=api, vault_id=vault_id,
        access_token=access_token, user_id=user_id, cfg=cfg,
        auto_start_mcp=auto_start_mcp,
    )
    root.mainloop()


if __name__ == "__main__":
    # Standalone: the user can also launch the dashboard directly. It signs
    # in lazily once they hit Reconnect.
    try:
        from app import _sign_in_for_gui, load_config
        cfg = load_config(PROJECT_ROOT / "config.json")
        api, vault_id, access_token, user_id = asyncio.run(_sign_in_for_gui(cfg))
        launch_launcher(api=api, vault_id=vault_id,
                        access_token=access_token, user_id=user_id, cfg=cfg)
    except SystemExit:
        # load_config exits hard on missing config — fall through to empty
        launch_launcher()
    except Exception:
        launch_launcher()
