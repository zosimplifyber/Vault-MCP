"""
Tkinter dialog for the Manufacturing Order Package builder.

Opens as a Toplevel from the launcher (or standalone) with the live Vault
session attached. The user enters a top-level part number, picks where the
output goes, and clicks Build — the worker thread walks the BOM, downloads
PDFs (watermarked RELEASED / FOR REVIEW per item state) and STEP files,
and renders an MFG BOM Excel sheet into the output folder.
"""

from __future__ import annotations

import asyncio
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional

import tkinter as tk
from tkinter import filedialog, messagebox

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from gui.release_workflow import (  # noqa: E402
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY,
    WHITE, RUST_ORANGE, OLIVE_GREEN,
    _pil_available, _resource_path,
)
from gui.search_dialog import SearchDialog  # noqa: E402

# NOTE: this is the root-level engine module (``mfg_package.py``), not this
# GUI file. Python resolves the unqualified import via ``PROJECT_ROOT`` on
# sys.path; this file is reached as ``gui.mfg_package``, so the names don't
# collide.
from mfg_package import build_mfg_package, default_output_dir  # noqa: E402


class MFGPackageGUI:
    """Toplevel wizard. One per launch — closes when the user clicks Close."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        api: Any,
        vault_id: str,
        cfg: Optional[dict[str, Any]] = None,
        prefill_part_number: str = "",
    ) -> None:
        self.api = api
        self.vault_id = vault_id
        self.cfg = cfg or {}

        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self.last_output_dir: Optional[Path] = None
        self._user_picked_outdir = False

        self.win = tk.Toplevel(master)
        self.win.title("Simplifyber — Manufacturing Order Package")
        self.win.geometry("780x640")
        self.win.minsize(640, 540)
        self.win.configure(bg=LIGHT_GRAY)
        # ``SearchDialog`` (gui.search_dialog) reaches into its
        # parent for ``parent.root`` to anchor its modal Toplevel and after()
        # callbacks. Aliasing keeps that contract without renaming our window.
        self.root = self.win

        self._logo_img = None
        self._icon_img = None

        self.pn_var = tk.StringVar(value=prefill_part_number)
        self.outdir_var = tk.StringVar()
        self.wm_mode_var = tk.StringVar(value="auto")

        self.pn_var.trace_add("write", lambda *_: self._refresh_default_outdir())

        self._set_window_icon()
        self._build_ui()
        self._refresh_default_outdir()

        self.win.after(100, self._drain_queue)

    # ----- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        # Header bar
        header = tk.Frame(self.win, bg=DARK_BLUE, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        if _pil_available:
            try:
                from PIL import Image as PILImage, ImageTk
                logo = _resource_path("Simplifyber_Logo_White.png")
                if os.path.isfile(logo):
                    img = PILImage.open(logo).convert("RGBA")
                    h = 36
                    w = int(h * img.width / img.height)
                    img = img.resize((w, h), PILImage.LANCZOS)
                    self._logo_img = ImageTk.PhotoImage(img)
                    tk.Label(header, image=self._logo_img,
                             bg=DARK_BLUE).pack(side="left", padx=14)
            except Exception:  # noqa: BLE001
                pass
        title_box = tk.Frame(header, bg=DARK_BLUE)
        title_box.pack(side="left", expand=True, fill="both")
        tk.Label(
            title_box, text="Manufacturing Order Package",
            font=("Arial", 13, "bold"),
            fg=WHITE, bg=DARK_BLUE,
        ).pack(side="top", anchor="w", pady=(12, 0))
        tk.Label(
            title_box,
            text="MFG BOM + watermarked PDFs + STEP files → one clean folder",
            font=("Arial", 9), fg=PALE_BLUE, bg=DARK_BLUE,
        ).pack(side="top", anchor="w")
        tk.Frame(self.win, bg=MID_BLUE, height=3).pack(fill="x")

        # Inputs
        body = tk.Frame(self.win, bg=LIGHT_GRAY, padx=18, pady=14)
        body.pack(fill="x")
        body.columnconfigure(1, weight=1)

        def lbl(parent, text, **kw):
            return tk.Label(
                parent, text=text, bg=LIGHT_GRAY, fg=DARK_BLUE,
                font=("Arial", 9, "bold"), anchor="w", **kw,
            )

        def ent(parent, var, w=20):
            return tk.Entry(
                parent, textvariable=var, width=w,
                font=("Arial", 10),
                bg=WHITE, relief="solid", bd=1,
                highlightthickness=1,
                highlightbackground=GRAY_BDR,
                highlightcolor=MID_BLUE,
            )

        lbl(body, "Part Number").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        pn_frame = tk.Frame(body, bg=LIGHT_GRAY)
        pn_frame.grid(row=0, column=1, sticky="ew", pady=4)
        pn_frame.columnconfigure(0, weight=1)
        ent(pn_frame, self.pn_var, w=10).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._brand_btn(
            pn_frame, "Search…", self._open_search_dialog, primary=False,
        ).grid(row=0, column=1)

        lbl(body, "Output Folder").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        outdir_frame = tk.Frame(body, bg=LIGHT_GRAY)
        outdir_frame.grid(row=1, column=1, sticky="ew", pady=4)
        outdir_frame.columnconfigure(0, weight=1)
        ent(outdir_frame, self.outdir_var, w=10).grid(
            row=0, column=0, sticky="ew", padx=(0, 6),
        )
        self._brand_btn(
            outdir_frame, "Browse…", self._browse_outdir, primary=False,
        ).grid(row=0, column=1)

        lbl(body, "Watermark").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        wm_frame = tk.Frame(body, bg=LIGHT_GRAY)
        wm_frame.grid(row=2, column=1, sticky="w", pady=4)
        for value, text in (
            ("auto", "Auto (per item state)"),
            ("released", "Force RELEASED"),
            ("review", "Force FOR REVIEW"),
        ):
            tk.Radiobutton(
                wm_frame, text=text, variable=self.wm_mode_var, value=value,
                bg=LIGHT_GRAY, fg=DARK_BLUE,
                activebackground=LIGHT_GRAY, activeforeground=DARK_BLUE,
                selectcolor=WHITE, font=("Arial", 9),
            ).pack(side="left", padx=(0, 14))

        # Action bar
        actions = tk.Frame(self.win, bg=LIGHT_GRAY, padx=18, pady=4)
        actions.pack(fill="x")
        self.btn_build = self._brand_btn(
            actions, "  Build Package  ", self._on_build, primary=True,
        )
        self.btn_build.pack(side="left", padx=(0, 8))
        self.btn_open = self._brand_btn(
            actions, "Open Folder", self._on_open_folder, primary=False,
        )
        self.btn_open.configure(state="disabled")
        self.btn_open.pack(side="left", padx=(0, 8))
        self._brand_btn(
            actions, "Close", self._on_close, primary=False,
        ).pack(side="right")

        # Progress log
        log_card = tk.Frame(
            self.win, bg=WHITE,
            highlightthickness=1, highlightbackground=GRAY_BDR,
        )
        log_card.pack(fill="both", expand=True, padx=18, pady=(8, 8))
        tk.Label(
            log_card, text="  PROGRESS",
            bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 10, "bold"),
            anchor="w", padx=10, pady=6,
        ).pack(fill="x")
        tk.Frame(log_card, bg=MID_BLUE, height=2).pack(fill="x")

        text_frame = tk.Frame(log_card, bg=WHITE)
        text_frame.pack(fill="both", expand=True)
        self.text = tk.Text(
            text_frame, wrap="word", font=("Consolas", 10),
            bg=WHITE, fg="#222222", insertbackground=DARK_BLUE,
            borderwidth=0, highlightthickness=0,
            padx=12, pady=10,
        )
        ys = tk.Scrollbar(
            text_frame, orient="vertical", command=self.text.yview,
            bg=LIGHT_GRAY, troughcolor=PALE_BLUE, activebackground=MID_BLUE,
        )
        self.text.configure(yscrollcommand=ys.set, state="disabled")
        self.text.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.text.tag_configure("ok", foreground="#1F6B2E",
                                font=("Consolas", 10, "bold"))
        self.text.tag_configure("err", foreground=RUST_ORANGE,
                                font=("Consolas", 10, "bold"))
        self.text.tag_configure("dim", foreground=DARK_GRAY)

        # Status bar
        self.status_var = tk.StringVar(
            value="Ready. Enter a part number and click Build Package."
        )
        bar = tk.Frame(
            self.win, bg=PALE_BLUE,
            highlightthickness=1, highlightbackground=GRAY_BDR,
        )
        bar.pack(fill="x", side="bottom")
        tk.Label(
            bar, textvariable=self.status_var,
            bg=PALE_BLUE, fg=DARK_BLUE,
            font=("Arial", 9), anchor="w",
            padx=12, pady=4,
        ).pack(fill="x", side="left", expand=True)

    def _brand_btn(self, parent, text, command, *, primary: bool) -> tk.Button:
        if primary:
            bg, fg = DARK_BLUE, WHITE
            active_bg = MID_BLUE
            font = ("Arial", 10, "bold")
        else:
            bg, fg = MID_BLUE, WHITE
            active_bg = DARK_BLUE
            font = ("Arial", 9, "bold")
        return tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, font=font,
            relief="flat",
            padx=14 if primary else 10,
            pady=6 if primary else 4,
            cursor="hand2",
            activebackground=active_bg, activeforeground=WHITE,
            disabledforeground="#DDDDDD",
            borderwidth=0, highlightthickness=0,
        )

    def _set_window_icon(self) -> None:
        if not _pil_available:
            return
        try:
            from PIL import Image as PILImage, ImageTk
            icon = _resource_path("Simplifyber_Logo.png")
            if not os.path.isfile(icon):
                return
            ico = PILImage.open(icon).convert("RGBA")
            size = max(ico.width, ico.height)
            sq = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
            sq.paste(ico, ((size - ico.width) // 2,
                           (size - ico.height) // 2))
            sq = sq.resize((64, 64), PILImage.LANCZOS)
            self._icon_img = ImageTk.PhotoImage(sq)
            self.win.iconphoto(True, self._icon_img)
        except Exception:  # noqa: BLE001 — icon is cosmetic
            pass

    # ----- Default outdir bookkeeping --------------------------------------

    def _refresh_default_outdir(self) -> None:
        """Track the part number and update the suggested output path —
        but never overwrite a directory the user has already typed/browsed.
        """
        if self._user_picked_outdir:
            return
        pn = self.pn_var.get().strip() or "MFG"
        self.outdir_var.set(str(default_output_dir(pn)))

    def _browse_outdir(self) -> None:
        initial = self.outdir_var.get() or str(Path.home() / "Downloads")
        # Browse from the parent of the suggested path so the user lands in
        # Downloads rather than inside a not-yet-created timestamp folder.
        parent_dir = str(Path(initial).expanduser().parent)
        d = filedialog.askdirectory(
            initialdir=parent_dir if Path(parent_dir).exists() else str(Path.home()),
            title="Choose output folder",
            parent=self.win,
        )
        if d:
            self.outdir_var.set(d)
            self._user_picked_outdir = True

    # ----- Worker plumbing -------------------------------------------------

    def _log(self, msg: str, tag: Optional[str] = None) -> None:
        self.text.configure(state="normal")
        if tag:
            self.text.insert("end", msg + "\n", tag)
        else:
            self.text.insert("end", msg + "\n")
        self.text.configure(state="disabled")
        self.text.see("end")

    def _clear_log(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "done":
                    self._on_done(payload)
        except queue.Empty:
            pass
        self.win.after(100, self._drain_queue)

    # ----- Build action ----------------------------------------------------

    def _on_build(self) -> None:
        if self.busy:
            return
        pn = self.pn_var.get().strip()
        if not pn:
            messagebox.showwarning(
                "Missing part number",
                "Enter a top-level part number first.",
                parent=self.win,
            )
            return
        if not (self.api and self.vault_id):
            messagebox.showerror(
                "No Vault session",
                "No authenticated Vault session is attached to this dialog. "
                "Re-launch from the launcher dashboard after Reconnect.",
                parent=self.win,
            )
            return

        outdir_str = self.outdir_var.get().strip()
        outdir = Path(outdir_str).expanduser() if outdir_str else default_output_dir(pn)

        wm_override: Optional[str] = None
        mode = self.wm_mode_var.get()
        if mode == "released":
            wm_override = "RELEASED"
        elif mode == "review":
            wm_override = "FOR REVIEW"

        self.busy = True
        self.btn_build.configure(state="disabled")
        self.btn_open.configure(state="disabled")
        self._clear_log()
        self.status_var.set(f"Building MFG package for {pn}…")
        self._log(f"Starting MFG package build for {pn}")
        if wm_override:
            self._log(f"Watermark override: {wm_override}", "dim")

        def progress_cb(msg: str) -> None:
            self.q.put(("log", msg))

        def runner() -> None:
            try:
                result = asyncio.run(build_mfg_package(
                    self.api, self.vault_id, pn,
                    output_dir=outdir,
                    on_progress=progress_cb,
                    watermark_override=wm_override,
                ))
            except Exception as exc:  # noqa: BLE001 — surface to UI
                result = {"error": True, "message": f"Unexpected error: {exc}"}
            self.q.put(("done", result))

        threading.Thread(target=runner, daemon=True, name="mfg-package").start()

    def _on_done(self, result: dict[str, Any]) -> None:
        self.busy = False
        self.btn_build.configure(state="normal")
        if result.get("error"):
            self._log("")
            self._log(f"FAILED: {result.get('message', 'unknown error')}", "err")
            self.status_var.set("Build failed.")
            return

        outdir = Path(result["output_dir"])
        self.last_output_dir = outdir
        self.btn_open.configure(state="normal")
        self._log("")
        self._log("DONE", "ok")
        self._log(f"  Folder        : {outdir}")
        self._log(f"  Items walked  : {result.get('items_collected', 0)}")
        self._log(f"  PDFs          : {len(result.get('pdfs') or [])}")
        self._log(f"  STEP files    : {len(result.get('steps') or [])}")
        if result.get("bom_path"):
            self._log(f"  MFG BOM xlsx  : {result['bom_path']}")
        else:
            self._log("  MFG BOM xlsx  : (not generated)", "dim")
        self.status_var.set(f"Done — {outdir}")

    def _on_open_folder(self) -> None:
        d = self.last_output_dir
        if not d or not d.exists():
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(d))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(d)])
            else:
                subprocess.Popen(["xdg-open", str(d)])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Could not open folder", str(exc), parent=self.win,
            )

    # ----- Hooks for the shared SearchDialog -------------------------------

    # SearchDialog calls ``parent._brand_button`` for its primary/secondary
    # buttons; we expose that name here as an alias of our internal one so
    # the dialog's buttons get our exact Simplifyber styling without us
    # having to fork SearchDialog itself.
    def _brand_button(self, parent, text, command, *, primary: bool) -> tk.Button:
        return self._brand_btn(parent, text, command, primary=primary)

    def _ensure_signed_in(self) -> bool:
        """SearchDialog calls this from its worker thread before issuing the
        search. The MFG dialog only opens with a live session attached
        (gated in gui.launcher), so we just confirm what we already have."""
        return bool(self.api and self.vault_id)

    def _open_search_dialog(self) -> None:
        if self.busy:
            messagebox.showwarning(
                "Busy", "Wait for the current build to finish before searching.",
                parent=self.win,
            )
            return
        if not self._ensure_signed_in():
            messagebox.showerror(
                "No Vault session",
                "No authenticated Vault session is attached to this dialog. "
                "Re-launch from the launcher dashboard after Reconnect.",
                parent=self.win,
            )
            return
        SearchDialog(self)

    def set_part_number(self, number: str) -> None:
        """Public hook the SearchDialog calls when the user picks a result."""
        self.pn_var.set(number)
        self.status_var.set(f"Part number set to {number}.")

    def _on_close(self) -> None:
        if self.busy:
            if not messagebox.askyesno(
                "Build in progress",
                "A build is still running. Close the window anyway?",
                parent=self.win,
            ):
                return
        self.win.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def launch_gui(
    *,
    api: Any = None,
    vault_id: str = "",
    cfg: Optional[dict[str, Any]] = None,
    parent: Optional[tk.Misc] = None,
    prefill_part_number: str = "",
) -> None:
    """Open the MFG Package dialog. ``parent`` should be the launcher root
    so the dialog opens as a Toplevel that doesn't take over the main loop;
    pass ``None`` to run standalone."""
    if parent is None:
        root = tk.Tk()
        root.withdraw()
        master: tk.Misc = root
        gui = MFGPackageGUI(
            master, api=api, vault_id=vault_id, cfg=cfg,
            prefill_part_number=prefill_part_number,
        )
        gui.win.protocol("WM_DELETE_WINDOW", lambda: (gui.win.destroy(), root.destroy()))
        root.mainloop()
    else:
        MFGPackageGUI(
            parent, api=api, vault_id=vault_id, cfg=cfg,
            prefill_part_number=prefill_part_number,
        )
