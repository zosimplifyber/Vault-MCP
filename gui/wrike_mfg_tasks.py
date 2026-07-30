# gui/wrike_mfg_tasks.py
"""Tkinter dialog for the BOM → Wrike manufacturing task builder.

Opens as a Toplevel from the launcher with the live Vault session and a Wrike
client attached. Load & Reconcile reads the purchasing sheet and checks every
part's supplier against Vault; Preview turns the resolved rows into a task
plan; Create Tasks writes it to Wrike.

Preview is gated on zero unresolved rows, and Create is gated on a fresh
Preview — the guard against writing a board twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sys
import threading
from datetime import date, datetime
from typing import Any, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from gui.release_workflow import (  # noqa: E402
    DARK_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY, WHITE,
    RUST_ORANGE,
)

import wrike_mfg_tasks as wmt  # noqa: E402

logger = logging.getLogger(__name__)

# Row colours in the Parts table. Green needs nothing, amber has a proposal to
# accept, red blocks the Preview button.
TAG_OK = "ok"
TAG_PROPOSED = "proposed"
TAG_BLOCKED = "blocked"
TAG_EXCLUDED = "excluded"


class WrikeMfgTasksGUI:
    """Toplevel dialog. One per launch."""

    def __init__(self, master: tk.Misc, *, api: Any = None, vault_id: str = "",
                 wrike: Any = None, cfg: Optional[dict[str, Any]] = None) -> None:
        self.api = api
        self.vault_id = vault_id
        self.wrike = wrike
        self.cfg = cfg or {}
        self.settings = (self.cfg.get("wrike") or {}).get("mfg_tasks") or {}

        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.rows: list[wmt.ReconcileRow] = []
        self.orders: list[wmt.SupplierOrder] = []
        self.projects: list[dict[str, Any]] = []
        self.contacts: list[dict[str, Any]] = []
        self._busy = False
        self._created = False
        # Inputs resolved by _on_create, held until the async zone check
        # (and, if the project is outside the safe zone, the user's
        # yes/no) comes back and _on_zone_checked can pick up where it
        # left off.
        self._pending_create: Optional[dict[str, Any]] = None

        self.win = tk.Toplevel(master)
        self.win.title("BOM → Manufacturing Tasks")
        self.win.configure(bg=LIGHT_GRAY)
        self.win.geometry("1040x700")
        self.win.minsize(900, 560)

        self.sheet_path = tk.StringVar()
        self.build = tk.StringVar()
        self.project_label = tk.StringVar()
        self.start_date = tk.StringVar(value=date.today().isoformat())
        self.d_purchasing = tk.StringVar(
            value=str(self.settings.get("purchasing_days", 2)))
        self.d_manufacturing = tk.StringVar(
            value=str(self.settings.get("manufacturing_days", 10)))
        self.d_shipping = tk.StringVar(
            value=str(self.settings.get("shipping_days", 3)))
        self.owner_labels = {
            wmt.STAGE_PURCHASING: tk.StringVar(),
            wmt.STAGE_MANUFACTURING: tk.StringVar(),
            wmt.STAGE_SHIPPING: tk.StringVar(),
        }
        self.summary_text = tk.StringVar(
            value="Pick a purchasing sheet and click Load & Reconcile.")

        self._build_ui()
        # Retargeting the sheet invalidates everything on screen: without this,
        # browsing to a different workbook leaves the old plan in the table
        # with Create still enabled.
        self.sheet_path.trace_add("write", self._invalidate)
        self.win.after(120, self._drain)
        self._load_wrike_metadata()

    # -------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        top = tk.Frame(self.win, bg=WHITE, padx=12, pady=10)
        top.pack(fill="x")

        tk.Label(top, text="Purchasing sheet", bg=WHITE, fg=DARK_GRAY).grid(
            row=0, column=0, sticky="w")
        tk.Entry(top, textvariable=self.sheet_path, width=62).grid(
            row=0, column=1, columnspan=3, sticky="we", padx=6)
        tk.Button(top, text="Browse...", command=self._on_browse).grid(
            row=0, column=4, sticky="w")

        tk.Label(top, text="Build", bg=WHITE, fg=DARK_GRAY).grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        tk.Entry(top, textvariable=self.build, width=18).grid(
            row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        tk.Label(top, text="Wrike project", bg=WHITE, fg=DARK_GRAY).grid(
            row=1, column=2, sticky="e", pady=(6, 0))
        self.project_box = ttk.Combobox(top, textvariable=self.project_label,
                                        state="readonly", width=34)
        self.project_box.grid(row=1, column=3, columnspan=2, sticky="we",
                              padx=6, pady=(6, 0))

        sched = tk.Frame(top, bg=WHITE)
        sched.grid(row=2, column=0, columnspan=5, sticky="w", pady=(8, 0))
        tk.Label(sched, text="Start", bg=WHITE, fg=DARK_GRAY).pack(side="left")
        tk.Entry(sched, textvariable=self.start_date, width=12).pack(
            side="left", padx=(4, 12))
        for label, var in (("Purchasing", self.d_purchasing),
                           ("MFG fallback", self.d_manufacturing),
                           ("Shipping", self.d_shipping)):
            tk.Label(sched, text=label, bg=WHITE, fg=DARK_GRAY).pack(side="left")
            tk.Entry(sched, textvariable=var, width=5).pack(
                side="left", padx=(4, 12))
        tk.Label(sched, text="business days", bg=WHITE, fg=DARK_GRAY,
                 font=("Arial", 8, "italic")).pack(side="left")

        owners = tk.Frame(top, bg=WHITE)
        owners.grid(row=3, column=0, columnspan=5, sticky="w", pady=(8, 0))
        tk.Label(owners, text="Owners", bg=WHITE, fg=DARK_GRAY).pack(side="left")
        self.owner_boxes = {}
        for stage in (wmt.STAGE_PURCHASING, wmt.STAGE_MANUFACTURING,
                      wmt.STAGE_SHIPPING):
            tk.Label(owners, text=stage, bg=WHITE, fg=DARK_GRAY).pack(
                side="left", padx=(12, 4))
            box = ttk.Combobox(owners, textvariable=self.owner_labels[stage],
                               state="readonly", width=20)
            box.pack(side="left")
            self.owner_boxes[stage] = box

        buttons = tk.Frame(top, bg=WHITE)
        buttons.grid(row=4, column=0, columnspan=5, sticky="e", pady=(10, 0))
        self.btn_load = tk.Button(buttons, text="Load & Reconcile",
                                  command=self._on_load)
        self.btn_load.pack(side="left", padx=4)
        self.btn_preview = tk.Button(buttons, text="Preview",
                                     command=self._on_preview, state="disabled")
        self.btn_preview.pack(side="left", padx=4)
        self.btn_create = tk.Button(buttons, text="Create Tasks",
                                    command=self._on_create, state="disabled")
        self.btn_create.pack(side="left", padx=4)

        book = ttk.Notebook(self.win)
        book.pack(fill="both", expand=True, padx=12, pady=(10, 0))

        parts_frame = tk.Frame(book, bg=WHITE)
        self.parts = ttk.Treeview(
            parts_frame, show="headings",
            columns=("part", "desc", "kind", "sheet", "vault", "supplier",
                     "status"))
        for key, label, width in (
            ("part", "Part", 130), ("desc", "Description", 240),
            ("kind", "Kind", 55), ("sheet", "Sheet vendor", 140),
            ("vault", "Vault vendor", 140), ("supplier", "Supplier", 140),
            ("status", "Status", 120),
        ):
            self.parts.heading(key, text=label)
            self.parts.column(key, width=width, anchor="w")
        self.parts.tag_configure(TAG_OK, background=WHITE)
        self.parts.tag_configure(TAG_PROPOSED, background=PALE_BLUE)
        self.parts.tag_configure(TAG_BLOCKED, background=RUST_ORANGE,
                                 foreground=WHITE)
        self.parts.tag_configure(TAG_EXCLUDED, foreground=GRAY_BDR)
        self.parts.pack(fill="both", expand=True, side="left")
        self.parts.bind("<Double-1>", self._on_edit_supplier)
        ttk.Scrollbar(parts_frame, orient="vertical",
                      command=self.parts.yview).pack(side="right", fill="y")
        book.add(parts_frame, text="Parts")

        plan_frame = tk.Frame(book, bg=WHITE)
        self.plan = ttk.Treeview(
            plan_frame, show="headings",
            columns=("supplier", "stage", "start", "due", "owner", "state"))
        for key, label, width in (
            ("supplier", "Supplier", 180), ("stage", "Stage", 140),
            ("start", "Start", 100), ("due", "Due", 100),
            ("owner", "Owner", 160), ("state", "", 130),
        ):
            self.plan.heading(key, text=label)
            self.plan.column(key, width=width, anchor="w")
        self.plan.pack(fill="both", expand=True)
        book.add(plan_frame, text="Task plan")
        self.book = book

        bottom = tk.Frame(self.win, bg=LIGHT_GRAY, padx=12, pady=8)
        bottom.pack(fill="both")
        tk.Button(bottom, text="Accept all proposals",
                  command=self._on_accept_all).pack(side="left")
        tk.Button(bottom, text="Exclude selected",
                  command=self._on_exclude).pack(side="left", padx=6)
        tk.Label(bottom, textvariable=self.summary_text, bg=LIGHT_GRAY,
                 fg=DARK_BLUE).pack(side="left", padx=12)
        tk.Button(bottom, text="Close", command=self.win.destroy).pack(
            side="right")

        self.log = tk.Text(self.win, height=7, bg=WHITE, fg=DARK_GRAY,
                           wrap="word")
        self.log.pack(fill="both", padx=12, pady=(0, 12))

    # ---------------------------------------------------------- helpers

    def _say(self, message: str) -> None:
        self.log.insert("end", message + "\n")
        self.log.see("end")

    def _invalidate(self, *_args) -> None:
        self.orders = []
        self._created = False
        self.btn_preview.configure(state="disabled")
        self.btn_create.configure(state="disabled")
        for item in self.plan.get_children():
            self.plan.delete(item)

    def _durations(self) -> wmt.Durations:
        def _int(var, fallback):
            try:
                return max(1, int(var.get().strip()))
            except (TypeError, ValueError):
                return fallback
        return wmt.Durations(
            purchasing=_int(self.d_purchasing, 2),
            manufacturing=_int(self.d_manufacturing, 10),
            shipping=_int(self.d_shipping, 3),
        )

    def _start(self) -> Optional[date]:
        try:
            return datetime.strptime(self.start_date.get().strip(),
                                     "%Y-%m-%d").date()
        except ValueError:
            return None

    def _selected_id(self, options, label_var, id_key="id"):
        label = label_var.get()
        for row in options:
            if self._label_of(row) == label:
                return row.get(id_key, "")
        return ""

    @staticmethod
    def _label_of(row: dict[str, Any]) -> str:
        return str(row.get("title") or row.get("firstName", "") + " "
                   + row.get("lastName", "")).strip()

    def _run(self, coro_factory, done_key: str) -> None:
        """Run an async engine call on a worker thread."""
        if self._busy:
            return
        self._busy = True

        def worker():
            try:
                result = asyncio.run(coro_factory())
                self.q.put((done_key, result))
            except Exception as exc:  # noqa: BLE001
                logger.exception("%s failed", done_key)
                self.q.put(("error", str(exc)))
            finally:
                self.q.put(("idle", None))

        threading.Thread(target=worker, daemon=True).start()

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._say(payload)
                elif kind == "idle":
                    self._busy = False
                elif kind == "error":
                    self._say(f"ERROR: {payload}")
                    messagebox.showerror("Failed", payload, parent=self.win)
                elif kind == "reconciled":
                    self.rows = payload
                    self._refresh_parts()
                elif kind == "created":
                    self._report_created(payload)
                elif kind == "metadata":
                    self._apply_metadata(payload)
                elif kind == "zone_checked":
                    self._on_zone_checked(payload)
        except queue.Empty:
            pass
        self.win.after(120, self._drain)

    # ------------------------------------------------------------ wrike

    def _load_wrike_metadata(self) -> None:
        if not self.wrike:
            self._say("No Wrike client — check the wrike block in config.json.")
            return

        async def fetch():
            projects = await self.wrike.list_projects()
            contacts = await self.wrike.list_contacts()
            return projects, contacts

        self._run(fetch, "metadata")

    def _apply_metadata(self, payload) -> None:
        projects, contacts = payload
        self.projects = [r for r in _rows(projects)]
        self.contacts = [r for r in _rows(contacts)]
        self.project_box["values"] = [self._label_of(p) for p in self.projects]
        names = [self._label_of(c) for c in self.contacts]
        for stage, box in self.owner_boxes.items():
            box["values"] = names
            saved = (self.settings.get("owners") or {}).get(stage)
            for contact in self.contacts:
                if contact.get("id") == saved:
                    self.owner_labels[stage].set(self._label_of(contact))
        saved_project = self.settings.get("project_id")
        for project in self.projects:
            if project.get("id") == saved_project:
                self.project_label.set(self._label_of(project))
        self._say(f"Wrike: {len(self.projects)} projects, "
                  f"{len(self.contacts)} contacts.")

    # ----------------------------------------------------------- actions

    def _on_browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.win, title="Select a generated purchasing sheet",
            filetypes=[("Excel workbook", "*.xlsx")],
        )
        if path:
            self.sheet_path.set(path)

    def _on_load(self) -> None:
        path = self.sheet_path.get().strip()
        if not path:
            messagebox.showwarning("No sheet", "Pick a purchasing sheet first.",
                                   parent=self.win)
            return

        parts, assembly, error = wmt.load_order_parts(
            path, on_progress=lambda m: self.q.put(("log", m)))
        if error:
            messagebox.showerror("Cannot read sheet", error, parent=self.win)
            self._say(f"ERROR: {error}")
            return
        if not parts:
            self._say("No orderable parts on this sheet.")
            self.summary_text.set("No orderable parts.")
            return
        if assembly and not self.build.get().strip():
            self.build.set(assembly)

        self._say(f"{len(parts)} line items. Checking suppliers against "
                  f"Vault...")
        self._run(
            lambda: wmt.reconcile_vendors(
                self.api, self.vault_id, parts,
                on_progress=lambda m: self.q.put(("log", m))),
            "reconciled",
        )

    def _refresh_parts(self) -> None:
        for item in self.parts.get_children():
            self.parts.delete(item)
        for index, row in enumerate(self.rows):
            if row.excluded:
                tag = TAG_EXCLUDED
            elif row.chosen:
                tag = TAG_OK
            elif row.proposal:
                tag = TAG_PROPOSED
            else:
                tag = TAG_BLOCKED
            self.parts.insert(
                "", "end", iid=str(index), tags=(tag,),
                values=(row.part.title, row.part.description, row.part.kind,
                        row.part.sheet_vendor, row.vault_vendor,
                        row.chosen or row.proposal or "-- pick", row.status))
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        unresolved = wmt.unresolved_count(self.rows)
        suppliers = len({wmt.vendor_key(r.chosen) for r in self.rows
                         if r.chosen and not r.excluded})
        self.summary_text.set(
            f"{len(self.rows)} rows - {suppliers} suppliers - "
            f"{unresolved} unresolved")
        ready = bool(self.rows) and unresolved == 0
        self.btn_preview.configure(state="normal" if ready else "disabled")
        if not ready:
            self.btn_create.configure(state="disabled")

    def _on_accept_all(self) -> None:
        accepted = wmt.accept_proposals(self.rows)
        self._say(f"Accepted {accepted} proposed suppliers.")
        self._invalidate()
        self._refresh_parts()

    def _on_exclude(self) -> None:
        for iid in self.parts.selection():
            self.rows[int(iid)].excluded = True
        self._invalidate()
        self._refresh_parts()

    def _on_edit_supplier(self, _event) -> None:
        selection = self.parts.selection()
        if not selection:
            return
        row = self.rows[int(selection[0])]
        dialog = tk.Toplevel(self.win)
        dialog.title(f"Supplier for {row.part.title}")
        var = tk.StringVar(value=row.chosen or row.proposal
                           or row.part.sheet_vendor or row.vault_vendor)
        tk.Label(dialog, text=f"Sheet: {row.part.sheet_vendor or '--'}    "
                              f"Vault: {row.vault_vendor or '--'}").pack(
            padx=12, pady=(12, 4))
        entry = tk.Entry(dialog, textvariable=var, width=32)
        entry.pack(padx=12, pady=4)
        entry.focus_set()

        def apply():
            row.chosen = var.get().strip()
            row.excluded = False
            dialog.destroy()
            self._invalidate()
            self._refresh_parts()

        tk.Button(dialog, text="Use this supplier", command=apply).pack(
            padx=12, pady=(4, 12))

    def _on_preview(self) -> None:
        start = self._start()
        if start is None:
            messagebox.showwarning(
                "Bad start date",
                "Enter the start date as YYYY-MM-DD.", parent=self.win)
            return
        if not self.build.get().strip():
            messagebox.showwarning("No build", "Enter a build number.",
                                   parent=self.win)
            return

        self.orders = wmt.schedule_orders(
            wmt.group_orders(self.rows), start=start,
            durations=self._durations())

        for item in self.plan.get_children():
            self.plan.delete(item)
        for order in self.orders:
            by_stage = {s.stage: s for s in order.schedule}
            parent_owner = (self.owner_labels[order.stages[0]].get()
                           if order.stages else "")
            self.plan.insert("", "end", values=(
                order.supplier, "(parent)",
                order.start.isoformat() if order.start else "",
                order.due.isoformat() if order.due else "",
                parent_owner, "new"))
            for stage in order.stages:
                sched = by_stage[stage]
                self.plan.insert("", "end", values=(
                    "", stage, sched.start.isoformat(), sched.due.isoformat(),
                    self.owner_labels[stage].get(), ""))

        tasks = sum(len(o.stages) + 1 for o in self.orders)
        self._say(f"Plan: {len(self.orders)} orders, {tasks} tasks.")
        self.book.select(1)
        self._created = False
        self.btn_create.configure(state="normal")

    def _on_create(self) -> None:
        """Resolve inputs, then dispatch an async safe-zone check before
        writing anything.

        ``create_task`` has no guard of its own — the folder allowlist is
        enforced by callers checking first, and this dialog was the gap: it
        populates the project picker from the unfiltered ``list_projects``,
        so nothing stopped a silent create outside the configured zone.
        The check itself is async and this method runs on the Tk thread, so
        it goes through the same worker-thread + queue pattern as every
        other engine call here (see ``_run``/``_drain``); the actual
        yes/no prompt and the follow-up dispatch happen in
        ``_on_zone_checked``, invoked from ``_drain`` on the Tk thread once
        the result lands.
        """
        if self._created:
            return
        folder_id = self._selected_id(self.projects, self.project_label)
        if not folder_id:
            messagebox.showwarning("No project",
                                   "Pick a Wrike project first.",
                                   parent=self.win)
            return
        owners = {stage: self._selected_id(self.contacts, var)
                  for stage, var in self.owner_labels.items()}
        build = self.build.get().strip()
        source = os.path.basename(self.sheet_path.get().strip())

        self._pending_create = {
            "folder_id": folder_id, "owners": owners,
            "build": build, "source": source,
        }
        # Disable immediately so a second click can't queue a second check
        # (or a second create) while this one is in flight; _created is the
        # same latch _on_zone_checked clears on a decline so the button can
        # be pressed again.
        self._created = True
        self.btn_create.configure(state="disabled")
        self._run(lambda: self.wrike.folder_is_outside_zone(folder_id),
                  "zone_checked")

    def _on_zone_checked(self, outside: bool) -> None:
        pending = self._pending_create
        self._pending_create = None
        if pending is None:
            return
        # The worker that produced this result has already finished; its
        # "idle" message is just still sitting behind this one in the
        # queue (both were queued before this drain pass started). Clear
        # busy now so the follow-up _run() below — dispatched from this
        # same handler, before that queued "idle" is processed — isn't
        # dropped by _run's own re-entrancy guard.
        self._busy = False

        if outside:
            project = next(
                (p for p in self.projects
                 if p.get("id") == pending["folder_id"]), {})
            title = self._label_of(project) or pending["folder_id"]
            task_count = sum(len(o.stages) + 1 for o in self.orders)
            proceed = messagebox.askyesno(
                "Outside the safe zone",
                f"'{title}' is OUTSIDE the configured Wrike safe zone "
                f"({self.wrike.zone_description()}).\n\n"
                f"{task_count} task(s) across {len(self.orders)} order(s) "
                f"are about to be created there.\n\nProceed anyway?",
                default=messagebox.NO, parent=self.win)
            if not proceed:
                self._say("Create cancelled: project is outside the "
                          "configured safe zone.")
                self._created = False
                self.btn_create.configure(state="normal")
                return

        self._save_settings(pending["folder_id"], pending["owners"])
        self._run(
            lambda: wmt.create_orders(
                self.wrike, folder_id=pending["folder_id"],
                build=pending["build"], orders=self.orders,
                owners=pending["owners"], source_name=pending["source"],
                on_progress=lambda m: self.q.put(("log", m))),
            "created",
        )

    def _report_created(self, result: wmt.CreateResult) -> None:
        self._say(
            f"Created {result.orders_created} orders "
            f"({len(result.task_ids)} tasks), "
            f"skipped {result.orders_skipped}, "
            f"{len(result.failures)} failures.")
        for title in result.skipped_titles:
            self._say(f"  already existed: {title}")
        for failure in result.check_errors:
            self._say(f"  EXISTENCE CHECK FAILED, skipped: {failure}")
        for failure in result.failures:
            self._say(f"  FAILED {failure}")
        for failure in result.dependency_failures:
            self._say(f"  dependency not linked: {failure}")
        self.summary_text.set(
            f"{result.orders_created} created - "
            f"{result.orders_skipped} skipped")
        if result.check_errors:
            messagebox.showwarning(
                "Some orders were not checked",
                f"{len(result.check_errors)} supplier order(s) were skipped "
                f"because the check for an existing order failed, not because "
                f"one exists. Nothing was created for them. See the log.",
                parent=self.win)

    def _save_settings(self, folder_id: str, owners: dict[str, str]) -> None:
        """Remember the picks so they are set once, not every run.

        Written back to config.json, not just the in-memory dict — the point
        is that the next session starts with them already filled in. A write
        failure is logged and shrugged off: losing a preference must never
        take down a run that already created tasks.
        """
        durations = self._durations()
        block = self.cfg.setdefault("wrike", {}).setdefault("mfg_tasks", {})
        block["project_id"] = folder_id
        block["owners"] = {k: v for k, v in owners.items() if v}
        block["purchasing_days"] = durations.purchasing
        block["manufacturing_days"] = durations.manufacturing
        block["shipping_days"] = durations.shipping

        path = self.cfg.get("__path__") or os.path.join(PROJECT_ROOT,
                                                        "config.json")
        try:
            with open(path, encoding="utf-8") as fh:
                on_disk = json.load(fh)
            on_disk.setdefault("wrike", {})["mfg_tasks"] = block
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(on_disk, fh, indent=4)
            self._say("Saved your project, owners and durations to config.json.")
        except Exception as exc:  # noqa: BLE001 — a preference is not the work
            logger.warning("Could not save mfg_tasks settings: %s", exc)
            self._say(f"Could not save settings to config.json: {exc}")


def _rows(resp: dict[str, Any]) -> list[dict[str, Any]]:
    data = resp.get("data") if isinstance(resp, dict) else None
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            return [r for r in inner if isinstance(r, dict)]
    return []


def launch_gui(*, api=None, vault_id="", wrike=None, cfg=None, parent=None):
    """Open the dialog as a child of the launcher window."""
    return WrikeMfgTasksGUI(parent, api=api, vault_id=vault_id, wrike=wrike,
                            cfg=cfg)
