"""
GUI: build the Formed Fiber design-to-process handoff document.

Pick the general assembly from Vault, click the final pressed part in its CAD
BOM, choose the press, and type the four values nobody can look up. Material
and the filenames come from Vault; mass and volume come from the Inventor
model; both pressures come from the machine library.

Vault and Inventor work runs on a worker thread so the window stays
responsive, and results come back through a queue drained on the Tk thread.
No Tk call happens off the main thread.

The form works with no Vault session at all -- every pulled field stays
editable -- because a handoff written by hand is a legitimate thing to want.
"""
from __future__ import annotations

import asyncio
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from gui.theme import (  # noqa: E402
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY,
    WHITE, RUST_ORANGE, WARN_AMBER,
)

import formed_fiber_handoff as engine  # noqa: E402
import formed_fiber_vault as vault_lookup  # noqa: E402
from formed_fiber_pdf import render_handoff_pdf  # noqa: E402


def _card(parent, title: str):
    """A bordered panel with the brand's dark-blue caption bar. Returns body."""
    card = tk.Frame(parent, bg=WHITE, highlightthickness=1,
                    highlightbackground=GRAY_BDR)
    card.pack(fill="x", padx=16, pady=(0, 10))
    tk.Label(card, text=f"  {title}", bg=DARK_BLUE, fg=WHITE,
             font=("Arial", 10, "bold"), anchor="w", padx=10, pady=6).pack(fill="x")
    tk.Frame(card, bg=MID_BLUE, height=2).pack(fill="x")
    body = tk.Frame(card, bg=WHITE, padx=12, pady=10)
    body.pack(fill="both", expand=True)
    return body


