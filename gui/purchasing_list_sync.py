"""
GUI: add BOM parts to the "Engineering Purchased Parts" Microsoft List.

Pick an exported BOM, Scan to see which parts aren't in the list yet (dry run),
then Add them. Graph work runs on a worker thread so the UI stays responsive.

Launched from the launcher dashboard (Engineering Tools). Requires a prior
Microsoft sign-in (`python -m supplier_pricing probe`); if not signed in, the
Scan surfaces a clear message.
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

# Make the project root importable when this is launched as a Toplevel child.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def launch_bom_list_sync_gui(*, cfg=None, parent=None, **_ignored) -> None:
    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    win.title("BOM → Purchased Parts List")
    win.geometry("720x520")

    path_var = tk.StringVar()
    buyonly_var = tk.BooleanVar(value=False)
    update_var = tk.BooleanVar(value=False)
    state: dict = {"report": None, "busy": False}

    top = tk.Frame(win, padx=12, pady=10)
    top.pack(fill="x")
    tk.Label(top, text="BOM file:").grid(row=0, column=0, sticky="w")
    tk.Entry(top, textvariable=path_var, width=64).grid(row=0, column=1, padx=6)

    def browse():
        p = filedialog.askopenfilename(
            title="Select an exported BOM",
            filetypes=[("BOM files", "*.xlsx *.xls *.csv *.txt"), ("All files", "*.*")],
            parent=win)
        if p:
            path_var.set(p)
    tk.Button(top, text="Browse…", command=browse).grid(row=0, column=2)
    tk.Checkbutton(top, text="Only add Buy/Other parts (skip Make)",
                   variable=buyonly_var).grid(row=1, column=1, sticky="w", pady=(6, 0))
    tk.Checkbutton(top, text="Also update parts already in the list "
                   "(fix Title/Vendor/etc.; leaves Cost/Lead)",
                   variable=update_var).grid(row=2, column=1, sticky="w")

    btns = tk.Frame(win, padx=12)
    btns.pack(fill="x")
    scan_btn = tk.Button(btns, text="Scan (dry run)")
    scan_btn.pack(side="left")
    add_btn = tk.Button(btns, text="Add missing to List", state="disabled")
    add_btn.pack(side="left", padx=8)

    out = scrolledtext.ScrolledText(win, height=22, wrap="word")
    out.pack(fill="both", expand=True, padx=12, pady=10)

    def log(msg: str) -> None:
        out.insert("end", msg + "\n")
        out.see("end")

    def post(fn) -> None:
        win.after(0, fn)

    def set_busy(busy: bool) -> None:
        state["busy"] = busy
        scan_btn.config(state="disabled" if busy else "normal")
        cursor = "watch" if busy else ""
        win.config(cursor=cursor)

    def show_report(report: dict, applied: bool) -> None:
        state["report"] = report
        n = len(report["missing"])
        upd = report.get("updated", 0)
        log(f"\n{'ADDED' if applied else 'Found'} {report['created'] if applied else n} "
            f"missing part(s)"
            + (f", {'UPDATED ' if applied else ''}{upd} existing" if update_var.get() else "")
            + f". Already in list: {report['existing_count']}. "
            f"By source: {report['by_source']}")
        for r in report["rows"]:
            mark = "!" if r["status"] == "error" else "+"
            desc = str(r.get("description") or "").strip()
            log(f"   {mark} {r['number']}  [{r['source'] or '-'}]  {r['status']}"
                + (f"  {desc[:50]}" if desc else ""))
        errs = report.get("errors", [])
        if errs:
            log(f"\n{len(errs)} row(s) failed to write:")
            for e in errs:
                log(f"   ! {e['number']}: {e['error'][:140]}")
            if any("403" in e["error"] or "denied" in e["error"].lower() for e in errs):
                log("\n403 / access denied usually means the app registration is "
                    "missing the Sites.ReadWrite.All permission (admin consent), "
                    "or you need to re-run `python -m supplier_pricing probe`.")
        if not applied and n > 0:
            add_btn.config(state="normal", text=f"Add {n} missing to List")
        else:
            add_btn.config(state="disabled", text="Add missing to List")
        set_busy(False)

    def run(apply: bool) -> None:
        if state["busy"]:
            return
        path = path_var.get().strip()
        if not path:
            messagebox.showwarning("No file", "Choose a BOM file first.", parent=win)
            return
        set_busy(True)
        log(f"\n{'Adding' if apply else 'Scanning'}: {os.path.basename(path)} …")

        def work():
            try:
                import bom_list_sync
                df, err = bom_list_sync.bom_dataframe_from_file(path)
                if err:
                    post(lambda: (log(f"BOM parse error: {err}"), set_busy(False)))
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
                post(lambda: (log(f"ERROR: {msg}"), set_busy(False)))

        threading.Thread(target=work, daemon=True).start()

    scan_btn.config(command=lambda: run(False))
    add_btn.config(command=lambda: run(True))

    log("Pick a BOM, then Scan to preview which parts are missing from the "
        "Engineering Purchased Parts list. Nothing is written until you click Add.")

    if parent is None:
        win.mainloop()


if __name__ == "__main__":
    launch_bom_list_sync_gui()
