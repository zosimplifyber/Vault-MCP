"""
Tkinter GUI for the Vault release workflow.

A single-window wizard over the six file- and BOM-driven step engines in
``release_steps.py``:

    [ Top File (steps 1-3) / target state · BOM Export (steps 4-6) ]
    [ Step list with live status: Pending / Running / Review / OK / … ]
    [ Output log (per-step engine lines) ]
    [ Action buttons: Run next step | Skip step | Run all remaining | Reset ]

Two inputs, two halves. Steps 1-3 (Property Check, Sync Properties, Release
Files) work from a top file name; steps 4-6 (Purchased Parts List, Publish
Deliverables, Purchasing Sheet) work from an exported BOM. Changing one input
invalidates only the steps that read it — see ``_invalidate``.

Each step runs on a worker thread so the UI never freezes. A step that would
write to Vault or SharePoint returns a staged ``pending_apply`` instead of
writing: the wizard shows the preview, parks the step in REVIEW, and performs
the write only when the user clicks Apply.

Launch:
    python scripts/release_workflow.py --gui
    python scripts/release_workflow.py CD-001659.iam --gui   # pre-fill top file
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

# The item-based SearchDialog lives in gui/search_dialog.py and belongs to
# gui.mfg_package now. This wizard works from file names, so it does not import
# it — FileSearchDialog at the bottom of this module is its replacement. Do not
# add a re-export here, and do not repoint search_dialog.py at search_files.

# Every step runs through release_steps — the headless engines. The wizard
# holds no Vault logic of its own: check_item_properties and the item-based
# release_workflow script are deliberately NOT imported here any more.
import release_steps  # noqa: E402

# self.compliance is now a *file* property-check result, so the report
# formatter has to be the file one. Imported at module scope on purpose: a
# lazy import inside the save handler would only surface as a traceback the
# first time somebody clicks Save report.
from check_file_properties import format_markdown_report  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------

STEPS = [
    ("1", "Property Check",
     "Run the file property rules over the assembly and its CAD BOM"),
    ("2", "Sync Properties",
     "Submit Autodesk.Vault.SyncProperties for every file"),
    ("3", "Release Files",
     "Promote every file to the target lifecycle state"),
    ("4", "BOM → Purchased Parts List",
     "Add parts missing from the Engineering Purchased Parts list"),
    ("5", "BOM → Publish Deliverables",
     "Queue PDF and STEP publish jobs for every Make part"),
    ("6", "BOM → Purchasing Sheet",
     "Build the branded purchasing workbook"),
]

# Steps 1-3 work from the top file name; 4-6 work from the BOM export. Changing
# one input must only invalidate the steps that read it.
VAULT_STEPS = ("1", "2", "3")
BOM_STEPS = ("4", "5", "6")

STATUS_PENDING  = "PENDING"
STATUS_RUNNING  = "RUNNING"
STATUS_REVIEW   = "REVIEW"
STATUS_OK       = "OK"
STATUS_SKIPPED  = "SKIPPED"
STATUS_FAILED   = "FAILED"
STATUS_BLOCKED  = "BLOCKED"

STATUS_TAGS = {
    STATUS_PENDING: (DARK_GRAY,    PALE_BLUE,   "·"),
    STATUS_RUNNING: (WHITE,        MID_BLUE,    "▶"),
    # Amber, not blue: a step waiting on a human must not read as one still
    # talking to Vault.
    STATUS_REVIEW:  (WHITE,        WARN_AMBER,  "?"),
    STATUS_OK:      (DARK_BLUE,    OLIVE_GREEN, "✓"),
    STATUS_SKIPPED: (DARK_GRAY,    LIGHT_GRAY,  "—"),
    STATUS_FAILED:  (WHITE,        RUST_ORANGE, "✗"),
    STATUS_BLOCKED: (WHITE,        RUST_ORANGE, "■"),
}

# Every tag release_steps emits, plus the wizard's own chrome. Built as a dict
# so a test can assert it covers release_steps.ALL_TAGS — Tk silently renders
# unstyled text for an unconfigured tag, so a dropped entry has no other
# symptom.
TAG_STYLES: dict[str, dict[str, Any]] = {
    "h1":   {"foreground": DARK_BLUE, "font": ("Arial", 12, "bold"),
             "spacing1": 8, "spacing3": 4},
    "h2":   {"foreground": DARK_BLUE, "font": ("Arial", 10, "bold"),
             "spacing1": 6, "spacing3": 2},
    "dim":  {"foreground": DARK_GRAY},
    "pass": {"foreground": "#1F6B2E", "font": ("Consolas", 10, "bold")},
    "fail": {"foreground": RUST_ORANGE, "font": ("Consolas", 10, "bold")},
    "warn": {"foreground": WARN_AMBER},
    "info": {"foreground": MID_BLUE},
    "step_banner": {"foreground": WHITE, "background": DARK_BLUE,
                    "font": ("Arial", 10, "bold"),
                    "spacing1": 10, "spacing3": 4},
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
        # A step that staged a write parks it here until the user clicks Apply.
        self.pending_apply: Optional[Callable[[], Any]] = None
        self.pending_step: Optional[str] = None
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

        # ``prefill_part_number`` is the pre-rewrite entry point (app.py --gui,
        # scripts/release_workflow.py, the launcher). The wizard now keys off a
        # top *file* name, and a bare part number is not one — dropping it into
        # the Top File box would send step 1 looking for a file that cannot
        # exist. Accept it only when it actually names a file.
        if prefill_part_number and Path(prefill_part_number).suffix:
            self.top_file_var.set(prefill_part_number)

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

        # Row 0 — top file name (drives steps 1-3)
        label(inputs, "Top File").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.top_file_var = tk.StringVar()
        # Wrap entry + Search button in a sub-frame so the surrounding column
        # grid (target state / state id) doesn't have to shift.
        tf_frame = tk.Frame(inputs, bg=LIGHT_GRAY)
        tf_frame.grid(row=0, column=1, sticky="ew", padx=(0, 14))
        entry(tf_frame, self.top_file_var, width=18).pack(
            side="left", fill="x", expand=True
        )
        self._brand_button(
            tf_frame, "Search…", self._open_search_dialog, primary=False,
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

        # Row 1 — BOM export (drives steps 4-6)
        label(inputs, "BOM Export").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.bom_path_var = tk.StringVar()
        entry(inputs, self.bom_path_var, width=10).grid(
            row=1, column=1, columnspan=5, sticky="ew",
            padx=(0, 6), pady=(10, 0),
        )
        self._brand_button(
            inputs, "Browse…", self._browse_bom, primary=False,
        ).grid(row=1, column=6, sticky="w", pady=(10, 0))

        # Row 2 — toggles
        toggles = tk.Frame(inputs, bg=LIGHT_GRAY)
        toggles.grid(row=2, column=0, columnspan=8, sticky="w", pady=(10, 0))

        self.force_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            toggles, text="Force past compliance gate",
            variable=self.force_var,
            bg=LIGHT_GRAY, fg=DARK_BLUE,
            activebackground=LIGHT_GRAY, activeforeground=DARK_BLUE,
            selectcolor=WHITE, font=("Arial", 9),
        ).pack(side="left", padx=(0, 16))

        self.buy_only_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            toggles, text="Buy/Other rows only (list sync)",
            variable=self.buy_only_var,
            bg=LIGHT_GRAY, fg=DARK_BLUE,
            activebackground=LIGHT_GRAY, activeforeground=DARK_BLUE,
            selectcolor=WHITE, font=("Arial", 9),
        ).pack(side="left", padx=(0, 16))

        # A stale step 1 result must never feed steps 2-3 after the file name
        # changes, and a stale scan must never feed a submit after the BOM
        # changes. Mirrors publish_bom's _invalidate_scan.
        self.top_file_var.trace_add(
            "write", lambda *_a: self._invalidate(VAULT_STEPS))
        self.bom_path_var.trace_add(
            "write", lambda *_a: self._invalidate(BOM_STEPS))

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

        # Light-theme colour tags. Driven from the module-level TAG_STYLES so
        # a test can check it against release_steps.ALL_TAGS — Tk accepts an
        # unconfigured tag name silently and just renders plain text.
        for tag, style in TAG_STYLES.items():
            self.text.tag_configure(tag, **style)

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
            value="Ready. Enter a top file name or browse to a BOM export, "
                  "then click 'Run next step'."
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

    def _browse_bom(self) -> None:
        f = filedialog.askopenfilename(
            title="Pick an exported BOM",
            filetypes=[("BOM export", "*.xlsx *.xls *.csv *.txt"),
                       ("All files", "*.*")],
        )
        if f:
            self.bom_path_var.set(f)

    # ----- Input invalidation ----------------------------------------------

    def _invalidate(self, nums: tuple[str, ...]) -> None:
        """Reset the given steps to PENDING and drop anything they staged."""
        if self.busy:
            return
        for num in nums:
            if self.statuses.get(num) != STATUS_PENDING:
                self._update_step_label(num, STATUS_PENDING)
        if self.pending_step in nums:
            self._clear_pending()
        if "1" in nums:
            self.compliance = None

    def _clear_pending(self) -> None:
        """Drop a staged write without performing it, and restore the button."""
        self.pending_apply = None
        self.pending_step = None
        self.btn_run.configure(text="  Run next step  ")

    # ----- Action-bar handlers ---------------------------------------------

    def _on_run_next(self) -> None:
        if self.busy:
            return
        if self.pending_apply is not None:
            # The button reads "Apply" right now — this click is the human
            # consent the staged write has been waiting for.
            self._run_pending_apply()
            return
        nxt = self._next_pending_step()
        if not nxt:
            messagebox.showinfo("Workflow complete", "No more pending steps.")
            return
        self._run_step(nxt, run_all_after=False)

    def _on_skip(self) -> None:
        if self.busy:
            return
        if self.pending_apply is not None:
            # Drop the staged closure on the floor. Nothing was written.
            num = self.pending_step
            self._clear_pending()
            self._update_step_label(num, STATUS_SKIPPED)
            self._write(f"\n[skipped] Step {num} — nothing was written.", "dim")
            self.status_var.set(f"Step {num} skipped.")
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
        if self.pending_apply is not None:
            # Not a matter of taste: _next_pending_step() skips over a REVIEW
            # step, so running on from here would strand it at REVIEW forever
            # and overwrite self.pending_apply with the next step's closure —
            # silently discarding a write the user had been shown. And
            # applying it on their behalf is exactly the unattended write the
            # gate exists to prevent. So: neither. Ask.
            messagebox.showwarning(
                "Step awaiting review",
                f"Step {self.pending_step} has a change staged but not written. "
                "Click Apply to write it, or Skip to discard it, before "
                "running the rest.")
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
            initialfile=(f"release_readiness_"
                         f"{self.top_file_var.get().strip() or 'report'}.md"),
        )
        if not f:
            return
        Path(f).write_text(format_markdown_report(self.compliance), encoding="utf-8")
        self.status_var.set(f"Report saved: {f}")

    def _on_reset(self) -> None:
        if self.busy:
            messagebox.showwarning("Busy", "Wait for the current step to finish.")
            return
        # Reset the *workflow*, not the connection. The wizard has no sign-in
        # of its own any more — the launcher (or app.py --gui) hands it a
        # session — so clearing self.api here would leave the window unable to
        # run a single Vault step and no way to get back. set_top_file() calls
        # this on every pick from the search dialog, which made that a
        # one-click brick.
        self.compliance = None
        self._clear_pending()
        for num in self.statuses:
            self._update_step_label(num, STATUS_PENDING)
        self._clear_output()
        self.btn_save_report.configure(state="disabled")
        self.status_var.set("Reset. Ready.")

    # ----- Step dispatch ----------------------------------------------------

    def _missing_input_for(self, num: str) -> Optional[str]:
        """Return why this step cannot run yet, or None when it can.

        The two groups are independent: a missing BOM never blocks steps 1-3
        and a missing top file never blocks steps 4-6.
        """
        if num in VAULT_STEPS and not self.top_file_var.get().strip():
            return "Enter a top file name (e.g. CD-001659.iam) first."
        if num in BOM_STEPS and not self.bom_path_var.get().strip():
            return "Browse to an exported BOM first."
        return None

    def _run_step(self, num: str, *, run_all_after: bool) -> None:
        missing = self._missing_input_for(num)
        if missing:
            messagebox.showwarning("Missing input", missing)
            return
        if num in VAULT_STEPS and not self._ensure_signed_in_ui():
            return

        name = next((n for k, n, *_ in STEPS if k == num), "?")
        self._banner(num, name)
        self._update_step_label(num, STATUS_RUNNING)
        self._set_busy(True)
        self.status_var.set(f"Step {num} ({name}) running…")

        runner = self._step_runner(num)

        def thread_main() -> None:
            try:
                outcome = runner()
            except Exception as exc:  # noqa: BLE001 — surface any error to UI
                outcome = release_steps.StepOutcome(
                    ok=False, summary=f"{type(exc).__name__}: {exc}",
                    lines=[(f"  [error] {type(exc).__name__}: {exc}", "fail")])
            self.q.put(WorkerSignal("step_done", (num, outcome, run_all_after)))

        self.worker_thread = threading.Thread(target=thread_main, daemon=True)
        self.worker_thread.start()

    def _run_pending_apply(self) -> None:
        """Perform the write a step staged. Only reachable from the Apply button.

        Nothing else in this class may call this — it is the single point at
        which a ``pending_apply`` closure is invoked.
        """
        num, apply_fn = self.pending_step, self.pending_apply
        if not num or not apply_fn:
            return
        self._clear_pending()
        self._update_step_label(num, STATUS_RUNNING)
        self._set_busy(True)
        self.status_var.set(f"Step {num} applying…")

        def thread_main() -> None:
            try:
                outcome = apply_fn()
            except Exception as exc:  # noqa: BLE001
                outcome = release_steps.StepOutcome(
                    ok=False, summary=f"{type(exc).__name__}: {exc}",
                    lines=[(f"  [error] {type(exc).__name__}: {exc}", "fail")])
            if not isinstance(outcome, release_steps.StepOutcome):
                # A closure that returned nothing has told us nothing about
                # what it wrote. Report that, don't assume it went fine — and
                # don't let it blow up the queue drain on the Tk thread.
                outcome = release_steps.StepOutcome(
                    ok=False,
                    summary=(f"Step {num} apply returned {outcome!r}, not a "
                             "StepOutcome — what it wrote is unknown."),
                    lines=[(f"  [error] apply returned {outcome!r}; the write "
                            "may be partial — verify in Vault.", "fail")])
            self.q.put(WorkerSignal("step_done", (num, outcome, False)))

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
            num, outcome, run_all_after = sig.payload
            for line, tag in outcome.lines:
                self._write(line, tag)

            if num == "1":
                # Assign unconditionally, including None. Steps 2 and 3 read
                # their file list from here; a step 1 that produced nothing
                # must leave them with nothing, not with the previous run's
                # clean result.
                self.compliance = outcome.result
                self.btn_save_report.configure(
                    state="normal" if outcome.result else "disabled")

            if outcome.needs_review:
                # Staged, not written. Park it and wait for a human.
                # MUST be tested before `ok` — a preview may report problems
                # (ok=False) and still legitimately offer Apply; step 5
                # previewing drawing gaps is exactly that case. This is the
                # line the "nothing reaches Vault unattended" guarantee rests
                # on; do not replace it with a raw `pending_apply is not None`.
                self.pending_apply = outcome.pending_apply
                self.pending_step = num
                self._update_step_label(num, STATUS_REVIEW)
                self.btn_run.configure(text="  Apply  ")
                self._set_busy(False)
                self.status_var.set(outcome.summary)
                return   # never auto-continue a Run all through a write

            self._update_step_label(
                num, STATUS_OK if outcome.ok else STATUS_FAILED)
            self._set_busy(False)
            self.status_var.set(outcome.summary)
            if not outcome.ok:
                return
            if run_all_after:
                nxt = self._next_pending_step()
                if nxt:
                    self._run_step(nxt, run_all_after=True)
                else:
                    self.status_var.set("All steps complete.")

    # ----- Per-step runners (executed on worker thread) --------------------

    def _step_runner(self, num: str) -> Callable[[], Any]:
        """Return a zero-arg callable that runs this step on the worker thread.

        Every input is read *here*, on the Tk thread, and captured by the
        closure — the worker must never touch a Tk variable.
        """
        top_file = self.top_file_var.get().strip()
        bom_path = self.bom_path_var.get().strip()
        target_state = self.target_state_var.get().strip() or "Released"
        state_id = self._target_state_id_or_none()
        buy_only = self.buy_only_var.get()
        # Read on the Tk thread, not inside gated(): a tk.BooleanVar.get()
        # from the worker raises "main thread is not in main loop", and the
        # wrapper would turn the compliance gate into a plain step failure
        # rather than the block it is.
        force = self.force_var.get()

        def gated(fn: Callable[[], Any]) -> Callable[[], Any]:
            """Steps 2 and 3 only: refuse when Property Check is not clean.

            ``property_check_blocked`` is a module-level function in
            release_steps, not a method here — the wizard holds no copy of
            the gate logic.
            """
            def run() -> Any:
                reason = release_steps.property_check_blocked(
                    self.compliance, force=force)
                if reason:
                    return release_steps.StepOutcome(
                        ok=False, summary=reason,
                        lines=[(f"  [blocked] {reason}", "fail")])
                return fn()
            return run

        return {
            "1": lambda: release_steps.run_property_check(
                top_file, api=self.api, vault_id=self.vault_id),
            "2": gated(lambda: release_steps.run_sync_properties(
                self.api, self.vault_id, self.compliance)),
            "3": gated(lambda: release_steps.run_release_files(
                self.api, self.vault_id, self.compliance,
                target_state=target_state, state_id=state_id)),
            "4": lambda: release_steps.run_purchased_parts_list(
                bom_path, buy_only=buy_only),
            "5": lambda: release_steps.run_publish_deliverables(
                self.api, self.vault_id, bom_path,
                top_assembly=Path(bom_path).stem if bom_path else ""),
            "6": lambda: release_steps.run_purchasing_sheet(
                bom_path, Path(bom_path).stem if bom_path else "BOM"),
        }[num]

    def _log(self, line: str, tag: Optional[str] = None) -> None:
        self.q.put(WorkerSignal("log", (line, tag)))

    def _set_status(self, msg: str) -> None:
        self.q.put(WorkerSignal("status", msg))

    # ----- Vault session ----------------------------------------------------

    def _ensure_signed_in(self) -> bool:
        """Confirm a live Vault session. Called from worker threads.

        The wizard no longer signs itself in: it is launched from the
        launcher dashboard or ``app.py --gui``, both of which hand it an
        already-authenticated session. Doing our own sign-in here would mean
        a second login and a second audit trail for the same user.
        """
        return bool(self.api is not None and self.vault_id)

    def _ensure_signed_in_ui(self) -> bool:
        """Tk-thread version — warns in a dialog instead of logging."""
        if self._ensure_signed_in():
            return True
        messagebox.showwarning(
            "Not signed in",
            "This step needs a Vault session. Open the workflow from the "
            "launcher, or click Reconnect there first.")
        return False

    # ----- Misc helpers ----------------------------------------------------

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
        """Open the wizard's own file search.

        Deliberately NOT the item ``SearchDialog`` in gui/search_dialog.py:
        that one belongs to MFG Order Package and hands back a *part number*,
        and this field wants a *file name*. Feeding one to the other would
        silently put a value in the box that step 1 can never resolve.
        """
        if self.busy:
            messagebox.showwarning(
                "Busy", "Wait for the current step to finish before searching.")
            return
        if not self._ensure_signed_in():
            messagebox.showwarning(
                "Not signed in",
                "Searching Vault needs a session. Open the workflow from the "
                "launcher, or click Reconnect there first.")
            return
        FileSearchDialog(self)

    def set_top_file(self, file_name: str) -> None:
        """Hook FileSearchDialog calls when the user picks a file.

        Reset first, then set the var. Reversing it means ``_on_reset``
        overwrites the status line with "Reset. Ready." and the user never
        sees which file they just picked — and if ``_on_reset`` ever grows to
        clear the inputs (it clears every other piece of run state already),
        reversing it would throw the pick away outright.
        """
        self._on_reset()
        self.top_file_var.set(file_name)
        self.status_var.set(
            f"Top file set to {file_name}. Click 'Run next step' to begin.")

    # NOTE: the old ``set_part_number`` hook is deliberately gone. It existed
    # only to serve the item ``SearchDialog``, which moved out to
    # gui/search_dialog.py and belongs to gui.mfg_package (which has its own
    # copy of the hook). Nothing in the repo calls this class's version.


# ---------------------------------------------------------------------------
# The wizard's file search dialog
# ---------------------------------------------------------------------------


def _summarise_file_for_search(record: dict[str, Any]) -> dict[str, str]:
    """Pick out the fields the search dialog shows for a file record.

    Vault returns file properties either flattened at the root or nested under
    ``properties``; this normalises both. Anything genuinely absent stays an
    empty string — the Treeview must show a blank cell rather than a
    plausible-looking value the record never carried.
    """
    props = record.get("properties")
    props = props if isinstance(props, dict) else {}

    def pick(*keys: str, default: str = "") -> str:
        for source in (record, props):
            for k in keys:
                v = source.get(k)
                if v not in (None, ""):
                    return str(v)
        return default

    return {
        "file_name": pick("name", "Name", "fileName"),
        "revision":  pick("revision", "Revision"),
        "state":     pick("state", "State", "lifecycleState"),
        "category":  pick("category", "Category Name", "categoryName"),
        "folder":    pick("folderPath", "Folder Path", "path"),
    }


class FileSearchDialog:
    """Modal Vault **file** search — query box, results table, pick a file.

    The wizard's counterpart to gui/search_dialog.py's item ``SearchDialog``.
    It is a separate class on purpose: that one belongs to MFG Order Package
    and genuinely wants ``api.search_items`` and a part number back. This one
    calls ``api.search_files`` and hands a *file name* to
    ``parent.set_top_file``. Do not merge them.

    The threading shape — worker thread, ``queue.Queue``, drain on the Tk
    thread via ``parent.root.after`` — is copied from ``SearchDialog``
    deliberately: no Tk call may happen off the main thread.
    """

    COLUMNS = [
        ("file_name",   "File Name",   220),
        ("revision",    "Rev",          50),
        ("state",       "State",       130),
        ("category",    "Category",    170),
        ("folder",      "Folder",      280),
    ]

    def __init__(self, parent_gui: Any) -> None:
        self.parent = parent_gui
        self.results: list[dict[str, Any]] = []
        self.busy = False
        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._build_window()
        # Pre-fill from the Top File box so "search for what I have" is one
        # click plus Enter.
        existing = parent_gui.top_file_var.get().strip()
        if existing:
            self.query_var.set(existing)
        self.query_entry.focus_set()
        self.parent.root.after(100, self._drain_queue)

    # ----- Window construction ---------------------------------------------

    def _build_window(self) -> None:
        self.win = tk.Toplevel(self.parent.root)
        self.win.title("Search Vault Files")
        self.win.geometry("920x520")
        self.win.minsize(640, 360)
        self.win.configure(bg=LIGHT_GRAY)
        self.win.transient(self.parent.root)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self._on_cancel)

        hdr = tk.Frame(self.win, bg=DARK_BLUE, height=44)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="  Search Vault Files",
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

        # Results table
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
            value="Enter a file name or keyword (e.g. CD-001659) and press Enter."
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
                "Missing query", "Type a file name or keyword to search for.",
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
                    self._post_done(
                        error="No Vault session — reconnect from the launcher.")
                    return
                resp = asyncio.run(self.parent.api.search_files(
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

    def _render_done(self, rows: list[dict[str, Any]], query: str,
                     error: str) -> None:
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
        """Pull a list of file records out of the search response."""
        files: list[dict[str, Any]] = []
        if data is None:
            return files
        if isinstance(data, list):
            files = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            for key in ("results", "files", "fileVersions",
                        "data", "value", "records"):
                inner = data.get(key)
                if isinstance(inner, list):
                    files = [r for r in inner if isinstance(r, dict)]
                    break
            else:
                if data.get("id") or data.get("masterId") or data.get("name"):
                    files = [data]

        return [_summarise_file_for_search(f) for f in files]

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
        name = str(row.get("file_name") or "").strip()
        if not name:
            messagebox.showwarning(
                "No file name",
                "Selected row has no file name — pick a different result.",
                parent=self.win,
            )
            return
        self.parent.set_top_file(name)
        self._close()

    def _on_cancel(self) -> None:
        self._close()

    def _close(self) -> None:
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()


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
