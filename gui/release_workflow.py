"""
Tkinter GUI for the Vault release workflow.

Wraps `release_workflow.py` in a single-window wizard:

    [ Part number / config / workfolder / target state ]
    [ Step list with live status: Pending / Running / OK / Skipped / Failed ]
    [ Output log (Markdown report + per-step messages) ]
    [ Action buttons: Run Step | Skip Step | Run All Remaining | Stop ]

Each step runs on a worker thread so the UI never freezes. Confirmations are
replaced by explicit per-step buttons — the user *clicks* "Run Step 3" rather
than typing `y` at a console prompt. Compliance failures hard-stop the wizard
unless the user explicitly clicks "Force continue".

Launch:
    python scripts/release_workflow.py --gui
    python scripts/release_workflow.py SF-001702 --gui      # pre-fill PN
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# The palette lives in gui/theme.py. These re-exports exist because six
# modules — gui.launcher, gui.purchasing, gui.mfg_package, gui.publish_bom,
# gui.file_property_check, gui.purchasing_list_sync — still import the names
# from here; do not remove them without updating every one of those six.
# (app.py and scripts/release_workflow.py also import from this module, but
# only for launch_gui, which this shim has nothing to do with.)
from gui.theme import (  # noqa: F401,E402
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY,
    WHITE, OLIVE_GREEN, RUST_ORANGE, WARN_AMBER,
    _pil_available, _resource_path,
    PILImage, ImageTk,
)

from check_item_properties import (  # noqa: E402
    DEFAULT_RULES_PATH,
    check_part_number,
    format_markdown_report,
    load_json,
)
from release_workflow import (  # noqa: E402
    CONFIG_PATH,
    DEFAULT_WORKFOLDER,
    _associated_file_versions,
    _collect_file_master_ids,
    _sign_in,
)
# vault_soap was the previous lifecycle-change path; it's been replaced by
# vault_sdk (PowerShell ↔ .NET SDK bridge). The import lives inline at the
# step-runner that needs it so we fail late and clearly if the bridge is
# missing rather than blocking GUI startup.

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------

STEPS = [
    ("1", "Compliance check",   "Walk BOM and run property rules"),
    ("2", "Readiness report",   "Render the markdown gate report"),
    ("3", "Sync properties",    "Submit Autodesk.Vault.SyncProperties for every CAD file"),
    ("4", "Download local",     "REST-download every referenced file to the workfolder"),
    ("5", "Inventor rebuild",   "Open .iam in Inventor, Update2(), Save"),
    ("6", "Release CAD",        "SOAP UpdateFileLifeCycleStates"),
    ("7", "Release items",      "SOAP UpdateItemLifeCycleStates"),
]

STATUS_PENDING  = "PENDING"
STATUS_RUNNING  = "RUNNING"
STATUS_OK       = "OK"
STATUS_SKIPPED  = "SKIPPED"
STATUS_FAILED   = "FAILED"
STATUS_BLOCKED  = "BLOCKED"

STATUS_TAGS = {
    STATUS_PENDING: (DARK_GRAY,    PALE_BLUE,   "·"),
    STATUS_RUNNING: (WHITE,        MID_BLUE,    "▶"),
    STATUS_OK:      (DARK_BLUE,    OLIVE_GREEN, "✓"),
    STATUS_SKIPPED: (DARK_GRAY,    LIGHT_GRAY,  "—"),
    STATUS_FAILED:  (WHITE,        RUST_ORANGE, "✗"),
    STATUS_BLOCKED: (WHITE,        RUST_ORANGE, "■"),
}


# ---------------------------------------------------------------------------
# Worker plumbing
# ---------------------------------------------------------------------------

class WorkerSignal:
    """Cross-thread message: ``(kind, payload)`` posted via a queue.Queue."""
    __slots__ = ("kind", "payload")

    def __init__(self, kind: str, payload: Any = None):
        self.kind = kind
        self.payload = payload


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ReleaseWorkflowGUI:
    """The full wizard — one instance per run."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        prefill_part_number: str = "",
        api: Any = None,
        vault_id: str = "",
        access_token: str = "",
        user_id: str = "",
        cfg: Optional[dict[str, Any]] = None,
    ) -> None:
        """Build the wizard window.

        ``api`` / ``vault_id`` / ``access_token`` / ``cfg`` are optional. When
        passed (e.g. from ``app.py --gui``) the GUI reuses that already-signed-in
        Vault session for every step instead of running its own sign-in. This
        makes the GUI a first-class mode of the main Vault integration rather
        than a separate program with its own auth.
        """
        self.root = root
        self.root.title("Simplifyber — Vault Release Workflow")
        self.root.geometry("1180x820")
        self.root.minsize(960, 640)
        self.root.configure(bg=LIGHT_GRAY)

        # Cross-thread message queue
        self.q: queue.Queue[WorkerSignal] = queue.Queue()

        # Workflow state — populated either by the optional pre-auth params
        # below, or lazily on first need by ``_ensure_signed_in``.
        self.compliance: Optional[dict[str, Any]] = None
        self.api = api
        self.vault_id: str = vault_id or ""
        self.access_token: str = access_token or ""
        self.user_id: str = user_id or ""
        self.cfg: dict[str, Any] = cfg or {}
        self.downloads: list[dict[str, Any]] = []
        self.statuses: dict[str, str] = {n: STATUS_PENDING for n, *_ in STEPS}

        # Pre-auth provenance — keep the human-readable connection summary
        # so we can surface it in the status bar / output panel.
        self._preauth: bool = bool(api and vault_id and access_token)

        self.worker_thread: Optional[threading.Thread] = None
        self.busy = False

        # Tk PhotoImage references — kept on the instance so GC doesn't
        # collect the Pillow-backed images out from under the widgets.
        self._logo_img = None
        self._icon_img = None

        self._set_window_icon()
        self._build_ui()
        self._set_step_statuses_initial()

        if prefill_part_number:
            self.pn_var.set(prefill_part_number)

        if self._preauth:
            vault_cfg = (self.cfg.get("vault") or {})
            srv = vault_cfg.get("servername", "(server)")
            user = vault_cfg.get("username", "(user)")
            db = vault_cfg.get("database", "(db)")
            self.status_var.set(
                f"Connected to {srv}  /  vault '{db}'  as  {user}  "
                f"(vault_id={self.vault_id})"
            )

        # Drain the cross-thread message queue every 100 ms
        self.root.after(100, self._drain_queue)

        # Pull lifecycle state names from Vault to fill the Target State
        # dropdown. Async so the GUI doesn't block on construction; the
        # placeholder ["Released"] stays selectable in the meantime.
        self.root.after(50, self._populate_states_async)

    # ----- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        self._build_header()
        self._build_input_section()
        self._build_body()
        self._build_action_bar()
        self._build_status_bar()

    # -- Header bar ----------------------------------------------------------

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=DARK_BLUE, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)

        # White Simplifyber logo on the left of the header bar
        if _pil_available:
            logo_path = _resource_path("Simplifyber_Logo_White.png")
            if os.path.isfile(logo_path):
                try:
                    img = PILImage.open(logo_path).convert("RGBA")
                    target_h = 36
                    target_w = int(target_h * img.width / img.height)
                    img = img.resize((target_w, target_h), PILImage.LANCZOS)
                    self._logo_img = ImageTk.PhotoImage(img)
                    tk.Label(header, image=self._logo_img,
                             bg=DARK_BLUE).pack(side="left", padx=16)
                except Exception:  # noqa: BLE001
                    pass

        tk.Label(
            header,
            text="Vault Release Workflow",
            font=("Arial", 13, "bold"),
            fg=WHITE, bg=DARK_BLUE,
        ).pack(side="left", expand=True)

        # Mid-blue accent strip — same 3-px line used in bom_to_purchasing.
        tk.Frame(self.root, bg=MID_BLUE, height=3).pack(fill="x")

    # -- Inputs section ------------------------------------------------------

    def _build_input_section(self) -> None:
        inputs = tk.Frame(self.root, bg=LIGHT_GRAY, padx=20, pady=14)
        inputs.pack(fill="x")
        for c in (1, 3, 5):
            inputs.columnconfigure(c, weight=1)

        def label(parent, text, **kw):
            return tk.Label(
                parent, text=text, bg=LIGHT_GRAY,
                fg=DARK_BLUE, font=("Arial", 9, "bold"),
                anchor="w", **kw,
            )

        def entry(parent, var, width=20):
            return tk.Entry(
                parent, textvariable=var, width=width,
                font=("Arial", 10),
                bg=WHITE, relief="solid", bd=1,
                highlightthickness=1,
                highlightbackground=GRAY_BDR,
                highlightcolor=MID_BLUE,
            )

        # Row 0 — part number / target state / state id / soap version
        label(inputs, "Part Number").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.pn_var = tk.StringVar()
        # Wrap entry + Search button in a sub-frame so the surrounding column
        # grid (target state / state id / soap) doesn't have to shift.
        pn_frame = tk.Frame(inputs, bg=LIGHT_GRAY)
        pn_frame.grid(row=0, column=1, sticky="ew", padx=(0, 14))
        entry(pn_frame, self.pn_var, width=18).pack(
            side="left", fill="x", expand=True
        )
        self._brand_button(
            pn_frame, "Search…", self._open_search_dialog, primary=False,
        ).pack(side="left", padx=(6, 0))

        label(inputs, "Target State").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.target_state_var = tk.StringVar(value="Released")
        # Combobox lets the user pick a known state OR type one in if the
        # live lookup hasn't returned yet / fails. Values are populated
        # async — see _populate_states_async.
        self._apply_combobox_style()
        self.target_state_combo = ttk.Combobox(
            inputs,
            textvariable=self.target_state_var,
            values=["Released"],   # safe placeholder until Vault lookup completes
            width=18,
            style="Vault.TCombobox",
        )
        self.target_state_combo.grid(
            row=0, column=3, sticky="ew", padx=(0, 14)
        )

        label(inputs, "State ID (override)").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.target_state_id_var = tk.StringVar()
        entry(inputs, self.target_state_id_var, width=8).grid(
            row=0, column=5, sticky="ew", padx=(0, 14)
        )

        label(inputs, "SOAP").grid(row=0, column=6, sticky="w", padx=(0, 6))
        self.soap_version_var = tk.StringVar(value="v26")
        entry(inputs, self.soap_version_var, width=6).grid(row=0, column=7, sticky="w")

        # Row 1 — workfolder
        label(inputs, "Workfolder").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.workfolder_var = tk.StringVar(value=str(DEFAULT_WORKFOLDER))
        wf_entry = entry(inputs, self.workfolder_var, width=10)
        wf_entry.grid(row=1, column=1, columnspan=6, sticky="ew",
                      padx=(0, 6), pady=(10, 0))
        self._brand_button(
            inputs, "Browse…", self._browse_workfolder, primary=False,
        ).grid(row=1, column=7, sticky="w", pady=(10, 0))

        # Row 2 — top assembly
        label(inputs, "Top .iam").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.top_iam_var = tk.StringVar()
        iam_entry = entry(inputs, self.top_iam_var, width=10)
        iam_entry.grid(row=2, column=1, columnspan=6, sticky="ew",
                       padx=(0, 6), pady=(6, 0))
        self._brand_button(
            inputs, "Browse…", self._browse_top_iam, primary=False,
        ).grid(row=2, column=7, sticky="w", pady=(6, 0))

        # Row 3 — toggles
        toggles = tk.Frame(inputs, bg=LIGHT_GRAY)
        toggles.grid(row=3, column=0, columnspan=8, sticky="w", pady=(10, 0))

        self.force_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            toggles, text="Force past compliance gate",
            variable=self.force_var,
            bg=LIGHT_GRAY, fg=DARK_BLUE,
            activebackground=LIGHT_GRAY, activeforeground=DARK_BLUE,
            selectcolor=WHITE, font=("Arial", 9),
        ).pack(side="left", padx=(0, 16))

        self.visible_inventor_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            toggles, text="Show Inventor window",
            variable=self.visible_inventor_var,
            bg=LIGHT_GRAY, fg=DARK_BLUE,
            activebackground=LIGHT_GRAY, activeforeground=DARK_BLUE,
            selectcolor=WHITE, font=("Arial", 9),
        ).pack(side="left", padx=(0, 16))

    # -- Body (steps + output) ----------------------------------------------

    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=LIGHT_GRAY, padx=20, pady=4)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=0, minsize=320)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # Steps card — pale-blue panel with dark-blue title strip
        steps_card = tk.Frame(body, bg=PALE_BLUE,
                              highlightthickness=1,
                              highlightbackground=GRAY_BDR)
        steps_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))

        tk.Label(
            steps_card, text="  WORKFLOW STEPS",
            bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 10, "bold"),
            anchor="w", padx=10, pady=6,
        ).pack(fill="x")
        tk.Frame(steps_card, bg=MID_BLUE, height=2).pack(fill="x")

        self.step_labels: dict[str, tk.Label] = {}
        steps_inner = tk.Frame(steps_card, bg=PALE_BLUE, padx=10, pady=10)
        steps_inner.pack(fill="both", expand=True)

        for num, name, desc in STEPS:
            row = tk.Frame(steps_inner, bg=PALE_BLUE)
            row.pack(fill="x", pady=4)

            badge = tk.Label(
                row, text=f"  ·  Step {num}: {name}  [PENDING]",
                anchor="w", font=("Arial", 10, "bold"),
                bg=PALE_BLUE, fg=DARK_GRAY,
                padx=8, pady=5,
                relief="solid", bd=1,
                highlightthickness=0,
            )
            # Tk Labels can't have a coloured border like CSS, so we use
            # the underlying widget background to convey the status colour
            # — simpler and more readable than re-styling on every change.
            badge.pack(fill="x")
            self.step_labels[num] = badge

            sub = tk.Label(
                row, text=f"        {desc}",
                bg=PALE_BLUE, fg=DARK_GRAY,
                font=("Arial", 8), anchor="w",
            )
            sub.pack(fill="x", padx=(2, 0))

        # Output card — white "document" with dark-blue header strip
        out_card = tk.Frame(body, bg=WHITE,
                            highlightthickness=1,
                            highlightbackground=GRAY_BDR)
        out_card.grid(row=0, column=1, sticky="nsew")

        tk.Label(
            out_card, text="  OUTPUT",
            bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 10, "bold"),
            anchor="w", padx=10, pady=6,
        ).pack(fill="x")
        tk.Frame(out_card, bg=MID_BLUE, height=2).pack(fill="x")

        text_frame = tk.Frame(out_card, bg=WHITE)
        text_frame.pack(fill="both", expand=True)

        self.text = tk.Text(
            text_frame, wrap="word",
            font=("Consolas", 10),
            bg=WHITE, fg="#222222",
            insertbackground=DARK_BLUE,
            borderwidth=0, highlightthickness=0,
            padx=12, pady=10,
        )
        ys = tk.Scrollbar(text_frame, orient="vertical",
                          command=self.text.yview,
                          bg=LIGHT_GRAY, troughcolor=PALE_BLUE,
                          activebackground=MID_BLUE)
        self.text.configure(yscrollcommand=ys.set, state="disabled")
        self.text.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")

        # Light-theme color tags — same naming as before, new palette
        self.text.tag_configure("h1",   foreground=DARK_BLUE,
                                font=("Arial", 12, "bold"),
                                spacing1=8, spacing3=4)
        self.text.tag_configure("h2",   foreground=DARK_BLUE,
                                font=("Arial", 10, "bold"),
                                spacing1=6, spacing3=2)
        self.text.tag_configure("dim",  foreground=DARK_GRAY)
        self.text.tag_configure("pass", foreground="#1F6B2E",
                                font=("Consolas", 10, "bold"))
        self.text.tag_configure("fail", foreground=RUST_ORANGE,
                                font=("Consolas", 10, "bold"))
        self.text.tag_configure("warn", foreground=WARN_AMBER)
        self.text.tag_configure("info", foreground=MID_BLUE)
        self.text.tag_configure("step_banner",
                                foreground=WHITE, background=DARK_BLUE,
                                font=("Arial", 10, "bold"),
                                spacing1=10, spacing3=4)

    # -- Action bar ----------------------------------------------------------

    def _build_action_bar(self) -> None:
        actions = tk.Frame(self.root, bg=LIGHT_GRAY, padx=20, pady=12)
        actions.pack(fill="x")

        self.btn_run = self._brand_button(
            actions, "  Run next step  ", self._on_run_next, primary=True,
        )
        self.btn_run.pack(side="left", padx=(0, 8))

        self.btn_skip = self._brand_button(
            actions, "Skip step", self._on_skip, primary=False,
        )
        self.btn_skip.pack(side="left", padx=(0, 8))

        self.btn_run_all = self._brand_button(
            actions, "Run all remaining", self._on_run_all, primary=False,
        )
        self.btn_run_all.pack(side="left", padx=(0, 8))

        self.btn_save_report = self._brand_button(
            actions, "Save report…", self._on_save_report, primary=False,
        )
        self.btn_save_report.configure(state="disabled")
        self.btn_save_report.pack(side="left", padx=(0, 8))

        self.btn_reset = self._brand_button(
            actions, "Reset", self._on_reset, primary=False,
        )
        self.btn_reset.pack(side="right")

    # -- Status bar ----------------------------------------------------------

    def _build_status_bar(self) -> None:
        self.status_var = tk.StringVar(
            value="Ready. Enter a part number and click 'Run next step'."
        )
        bar = tk.Frame(self.root, bg=PALE_BLUE,
                       highlightthickness=1, highlightbackground=GRAY_BDR)
        bar.pack(fill="x", side="bottom")
        tk.Label(
            bar, textvariable=self.status_var,
            bg=PALE_BLUE, fg=DARK_BLUE,
            font=("Arial", 9), anchor="w",
            padx=12, pady=4,
        ).pack(fill="x", side="left", expand=True)

    # -- Brand button factory ------------------------------------------------

    def _brand_button(self, parent, text, command, *, primary: bool) -> tk.Button:
        if primary:
            bg, fg = DARK_BLUE, WHITE
            active_bg, active_fg = MID_BLUE, WHITE
            font = ("Arial", 10, "bold")
        else:
            bg, fg = MID_BLUE, WHITE
            active_bg, active_fg = DARK_BLUE, WHITE
            font = ("Arial", 9, "bold")
        btn = tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, font=font,
            relief="flat", padx=14 if primary else 10, pady=6 if primary else 4,
            cursor="hand2",
            activebackground=active_bg, activeforeground=active_fg,
            disabledforeground="#DDDDDD",
            borderwidth=0, highlightthickness=0,
        )
        return btn

    # -- Lifecycle-state dropdown plumbing ----------------------------------

    def _apply_combobox_style(self) -> None:
        """One-shot ttk style configuration for the Target State combobox.

        Runs idempotently (Tk just re-applies). Uses 'clam' so the
        background / foreground colours actually take effect on Windows
        — the default 'vista' theme ignores most options on Combobox.
        """
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Vault.TCombobox",
            fieldbackground=WHITE, background=WHITE,
            foreground="#222222",
            bordercolor=GRAY_BDR, lightcolor=GRAY_BDR, darkcolor=GRAY_BDR,
            arrowcolor=DARK_BLUE,
            padding=2,
        )
        style.map(
            "Vault.TCombobox",
            fieldbackground=[("readonly", WHITE), ("focus", WHITE)],
            bordercolor=[("focus", MID_BLUE)],
        )
        # The dropdown listbox itself uses a separate "ComboboxListbox"
        # element — colour it to match the rest of the UI.
        self.root.option_add("*TCombobox*Listbox.background", WHITE)
        self.root.option_add("*TCombobox*Listbox.foreground", "#222222")
        self.root.option_add("*TCombobox*Listbox.selectBackground", MID_BLUE)
        self.root.option_add("*TCombobox*Listbox.selectForeground", WHITE)
        self.root.option_add("*TCombobox*Listbox.font", ("Arial", 10))

    def _populate_states_async(self) -> None:
        """Fetch lifecycle state names from Vault on a worker thread and
        post the result onto the cross-thread queue. The ``states_loaded``
        signal handler then updates the combobox values.

        Uses the .NET-SDK PowerShell bridge (``vault_sdk``) — REST v2 has
        no lifecycle-definitions endpoint, and the legacy ASMX services
        the SOAP client targeted aren't exposed on this server, so the
        SDK is the only reliable path.
        """
        def worker() -> None:
            try:
                from vault_sdk import get_distinct_state_names
                names = get_distinct_state_names()
            except Exception as exc:  # noqa: BLE001
                self.q.put(WorkerSignal("states_loaded", ([], str(exc))))
                return
            self.q.put(WorkerSignal("states_loaded", (names, "")))

        threading.Thread(target=worker, daemon=True).start()

    # -- Window icon ---------------------------------------------------------

    def _set_window_icon(self) -> None:
        if not _pil_available:
            return
        icon_path = _resource_path("Simplifyber_Logo.png")
        if not os.path.isfile(icon_path):
            return
        try:
            ico = PILImage.open(icon_path).convert("RGBA")
            size = max(ico.width, ico.height)
            square = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
            square.paste(ico, ((size - ico.width) // 2,
                               (size - ico.height) // 2))
            square = square.resize((64, 64), PILImage.LANCZOS)
            self._icon_img = ImageTk.PhotoImage(square)
            self.root.iconphoto(True, self._icon_img)
        except Exception:  # noqa: BLE001 — icon is cosmetic, never fail launch
            pass

    # ----- Step status display ---------------------------------------------

    def _set_step_statuses_initial(self) -> None:
        for num in self.statuses:
            self._update_step_label(num, STATUS_PENDING)

    def _update_step_label(self, num: str, status: str) -> None:
        self.statuses[num] = status
        fg, bg, glyph = STATUS_TAGS[status]
        name = next((n for k, n, *_ in STEPS if k == num), "?")
        self.step_labels[num].configure(
            text=f"  {glyph}  Step {num}: {name}  [{status}]",
            foreground=fg, background=bg,
        )

    def _next_pending_step(self) -> Optional[str]:
        for num, *_ in STEPS:
            if self.statuses[num] == STATUS_PENDING:
                return num
            # If a previous step failed, block the rest
            if self.statuses[num] == STATUS_FAILED:
                return None
        return None

    # ----- Output helpers ---------------------------------------------------

    def _write(self, line: str = "", tag: Optional[str] = None) -> None:
        self.text.configure(state="normal")
        if tag:
            self.text.insert("end", line + "\n", tag)
        else:
            self.text.insert("end", line + "\n")
        self.text.configure(state="disabled")
        self.text.see("end")

    def _clear_output(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _banner(self, num: str, name: str) -> None:
        bar = "─" * 70
        self._write("")
        self._write(bar, "step_banner")
        self._write(f"  STEP {num} — {name}", "step_banner")
        self._write(bar, "step_banner")

    # ----- Browse helpers ---------------------------------------------------

    def _browse_workfolder(self) -> None:
        d = filedialog.askdirectory(initialdir=self.workfolder_var.get() or str(Path.home()))
        if d:
            self.workfolder_var.set(d)

    def _browse_top_iam(self) -> None:
        f = filedialog.askopenfilename(
            initialdir=self.workfolder_var.get() or str(Path.home()),
            filetypes=[("Inventor assembly", "*.iam"), ("All files", "*.*")],
        )
        if f:
            self.top_iam_var.set(f)

    # ----- Action-bar handlers ---------------------------------------------

    def _on_run_next(self) -> None:
        if self.busy:
            return
        nxt = self._next_pending_step()
        if not nxt:
            messagebox.showinfo("Workflow complete", "No more pending steps.")
            return
        self._run_step(nxt, run_all_after=False)

    def _on_skip(self) -> None:
        if self.busy:
            return
        nxt = self._next_pending_step()
        if not nxt:
            return
        self._update_step_label(nxt, STATUS_SKIPPED)
        self._write(f"\n[skipped] Step {nxt}", "dim")
        self.status_var.set(f"Step {nxt} skipped. Click 'Run next step' to continue.")

    def _on_run_all(self) -> None:
        if self.busy:
            return
        nxt = self._next_pending_step()
        if not nxt:
            return
        self._run_step(nxt, run_all_after=True)

    def _on_save_report(self) -> None:
        if not self.compliance:
            messagebox.showwarning("No report", "Run the compliance check first.")
            return
        f = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("All files", "*.*")],
            initialfile=f"release_readiness_{self.pn_var.get().strip() or 'report'}.md",
        )
        if not f:
            return
        Path(f).write_text(format_markdown_report(self.compliance), encoding="utf-8")
        self.status_var.set(f"Report saved: {f}")

    def _on_reset(self) -> None:
        if self.busy:
            messagebox.showwarning("Busy", "Wait for the current step to finish.")
            return
        self.compliance = None
        self.api = None
        self.vault_id = ""
        self.access_token = ""
        self.cfg = {}
        self.downloads = []
        for num in self.statuses:
            self._update_step_label(num, STATUS_PENDING)
        self._clear_output()
        self.btn_save_report.configure(state="disabled")
        self.status_var.set("Reset. Ready.")

    # ----- Step dispatch ----------------------------------------------------

    def _run_step(self, num: str, *, run_all_after: bool) -> None:
        pn = self.pn_var.get().strip()
        if not pn:
            messagebox.showwarning("Missing part number", "Enter a part number first.")
            return

        # Per-step pre-flight UI updates
        name = next((n for k, n, *_ in STEPS if k == num), "?")
        self._banner(num, name)
        self._update_step_label(num, STATUS_RUNNING)
        self._set_busy(True)
        self.status_var.set(f"Step {num} ({name}) running…")

        # Pick the worker function for this step
        runner = self._step_runner(num)

        def thread_main() -> None:
            try:
                ok = runner()  # may post messages onto self.q
            except Exception as exc:  # noqa: BLE001 — surface any error to UI
                self.q.put(WorkerSignal("err", f"{type(exc).__name__}: {exc}"))
                ok = False
            self.q.put(WorkerSignal("step_done", (num, ok, run_all_after)))

        self.worker_thread = threading.Thread(target=thread_main, daemon=True)
        self.worker_thread.start()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        new_state = "disabled" if busy else "normal"
        for b in (self.btn_run, self.btn_skip, self.btn_run_all,
                  self.btn_save_report, self.btn_reset):
            try:
                b.configure(state=new_state)
            except tk.TclError:
                pass
        # Save-report only re-enables when we actually have a report
        if not busy and self.compliance is None:
            self.btn_save_report.configure(state="disabled")

    # ----- Cross-thread queue drain ----------------------------------------

    def _drain_queue(self) -> None:
        try:
            while True:
                sig = self.q.get_nowait()
                self._handle_signal(sig)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def _handle_signal(self, sig: WorkerSignal) -> None:
        if sig.kind == "log":
            line, tag = sig.payload
            self._write(line, tag)
        elif sig.kind == "status":
            self.status_var.set(str(sig.payload))
        elif sig.kind == "err":
            self._write(f"  [error] {sig.payload}", "fail")
        elif sig.kind == "states_loaded":
            names, err = sig.payload
            if err:
                # Soft failure — log to status bar, leave placeholder values
                self.status_var.set(
                    f"Could not load lifecycle states from Vault: {err}"
                )
                return
            if not names:
                return
            current = self.target_state_var.get().strip()
            self.target_state_combo.configure(values=names)
            # Preserve the user's current pick if it's still valid; otherwise
            # default to "Released" if available, else first state.
            if current in names:
                pass
            elif "Released" in names:
                self.target_state_var.set("Released")
            else:
                self.target_state_var.set(names[0])
        elif sig.kind == "step_done":
            num, ok, run_all_after = sig.payload
            new_status = STATUS_OK if ok else STATUS_FAILED
            self._update_step_label(num, new_status)
            self._set_busy(False)
            if num == "1" and ok:
                self.btn_save_report.configure(state="normal")
            if not ok:
                self.status_var.set(f"Step {num} failed — fix the issue and click 'Run next step' or 'Skip'.")
                # Don't auto-continue past a failure
                return
            self.status_var.set(f"Step {num} OK.")
            if run_all_after:
                nxt = self._next_pending_step()
                if nxt:
                    self._run_step(nxt, run_all_after=True)
                else:
                    self.status_var.set("All steps complete.")

    # ----- Per-step runners (executed on worker thread) --------------------

    def _step_runner(self, num: str) -> Callable[[], bool]:
        return {
            "1": self._run_step_1_compliance,
            "2": self._run_step_2_report,
            "3": self._run_step_3_sync,
            "4": self._run_step_4_download,
            "5": self._run_step_5_inventor,
            "6": self._run_step_6_release_cad,
            "7": self._run_step_7_release_items,
        }[num]

    def _log(self, line: str, tag: Optional[str] = None) -> None:
        self.q.put(WorkerSignal("log", (line, tag)))

    def _set_status(self, msg: str) -> None:
        self.q.put(WorkerSignal("status", msg))

    # Helper: run an async coroutine to completion on this worker thread
    def _run_async(self, coro):
        return asyncio.run(coro)

    def _ensure_signed_in(self) -> bool:
        # Pre-authenticated session passed in from app.py --gui? Reuse it.
        if self.api is not None and self.vault_id and self.access_token:
            return True
        try:
            self.cfg = load_json(Path(self.cfg_path()))
            self.api, self.vault_id, self.access_token = self._run_async(_sign_in(self.cfg))
            self._log(f"  [ok] Signed in to Vault (vault_id={self.vault_id})", "pass")
            return True
        except Exception as exc:  # noqa: BLE001
            self._log(f"  [fail] Vault sign-in failed: {exc}", "fail")
            return False

    def cfg_path(self) -> str:
        return str(CONFIG_PATH)

    # ----- Step 1: compliance ----------------------------------------------

    def _run_step_1_compliance(self) -> bool:
        pn = self.pn_var.get().strip()
        self._log(f"  Walking BOM for {pn} and running rules…", "info")
        try:
            result = self._run_async(check_part_number(
                pn, config_path=CONFIG_PATH,
                rules_path=DEFAULT_RULES_PATH, recursive=True,
            ))
        except Exception as exc:  # noqa: BLE001
            self._log(f"  [fail] {exc}", "fail")
            return False

        self.compliance = result
        self._log_compliance_summary(result)

        top_failed = bool((result.get("report") or {}).get("failed", 0))
        kids_failed = any(
            c.get("error") is not None or
            (c.get("report") or {}).get("failed", 0) > 0
            for c in (result.get("children") or [])
        )
        if top_failed or kids_failed:
            if not self.force_var.get():
                self._log(
                    "  Compliance gate is engaged — fix the items above then "
                    "re-run, or tick 'Force past compliance gate' and continue. "
                    "Step 2 will render the full markdown report.",
                    "warn",
                )
                # Still report success at the *step* level — the gate is a
                # separate decision made before subsequent steps.
        return True

    # ----- Step 1 output formatting ----------------------------------------

    def _log_compliance_summary(self, result: dict[str, Any]) -> None:
        """Write a compact, scannable failure summary to the output panel.

        Surfaces *what* failed, not just *that* something failed:
          • top-item header with pass/total + every failing property
          • children roll-up (counts) + per-child compact failure lines
          • final verdict banner
        Step 2 still renders the full markdown report — this is the
        scan-at-a-glance view a user gets right after Step 1 finishes.
        """
        pn = result.get("part_number", "?")
        info = result.get("info") or {}
        top_props = info.get("properties") or {}
        report = result.get("report") or {}
        children = result.get("children") or []
        category = result.get("category_resolved") or result.get("category_raw") or "(no rule set)"

        # ---- Top-level item ------------------------------------------------
        top_total = report.get("total", 0)
        top_pass = report.get("passed", 0)
        top_fail = report.get("failed", 0)
        verdict = "PASS" if top_fail == 0 else "FAIL"
        verdict_tag = "pass" if top_fail == 0 else "fail"

        self._log("")
        self._log(f"  Top item — {pn}  [{category}]", "h2")
        self._log(
            f"    revision={top_props.get('Revision', '?')!r}  "
            f"state={top_props.get('State', '?')!r}",
            "dim",
        )
        self._log(f"    {verdict}  ({top_pass}/{top_total} checks pass)", verdict_tag)

        if top_fail:
            for r in report.get("results") or []:
                if r.get("passed"):
                    continue
                v = r.get("value")
                v_str = "(empty)" if v in (None, "") else str(v)
                self._log(
                    f"      • {r['property']:24s}  = {v_str[:40]}",
                    "fail",
                )
                for f in r.get("failures") or []:
                    self._log(f"          → {f}", "fail")

        # ---- BOM children --------------------------------------------------
        if not children:
            return

        statuses = [self._child_status(c) for c in children]
        n_pass = statuses.count("PASS")
        n_fail = statuses.count("FAIL")
        n_skip = statuses.count("SKIP")
        n_err = statuses.count("ERROR")

        self._log("")
        self._log(f"  BOM children — {len(children)} item(s)", "h2")
        roll_tag = "pass" if (n_fail == 0 and n_err == 0) else "fail"
        self._log(
            f"    {n_pass} pass · {n_fail} fail · {n_skip} skipped · {n_err} errored",
            roll_tag,
        )

        # Compact per-child failure lines (only the offenders)
        offenders = [(c, st) for c, st in zip(children, statuses)
                     if st in ("FAIL", "ERROR")]
        if offenders:
            self._log("")
            self._log(f"    Offenders ({len(offenders)}):", "h2")
            for c, st in offenders:
                child_pn = c.get("part_number") or "?"
                cat = c.get("category_resolved") or c.get("category_raw") or "(no rule set)"

                if st == "ERROR":
                    self._log(
                        f"      • {child_pn:14s} [{cat}]  ERROR: {c.get('error', '')}",
                        "fail",
                    )
                    continue

                rep = c.get("report") or {}
                bad_props = [r for r in (rep.get("results") or []) if not r.get("passed")]
                # One-line summary: "SF-001702 [Assembly - Engineering] · 3 fail · Revision, Source, Engr Approved"
                names = ", ".join(b["property"] for b in bad_props)
                self._log(
                    f"      • {child_pn:14s} [{cat:24s}]  "
                    f"{len(bad_props)} fail · {names}",
                    "fail",
                )
                # Indented per-property reasons (one line each, no value to keep it scannable)
                for b in bad_props:
                    reasons = "; ".join(b.get("failures") or [])
                    self._log(f"          → {b['property']}: {reasons}", "dim")

        # ---- Final verdict line -------------------------------------------
        self._log("")
        if top_fail == 0 and n_fail == 0 and n_err == 0:
            self._log(
                f"  [OK]  All {1 + len(children)} item(s) pass compliance.",
                "pass",
            )
        else:
            total_failed = (1 if top_fail else 0) + n_fail + n_err
            self._log(
                f"  [WARN]  {total_failed} of {1 + len(children)} item(s) failed compliance.",
                "warn",
            )

    @staticmethod
    def _child_status(child: dict[str, Any]) -> str:
        if child.get("error"):
            return "ERROR"
        if not child.get("category_resolved"):
            return "SKIP"
        return "PASS" if (child.get("report") or {}).get("failed", 0) == 0 else "FAIL"

    # ----- Step 2: render readiness report ---------------------------------

    def _run_step_2_report(self) -> bool:
        if not self.compliance:
            self._log("  [fail] No compliance result — run step 1 first.", "fail")
            return False
        md = format_markdown_report(self.compliance)
        # Render line-by-line so headings get a heading colour
        for line in md.splitlines():
            tag = None
            if line.startswith("# "):
                tag = "h1"
            elif line.startswith("## "):
                tag = "h2"
            elif line.startswith("**READY**"):
                tag = "pass"
            elif line.startswith("**NOT READY**"):
                tag = "fail"
            self._log(line, tag)
        self._set_status("Readiness report rendered. Use 'Save report…' to write to disk.")
        return True

    # ----- Step 3: sync properties -----------------------------------------

    def _run_step_3_sync(self) -> bool:
        # Compliance gate
        if self._compliance_blocked():
            return False
        if not self._ensure_signed_in():
            return False

        async def gather_files() -> dict[str, dict[str, Any]]:
            files: dict[str, dict[str, Any]] = {}
            top_iv = (self.compliance.get("info") or {}).get("item_version_id") or ""
            for fv in await _associated_file_versions(self.api, self.vault_id, top_iv):
                fid = str(fv.get("id") or "")
                if fid:
                    files[fid] = fv
            for child in self.compliance.get("children") or []:
                cv = child.get("item_version_id") or ""
                if not cv:
                    continue
                for fv in await _associated_file_versions(self.api, self.vault_id, cv):
                    fid = str(fv.get("id") or "")
                    if fid and fid not in files:
                        files[fid] = fv
            return files

        try:
            files = self._run_async(gather_files())
        except Exception as exc:  # noqa: BLE001
            self._log(f"  [fail] could not enumerate files: {exc}", "fail")
            return False

        if not files:
            self._log("  [warn] No CAD files found — nothing to sync.", "warn")
            return True

        self._log(f"  Submitting Autodesk.Vault.SyncProperties for {len(files)} file(s)…", "info")

        async def submit_all() -> tuple[int, int]:
            ok_n = bad_n = 0
            for fid, fv in files.items():
                name = fv.get("name") or "(file)"
                resp = await self.api.submit_job(
                    vault_id=self.vault_id,
                    job_type="Autodesk.Vault.SyncProperties",
                    params={"FileVersionId": fid},
                    description=f"SyncProperties: {name}",
                    priority=10,
                )
                if resp["error"]:
                    self._log(f"    [fail] {name}: {resp['data']}", "fail")
                    bad_n += 1
                else:
                    job_id = str(((resp["data"] or {}).get("job") or {}).get("id")
                                 or resp["data"].get("id") or "?")
                    self._log(f"    [ok]   {name}  (job {job_id})", "pass")
                    ok_n += 1
            return ok_n, bad_n

        ok_n, bad_n = self._run_async(submit_all())
        self._log(f"  {ok_n} queued, {bad_n} failed.", "info")
        return bad_n == 0

    # ----- Step 4: download local ------------------------------------------

    def _run_step_4_download(self) -> bool:
        if self._compliance_blocked():
            return False
        if not self._ensure_signed_in():
            return False
        wf = Path(self.workfolder_var.get()).expanduser().resolve()
        wf.mkdir(parents=True, exist_ok=True)

        async def gather_and_download() -> list[dict[str, Any]]:
            files: dict[str, dict[str, Any]] = {}
            top_iv = (self.compliance.get("info") or {}).get("item_version_id") or ""
            for fv in await _associated_file_versions(self.api, self.vault_id, top_iv):
                fid = str(fv.get("id") or "")
                if fid:
                    files[fid] = fv
            for child in self.compliance.get("children") or []:
                cv = child.get("item_version_id") or ""
                if not cv:
                    continue
                for fv in await _associated_file_versions(self.api, self.vault_id, cv):
                    fid = str(fv.get("id") or "")
                    if fid and fid not in files:
                        files[fid] = fv

            results: list[dict[str, Any]] = []
            for fid, fv in files.items():
                name = fv.get("name") or f"file_{fid}"
                target = wf / name
                resp = await self.api.download_file_version_content(
                    vault_id=self.vault_id, file_version_id=fid
                )
                if resp["error"]:
                    self._log(f"    [fail] {name}: {resp['data']}", "fail")
                    results.append({"name": name, "ok": False, "error": resp["data"]})
                    continue
                target.write_bytes(resp["data"])
                self._log(f"    [ok]   {name}  ({len(resp['data']):,} bytes)", "pass")
                results.append({"name": name, "ok": True, "path": str(target),
                                "size": len(resp["data"])})
            return results

        self._log(f"  Downloading into {wf}", "info")
        try:
            self.downloads = self._run_async(gather_and_download())
        except Exception as exc:  # noqa: BLE001
            self._log(f"  [fail] download failed: {exc}", "fail")
            return False
        ok_n = sum(1 for d in self.downloads if d.get("ok"))
        self._log(f"  {ok_n}/{len(self.downloads)} files downloaded.", "info")
        return ok_n == len(self.downloads)

    # ----- Step 5: Inventor rebuild ----------------------------------------

    def _run_step_5_inventor(self) -> bool:
        if self._compliance_blocked():
            return False
        # Pick the .iam to open
        explicit = self.top_iam_var.get().strip()
        if explicit:
            target = Path(explicit)
        else:
            target = self._guess_top_assembly()
            if not target:
                self._log(
                    "  [fail] Could not auto-detect a top .iam. "
                    "Use the Top .iam Browse button to pick one explicitly.",
                    "fail",
                )
                return False

        self._log(f"  Top assembly: {target}", "info")
        self._log("  Launching Inventor (may take a moment on first start)…", "info")

        try:
            from inventor_automation import (
                InventorUnavailableError,
                rebuild_and_save_assembly,
            )
        except ImportError as exc:
            self._log(f"  [fail] inventor_automation unavailable: {exc}", "fail")
            return False

        try:
            path_used = rebuild_and_save_assembly(
                target, visible=self.visible_inventor_var.get()
            )
        except InventorUnavailableError as exc:
            self._log(f"  [fail] {exc}", "fail")
            self._log(
                "  Open the assembly manually in Inventor, rebuild it, "
                "and check it back in to Vault before running step 6.",
                "warn",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            self._log(f"  [fail] Inventor rebuild failed: {exc}", "fail")
            return False

        self._log(f"  [ok] Rebuilt and saved: {path_used}", "pass")
        self._log(
            "  IMPORTANT: use the Vault add-in inside Inventor to Check In the "
            "updated assembly before running step 6.",
            "warn",
        )
        return True

    def _guess_top_assembly(self) -> Optional[Path]:
        iams = [d for d in self.downloads
                if d.get("ok") and d.get("name", "").lower().endswith(".iam")]
        if not iams:
            return None
        pn = self.pn_var.get().strip().lower()
        for d in iams:
            if pn and pn in d["name"].lower():
                return Path(d["path"])
        best = max(iams, key=lambda d: d.get("size", 0))
        return Path(best["path"])

    # ----- Step 6: release CAD ---------------------------------------------

    def _run_step_6_release_cad(self) -> bool:
        if self._compliance_blocked():
            return False
        if not self._ensure_signed_in():
            return False

        target_state = self.target_state_var.get().strip() or "Released"
        explicit_state_id = self._target_state_id_or_none()

        async def gather_masters() -> list[int]:
            files: dict[str, dict[str, Any]] = {}
            top_iv = (self.compliance.get("info") or {}).get("item_version_id") or ""
            for fv in await _associated_file_versions(self.api, self.vault_id, top_iv):
                fid = str(fv.get("id") or "")
                if fid:
                    files[fid] = fv
            for child in self.compliance.get("children") or []:
                cv = child.get("item_version_id") or ""
                if not cv:
                    continue
                for fv in await _associated_file_versions(self.api, self.vault_id, cv):
                    fid = str(fv.get("id") or "")
                    if fid and fid not in files:
                        files[fid] = fv
            return _collect_file_master_ids(files)

        try:
            masters = self._run_async(gather_masters())
        except Exception as exc:  # noqa: BLE001
            self._log(f"  [fail] could not enumerate file masters: {exc}", "fail")
            return False

        if not masters:
            self._log("  [warn] No CAD files found to release.", "warn")
            return True

        # Resolve the state id. We need it to live in the file lifecycle —
        # not the item lifecycle — so look up the first file's lifecycle
        # def and pick the matching state from there.
        try:
            from vault_sdk import (
                VaultSDKError, lookup_file, find_state_id_for_file,
                update_file_lifecycle_states,
            )
        except ImportError as exc:
            self._log(f"  [fail] vault_sdk unavailable: {exc}", "fail")
            return False

        state_id = explicit_state_id
        if state_id is None:
            try:
                first = lookup_file(masters[0])
            except VaultSDKError as exc:
                self._log(f"  [fail] file lookup failed: {exc}", "fail")
                return False
            if not first.get("found"):
                self._log(f"  [fail] could not look up file masterId={masters[0]}", "fail")
                return False
            state_id = find_state_id_for_file(first, target_state)
        if state_id is None:
            self._log(
                f"  [fail] Could not resolve lifecycle state id for "
                f"{target_state!r} in the file's lifecycle. "
                "Set 'State ID (override)' explicitly.",
                "fail",
            )
            return False

        self._log(
            f"  Promoting {len(masters)} file(s) to '{target_state}' "
            f"(state_id={state_id}) via Vault SDK…",
            "info",
        )
        try:
            result = update_file_lifecycle_states(
                masters, state_id,
                comment=f"Released via gui.release_workflow to {target_state}",
            )
        except VaultSDKError as exc:
            self._log(f"  [fail] {exc}", "fail")
            return False
        self._log(f"  [ok] Released {result.get('updated', len(masters))} file(s).", "pass")
        return True

    # ----- Step 7: release items -------------------------------------------

    def _run_step_7_release_items(self) -> bool:
        if self._compliance_blocked():
            return False

        target_state = self.target_state_var.get().strip() or "Released"
        explicit_state_id = self._target_state_id_or_none()

        # SDK lifecycle change works on master IDs, not item-version IDs.
        # Pull each child's masterId from the compliance result, plus the
        # top item's masterId from its info.
        master_ids: list[int] = []
        seen: set[int] = set()

        def add(v: Any) -> None:
            if v is None:
                return
            try:
                mid = int(v)
            except (TypeError, ValueError):
                return
            if mid not in seen:
                seen.add(mid)
                master_ids.append(mid)

        # Top-level item master id — pull from info.master.id (the master record)
        top_master = ((self.compliance.get("info") or {}).get("master") or {}).get("id")
        add(top_master)
        for child in self.compliance.get("children") or []:
            child_master = ((child.get("properties") or {}).get("item") or {}).get("id")
            add(child_master)

        if not master_ids:
            self._log("  [warn] No item master IDs collected.", "warn")
            return True

        try:
            from vault_sdk import (
                VaultSDKError, lookup_item, find_state_id_for_item,
                update_item_lifecycle_states,
            )
        except ImportError as exc:
            self._log(f"  [fail] vault_sdk unavailable: {exc}", "fail")
            return False

        state_id = explicit_state_id
        if state_id is None:
            # Look up the top item via SDK to get its lifecycle def, then
            # find Released within THAT def (not Basic Release Process etc.)
            top_pn = (self.compliance.get("info") or {}).get("properties", {}).get("Number")
            if not top_pn:
                self._log("  [fail] could not determine top item part number", "fail")
                return False
            try:
                top_item = lookup_item(top_pn)
            except VaultSDKError as exc:
                self._log(f"  [fail] item lookup failed: {exc}", "fail")
                return False
            if not top_item.get("found"):
                self._log(f"  [fail] item {top_pn!r} not found via SDK", "fail")
                return False
            state_id = find_state_id_for_item(top_item, target_state)
        if state_id is None:
            self._log(
                f"  [fail] Could not resolve lifecycle state id for "
                f"{target_state!r} in the item's lifecycle. "
                "Set 'State ID (override)' explicitly.",
                "fail",
            )
            return False

        self._log(
            f"  Promoting {len(master_ids)} item(s) to '{target_state}' "
            f"(state_id={state_id}) via Vault SDK…",
            "info",
        )
        try:
            result = update_item_lifecycle_states(
                master_ids, state_id,
                comment=f"Released via gui.release_workflow to {target_state}",
            )
        except VaultSDKError as exc:
            self._log(f"  [fail] {exc}", "fail")
            return False
        self._log(f"  [ok] Released {result.get('updated', len(master_ids))} item(s).", "pass")
        return True

    # ----- Misc helpers ----------------------------------------------------

    def _compliance_blocked(self) -> bool:
        """Return True (and log a clear message) if the compliance gate is
        engaged — i.e. compliance ran, has failures, and force isn't set."""
        if not self.compliance:
            self._log("  [fail] Run step 1 (compliance check) first.", "fail")
            return True
        top_failed = bool((self.compliance.get("report") or {}).get("failed", 0))
        kids_failed = any(
            c.get("error") is not None or
            (c.get("report") or {}).get("failed", 0) > 0
            for c in (self.compliance.get("children") or [])
        )
        if (top_failed or kids_failed) and not self.force_var.get():
            self._log(
                "  [blocked] Compliance gate engaged — fix failing properties or "
                "tick 'Force past compliance gate' to continue.",
                "fail",
            )
            return True
        return False

    def _target_state_id_or_none(self) -> Optional[int]:
        raw = self.target_state_id_var.get().strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    # ----- Vault search dialog ---------------------------------------------

    def _open_search_dialog(self) -> None:
        if self.busy:
            messagebox.showwarning(
                "Busy", "Wait for the current step to finish before searching.")
            return
        SearchDialog(self)

    def set_part_number(self, number: str) -> None:
        """Public hook the SearchDialog calls when the user picks a result."""
        self.pn_var.set(number)
        # Reset workflow state when the part number changes — stale compliance
        # / downloads from the previous part number would silently leak in.
        self._on_reset()
        self.pn_var.set(number)
        self.status_var.set(f"Part number set to {number}. Click 'Run next step' to begin.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Vault search dialog
# ---------------------------------------------------------------------------

class SearchDialog:
    """Modal Vault item search — query box, results table, double-click to pick.

    Reuses the parent GUI's authenticated ``VaultRestAPI`` client (signs in
    lazily via ``parent._ensure_signed_in`` if no session yet). All search
    work runs on a worker thread so the dialog never freezes; results are
    posted back to the UI via ``parent.root.after``.
    """

    COLUMNS = [
        ("number",      "Number",      120),
        ("title",       "Title",       180),
        ("description", "Description", 320),
        ("revision",    "Rev",          50),
        ("state",       "State",       110),
        ("category",    "Category",    180),
    ]

    def __init__(self, parent_gui: "ReleaseWorkflowGUI") -> None:
        self.parent = parent_gui
        self.results: list[dict[str, Any]] = []
        self.busy = False
        # Cross-thread queue — Tk widget calls must happen on the Tk thread,
        # so the worker posts results here and a Tk-thread drain consumes them.
        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._build_window()
        # Pre-fill with whatever's already in the main Part Number entry so
        # the typical "search for what I have" flow is one click + Enter.
        existing = parent_gui.pn_var.get().strip()
        if existing:
            self.query_var.set(existing)
        self.query_entry.focus_set()
        self.parent.root.after(100, self._drain_queue)

    # ----- Window construction ---------------------------------------------

    def _build_window(self) -> None:
        self.win = tk.Toplevel(self.parent.root)
        self.win.title("Search Vault")
        self.win.geometry("880x520")
        self.win.minsize(640, 360)
        self.win.configure(bg=LIGHT_GRAY)
        self.win.transient(self.parent.root)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # Header strip — same dark blue / mid blue treatment as the main window
        hdr = tk.Frame(self.win, bg=DARK_BLUE, height=44)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="  Search Vault",
            bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 12, "bold"),
            anchor="w", padx=12,
        ).pack(side="left", fill="y")
        tk.Frame(self.win, bg=MID_BLUE, height=2).pack(fill="x")

        # Query bar
        bar = tk.Frame(self.win, bg=LIGHT_GRAY, padx=14, pady=12)
        bar.pack(fill="x")
        tk.Label(
            bar, text="Query",
            bg=LIGHT_GRAY, fg=DARK_BLUE,
            font=("Arial", 9, "bold"),
        ).pack(side="left", padx=(0, 6))

        self.query_var = tk.StringVar()
        self.query_entry = tk.Entry(
            bar, textvariable=self.query_var,
            font=("Arial", 10),
            bg=WHITE, relief="solid", bd=1,
            highlightthickness=1,
            highlightbackground=GRAY_BDR,
            highlightcolor=MID_BLUE,
        )
        self.query_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.query_entry.bind("<Return>", lambda _e: self._do_search())

        self.search_btn = self.parent._brand_button(
            bar, "Search", self._do_search, primary=True,
        )
        self.search_btn.pack(side="left", padx=(0, 6))

        self.limit_var = tk.StringVar(value="50")
        tk.Label(
            bar, text="Limit",
            bg=LIGHT_GRAY, fg=DARK_BLUE, font=("Arial", 9),
        ).pack(side="left", padx=(8, 4))
        tk.Entry(
            bar, textvariable=self.limit_var, width=5,
            font=("Arial", 10), bg=WHITE,
            relief="solid", bd=1,
            highlightthickness=1, highlightbackground=GRAY_BDR,
            highlightcolor=MID_BLUE,
        ).pack(side="left")

        # Results table — ttk.Treeview with Simplifyber-styled headers
        body = tk.Frame(self.win, bg=LIGHT_GRAY, padx=14, pady=0)
        body.pack(fill="both", expand=True, pady=(0, 10))

        style = ttk.Style(self.win)
        # 'clam' is the only built-in ttk theme that respects every colour
        # option on Treeview headings — the native Windows theme ignores them.
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Vault.Treeview",
            background=WHITE, fieldbackground=WHITE,
            foreground="#222222",
            rowheight=24, borderwidth=0,
            font=("Arial", 10),
        )
        style.configure(
            "Vault.Treeview.Heading",
            background=DARK_BLUE, foreground=WHITE,
            font=("Arial", 10, "bold"),
            relief="flat",
        )
        style.map(
            "Vault.Treeview",
            background=[("selected", MID_BLUE)],
            foreground=[("selected", WHITE)],
        )
        style.map(
            "Vault.Treeview.Heading",
            background=[("active", MID_BLUE)],
        )

        col_ids = [c[0] for c in self.COLUMNS]
        self.tree = ttk.Treeview(
            body, columns=col_ids, show="headings",
            style="Vault.Treeview", selectmode="browse",
        )
        for cid, label_text, width in self.COLUMNS:
            self.tree.heading(cid, text=label_text)
            self.tree.column(cid, width=width, anchor="w", stretch=True)
        self.tree.tag_configure("alt", background=PALE_BLUE)

        ys = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda _e: self._on_use_selected())
        self.tree.bind("<Return>",   lambda _e: self._on_use_selected())

        # Action bar
        actions = tk.Frame(self.win, bg=LIGHT_GRAY, padx=14, pady=10)
        actions.pack(fill="x")
        self.use_btn = self.parent._brand_button(
            actions, "Use selected", self._on_use_selected, primary=True,
        )
        self.use_btn.pack(side="left", padx=(0, 8))
        self.parent._brand_button(
            actions, "Cancel", self._on_cancel, primary=False,
        ).pack(side="left")

        # Status bar
        self.status_var = tk.StringVar(
            value="Enter a query (part number, title, or keyword) and press Enter."
        )
        bar2 = tk.Frame(self.win, bg=PALE_BLUE,
                        highlightthickness=1, highlightbackground=GRAY_BDR)
        bar2.pack(fill="x", side="bottom")
        tk.Label(
            bar2, textvariable=self.status_var,
            bg=PALE_BLUE, fg=DARK_BLUE,
            font=("Arial", 9), anchor="w",
            padx=12, pady=4,
        ).pack(fill="x", side="left", expand=True)

    # ----- Search execution -------------------------------------------------

    def _do_search(self) -> None:
        if self.busy:
            return
        query = self.query_var.get().strip()
        if not query:
            messagebox.showwarning(
                "Missing query", "Type a part number or keyword to search for.",
                parent=self.win,
            )
            return
        try:
            limit = max(1, int(self.limit_var.get().strip() or "50"))
        except ValueError:
            limit = 50

        self.busy = True
        self.search_btn.configure(state="disabled")
        self.status_var.set(f"Searching for {query!r} …")
        self._clear_results()

        def worker() -> None:
            try:
                if not self.parent._ensure_signed_in():
                    self._post_done(error="Vault sign-in failed — see main window log.")
                    return
                resp = asyncio.run(self.parent.api.search_items(
                    vault_id=self.parent.vault_id,
                    query=query,
                    limit=limit,
                ))
            except Exception as exc:  # noqa: BLE001
                self._post_done(error=f"{type(exc).__name__}: {exc}")
                return
            if resp.get("error"):
                self._post_done(error=str(resp.get("data")))
                return
            rows = self._extract_rows(resp.get("data"))
            self._post_done(rows=rows, query=query)

        threading.Thread(target=worker, daemon=True).start()

    def _post_done(self, *, rows: Optional[list[dict[str, Any]]] = None,
                   query: str = "", error: str = "") -> None:
        # Hop back onto the Tk thread before touching widgets — the drain
        # loop (running under root.after) calls _render_done from the right
        # thread. Calling root.after directly from a worker is unsafe.
        self.q.put(("done", (rows or [], query, error)))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "done":
                    rows, query, error = payload
                    self._render_done(rows, query, error)
        except queue.Empty:
            pass
        # Re-arm only while the dialog window is alive
        try:
            if self.win.winfo_exists():
                self.parent.root.after(100, self._drain_queue)
        except tk.TclError:
            pass

    def _render_done(self, rows: list[dict[str, Any]], query: str, error: str) -> None:
        self.busy = False
        self.search_btn.configure(state="normal")
        if error:
            self.status_var.set(f"Search failed: {error}")
            return
        self.results = rows
        self._populate_tree(rows)
        if rows:
            self.status_var.set(
                f"{len(rows)} result(s) for {query!r}. "
                "Double-click or 'Use selected' to pick one."
            )
            # Pre-select the first row for keyboard-only flow
            first = self.tree.get_children()
            if first:
                self.tree.selection_set(first[0])
                self.tree.focus(first[0])
        else:
            self.status_var.set(f"No results for {query!r}.")

    # ----- Result extraction & rendering -----------------------------------

    @staticmethod
    def _extract_rows(data: Any) -> list[dict[str, Any]]:
        """Pull a list of item records out of the search response."""
        items: list[dict[str, Any]] = []
        if data is None:
            return items
        if isinstance(data, list):
            items = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            for key in ("results", "items", "itemVersions",
                        "data", "value", "records"):
                inner = data.get(key)
                if isinstance(inner, list):
                    items = [r for r in inner if isinstance(r, dict)]
                    break
            else:
                if data.get("id") or data.get("masterId"):
                    items = [data]

        rows: list[dict[str, Any]] = []
        for it in items:
            rows.append(_summarise_item_for_search(it))
        return rows

    def _populate_tree(self, rows: list[dict[str, Any]]) -> None:
        for i, row in enumerate(rows):
            tags = ("alt",) if i % 2 == 1 else ()
            values = [row.get(cid, "") for cid, *_ in self.COLUMNS]
            self.tree.insert("", "end", iid=str(i), values=values, tags=tags)

    def _clear_results(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.results = []

    # ----- Use / cancel ----------------------------------------------------

    def _on_use_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(
                "No selection", "Pick a row first.", parent=self.win)
            return
        idx = int(sel[0])
        row = self.results[idx]
        number = str(row.get("number") or "").strip()
        if not number:
            messagebox.showwarning(
                "No part number",
                "Selected row has no Number — pick a different result.",
                parent=self.win,
            )
            return
        self.parent.set_part_number(number)
        self._close()

    def _on_cancel(self) -> None:
        self._close()

    def _close(self) -> None:
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()


def _summarise_item_for_search(record: dict[str, Any]) -> dict[str, str]:
    """Pick out the small set of fields the search dialog displays.

    Vault returns item records in a couple of different shapes (flat at the
    root vs. nested under ``itemVersion``); this normalises both into the
    short string dict the Treeview expects.
    """
    def pick(*keys, default: str = "") -> str:
        for k in keys:
            v = record.get(k)
            if v not in (None, ""):
                return str(v)
        # Fall back to the embedded latest version if present
        for vkey in ("itemVersion", "latestItemVersion", "latestVersion"):
            inner = record.get(vkey)
            if isinstance(inner, dict):
                for k in keys:
                    v = inner.get(k)
                    if v not in (None, ""):
                        return str(v)
        return default

    return {
        "number":      pick("number", "Number", "itemNumber", "partNumber"),
        "title":       pick("title", "Title (Item,CO)", "name"),
        "description": pick("description", "Description (Item,CO)", "desc"),
        "revision":    pick("revision", "Revision", "revisionNumber"),
        "state":       pick("state", "State", "lifecycleState"),
        "category":    pick("category", "Category Name",
                            "categoryName", "itemCategory"),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def launch_gui(
    prefill_part_number: str = "",
    *,
    api: Any = None,
    vault_id: str = "",
    access_token: str = "",
    user_id: str = "",
    cfg: Optional[dict[str, Any]] = None,
    parent: Optional[tk.Misc] = None,
) -> tk.Misc:
    """Open the wizard. Pass an authenticated Vault session to skip sign-in.

    The ``api`` / ``vault_id`` / ``access_token`` / ``user_id`` / ``cfg``
    arguments are the integration seam used by ``app.py --gui``: the main
    entry point signs in once with credentials from ``config.json`` and
    hands the session to the GUI so every step reuses it. ``user_id`` is
    needed so the SOAP client can populate its ``SecurityHeader`` (the
    REST access token doesn't embed it).

    Pass ``parent`` to open the wizard as a ``Toplevel`` child of an existing
    Tk window (e.g. the launcher dashboard); the function then *returns
    immediately* without entering its own ``mainloop``. When ``parent`` is
    None this behaves the same as before — creates a root ``Tk`` and runs
    its mainloop, blocking until the user closes the window.
    """
    if parent is None:
        root = tk.Tk()
        ReleaseWorkflowGUI(
            root,
            prefill_part_number=prefill_part_number,
            api=api, vault_id=vault_id,
            access_token=access_token, user_id=user_id, cfg=cfg,
        )
        root.mainloop()
        return root

    win = tk.Toplevel(parent)
    ReleaseWorkflowGUI(
        win,
        prefill_part_number=prefill_part_number,
        api=api, vault_id=vault_id,
        access_token=access_token, user_id=user_id, cfg=cfg,
    )
    return win


if __name__ == "__main__":
    pn = sys.argv[1] if len(sys.argv) > 1 else ""
    launch_gui(prefill_part_number=pn)
