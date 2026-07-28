"""
GUI: check a Vault file's properties against the compliance rules.

Type a file name — ``CD-001659.iam`` — press Check, and every rule in
``file_property_rules.json`` runs against that file's properties. Optionally
walks the CAD BOM and checks every child file too.

Vault work runs on a worker thread so the window stays responsive.

Styling matches the other Simplifyber tools — brand palette, logo header,
section cards and status bar all come from ``gui.release_workflow``.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Any

# Make the project root and scripts/ importable when launched as a Toplevel child.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from gui.release_workflow import (  # noqa: E402
    DARK_BLUE, MID_BLUE, PALE_BLUE, LIGHT_GRAY, GRAY_BDR, DARK_GRAY,
    WHITE, RUST_ORANGE,
    _pil_available, _resource_path,
)

from check_file_properties import (  # noqa: E402
    CONFIG_PATH, DEFAULT_RULES_PATH,
    check_file_name, child_status, load_json,
)

PASS_GREEN = "#1F6B2E"   # legible on white, unlike the pale spreadsheet olive


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


def run_gui(
    *,
    default_config: Path = CONFIG_PATH,
    default_rules: Path = DEFAULT_RULES_PATH,
    parent=None,
    **_ignored,
) -> None:
    """Open the file property checker. Blocks until the window is closed."""
    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    if parent is not None:
        win.transient(parent)
    win.title("Simplifyber — File Property Check")
    win.geometry("820x760")
    win.minsize(700, 620)
    win.configure(bg=LIGHT_GRAY)

    # Rule categories populate the override dropdown. If the rules won't load,
    # fall through — the check itself surfaces the real error.
    try:
        category_keys = sorted(
            (load_json(Path(default_rules)).get("categories") or {}).keys()
        )
    except Exception:                                   # noqa: BLE001
        category_keys = []

    AUTO = "(auto-detect from the file)"

    name_var = tk.StringVar()
    cat_var = tk.StringVar(value=AUTO)
    show_all_var = tk.BooleanVar(value=False)
    recursive_var = tk.BooleanVar(value=False)
    show_kids_var = tk.BooleanVar(value=False)
    status_var = tk.StringVar(value="Ready.")
    state: dict = {"busy": False, "logo": None, "icon": None}

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
                state["icon"] = ImageTk.PhotoImage(
                    square.resize((64, 64), PILImage.LANCZOS))
                win.iconphoto(True, state["icon"])
        except Exception:                               # noqa: BLE001
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
                img = img.resize(
                    (int(target_h * img.width / img.height), target_h),
                    PILImage.LANCZOS)
                state["logo"] = ImageTk.PhotoImage(img)
                tk.Label(header, image=state["logo"], bg=DARK_BLUE
                         ).pack(side="left", padx=16)
        except Exception:                               # noqa: BLE001
            pass

    title_box = tk.Frame(header, bg=DARK_BLUE)
    title_box.pack(side="left", expand=True, fill="both")
    tk.Label(title_box, text="File Property Check",
             font=("Arial", 13, "bold"), fg=WHITE, bg=DARK_BLUE,
             ).pack(side="top", anchor="w", pady=(12, 0))
    tk.Label(title_box, text="Pull a file's properties from Vault and flag "
                             "anything out of compliance",
             font=("Arial", 9), fg=PALE_BLUE, bg=DARK_BLUE
             ).pack(side="top", anchor="w")
    tk.Frame(win, bg=MID_BLUE, height=3).pack(fill="x")

    # ----- file card -------------------------------------------------------
    src = _card(win, "FILE", pady=(14, 8))
    row = tk.Frame(src, bg=WHITE)
    row.pack(fill="x")
    tk.Label(row, text="File name:", bg=WHITE, fg=DARK_BLUE,
             font=("Arial", 9, "bold"), anchor="w", width=11).pack(side="left")
    name_entry = tk.Entry(row, textvariable=name_var, relief="solid", bd=1,
                          font=("Arial", 10), highlightthickness=1,
                          highlightbackground=GRAY_BDR, highlightcolor=MID_BLUE)
    name_entry.pack(side="left", fill="x", expand=True, padx=(4, 8))
    _hint(src, "The name as it appears in Vault, with its extension — "
               "e.g. CD-001659.iam, CD-001624.ipt")

    cat_row = tk.Frame(src, bg=WHITE)
    cat_row.pack(fill="x", pady=(10, 0))
    tk.Label(cat_row, text="Rule set:", bg=WHITE, fg=DARK_BLUE,
             font=("Arial", 9, "bold"), anchor="w", width=11).pack(side="left")
    cat_menu = tk.OptionMenu(cat_row, cat_var, AUTO, *category_keys)
    cat_menu.configure(bg=WHITE, fg=DARK_BLUE, font=("Arial", 9), relief="solid",
                       bd=1, highlightthickness=0, activebackground=PALE_BLUE,
                       anchor="w", cursor="hand2")
    cat_menu["menu"].configure(bg=WHITE, fg=DARK_BLUE, font=("Arial", 9))
    cat_menu.pack(side="left", fill="x", expand=True, padx=(4, 0))

    # ----- options card ----------------------------------------------------
    opts = _card(win, "OPTIONS")
    _check(opts, "Check children (walk the CAD BOM)", recursive_var).pack(fill="x")
    _check(opts, "Show passing child details", show_kids_var).pack(fill="x")
    _check(opts, "Show every property Vault returned", show_all_var).pack(fill="x")

    # Status and action bars are packed before the output card and anchored to
    # the bottom, so the report's natural height can't push them off the edge.
    status = tk.Frame(win, bg=PALE_BLUE, highlightthickness=1,
                      highlightbackground=GRAY_BDR)
    status.pack(fill="x", side="bottom")
    tk.Label(status, textvariable=status_var, bg=PALE_BLUE, fg=DARK_BLUE,
             font=("Arial", 9), anchor="w", padx=12, pady=4
             ).pack(fill="x", side="left", expand=True)

    bar = tk.Frame(win, bg=LIGHT_GRAY)
    bar.pack(fill="x", side="bottom", padx=18, pady=(6, 8))

    # ----- output card -----------------------------------------------------
    out_card = tk.Frame(win, bg=WHITE, highlightthickness=1,
                        highlightbackground=GRAY_BDR)
    out_card.pack(fill="both", expand=True, padx=18, pady=(0, 4))
    tk.Label(out_card, text="  COMPLIANCE REPORT", bg=DARK_BLUE, fg=WHITE,
             font=("Arial", 10, "bold"), anchor="w", padx=10, pady=6).pack(fill="x")
    tk.Frame(out_card, bg=MID_BLUE, height=2).pack(fill="x")

    text_frame = tk.Frame(out_card, bg=WHITE)
    text_frame.pack(fill="both", expand=True)
    out = tk.Text(text_frame, wrap="word", font=("Consolas", 10), height=12,
                  bg=WHITE, fg="#222222", insertbackground=DARK_BLUE,
                  borderwidth=0, highlightthickness=0, padx=12, pady=10)
    ys = tk.Scrollbar(text_frame, orient="vertical", command=out.yview,
                      bg=LIGHT_GRAY, troughcolor=PALE_BLUE, activebackground=MID_BLUE)
    out.configure(yscrollcommand=ys.set, state="disabled")
    out.pack(side="left", fill="both", expand=True)
    ys.pack(side="right", fill="y")
    out.tag_configure("dim", foreground=DARK_GRAY)
    out.tag_configure("head", foreground=DARK_BLUE, font=("Arial", 10, "bold"),
                      spacing1=8, spacing3=2)
    out.tag_configure("pass", foreground=PASS_GREEN, font=("Consolas", 10, "bold"))
    out.tag_configure("fail", foreground=RUST_ORANGE, font=("Consolas", 10, "bold"))
    out.tag_configure("skip", foreground=DARK_GRAY, font=("Consolas", 10, "bold"))
    out.tag_configure("err", foreground=RUST_ORANGE)
    out.tag_configure("summary_ok", foreground=PASS_GREEN,
                      font=("Arial", 10, "bold"), spacing1=6)
    out.tag_configure("summary_fail", foreground=RUST_ORANGE,
                      font=("Arial", 10, "bold"), spacing1=6)

    def write(msg: str = "", tag: str = "") -> None:
        out.configure(state="normal")
        out.insert("end", msg + "\n", tag or ())
        out.see("end")
        out.configure(state="disabled")

    def write_parts(*parts: tuple[str, str]) -> None:
        """Write one line assembled from (text, tag) pairs."""
        out.configure(state="normal")
        for text, tag in parts:
            out.insert("end", text, tag or ())
        out.insert("end", "\n")
        out.see("end")
        out.configure(state="disabled")

    def clear() -> None:
        out.configure(state="normal")
        out.delete("1.0", "end")
        out.configure(state="disabled")

    # ----- action bar ------------------------------------------------------
    check_btn = tk.Button(bar, text="  Check  ", bg=DARK_BLUE, fg=WHITE,
                          font=("Arial", 11, "bold"), relief="flat", padx=16,
                          pady=8, cursor="hand2", activebackground=MID_BLUE,
                          activeforeground=WHITE, disabledforeground="#DDDDDD",
                          borderwidth=0, highlightthickness=0)
    check_btn.pack(side="left")
    tk.Button(bar, text="Close", command=win.destroy, bg=MID_BLUE, fg=WHITE,
              font=("Arial", 9, "bold"), relief="flat", padx=12, pady=4,
              cursor="hand2", activebackground=DARK_BLUE, activeforeground=WHITE,
              borderwidth=0, highlightthickness=0).pack(side="right")

    # ----- rendering -------------------------------------------------------

    def _value(value: Any) -> str:
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "(empty)"
        return str(value)

    def render(result: dict) -> None:
        clear()
        info = result["info"]
        props = info["properties"]
        report = result["report"]

        write(f"{result['file_name']}", "head")
        write(f"  Name          : {props.get('File Name', '(unknown)')}")
        write(f"  Title         : {_value(props.get('Title'))}")
        write(f"  Description   : {_value(props.get('Description (File)'))}")
        write(f"  Category      : {props.get('Category Name', '(unknown)')}")
        write(f"  Revision/State: {props.get('Revision', '?')} / "
              f"{props.get('State', '?')}")
        if info.get("note"):
            write(f"  Note: {info['note']}", "err")

        if not result["category_resolved"]:
            write()
            write("No rule set matched this file's category "
                  f"({result['category_raw'] or 'none'}).", "err")
            write("Add one to file_property_rules.json, or pick a rule set "
                  "above to override.", "dim")
            status_var.set("No rule set matched — pick one to override.")
        else:
            write()
            write(f"Rules: {result['category_resolved']}", "head")
            results = report["results"]
            name_w = max((len(r["property"]) for r in results), default=18)
            name_w = max(name_w, 18)
            for r in results:
                tag = "pass" if r["passed"] else "fail"
                mark = "PASS" if r["passed"] else "FAIL"
                value_str = _value(r["value"])
                write_parts(
                    ("  [", ""), (mark, tag),
                    (f"]  {r['property']:<{name_w}}  ", ""),
                    (value_str, "dim" if value_str == "(empty)" else ""),
                )
                for f in r["failures"]:
                    write(f"          > {f}", "fail")

            summary = f"{report['passed']}/{report['total']} properties passed"
            write(summary, "summary_ok" if report["failed"] == 0 else "summary_fail")
            status_var.set(summary)

        if show_all_var.get():
            write()
            write("Every property Vault returned", "head")
            for k in sorted(props):
                write(f"  {k:<34} = {_value(props[k])}")

        # ----- children ----------------------------------------------------
        if result.get("recursive"):
            write()
            if result.get("children_error"):
                write(result["children_error"], "err")
            elif not result["children"]:
                write("No CAD BOM children found.", "dim")
            else:
                children = result["children"]
                statuses = [child_status(c) for c in children]
                write(f"CAD BOM children ({len(children)} files)", "head")
                show_passing = show_kids_var.get()
                for c, st in zip(children, statuses):
                    st_tag = {"PASS": "pass", "FAIL": "fail",
                              "SKIP": "skip", "ERROR": "fail"}[st]
                    rep = c.get("report") or {}
                    score = (f"{rep.get('passed', 0)}/{rep.get('total', 0)}"
                             if st in ("PASS", "FAIL") else "")
                    cat = (c.get("category_resolved") or c.get("category_raw")
                           or "(no rule set)")
                    write_parts(
                        ("  [", ""), (f"{st:<5}", st_tag),
                        (f"]  {c['file_name']:<24}  {cat:<24}  {score:>7}", ""),
                    )
                    if c.get("error"):
                        write(f"          > {c['error']}", "fail")
                        continue
                    if st == "FAIL" or (show_passing and st in ("PASS", "FAIL")):
                        for r in rep.get("results") or []:
                            if r["passed"] and not show_passing:
                                continue
                            inner_tag = "pass" if r["passed"] else "fail"
                            inner_mark = "PASS" if r["passed"] else "FAIL"
                            value_str = _value(r["value"])
                            write_parts(
                                ("          [", ""), (inner_mark, inner_tag),
                                (f"]  {r['property']:<24}  ", ""),
                                (value_str, "dim" if value_str == "(empty)" else ""),
                            )
                            for f in r["failures"]:
                                write(f"                  > {f}", "fail")

                kids = (f"Children: {statuses.count('PASS')} pass, "
                        f"{statuses.count('FAIL')} fail, "
                        f"{statuses.count('SKIP')} skipped, "
                        f"{statuses.count('ERROR')} errored.")
                clean = statuses.count("FAIL") == 0 and statuses.count("ERROR") == 0
                write(kids, "summary_ok" if clean else "summary_fail")
                status_var.set(f"{status_var.get()} | {kids}")

    def finish(result: dict | None, error: str | None) -> None:
        state["busy"] = False
        check_btn.configure(state="normal")
        name_entry.configure(state="normal")
        if error:
            clear()
            write("Could not check that file.", "head")
            write(error, "err")
            status_var.set("Error.")
            return
        render(result)

    def do_check(*_args) -> None:
        if state["busy"]:
            return
        file_name = name_var.get().strip()
        if not file_name:
            messagebox.showwarning("Missing file name",
                                   "Enter a file name to check, e.g. CD-001659.iam.",
                                   parent=win)
            return

        category = "" if cat_var.get() == AUTO else cat_var.get()
        recursive = recursive_var.get()

        clear()
        write(f"Looking up '{file_name}' in Vault…", "dim")
        if recursive:
            write("Walking the CAD BOM and checking children…", "dim")
        status_var.set("Checking…")
        state["busy"] = True
        check_btn.configure(state="disabled")
        name_entry.configure(state="disabled")

        def worker() -> None:
            try:
                result = asyncio.run(check_file_name(
                    file_name,
                    config_path=Path(default_config),
                    rules_path=Path(default_rules),
                    category_override=category,
                    recursive=recursive,
                ))
                win.after(0, finish, result, None)
            except (RuntimeError, FileNotFoundError, ValueError) as exc:
                win.after(0, finish, None, str(exc))
            except Exception as exc:                    # noqa: BLE001
                win.after(0, finish, None, f"{type(exc).__name__}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    check_btn.configure(command=do_check)
    name_entry.bind("<Return>", do_check)
    name_entry.focus_set()

    # Exposed so tests can drive the render path without a Vault round-trip.
    win.render_for_test = render

    write("Enter a file name above and press Check (or hit Enter).", "dim")

    if parent is None:
        win.mainloop()


# Alias matching the naming the launcher uses for the other tools.
launch_file_property_check_gui = run_gui


if __name__ == "__main__":
    run_gui()