class HandoffGUI:
    """The handoff form.

    Also satisfies ``FileSearchDialog``'s duck-typed contract -- ``root``,
    ``api``, ``vault_id``, ``top_file_var``, ``set_top_file``,
    ``_brand_button``, ``_ensure_signed_in`` -- so the wizard's file picker
    can be reused without modifying it. ``gui/search_dialog.py`` is NOT the
    one to use here: it is item-based and returns a part number, and both
    modules carry explicit "do not merge them" notes.
    """

    # Description sits next to the file name because together they are what
    # you actually scan to find the pressed part -- CD-001488.iam alone does
    # not tell you which child it is.
    BOM_COLUMNS = [
        ("file_name", "File Name", 165),
        ("description", "Description", 245),
        ("revision", "Rev", 40),
        ("state", "State", 95),
        ("material", "Material", 160),
    ]

    def __init__(
        self,
        *,
        parent=None,
        api: Any = None,
        vault_id: str = "",
        cfg: Optional[dict] = None,
        machines_path: Path | str = engine.MACHINES_PATH,
    ) -> None:
        self.api = api
        self.vault_id = vault_id or ""
        self.cfg = cfg or {}
        self.workspace_root = engine.workspace_root_from_config(self.cfg)
        self.machines = engine.load_machines(machines_path)

        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.assembly: dict[str, str] = {}
        self.part: dict[str, str] = {}
        self.children: list[dict[str, str]] = []
        self.busy = False
        # Bumped on every Inventor read so a slow result for a part the user
        # has clicked away from can be recognised and dropped.
        self._inventor_generation = 0
        self._sdw_tracking = True
        self._sdw_updating = False

        self.win = tk.Toplevel(parent) if parent is not None else tk.Tk()
        # FileSearchDialog reaches for parent.root, so expose the window there.
        self.root = self.win
        if parent is not None:
            self.win.transient(parent)
        self.win.title("Simplifyber — Formed Fiber Design-to-Process Handoff")
        self.win.geometry("880x900")
        self.win.minsize(760, 700)
        self.win.configure(bg=LIGHT_GRAY)

        self.vars: dict[str, tk.StringVar] = {}
        # Entry widgets by field name. Kept so tests can drive real key
        # events at a field rather than calling its handler by hand -- the
        # earlier SDW test did the latter and so could not catch a binding
        # that fired on the wrong events.
        self.entries: dict[str, tk.Entry] = {}
        self.target_vars: dict[str, tk.BooleanVar] = {}
        self.top_file_var = tk.StringVar()          # FileSearchDialog contract
        self.status_var = tk.StringVar(value="Ready. Find the general assembly to start.")
        self.ga_detail_var = tk.StringVar(value="")
        self.part_detail_var = tk.StringVar(value="")
        self.state_warning_var = tk.StringVar(value="")
        self.machine_warning_var = tk.StringVar(value="")
        self.inventor_note_var = tk.StringVar(value="")
        self.out_dir_var = tk.StringVar(value="")
        self.out_name_var = tk.StringVar(value="")

        self._build_ui()
        self._wire_derived_fields()
        if not self.machines:
            self.status_var.set(
                "machines.json could not be read — the machine fields are free "
                "text for this session."
            )
        self.win.after(100, self._drain_queue)

    # ----- FileSearchDialog contract ---------------------------------------

    def _ensure_signed_in(self) -> bool:
        """Called from worker threads. The launcher hands us its session."""
        return bool(self.api is not None and self.vault_id)

    def _brand_button(self, parent, text, command, *, primary: bool) -> tk.Button:
        bg, fg = (DARK_BLUE, WHITE) if primary else (MID_BLUE, WHITE)
        active_bg = MID_BLUE if primary else DARK_BLUE
        return tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, activebackground=active_bg, activeforeground=WHITE,
            font=("Arial", 10, "bold" if primary else "normal"),
            relief="flat", bd=0, padx=12, pady=5, cursor="hand2",
        )

    def set_top_file(self, name: str) -> None:
        """FileSearchDialog hands the picked assembly here."""
        self.top_file_var.set(name)
        self._load_assembly(name)

    # ----- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        header = tk.Frame(self.win, bg=DARK_BLUE, height=46)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="  Formed Fiber: Design-to-Process Handoff",
                 bg=DARK_BLUE, fg=WHITE, font=("Arial", 13, "bold"),
                 anchor="w", padx=12).pack(side="left", fill="y")
        tk.Frame(self.win, bg=MID_BLUE, height=2).pack(fill="x")

        # Status bar first, packed to the bottom, so it keeps its place when
        # the scroll area below claims the remaining space.
        bar = tk.Frame(self.win, bg=PALE_BLUE, highlightthickness=1,
                       highlightbackground=GRAY_BDR)
        bar.pack(fill="x", side="bottom")
        tk.Label(bar, textvariable=self.status_var, bg=PALE_BLUE, fg=DARK_BLUE,
                 font=("Arial", 9), anchor="w", padx=12, pady=4).pack(fill="x")

        body = self._build_scroll_area()

        self._build_assembly_card(body)
        self._build_bom_card(body)
        self._build_machine_card(body)
        self._build_production_card(body)
        self._build_output_card(body)

    def _build_scroll_area(self) -> tk.Frame:
        """Vertically-scrollable container for the cards. Returns the inner frame.

        Without this the form clips: five cards do not fit 900px on a laptop,
        and the bottom of Production Details -- Standard Dry Weight and
        Dryness -- was simply cut off with nothing to indicate more existed.
        Same canvas-plus-inner-frame idiom as gui/launcher.py's own.
        """
        outer = tk.Frame(self.win, bg=LIGHT_GRAY)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=LIGHT_GRAY, highlightthickness=0)
        vsb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        content = tk.Frame(canvas, bg=LIGHT_GRAY, pady=12)
        win_id = canvas.create_window((0, 0), window=content, anchor="nw")

        content.bind("<Configure>",
                     lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win_id, width=e.width))

        # Wheel bound only while the pointer is over this form, so the
        # launcher behind it keeps its own scrolling.
        def _on_wheel(event):
            canvas.yview_scroll(int(-event.delta / 120), "units")
        canvas.bind("<Enter>",
                    lambda _e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        return content

    def _build_assembly_card(self, parent) -> None:
        body = _card(parent, "GENERAL ASSEMBLY")
        row = tk.Frame(body, bg=WHITE)
        row.pack(fill="x")
        tk.Entry(row, textvariable=self.top_file_var, state="readonly",
                 font=("Consolas", 10), readonlybackground=LIGHT_GRAY,
                 relief="flat", highlightthickness=1,
                 highlightbackground=GRAY_BDR).pack(
            side="left", fill="x", expand=True, padx=(0, 8), ipady=3)
        self._brand_button(row, "  Find GA  ", self._on_find_ga,
                           primary=True).pack(side="left")
        tk.Label(body, textvariable=self.ga_detail_var, bg=WHITE, fg=DARK_GRAY,
                 font=("Arial", 9), anchor="w").pack(fill="x", pady=(6, 0))
        tk.Label(body, textvariable=self.state_warning_var, bg=WHITE,
                 fg=WARN_AMBER, font=("Arial", 9, "bold"), anchor="w",
                 wraplength=780, justify="left").pack(fill="x")

    def _build_bom_card(self, parent) -> None:
        body = _card(parent, "FINAL PRESSED PART — PICK FROM THE CAD BOM")
        columns = [c[0] for c in self.BOM_COLUMNS]
        self.bom_tree = ttk.Treeview(body, columns=columns, show="headings",
                                     height=6, selectmode="browse")
        for key, label, width in self.BOM_COLUMNS:
            self.bom_tree.heading(key, text=label)
            self.bom_tree.column(key, width=width, anchor="w")
        self.bom_tree.pack(fill="x")
        self.bom_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_pick_part())
        tk.Label(body, textvariable=self.part_detail_var, bg=WHITE, fg=DARK_GRAY,
                 font=("Arial", 9), anchor="w").pack(fill="x", pady=(6, 0))

    def _field_grid(self, body) -> tk.Frame:
        """A three-column grid: label, input, optional marker.

        Grid rather than one packed Frame per row. Packing sized the label
        with `width=34` characters, which silently truncated "Wet Part
        Thickness [mm] – Or Transfer GAPS" mid-word, and left rows without a
        target checkbox ending further right than rows with one. A grid sizes
        column 0 to the widest label on its own and lands every input on the
        same left and right edge.
        """
        grid = tk.Frame(body, bg=WHITE)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)   # the input column takes the slack
        return grid

    def _field_label(self, grid, row: int, text: str) -> None:
        tk.Label(grid, text=text, bg=WHITE, fg=DARK_BLUE,
                 font=("Arial", 9, "bold"), anchor="w").grid(
            row=row, column=0, sticky="w", padx=(0, 14), pady=3)

    def _build_machine_card(self, parent) -> None:
        body = _card(parent, "1. MACHINE AND PROCESS DETAILS")
        grid = self._field_grid(body)
        for index, (name, label) in enumerate(engine.MACHINE_FIELDS):
            self._field_label(grid, index, label)
            var = tk.StringVar()
            self.vars[name] = var
            if name == "machine":
                widget = ttk.Combobox(grid, textvariable=var, state="normal",
                                      values=[m.name for m in self.machines])
                widget.bind("<<ComboboxSelected>>",
                            lambda _e: self.on_machine_selected())
            else:
                widget = tk.Entry(grid, textvariable=var, font=("Arial", 10),
                                  relief="flat", highlightthickness=1,
                                  highlightbackground=GRAY_BDR)
            widget.grid(row=index, column=1, sticky="ew", pady=3, ipady=2)
        tk.Label(body, textvariable=self.machine_warning_var, bg=WHITE,
                 fg=RUST_ORANGE, font=("Arial", 9, "bold"), anchor="w",
                 wraplength=780, justify="left").pack(fill="x", pady=(6, 0))

    def _build_production_card(self, parent) -> None:
        body = _card(parent, "2. PRODUCTION DETAILS")
        grid = self._field_grid(body)

        def add_row(index: int, name: str, label: str, *, markable: bool) -> None:
            self._field_label(grid, index, label)
            var = tk.StringVar()
            self.vars[name] = var
            entry = tk.Entry(grid, textvariable=var, font=("Arial", 10),
                             relief="flat", highlightthickness=1,
                             highlightbackground=GRAY_BDR)
            entry.grid(row=index, column=1, sticky="ew", pady=3, ipady=2)
            self.entries[name] = entry
            if not markable:
                return
            target = tk.BooleanVar(value=False)
            self.target_vars[name] = target
            tk.Checkbutton(grid, text="target", variable=target, bg=WHITE,
                           fg=DARK_GRAY, activebackground=WHITE,
                           activeforeground=DARK_GRAY, selectcolor=PALE_BLUE,
                           font=("Arial", 8)).grid(
                row=index, column=2, sticky="w", padx=(10, 0))

        index = 0
        # Material and volume are pulled, not measured -- no target checkbox.
        # They still sit in the same grid, so their inputs line up with the
        # rest instead of running wider by the width of a missing checkbox.
        for name, label in (("material", engine.MATERIAL_LABEL),
                            ("volume", engine.VOLUME_LABEL)):
            add_row(index, name, label, markable=False)
            index += 1
        for name, label in engine.PRODUCTION_FIELDS:
            add_row(index, name, label, markable=True)
            index += 1

        tk.Label(body, textvariable=self.inventor_note_var, bg=WHITE,
                 fg=DARK_GRAY, font=("Arial", 8, "italic"), anchor="w",
                 wraplength=780, justify="left").pack(fill="x", pady=(6, 0))

    def _build_output_card(self, parent) -> None:
        body = _card(parent, "3. OUTPUT")
        row = tk.Frame(body, bg=WHITE)
        row.pack(fill="x")
        tk.Label(row, text="Folder", bg=WHITE, fg=DARK_BLUE, width=10,
                 font=("Arial", 9, "bold"), anchor="w").pack(side="left")
        tk.Entry(row, textvariable=self.out_dir_var, font=("Consolas", 9),
                 relief="flat", highlightthickness=1,
                 highlightbackground=GRAY_BDR).pack(
            side="left", fill="x", expand=True, ipady=2)

        row2 = tk.Frame(body, bg=WHITE)
        row2.pack(fill="x", pady=(4, 0))
        tk.Label(row2, text="File", bg=WHITE, fg=DARK_BLUE, width=10,
                 font=("Arial", 9, "bold"), anchor="w").pack(side="left")
        tk.Entry(row2, textvariable=self.out_name_var, font=("Consolas", 9),
                 relief="flat", highlightthickness=1,
                 highlightbackground=GRAY_BDR).pack(
            side="left", fill="x", expand=True, ipady=2)

        actions = tk.Frame(body, bg=WHITE)
        actions.pack(fill="x", pady=(10, 0))
        self._brand_button(actions, "  Generate Handoff PDF  ",
                           self._on_generate, primary=True).pack(side="left")
        self._brand_button(actions, "  Open Folder  ", self._on_open_folder,
                           primary=False).pack(side="left", padx=(8, 0))

    # ----- Derived fields -----------------------------------------------------

    def _wire_derived_fields(self) -> None:
        self.vars["bone_dry_weight"].trace_add(
            "write", lambda *_a: self._refresh_standard_dry_weight())
        self.target_vars["bone_dry_weight"].trace_add(
            "write", lambda *_a: self._refresh_standard_dry_weight())
        # Detach on an actual VALUE CHANGE, not on a keypress. The obvious
        # `entry.bind("<Key>", ...)` fires for arrow keys and Tab too, so
        # clicking into the field to read the number and pressing Left would
        # silently and permanently stop it tracking -- with no visual cue, on
        # a field whose whole point is that it stays correct.
        self.vars["standard_dry_weight"].trace_add(
            "write", lambda *_a: self.on_standard_dry_weight_edited())

    def _refresh_standard_dry_weight(self) -> None:
        """Recompute while the field is still tracking bone dry weight.

        A value derived from a target is itself a target, so the checkbox
        mirrors too -- until the user overrides the value, at which point both
        become independent.
        """
        if not self._sdw_tracking:
            return
        self._sdw_updating = True
        try:
            self.vars["standard_dry_weight"].set(
                engine.standard_dry_weight(self.vars["bone_dry_weight"].get()))
            self.target_vars["standard_dry_weight"].set(
                self.target_vars["bone_dry_weight"].get())
        finally:
            self._sdw_updating = False

    def on_standard_dry_weight_edited(self) -> None:
        """Changing the field's value detaches it from the derivation for good.

        Guarded by ``_sdw_updating`` so the derivation's own write does not
        count as a user edit -- without that, the first recompute would
        immediately switch tracking off.
        """
        if not self._sdw_updating:
            self._sdw_tracking = False

    def on_machine_selected(self) -> None:
        """Fill both pressures from the picked profile."""
        machine = engine.find_machine(self.machines, self.vars["machine"].get())
        if machine is None:
            self.machine_warning_var.set("")
            return
        # Only fill what the profile actually knows. A press whose pressures
        # have not been recorded in machines.json yet would otherwise wipe
        # values already typed by hand, just by being selected.
        if machine.vacuum_pressure:
            self.vars["vacuum_pressure"].set(machine.vacuum_pressure)
        if machine.press_force:
            self.vars["press_force"].set(machine.press_force)
        if machine.characterized:
            self.machine_warning_var.set("")
        else:
            self.machine_warning_var.set(
                f"{machine.name} is not characterized. The document requires a "
                "machine to be characterized before the first production run."
            )

    # ----- Vault + Inventor ---------------------------------------------------

    def _on_find_ga(self) -> None:
        if not self._ensure_signed_in():
            messagebox.showwarning(
                "Not signed in",
                "Finding an assembly needs a Vault session. Open this tool "
                "from the launcher, or click Reconnect there first.\n\n"
                "You can still fill the form in by hand.",
                parent=self.win)
            return
        from gui.release_workflow import FileSearchDialog
        FileSearchDialog(self)

    def _load_assembly(self, file_name: str) -> None:
        if self.busy or not self._ensure_signed_in():
            return
        self.busy = True
        self.status_var.set(f"Looking up {file_name} in Vault …")

        def worker() -> None:
            try:
                result = asyncio.run(
                    vault_lookup.load_assembly(self.api, self.vault_id, file_name))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("assembly_error", f"{type(exc).__name__}: {exc}"))
                return
            self.q.put(("assembly", result))

        threading.Thread(target=worker, daemon=True).start()

    def _on_pick_part(self) -> None:
        selection = self.bom_tree.selection()
        if not selection:
            return
        index = self.bom_tree.index(selection[0])
        if index >= len(self.children):
            return
        self.part = self.children[index]
        self.vars["material"].set(self.part.get("material", ""))
        self.part_detail_var.set(
            f"{self.part.get('file_name', '')} — Rev "
            f"{self.part.get('revision', '?')}, {self.part.get('state', '?')}")
        self._refresh_state_warning()
        self._read_physical_properties()

    def _read_physical_properties(self) -> None:
        """Pull mass and volume off the model, on the worker thread.

        Each read carries the generation it was dispatched in. Opening
        Inventor takes seconds, and clicking down a BOM comparing parts is
        the normal way to use this -- so a slower read for part A can land
        after a faster one for part B and overwrite B's numbers with A's.
        Silently, on a manufacturing document. ``_handle`` drops any result
        whose generation is no longer current.
        """
        self._inventor_generation += 1
        generation = self._inventor_generation
        path = engine.part_local_path(
            self.part.get("folder_path", ""), self.part.get("file_name", ""),
            workspace_root=self.workspace_root)
        self.inventor_note_var.set("")
        self.status_var.set("Reading mass and volume from Inventor …")

        def worker() -> None:
            try:
                from inventor_automation import read_part_physical_properties
                props = read_part_physical_properties(path)
            except Exception as exc:  # noqa: BLE001
                self.q.put(("inventor_error", (generation, f"{exc}")))
                return
            self.q.put(("inventor", (generation, props)))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_state_warning(self) -> None:
        """The document asks for filenames 'exactly as released'."""
        unreleased = [
            f"{row.get('file_name')} is {row.get('state') or 'in an unknown state'}"
            for row in (self.assembly, self.part)
            if row and row.get("state") and row.get("state") != "Released"
        ]
        self.state_warning_var.set(
            "Not released: " + "; ".join(unreleased) if unreleased else "")

    # ----- Queue drain ----------------------------------------------------------

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self.win.after(150, self._drain_queue)

    def _handle(self, kind: str, payload: Any) -> None:
        if kind == "assembly":
            self.busy = False
            self.assembly = payload["assembly"]
            self.children = payload["children"]
            self._populate_bom(payload.get("children_error", ""))
        elif kind == "assembly_error":
            self.busy = False
            self.status_var.set(f"Vault lookup failed: {payload}")
        elif kind == "inventor":
            generation, props = payload
            # Stale read for a part the user has already clicked away from.
            # Writing it would put another part's mass on this document.
            if generation != self._inventor_generation:
                return
            self.vars["bone_dry_weight"].set(f"{props.mass_g:.2f}")
            self.vars["volume"].set(f"{props.volume_cm3:.2f}")
            self.inventor_note_var.set(
                "Bone dry weight and volume read from the Inventor model. The "
                "mass is only the bone dry weight if the part's material "
                "density is the dried fibre density — check it."
            )
            self.status_var.set("Mass and volume read from the model.")
        elif kind == "inventor_error":
            generation, message = payload
            if generation != self._inventor_generation:
                return
            self.inventor_note_var.set(
                f"Could not read mass and volume — type them in. ({message})")
            self.status_var.set("Inventor read failed; the fields stay manual.")

    def _populate_bom(self, children_error: str) -> None:
        self.bom_tree.delete(*self.bom_tree.get_children())
        for child in self.children:
            self.bom_tree.insert("", "end", values=[
                child.get(key, "") for key, *_ in self.BOM_COLUMNS])

        self.ga_detail_var.set(
            f"{self.assembly.get('file_name', '')} — Rev "
            f"{self.assembly.get('revision', '?')}, "
            f"{self.assembly.get('state', '?')}")
        self._refresh_state_warning()

        directory, note = engine.resolve_output_dir(
            self.assembly.get("folder_path", ""),
            workspace_root=self.workspace_root)
        self.out_dir_var.set(str(directory))
        self.out_name_var.set(
            engine.handoff_filename(self.assembly.get("file_name", "")))

        if children_error:
            self.status_var.set(children_error)
        elif note:
            self.status_var.set(note)
        else:
            self.status_var.set(
                f"{len(self.children)} child files — pick the final pressed part.")

    # ----- Generate ---------------------------------------------------------

    def collect(self) -> engine.HandoffData:
        """The form's current contents as a HandoffData."""
        machine = engine.find_machine(self.machines, self.vars["machine"].get())
        values = {
            name: engine.Value(self.vars[name].get().strip(),
                               bool(self.target_vars[name].get()))
            for name, _ in engine.PRODUCTION_FIELDS
        }
        return engine.HandoffData(
            machine=self.vars["machine"].get().strip(),
            vacuum_pressure=self.vars["vacuum_pressure"].get().strip(),
            press_force=self.vars["press_force"].get().strip(),
            machine_characterized=(machine.characterized if machine else True),
            material=self.vars["material"].get().strip(),
            volume=self.vars["volume"].get().strip(),
            ga_filename=engine.format_file_reference(
                self.assembly.get("file_name", ""),
                self.assembly.get("revision", "")),
            part_filename=engine.format_file_reference(
                self.part.get("file_name", ""), self.part.get("revision", "")),
            **values,
        )

    def confirm_blank_fields(self, missing: list[str]) -> bool:
        """Ask before generating an incomplete document. Blocking is wrong --
        a partly-filled handoff is sometimes exactly what is wanted."""
        return messagebox.askyesno(
            "Some fields are blank",
            "These fields will print as an em dash:\n\n  "
            + "\n  ".join(missing)
            + "\n\nGenerate anyway?",
            parent=self.win)

    def generate(self) -> Optional[Path]:
        """Write the PDF. Returns the path, or None if nothing was written."""
        data = self.collect()
        missing = engine.missing_fields(data)
        if missing and not self.confirm_blank_fields(missing):
            return None

        directory = Path(self.out_dir_var.get().strip() or ".")
        name = self.out_name_var.get().strip() or engine.handoff_filename("")
        try:
            written = render_handoff_pdf(data, directory / name)
        except OSError as exc:
            messagebox.showerror(
                "Could not write the PDF",
                f"{directory / name}\n\n{exc}", parent=self.win)
            return None
        self.status_var.set(f"Wrote {written}")
        return written

    def _on_generate(self) -> None:
        written = self.generate()
        if written is not None:
            messagebox.showinfo("Handoff written", str(written), parent=self.win)

    def _on_open_folder(self) -> None:
        directory = self.out_dir_var.get().strip()
        if not directory or not os.path.isdir(directory):
            messagebox.showwarning(
                "No folder", f"{directory or '(blank)'} is not a folder.",
                parent=self.win)
            return
        # Three-way on sys.platform, matching gui/launcher.py's _on_open_logs.
        # Catching AttributeError from os.startfile and falling through to
        # xdg-open sends macOS at a command it does not have.
        try:
            if sys.platform == "win32":
                os.startfile(directory)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", directory])
            else:
                subprocess.Popen(["xdg-open", directory])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Could not open folder", str(exc), parent=self.win)


def launch_gui(*, api=None, vault_id: str = "", cfg=None, parent=None) -> HandoffGUI:
    """Entry point used by gui/launcher.py."""
    return HandoffGUI(parent=parent, api=api, vault_id=vault_id, cfg=cfg)
