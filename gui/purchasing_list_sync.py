"""
GUI: add BOM parts to the "Engineering Purchased Parts" Microsoft List.

Pick an exported BOM, Scan to see which parts aren't in the list yet (dry run),
then Add them. Graph work runs on a worker thread so the UI stays responsive.

Launched from the launcher dashboard (Engineering Tools). Requires a prior
Microsoft sign-in (`python -m supplier_pricing probe`); if not signed in, the
Scan surfaces a clear message.

Styling matches the other Simplifyber tools — brand palette, logo header,
section cards and status bar all come from ``gui.release_workflow``.
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

# Make the project root importable when this is launched as a Toplevel child.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gui.release_workflow import (  # noqa: E402
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY,
    WHITE, RUST_ORANGE,
    _pil_available, _resource_path,
)


def summary_line(report: dict, *, applied: bool, update_existing: bool = False) -> str:
    """One-line result summary for a scan / add run.

    Keeps the BOM-side counts (how many of THIS BOM's parts were checked and
    already present) distinct from the size of the list itself — quoting only
    existing_count read as though that many BOM parts had been found.
    """
    n = report.get("created", 0) if applied else len(report.get("missing", []))
    parts = [f"{'ADDED' if applied else 'Found'} {n} missing part(s) "
             f"of {report.get('checked', 0)} BOM part(s) checked"]
    if update_existing:
        parts.append(f", {report.get('updated', 0)} existing updated")
    parts.append(f"; {report.get('already_present', 0)} already in the list "
                 f"(which holds {report.get('existing_count', 0)} part numbers).")
    if report.get("by_source"):
        parts.append(f" By source: {report['by_source']}")
    return "".join(parts)


# --------------------------------------------------------------------------- ui bits

def _card(parent, title: str, *, bg: str = WHITE, pady=(0, 10)):
    """A bordered panel with the brand's dark-blue caption bar. Returns its body."""
    card = tk.Frame(parent, bg=bg, highlightthickness=1, highlightbackground=GRAY_BDR)
    card.pack(fill="x", padx=18, pady=pady)
    tk.Label(card, text=f"  {title}", bg=DARK_BLUE, fg=WHITE,
             font=("Arial", 10, "bold"), anchor="w", padx=10, pady=6).pack(fill="x")
    tk.Frame(card, bg=MID_BLUE, height=2).pack(fill="x")
    body = tk.Frame(card, bg=bg, padx=14, pady=10)
    body.pack(fill="both", expand=True)
    return body


def _hint(parent, text: str) -> None:
    tk.Label(parent, text=text, bg=parent["bg"], fg=DARK_GRAY,
             font=("Arial", 8, "italic"), anchor="w", justify="left",
             wraplength=620).pack(fill="x", pady=(4, 0))


def _check(parent, text: str, var) -> tk.Checkbutton:
    return tk.Checkbutton(parent, text=text, variable=var, bg=parent["bg"],
                          fg=DARK_BLUE, activebackground=parent["bg"],
                          activeforeground=DARK_BLUE, selectcolor=PALE_BLUE,
                          font=("Arial", 10), anchor="w")


