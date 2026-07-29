"""
Tkinter dialog for the BOM-driven deliverable publisher.

Opens as a Toplevel from the launcher with the live Vault session attached.
The user browses to an Inventor BOM export, confirms the top assembly, and
clicks Scan — the worker thread resolves every Make part to its model and
drawing in Vault and fills the results table. Submit then queues a PDF job per
drawing and a STEP job per model.

Fire and forget: jobs are queued, not polled. Watch them in Vault Explorer.
"""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
from typing import Any, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from gui.release_workflow import (  # noqa: E402
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY,
    WHITE, RUST_ORANGE, OLIVE_GREEN,
)

# The root-level engine module, not this file. Python resolves the unqualified
# import via PROJECT_ROOT on sys.path; this file is reached as
# ``gui.publish_bom``, so the names do not collide.
import publish_bom  # noqa: E402


class PublishBOMGUI:
    """Toplevel dialog. One per launch — closes when the user clicks Close."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        api: Any = None,
        vault_id: str = "",
        cfg: Optional[dict[str, Any]] = None,
    ) -> None:
        self.api = api
        self.vault_id = vault_id
        self.cfg = cfg or {}
        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.scan_result: list[publish_bom.ScanRow] = []
        self._busy = False

        self.win = tk.Toplevel(master)
        self.win.title("Publish BOM Deliverables")
        self.win.configure(bg=LIGHT_GRAY)
        self.win.geometry("880x640")
        self.win.minsize(760, 520)

        self.bom_path = tk.StringVar()
        self.top_assembly = tk.StringVar()
        self.summary_text = tk.StringVar(value="Pick a BOM and click Scan.")

        self._build_ui()
        self.win.after(100, self._drain_queue)

    # ----- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        header = tk.Frame(self.win, bg=DARK_BLUE, height=54)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="Publish BOM Deliverables", bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 13, "bold"),
        ).pack(side="left", padx=18)
        tk.Frame(self.win, bg=MID_BLUE, height=3).pack(fill="x")

        form = tk.Frame(self.win, bg=LIGHT_GRAY, padx=16, pady=12)
        form.pack(fill="x")

        tk.Label(form, text="BOM file", bg=LIGHT_GRAY, fg=DARK_BLUE,
                 font=("Arial", 10, "bold"), width=13, anchor="w").grid(
            row=0, column=0, sticky="w", pady=4)
        tk.Entry(form, textvariable=self.bom_path, width=58,
                 font=("Arial", 10)).grid(row=0, column=1, sticky="we", pady=4)
        tk.Button(form, text="Browse...", command=self._on_browse,
                  font=("Arial", 9)).grid(row=0, column=2, padx=(8, 0), pady=4)

        tk.Label(form, text="Top assembly", bg=LIGHT_GRAY, fg=DARK_BLUE,
                 font=("Arial", 10, "bold"), width=13, anchor="w").grid(
            row=1, column=0, sticky="w", pady=4)
        tk.Entry(form, textvariable=self.top_assembly, width=26,
                 font=("Arial", 10)).grid(row=1, column=1, sticky="w", pady=4)
        tk.Label(form, text="blank = skip the top-level jobs", bg=LIGHT_GRAY,
                 fg=DARK_GRAY, font=("Arial", 8)).grid(
            row=2, column=1, sticky="w")
        form.columnconfigure(1, weight=1)

        actions = tk.Frame(self.win, bg=LIGHT_GRAY, padx=16)
        actions.pack(fill="x")
        self.scan_btn = tk.Button(
            actions, text="  Scan  ", command=self._on_scan,
            bg=DARK_BLUE, fg=WHITE, font=("Arial", 10, "bold"),
            relief="flat", padx=10, pady=4, cursor="hand2",
        )
        self.scan_btn.pack(side="left")
        self.submit_btn = tk.Button(
            actions, text="  Submit Jobs  ", command=self._on_submit,
            bg=OLIVE_GREEN, fg=WHITE, font=("Arial", 10, "bold"),
            relief="flat", padx=10, pady=4, cursor="hand2",
            state="disabled",
        )
        self.submit_btn.pack(side="left", padx=(10, 0))

        table_frame = tk.Frame(self.win, bg=WHITE, padx=16, pady=10)
        table_frame.pack(fill="both", expand=True)
        columns = ("part", "description", "model", "drawing", "status")
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=10)
        for key, label, width in (
            ("part", "Part", 130),
            ("description", "Description", 200),
            ("model", "Model", 170),
            ("drawing", "Drawing", 170),
            ("status", "Status", 170),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w")
        vsb = ttk.Scrollbar(table_frame, orient="vertical",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("gap", foreground=RUST_ORANGE)

        tk.Label(self.win, textvariable=self.summary_text, bg=PALE_BLUE,
                 fg=DARK_BLUE, font=("Arial", 9, "bold"), anchor="w",
                 padx=16, pady=5).pack(fill="x")

        log_frame = tk.Frame(self.win, bg=LIGHT_GRAY)
        log_frame.pack(fill="both", padx=16, pady=(8, 12))
        self.log = tk.Text(log_frame, height=8, bg=WHITE, fg=DARK_GRAY,
                           font=("Consolas", 9), relief="flat",
                           highlightthickness=1, highlightbackground=GRAY_BDR)
        log_vsb = ttk.Scrollbar(log_frame, orient="vertical",
                                command=self.log.yview)
        self.log.configure(yscrollcommand=log_vsb.set, state="disabled")
        log_vsb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("ok", foreground=OLIVE_GREEN)
        self.log.tag_configure("err", foreground=RUST_ORANGE)
        self.log.tag_configure("dim", foreground=GRAY_BDR)

        tk.Button(self.win, text="  Close  ", command=self.win.destroy,
                  font=("Arial", 9)).pack(side="right", padx=16, pady=(0, 12))

    # ----- Logging ----------------------------------------------------------

    def _log(self, msg: str, tag: Optional[str] = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "scan_done":
                    self._render_scan(payload)
                elif kind == "submit_done":
                    self._render_submit(payload)
                elif kind == "error":
                    self._log(payload, "err")
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.win.after(100, self._drain_queue)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.scan_btn.configure(state="disabled" if busy else "normal")

    # ----- Actions ----------------------------------------------------------

    def _on_browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.win, title="Select a BOM export",
            filetypes=[("BOM exports", "*.xlsx *.xls *.csv *.txt"),
                       ("All files", "*.*")],
        )
        if not path:
            return
        self.bom_path.set(path)
        self.top_assembly.set(publish_bom.top_assembly_stem(path))

    def _require_session(self) -> bool:
        if self.api and self.vault_id:
            return True
        messagebox.showwarning(
            "Not signed in",
            "This tool needs an authenticated Vault session. Reconnect from "
            "the launcher and try again.",
            parent=self.win,
        )
        return False

    def _on_scan(self) -> None:
        if self._busy or not self._require_session():
            return
        path = self.bom_path.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showwarning(
                "No BOM selected", "Pick a BOM export first.", parent=self.win)
            return

        self.submit_btn.configure(state="disabled")
        self.scan_result = []
        for iid in self.tree.get_children():
            self.tree.delete(iid)

        self._set_busy(True)
        self._log(f"Scanning {os.path.basename(path)}")

        top = self.top_assembly.get().strip()

        def runner() -> None:
            try:
                rows, error = asyncio.run(publish_bom.scan_bom(
                    self.api, self.vault_id, path,
                    top_assembly=top,
                    on_progress=lambda m: self.q.put(("log", m)),
                ))
                if error:
                    self.q.put(("error", error))
                    return
                self.q.put(("scan_done", rows))
            except Exception as exc:  # noqa: BLE001 — surface, never crash the GUI
                self.q.put(("error", f"Scan failed: {exc}"))

        threading.Thread(target=runner, daemon=True, name="publish-bom-scan").start()

    def _render_scan(self, rows: list[publish_bom.ScanRow]) -> None:
        self.scan_result = rows
        for row in rows:
            part = f"{row.stem} (top)" if row.is_top else row.stem
            tag = "gap" if row.status != publish_bom.STATUS_BOTH else ""
            self.tree.insert("", "end", values=(
                part, row.description or "-",
                row.model_name or "-", row.drawing_name or "-", row.status,
            ), tags=(tag,))

        s = publish_bom.summarize(rows)
        self.summary_text.set(
            f"{s['rows']} part(s) - {s['models']} model(s) - "
            f"{s['drawings']} drawing(s) - {s['jobs']} job(s) to queue - "
            f"{s['missing_drawing']} missing a drawing - "
            f"{s['not_found']} not in Vault"
        )
        self._log(f"Scan complete: {s['jobs']} job(s) ready to queue.", "ok")
        self._set_busy(False)
        self.submit_btn.configure(state="normal" if s["jobs"] else "disabled")

    def _on_submit(self) -> None:
        if self._busy or not self.scan_result or not self._require_session():
            return
        s = publish_bom.summarize(self.scan_result)
        if not messagebox.askyesno(
            "Queue jobs?",
            f"Queue {s['jobs']} job(s) on the Vault job server?\n\n"
            "Jobs are submitted and not tracked from here — watch their "
            "progress in Vault Explorer.",
            parent=self.win,
        ):
            return

        self._set_busy(True)
        self.submit_btn.configure(state="disabled")
        self._log(f"Submitting {s['jobs']} job(s)...")

        rows = list(self.scan_result)

        def runner() -> None:
            try:
                result = asyncio.run(publish_bom.submit_jobs(
                    self.api, self.vault_id, rows,
                    on_progress=lambda m: self.q.put(("log", m)),
                ))
                self.q.put(("submit_done", result))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("error", f"Submit failed: {exc}"))

        threading.Thread(target=runner, daemon=True,
                         name="publish-bom-submit").start()

    def _render_submit(self, result: dict[str, Any]) -> None:
        self._log("")
        self._log(f"DONE - {result['submitted']} job(s) queued.", "ok")
        if result["failed"]:
            self._log(f"  {result['failed']} submission(s) failed.", "err")
        self._log("Watch the queue in Vault Explorer.", "dim")
        self._set_busy(False)
        # A second run needs a fresh Scan — the guard against queueing twice.
        self.submit_btn.configure(state="disabled")


def launch_gui(
    *,
    api: Any = None,
    vault_id: str = "",
    cfg: Optional[dict[str, Any]] = None,
    parent: Optional[tk.Misc] = None,
) -> None:
    """Open the dialog. ``parent`` should be the launcher root so it opens as a
    Toplevel that does not take over the main loop; pass None to run
    standalone."""
    if parent is None:
        root = tk.Tk()
        root.withdraw()
        gui = PublishBOMGUI(root, api=api, vault_id=vault_id, cfg=cfg)
        gui.win.protocol(
            "WM_DELETE_WINDOW", lambda: (gui.win.destroy(), root.destroy()))
        root.mainloop()
    else:
        PublishBOMGUI(parent, api=api, vault_id=vault_id, cfg=cfg)
