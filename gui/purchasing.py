"""
Purchasing Sheet GUI — generate Simplifyber-branded purchasing workbooks
from an exported BOM file. Opens as a Toplevel from the launcher dashboard.

A BOM export is the only source: parts are identified by their CAD file
name, which a live Vault item lookup cannot supply.

The actual workbook builder lives in ``bom_purchasing.py``; this module is
just the input form, threading, and status reporting around it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import queue
import webbrowser
from pathlib import Path
from typing import Any, Optional

import tkinter as tk
from tkinter import filedialog, messagebox

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import bom_purchasing  # noqa: E402
import purchasing_reference  # noqa: E402
from vault_rest_api import VaultRestAPI  # noqa: E402

# Reuse brand palette + helpers from the workflow GUI for visual consistency
from gui.release_workflow import (  # noqa: E402
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY,
    WHITE, OLIVE_GREEN, RUST_ORANGE,
    _pil_available, _resource_path,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class PurchasingGUI:
    """Toplevel window — input form for BOM → purchasing sheet generation."""

    def __init__(
        self,
        parent: Optional[tk.Misc] = None,
        *,
        api: Optional[VaultRestAPI] = None,
        vault_id: str = "",
        cfg: Optional[dict[str, Any]] = None,
    ) -> None:
        self.api = api
        self.vault_id = vault_id
        self.cfg = cfg or {}

        if parent is None:
            self.root = tk.Tk()
            self._owns_root = True
        else:
            self.root = tk.Toplevel(parent)
            self._owns_root = False
            self.root.transient(parent)

        self.root.title("Simplifyber — BOM → Purchasing Sheet")
        self.root.geometry("640x600")
        self.root.minsize(580, 540)
        self.root.configure(bg=LIGHT_GRAY)

        # Cross-thread queue for status updates from worker threads
        self.q: queue.Queue[tuple[str, Any]] = queue.Queue()

        # Tk image references (must outlive the function that creates them)
        self._logo_img = None
        self._icon_img = None

        self.busy = False   # guards against a second run mid-flight
        self.bom_var = tk.StringVar()
        self.out_var = tk.StringVar(value=bom_purchasing.default_output_dir())
        self.asm_var = tk.StringVar()
        self.ref_status_var = tk.StringVar(value="Searching for reference file…")
        self.status_var = tk.StringVar(value="Ready.")

        self._set_window_icon()
        self._build_ui()
        self._refresh_reference_status()
        self.root.after(100, self._drain_queue)

    # ----- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        self._build_header()
        self._build_reference_panel()
        self._build_source_panel()
        self._build_output_panel()
        self._build_action_bar()
        self._build_status_bar()

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=DARK_BLUE, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)

        if _pil_available:
            try:
                from PIL import Image as PILImage, ImageTk
                logo_path = _resource_path("Simplifyber_Logo_White.png")
                if os.path.isfile(logo_path):
                    img = PILImage.open(logo_path).convert("RGBA")
                    target_h = 36
                    target_w = int(target_h * img.width / img.height)
                    img = img.resize((target_w, target_h), PILImage.LANCZOS)
                    self._logo_img = ImageTk.PhotoImage(img)
                    tk.Label(header, image=self._logo_img,
                             bg=DARK_BLUE).pack(side="left", padx=16)
            except Exception:  # noqa: BLE001
                pass

        title_box = tk.Frame(header, bg=DARK_BLUE)
        title_box.pack(side="left", expand=True, fill="both")
        tk.Label(
            title_box, text="BOM → Purchasing Sheet",
            font=("Arial", 13, "bold"),
            fg=WHITE, bg=DARK_BLUE,
        ).pack(side="top", anchor="w", pady=(12, 0))
        tk.Label(
            title_box, text="Generate a Simplifyber purchasing workbook for budgeting & buying",
            font=("Arial", 9), fg=PALE_BLUE, bg=DARK_BLUE,
        ).pack(side="top", anchor="w")

        tk.Frame(self.root, bg=MID_BLUE, height=3).pack(fill="x")

    # -- Reference file panel -----------------------------------------------

    def _build_reference_panel(self) -> None:
        card = tk.Frame(self.root, bg=PALE_BLUE,
                        highlightthickness=1, highlightbackground=GRAY_BDR)
        card.pack(fill="x", padx=18, pady=(14, 8))

        tk.Label(
            card, text="  PURCHASED ITEMS REFERENCE (MICROSOFT LIST)",
            bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 10, "bold"),
            anchor="w", padx=10, pady=6,
        ).pack(fill="x")
        tk.Frame(card, bg=MID_BLUE, height=2).pack(fill="x")

        body = tk.Frame(card, bg=PALE_BLUE, padx=14, pady=10)
        body.pack(fill="x")

        self.ref_label = tk.Label(
            body, textvariable=self.ref_status_var,
            bg=PALE_BLUE, fg=DARK_GRAY, font=("Arial", 9),
            anchor="w", justify="left", wraplength=520,
        )
        self.ref_label.pack(fill="x")
        self.signin_btn = tk.Button(
            body, text="Sign in to Microsoft", command=self._sign_in,
            bg=MID_BLUE, fg=WHITE, relief="flat", font=("Arial", 8),
            padx=8, pady=2, cursor="hand2",
        )
        self.signin_btn.pack(anchor="w", pady=(6, 0))

    # -- Source panel (toggle between Vault lookup and File import) ---------

    def _build_source_panel(self) -> None:
        card = tk.Frame(self.root, bg=WHITE,
                        highlightthickness=1, highlightbackground=GRAY_BDR)
        card.pack(fill="x", padx=18, pady=8)

        tk.Label(
            card, text="  BOM SOURCE",
            bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 10, "bold"),
            anchor="w", padx=10, pady=6,
        ).pack(fill="x")
        tk.Frame(card, bg=MID_BLUE, height=2).pack(fill="x")

        body = tk.Frame(card, bg=WHITE, padx=14, pady=10)
        body.pack(fill="x")

        # A BOM export is the only source. The old "look up from Vault by part
        # number" mode is gone: rows are identified by their CAD file name now,
        # which only an export carries.
        self.file_frame = tk.Frame(body, bg=WHITE)
        self.file_frame.pack(fill="x")
        self._labeled_browse(
            self.file_frame, "BOM File:",
            self.bom_var, self._on_browse_bom, row=0,
        )
        tk.Label(
            self.file_frame,
            text="Inventor or Vault export (.xlsx / .xls / .csv / .txt). "
                 "Needs a Filename column — that is what parts are matched on.",
            bg=WHITE, fg=DARK_GRAY, font=("Arial", 8, "italic"),
            anchor="w", wraplength=500, justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))

    # -- Output panel -------------------------------------------------------

    def _build_output_panel(self) -> None:
        card = tk.Frame(self.root, bg=WHITE,
                        highlightthickness=1, highlightbackground=GRAY_BDR)
        card.pack(fill="x", padx=18, pady=8)

        tk.Label(
            card, text="  OUTPUT",
            bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 10, "bold"),
            anchor="w", padx=10, pady=6,
        ).pack(fill="x")
        tk.Frame(card, bg=MID_BLUE, height=2).pack(fill="x")

        body = tk.Frame(card, bg=WHITE, padx=14, pady=10)
        body.pack(fill="x")

        self._labeled_browse(
            body, "Save Output To:",
            self.out_var, self._on_browse_out, row=0, dir_picker=True,
        )
        self._labeled_entry(
            body, "Assembly / Job Label:",
            self.asm_var, width=30, row=1,
        )
        tk.Label(
            body,
            text='Output file: "{Assembly Label}-PurchasingExport.xlsx". '
                 "Leave blank to use the part number / file name.",
            bg=WHITE, fg=DARK_GRAY, font=("Arial", 8, "italic"),
            anchor="w", wraplength=500, justify="left",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))

    # -- Action bar ---------------------------------------------------------

    def _build_action_bar(self) -> None:
        bar = tk.Frame(self.root, bg=LIGHT_GRAY)
        bar.pack(fill="x", padx=18, pady=(8, 4))

        self.generate_btn = tk.Button(
            bar, text="  Generate Purchasing Sheet  ",
            command=self._on_generate,
            bg=DARK_BLUE, fg=WHITE,
            font=("Arial", 11, "bold"),
            relief="flat", padx=16, pady=8, cursor="hand2",
            activebackground=MID_BLUE, activeforeground=WHITE,
            disabledforeground="#DDDDDD",
            borderwidth=0, highlightthickness=0,
        )
        self.generate_btn.pack(side="left")

        tk.Button(
            bar, text="Close", command=self._on_close,
            bg=MID_BLUE, fg=WHITE, font=("Arial", 9, "bold"),
            relief="flat", padx=12, pady=4, cursor="hand2",
            activebackground=DARK_BLUE, activeforeground=WHITE,
            borderwidth=0, highlightthickness=0,
        ).pack(side="right")

    # -- Status bar ---------------------------------------------------------

    def _build_status_bar(self) -> None:
        bar = tk.Frame(self.root, bg=PALE_BLUE,
                       highlightthickness=1, highlightbackground=GRAY_BDR)
        bar.pack(fill="x", side="bottom")
        tk.Label(
            bar, textvariable=self.status_var,
            bg=PALE_BLUE, fg=DARK_BLUE, font=("Arial", 9),
            anchor="w", padx=12, pady=4,
        ).pack(fill="x", side="left", expand=True)

    # ----- Small UI helpers ------------------------------------------------

    def _labeled_entry(self, parent, text, var, *, width, row):
        tk.Label(
            parent, text=text, bg=parent["bg"], fg=DARK_BLUE,
            font=("Arial", 9, "bold"), anchor="w", width=22,
        ).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(
            parent, textvariable=var, width=width,
            relief="solid", bd=1, font=("Arial", 10),
            highlightthickness=1, highlightbackground=GRAY_BDR,
            highlightcolor=MID_BLUE,
        ).grid(row=row, column=1, sticky="w", pady=2, padx=(4, 0))

    def _labeled_browse(self, parent, text, var, command, *, row, dir_picker=False):
        tk.Label(
            parent, text=text, bg=parent["bg"], fg=DARK_BLUE,
            font=("Arial", 9, "bold"), anchor="w", width=22,
        ).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(
            parent, textvariable=var, width=46,
            relief="solid", bd=1, font=("Arial", 9),
            highlightthickness=1, highlightbackground=GRAY_BDR,
            highlightcolor=MID_BLUE,
        ).grid(row=row, column=1, sticky="we", pady=2, padx=(4, 4))
        tk.Button(
            parent, text="Browse…", command=command,
            bg=MID_BLUE, fg=WHITE, relief="flat",
            padx=10, pady=2, cursor="hand2", font=("Arial", 9, "bold"),
            activebackground=DARK_BLUE, activeforeground=WHITE,
            borderwidth=0, highlightthickness=0,
        ).grid(row=row, column=2, sticky="w")
        parent.columnconfigure(1, weight=1)

    def _brand_button(self, parent, text, command, *, primary: bool) -> tk.Button:
        """Brand-styled button factory — also satisfies the contract the
        the action bar uses to build its buttons."""
        if primary:
            bg, fg = DARK_BLUE, WHITE
            active_bg, active_fg = MID_BLUE, WHITE
            font = ("Arial", 10, "bold")
            padx, pady = 14, 6
        else:
            bg, fg = MID_BLUE, WHITE
            active_bg, active_fg = DARK_BLUE, WHITE
            font = ("Arial", 9, "bold")
            padx, pady = 10, 4
        return tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, font=font,
            relief="flat", padx=padx, pady=pady, cursor="hand2",
            activebackground=active_bg, activeforeground=active_fg,
            disabledforeground="#DDDDDD",
            borderwidth=0, highlightthickness=0,
        )

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
        except Exception:  # noqa: BLE001
            pass

    # ----- State updates ---------------------------------------------------

    def _refresh_reference_status(self) -> None:
        cfg = purchasing_reference.resolve_reference_config()
        if purchasing_reference.mslist_is_configured(cfg):
            if purchasing_reference.has_cached_login():
                self.ref_status_var.set(
                    "OK   Microsoft List (Purchased Items) — signed in."
                )
                self.ref_label.configure(fg="#1F6B2E")
            else:
                self.ref_status_var.set(
                    "Not signed in — click 'Sign in to Microsoft' to fill Vendor / Cost / "
                    "Material from the Purchased Items list. Until then those columns are blank."
                )
                self.ref_label.configure(fg=RUST_ORANGE)
            return
        # No Microsoft List configured — fall back to showing the Excel file status.
        info = bom_purchasing.reference_file_status()
        if info.get("found"):
            self.ref_status_var.set(
                f"OK   {os.path.basename(info.get('path', ''))}  "
                f"({info.get('part_count', 0)} parts)"
            )
            self.ref_label.configure(fg="#1F6B2E")
        else:
            self.ref_status_var.set(
                "WARN  No purchasing reference available — cost columns will be blank."
            )
            self.ref_label.configure(fg=RUST_ORANGE)

    def _sign_in(self) -> None:
        cfg = purchasing_reference.resolve_reference_config()
        if not purchasing_reference.mslist_is_configured(cfg):
            messagebox.showinfo("Not configured",
                                "No Microsoft List is configured.", parent=self.root)
            return
        self.signin_btn.configure(state="disabled")
        self.ref_status_var.set("Starting Microsoft sign-in…")

        def worker() -> None:
            try:
                purchasing_reference.acquire_token(
                    cfg["mslist"], interactive=True, printer=self._device_printer)
                self.root.after(0, self._on_sign_in_done, None)
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, self._on_sign_in_done, str(exc))

        threading.Thread(target=worker, daemon=True, name="purchasing-signin").start()

    def _device_printer(self, message: str) -> None:
        def show() -> None:
            try:
                webbrowser.open("https://microsoft.com/devicelogin")
            except Exception:
                pass
            messagebox.showinfo("Sign in to Microsoft", message, parent=self.root)
        self.root.after(0, show)

    def _on_sign_in_done(self, error: Optional[str]) -> None:
        self.signin_btn.configure(state="normal")
        if error:
            messagebox.showerror("Sign-in failed", error, parent=self.root)
        self._refresh_reference_status()

    # ----- Cross-thread queue ----------------------------------------------

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
        elif kind == "done":
            self.busy = False
            self.generate_btn.configure(state="normal")
            ok, info = payload
            if ok:
                out_path = info.get("output_path", "")
                matched = info.get("matched_parts", 0)
                total = info.get("total_purchased_parts", 0)
                unmatched = info.get("unmatched_parts", []) or []
                warnings = info.get("warnings", []) or []

                msg_lines = [
                    f"Saved to:\n{out_path}",
                    "",
                    f"{matched} of {total} purchased parts matched from the reference file.",
                ]
                if unmatched:
                    preview = ", ".join(unmatched[:8])
                    extra = "" if len(unmatched) <= 8 else f" (+{len(unmatched) - 8} more)"
                    msg_lines.append(
                        f"\n{len(unmatched)} part(s) not in the reference file: "
                        f"{preview}{extra}"
                    )
                if warnings:
                    msg_lines.append("\nWarnings:")
                    msg_lines.extend(f"  - {w}" for w in warnings)

                self.status_var.set(f"Saved {os.path.basename(out_path)}.")

                if messagebox.askyesno(
                    "Purchasing sheet generated",
                    "\n".join(msg_lines) + "\n\nOpen the file now?",
                    parent=self.root,
                ):
                    self._open_path(out_path)
            else:
                self.status_var.set(f"Failed: {info}")
                messagebox.showerror("Generation failed", str(info), parent=self.root)
        elif kind == "ref_refresh":
            self._refresh_reference_status()

    # ----- Action handlers -------------------------------------------------

    def _on_browse_bom(self) -> None:
        messagebox.showinfo(
            "Before you export from Inventor",
            "1. Sort the BOM by Description (descending), then renumber the items.\n"
            "2. Use a Structured / All-Levels BOM view (needed for per-assembly costs).\n"
            "3. Include columns —\n"
            "     Required:    Item, Part Number, QTY\n"
            "     Recommended: Description, Unit QTY, BOM Structure, REV, Material\n"
            "4. Export as .xlsx (preferred), tab-delimited .txt, or .csv.",
            parent=self.root,
        )
        path = filedialog.askopenfilename(
            title="Select a BOM export (Inventor or Vault)",
            filetypes=[("BOM export", "*.xls *.xlsx *.csv *.txt"),
                       ("All files", "*.*")],
            parent=self.root,
        )
        if path:
            self.bom_var.set(path)
            if not self.asm_var.get():
                self.asm_var.set(os.path.splitext(os.path.basename(path))[0])

    def _on_browse_out(self) -> None:
        path = filedialog.askdirectory(
            title="Select Output Folder",
            initialdir=self.out_var.get() or os.path.expanduser("~"),
            parent=self.root,
        )
        if path:
            self.out_var.set(path)

    def _on_generate(self) -> None:
        out_dir = self.out_var.get().strip()
        if not out_dir:
            messagebox.showerror(
                "Missing output folder",
                "Please choose where to save the workbook.",
                parent=self.root,
            )
            return

        bom_path = self.bom_var.get().strip()
        if not bom_path:
            messagebox.showerror(
                "Missing BOM file",
                "Select a BOM export (.xlsx, .xls, .csv, or .txt).",
                parent=self.root,
            )
            return
        asm_label = (
            self.asm_var.get().strip()
            or os.path.splitext(os.path.basename(bom_path))[0]
        )
        self._run_file_flow(bom_path, asm_label, out_dir)

    def _run_file_flow(self, bom_path: str, asm_label: str, out_dir: str) -> None:
        self.busy = True
        self.generate_btn.configure(state="disabled")
        self.status_var.set(f"Reading {os.path.basename(bom_path)}…")

        def worker() -> None:
            try:
                result = bom_purchasing.generate_from_file(
                    bom_file_path=bom_path,
                    assembly_number=asm_label,
                    output_dir=out_dir,
                )
                if result.get("error"):
                    self.q.put(("done", (False, result.get("message", "Unknown build error"))))
                    return
                self.q.put(("done", (True, result)))
            except Exception as exc:  # noqa: BLE001
                logger.exception("File purchasing flow failed")
                self.q.put(("done", (False, str(exc))))

        threading.Thread(target=worker, daemon=True, name="purchasing-file").start()

    def _open_path(self, path: str) -> None:
        if not path or not os.path.exists(path):
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Could not open file", str(exc), parent=self.root,
            )

    def _on_close(self) -> None:
        self.root.destroy()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def launch_purchasing_gui(
    *,
    api: Optional[VaultRestAPI] = None,
    vault_id: str = "",
    cfg: Optional[dict[str, Any]] = None,
    parent: Optional[tk.Misc] = None,
) -> PurchasingGUI:
    """Open the Purchasing Sheet window. Pass ``parent`` (the launcher root)
    to open as a Toplevel that shares the launcher's mainloop."""
    return PurchasingGUI(parent=parent, api=api, vault_id=vault_id, cfg=cfg)


if __name__ == "__main__":
    # Standalone debug entry — file-only mode (no Vault session).
    gui = launch_purchasing_gui()
    gui.root.mainloop()