def launch_bom_list_sync_gui(*, cfg=None, parent=None, **_ignored) -> None:
    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    if parent is not None:
        win.transient(parent)
    win.title("Simplifyber — BOM → Purchased Parts List")
    win.geometry("760x740")
    win.minsize(680, 640)
    win.configure(bg=LIGHT_GRAY)

    import bom_list_sync

    path_var = tk.StringVar()
    buyonly_var = tk.BooleanVar(value=False)
    update_var = tk.BooleanVar(value=False)
    status_var = tk.StringVar(value="Ready.")
    check_var = tk.StringVar(value="")
    state: dict = {"report": None, "busy": False, "logo": None, "icon": None,
                   "columns_ok": False}

    # ----- window icon -----------------------------------------------------
    if _pil_available:
        try:
            from PIL import Image as PILImage, ImageTk
            icon_path = _resource_path("Simplifyber_Logo.png")
            if os.path.isfile(icon_path):
                ico = PILImage.open(icon_path).convert("RGBA")
                size = max(ico.width, ico.height)
                square = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
                square.paste(ico, ((size - ico.width) // 2, (size - ico.height) // 2))
                state["icon"] = ImageTk.PhotoImage(square.resize((64, 64), PILImage.LANCZOS))
                win.iconphoto(True, state["icon"])
        except Exception:      # noqa: BLE001
            pass

    # ----- header ----------------------------------------------------------
    header = tk.Frame(win, bg=DARK_BLUE, height=64)
    header.pack(fill="x")
    header.pack_propagate(False)
    if _pil_available:
        try:
            from PIL import Image as PILImage, ImageTk
            logo_path = _resource_path("Simplifyber_Logo_White.png")
            if os.path.isfile(logo_path):
                img = PILImage.open(logo_path).convert("RGBA")
                target_h = 36
                img = img.resize((int(target_h * img.width / img.height), target_h),
                                 PILImage.LANCZOS)
                state["logo"] = ImageTk.PhotoImage(img)
                tk.Label(header, image=state["logo"], bg=DARK_BLUE).pack(side="left", padx=16)
        except Exception:      # noqa: BLE001
            pass

    title_box = tk.Frame(header, bg=DARK_BLUE)
    title_box.pack(side="left", expand=True, fill="both")
    tk.Label(title_box, text="BOM → Purchased Parts List",
             font=("Arial", 13, "bold"), fg=WHITE, bg=DARK_BLUE,
             ).pack(side="top", anchor="w", pady=(12, 0))
    tk.Label(title_box, text="Add parts that aren't in the Engineering Purchased Parts list yet",
             font=("Arial", 9), fg=PALE_BLUE, bg=DARK_BLUE).pack(side="top", anchor="w")
    tk.Frame(win, bg=MID_BLUE, height=3).pack(fill="x")

    # ----- BOM source card -------------------------------------------------
    src = _card(win, "BOM SOURCE", pady=(14, 8))
    row = tk.Frame(src, bg=WHITE)
    row.pack(fill="x")
    tk.Label(row, text="BOM file:", bg=WHITE, fg=DARK_BLUE,
             font=("Arial", 9, "bold"), anchor="w", width=10).pack(side="left")
    tk.Entry(row, textvariable=path_var, relief="solid", bd=1, font=("Arial", 10),
             highlightthickness=1, highlightbackground=GRAY_BDR,
             highlightcolor=MID_BLUE).pack(side="left", fill="x", expand=True, padx=(4, 8))

    def browse():
        # Start in the Vault working folder BOMs are exported to; once a file
        # has been picked, reopen wherever that came from.
        current = path_var.get().strip()
        initial = (os.path.dirname(current) if current and os.path.isdir(
            os.path.dirname(current)) else bom_list_sync.default_bom_dir())
        p = filedialog.askopenfilename(
            title="Select an exported BOM",
            filetypes=[("BOM files", "*.xlsx *.xls *.csv *.txt"), ("All files", "*.*")],
            initialdir=initial,
            parent=win)
        if p:
            path_var.set(p)

    tk.Button(row, text="Browse…", command=browse, bg=MID_BLUE, fg=WHITE,
              relief="flat", font=("Arial", 8), padx=8, pady=2, cursor="hand2",
              activebackground=DARK_BLUE, activeforeground=WHITE,
              borderwidth=0, highlightthickness=0).pack(side="left")
    _hint(src, "Inventor or Vault export — .xlsx / .xls / .csv / .txt")

    # Which columns the export has to carry, and what each extra one fills in.
    fields = tk.Frame(src, bg=WHITE)
    fields.pack(fill="x", pady=(8, 0))
    tk.Label(fields, text="Required:", bg=WHITE, fg=DARK_BLUE,
             font=("Arial", 9, "bold"), anchor="w", width=10).grid(row=0, column=0, sticky="w")
    tk.Label(fields, text=", ".join(bom_list_sync.REQUIRED_BOM_FIELDS),
             bg=WHITE, fg=DARK_BLUE, font=("Arial", 9), anchor="w",
             ).grid(row=0, column=1, sticky="w")
    tk.Label(fields, text="Optional:", bg=WHITE, fg=DARK_GRAY,
             font=("Arial", 9, "bold"), anchor="w", width=10).grid(row=1, column=0, sticky="w")
    tk.Label(fields, text=", ".join(bom_list_sync.OPTIONAL_BOM_FIELDS),
             bg=WHITE, fg=DARK_GRAY, font=("Arial", 9), anchor="w",
             wraplength=560, justify="left").grid(row=1, column=1, sticky="w")

    check_label = tk.Label(src, textvariable=check_var, bg=WHITE, fg=DARK_GRAY,
                           font=("Arial", 9, "bold"), anchor="w", justify="left",
                           wraplength=620)
    check_label.pack(fill="x", pady=(8, 0))

    # ----- options card ----------------------------------------------------
    opts = _card(win, "OPTIONS")
    _check(opts, "Only add Buy/Other parts (skip Make)", buyonly_var).pack(fill="x")
    _check(opts, "Also update parts already in the list "
                 "(fix Title/Vendor/etc.; leaves Cost/Lead)", update_var).pack(fill="x")

    # Status bar and action bar are packed BEFORE the output card and anchored
    # to the bottom: the log's natural height would otherwise claim the window
    # and push them off the edge.
    status = tk.Frame(win, bg=PALE_BLUE, highlightthickness=1, highlightbackground=GRAY_BDR)
    status.pack(fill="x", side="bottom")
    tk.Label(status, textvariable=status_var, bg=PALE_BLUE, fg=DARK_BLUE,
             font=("Arial", 9), anchor="w", padx=12, pady=4).pack(fill="x", side="left",
                                                                  expand=True)

    bar = tk.Frame(win, bg=LIGHT_GRAY)
    bar.pack(fill="x", side="bottom", padx=18, pady=(6, 8))

    # ----- output card -----------------------------------------------------
    out_card = tk.Frame(win, bg=WHITE, highlightthickness=1, highlightbackground=GRAY_BDR)
    out_card.pack(fill="both", expand=True, padx=18, pady=(0, 4))
    tk.Label(out_card, text="  OUTPUT", bg=DARK_BLUE, fg=WHITE,
             font=("Arial", 10, "bold"), anchor="w", padx=10, pady=6).pack(fill="x")
    tk.Frame(out_card, bg=MID_BLUE, height=2).pack(fill="x")

    text_frame = tk.Frame(out_card, bg=WHITE)
    text_frame.pack(fill="both", expand=True)
    out = tk.Text(text_frame, wrap="word", font=("Consolas", 10), height=10,
                  bg=WHITE, fg="#222222", insertbackground=DARK_BLUE,
                  borderwidth=0, highlightthickness=0, padx=12, pady=10)
    ys = tk.Scrollbar(text_frame, orient="vertical", command=out.yview,
                      bg=LIGHT_GRAY, troughcolor=PALE_BLUE, activebackground=MID_BLUE)
    out.configure(yscrollcommand=ys.set, state="disabled")
    out.pack(side="left", fill="both", expand=True)
    ys.pack(side="right", fill="y")
    out.tag_configure("dim", foreground=DARK_GRAY)
    out.tag_configure("info", foreground=MID_BLUE)
    out.tag_configure("head", foreground=DARK_BLUE, font=("Arial", 10, "bold"),
                      spacing1=8, spacing3=2)
    out.tag_configure("ok", foreground="#1F6B2E")
    out.tag_configure("err", foreground=RUST_ORANGE, font=("Consolas", 10, "bold"))

    def log(msg: str, tag: str = "") -> None:
        out.configure(state="normal")
        out.insert("end", msg + "\n", tag or ())
        out.see("end")
        out.configure(state="disabled")

    # ----- action bar (frame packed above, with the status bar) -------------
    scan_btn = tk.Button(bar, text="  Scan (dry run)  ", bg=DARK_BLUE, fg=WHITE,
                         font=("Arial", 11, "bold"), relief="flat", padx=16, pady=8,
                         cursor="hand2", activebackground=MID_BLUE,
                         activeforeground=WHITE, disabledforeground="#DDDDDD",
                         borderwidth=0, highlightthickness=0)
    scan_btn.pack(side="left")
    add_btn = tk.Button(bar, text="Add missing to List", state="disabled",
                        bg=MID_BLUE, fg=WHITE, font=("Arial", 9, "bold"),
                        relief="flat", padx=12, pady=6, cursor="hand2",
                        activebackground=DARK_BLUE, activeforeground=WHITE,
                        disabledforeground="#DDDDDD",
                        borderwidth=0, highlightthickness=0)
    add_btn.pack(side="left", padx=8)
    tk.Button(bar, text="Close", command=win.destroy, bg=MID_BLUE, fg=WHITE,
              font=("Arial", 9, "bold"), relief="flat", padx=12, pady=4,
              cursor="hand2", activebackground=DARK_BLUE, activeforeground=WHITE,
              borderwidth=0, highlightthickness=0).pack(side="right")

    # ----- behaviour -------------------------------------------------------

    def check_columns(*_args) -> None:
        """Read the picked file's header row and report what it carries."""
        path = path_var.get().strip()
        state["columns_ok"] = False
        if not path:
            check_var.set("")
            return
        if not os.path.isfile(path):
            check_var.set("File not found.")
            check_label.config(fg=RUST_ORANGE)
            return
        try:
            columns = bom_list_sync.bom_file_columns(path)
        except Exception as exc:                        # noqa: BLE001
            check_var.set(f"Could not read this file: {exc}")
            check_label.config(fg=RUST_ORANGE)
            return
        result = bom_list_sync.check_bom_columns(columns)
        state["columns_ok"] = result["ok"]
        if not result["ok"]:
            check_var.set("Missing required column(s): "
                          + ", ".join(result["missing_required"])
                          + " — re-export the BOM with those columns.")
            check_label.config(fg=RUST_ORANGE)
        elif result["missing_optional"]:
            check_var.set("All required columns found. Not in this export: "
                          + ", ".join(result["missing_optional"])
                          + " — those list fields stay blank.")
            check_label.config(fg=DARK_GRAY)
        else:
            check_var.set("All required and optional columns found.")
            check_label.config(fg="#1F6B2E")

    path_var.trace_add("write", check_columns)

    def post(fn) -> None:
        win.after(0, fn)

    def set_busy(busy: bool) -> None:
        state["busy"] = busy
        scan_btn.config(state="disabled" if busy else "normal")
        win.config(cursor="watch" if busy else "")

    def show_report(report: dict, applied: bool) -> None:
        state["report"] = report
        n = len(report["missing"])
        log(summary_line(report, applied=applied, update_existing=update_var.get()),
            "head")
        for r in report["rows"]:
            failed = r["status"] == "error"
            desc = str(r.get("description") or "").strip()
            log(f"   {'!' if failed else '+'} {r['number']}  [{r['source'] or '-'}]  "
                f"{r['status']}" + (f"  {desc[:50]}" if desc else ""),
                "err" if failed else ("ok" if applied else ""))
        errs = report.get("errors", [])
        if errs:
            log(f"\n{len(errs)} row(s) failed to write:", "err")
            for e in errs:
                log(f"   ! {e['number']}: {e['error'][:140]}", "err")
            if any("403" in e["error"] or "denied" in e["error"].lower() for e in errs):
                log("\n403 / access denied usually means the app registration is "
                    "missing the Sites.ReadWrite.All permission (admin consent), "
                    "or you need to re-run `python -m supplier_pricing probe`.", "dim")
        if not applied and n > 0:
            add_btn.config(state="normal", text=f"Add {n} missing to List")
        else:
            add_btn.config(state="disabled", text="Add missing to List")
        status_var.set(f"{'Added' if applied else 'Scanned'} — "
                       f"{report['created'] if applied else n} "
                       f"{'added' if applied else 'missing'}.")
        set_busy(False)

    def run(apply: bool) -> None:
        if state["busy"]:
            return
        path = path_var.get().strip()
        if not path:
            messagebox.showwarning("No file", "Choose a BOM file first.", parent=win)
            return
        check_columns()
        if not state["columns_ok"]:
            messagebox.showwarning("BOM columns", check_var.get(), parent=win)
            return
        set_busy(True)
        status_var.set("Adding to the list…" if apply else "Scanning…")
        log(f"\n{'Adding' if apply else 'Scanning'}: {os.path.basename(path)} …", "info")

        def work():
            try:
                df, err = bom_list_sync.bom_dataframe_from_file(path)
                if err:
                    post(lambda: (log(f"BOM parse error: {err}", "err"),
                                  status_var.set("BOM could not be read."),
                                  set_busy(False)))
                    return
                from supplier_pricing.cli import _connect_client
                client = _connect_client()
                sources = {"Buy", "Other"} if buyonly_var.get() else None
                report = bom_list_sync.add_missing_bom_rows(
                    client, df, dry_run=not apply, sources=sources,
                    update_existing=update_var.get())
                post(lambda: show_report(report, apply))
            except Exception as exc:                       # noqa: BLE001
                msg = str(exc)
                if "not signed in" in msg.lower():
                    msg += ("\n\nRun once in a terminal:\n"
                            "    python -m supplier_pricing probe")
                post(lambda: (log(f"ERROR: {msg}", "err"),
                              status_var.set("Failed — see the output above."),
                              set_busy(False)))

        threading.Thread(target=work, daemon=True).start()

    scan_btn.config(command=lambda: run(False))
    add_btn.config(command=lambda: run(True))

    log("Pick a BOM, then Scan to preview which parts are missing from the "
        "Engineering Purchased Parts list. Nothing is written until you click "
        "Add.", "dim")

    if parent is None:
        win.mainloop()


if __name__ == "__main__":
    launch_bom_list_sync_gui()
