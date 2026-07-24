"""
Simplifyber — standalone BOM -> Purchasing Sheet tool.

A self-contained Tk GUI that turns an Inventor or Vault BOM export into a
Simplifyber purchasing workbook using the shared ``bom_purchasing`` engine. Needs
NO Vault connection, MCP server, or app.py — only a BOM export file (and,
optionally, the purchased-items reference file). Build to a one-file .exe with
build_purchasing_exe.bat.
"""
from __future__ import annotations

import os
import threading
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox

import bom_purchasing as bp
import purchasing_reference as pref

DARK_BLUE, MID_BLUE, LIGHT_GRAY, PALE_BLUE = "#1F3864", "#2E75B6", "#F2F2F2", "#EAF3FB"

REMINDER = (
    "Before you export the BOM from Inventor:\n"
    "1. Sort the BOM by Description (descending), then renumber the items.\n"
    "2. Use a Structured / All-Levels BOM view (needed for per-assembly costs).\n"
    "3. Include columns —\n"
    "     Required:    Item, Part Number, QTY\n"
    "     Recommended: Description, Unit QTY, BOM Structure, REV,\n"
    "                  Material, Material Finish\n"
    "4. Export as .xlsx (preferred), tab-delimited .txt, or .csv."
)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Simplifyber — BOM to Purchasing Sheet")
        self.geometry("600x520")
        self.resizable(False, False)
        self.configure(bg=LIGHT_GRAY)
        self._icon_img = None
        self._set_window_icon()
        self._build_ui()
        self._detect_reference_file()

    def _set_window_icon(self):
        try:
            path = bp._resource_path("Simplifyber_Logo.png")
            if os.path.isfile(path):
                self._icon_img = tk.PhotoImage(file=path)
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass

    def _label(self, parent, text):
        return tk.Label(parent, text=text, bg=LIGHT_GRAY, font=("Arial", 10, "bold"), anchor="w")

    def _browse_row(self, parent, var, row, cmd):
        f = tk.Frame(parent, bg=LIGHT_GRAY)
        f.grid(row=row, column=0, sticky="ew", pady=(2, 12))
        f.columnconfigure(0, weight=1)
        tk.Entry(f, textvariable=var, width=48, relief="solid", bd=1).grid(row=0, column=0, sticky="ew")
        tk.Button(f, text="Browse…", command=cmd, bg=MID_BLUE, fg="white", relief="flat",
                  padx=10, pady=2, cursor="hand2").grid(row=0, column=1, padx=(6, 0))

    def _build_ui(self):
        header = tk.Frame(self, bg=DARK_BLUE, height=60)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="BOM → Purchasing Sheet", font=("Arial", 13, "bold"),
                 fg="white", bg=DARK_BLUE).pack(side="left", padx=16, expand=True)
        tk.Frame(self, bg=MID_BLUE, height=3).pack(fill="x")

        body = tk.Frame(self, bg=LIGHT_GRAY, padx=28, pady=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        ref = tk.Frame(body, bg=PALE_BLUE, padx=12, pady=8)
        ref.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ref.columnconfigure(0, weight=1)
        tk.Label(ref, text="Purchased Items Reference File:", bg=PALE_BLUE,
                 font=("Arial", 9, "bold"), anchor="w").grid(row=0, column=0, sticky="w")
        self.ref_status_var = tk.StringVar(value="Searching…")
        self.ref_label = tk.Label(ref, textvariable=self.ref_status_var, bg=PALE_BLUE,
                                  font=("Arial", 9), anchor="w", wraplength=440, justify="left")
        self.ref_label.grid(row=1, column=0, sticky="w", pady=(2, 4))
        self.signin_btn = tk.Button(ref, text="Sign in to Microsoft", command=self._sign_in,
                                    bg=MID_BLUE, fg="white", relief="flat", font=("Arial", 8),
                                    padx=8, pady=1, cursor="hand2")
        self.signin_btn.grid(row=2, column=0, sticky="w")

        self.bom_var = tk.StringVar()
        self._label(body, "BOM File (Inventor or Vault export):").grid(row=1, column=0, sticky="w")
        self._browse_row(body, self.bom_var, 2, self._browse_bom)

        self.out_var = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads"))
        self._label(body, "Save Output To:").grid(row=3, column=0, sticky="w")
        self._browse_row(body, self.out_var, 4, self._browse_out)

        self.asm_var = tk.StringVar()
        self._label(body, "Assembly / Job Number:").grid(row=5, column=0, sticky="w")
        af = tk.Frame(body, bg=LIGHT_GRAY)
        af.grid(row=6, column=0, sticky="w", pady=(2, 16))
        tk.Entry(af, textvariable=self.asm_var, width=30, relief="solid", bd=1).pack()

        self.btn = tk.Button(body, text="  Generate Purchasing Sheet  ", command=self._generate,
                             bg=DARK_BLUE, fg="white", font=("Arial", 11, "bold"), relief="flat",
                             padx=16, pady=8, cursor="hand2",
                             activebackground=MID_BLUE, activeforeground="white")
        self.btn.grid(row=7, column=0, sticky="w")
        self.status_var = tk.StringVar()
        tk.Label(body, textvariable=self.status_var, bg=LIGHT_GRAY, fg=MID_BLUE,
                 font=("Arial", 9), anchor="w", wraplength=520, justify="left"
                 ).grid(row=8, column=0, sticky="w", pady=(8, 0))

    def _detect_reference_file(self):
        cfg = pref.resolve_reference_config()
        if pref.mslist_is_configured(cfg):
            if pref.has_cached_login():
                self.ref_status_var.set("✓  Microsoft List (Purchased Items) — signed in")
                self.ref_label.config(fg="#1F6B2E")
            else:
                self.ref_status_var.set(
                    "Microsoft List (Purchased Items): click 'Sign in to Microsoft' to use it "
                    "(otherwise the Excel file is used if found).")
                self.ref_label.config(fg="#8B4000")
        else:
            path = bp.find_purchased_items_file()
            if path:
                self.ref_status_var.set(f"✓  Excel reference: {os.path.basename(path)}")
                self.ref_label.config(fg="#1F6B2E")
            else:
                self.ref_status_var.set("No purchasing reference found — cost columns left blank.")
                self.ref_label.config(fg="#8B4000")

    def _sign_in(self):
        cfg = pref.resolve_reference_config()
        if not pref.mslist_is_configured(cfg):
            messagebox.showinfo("Not configured", "No Microsoft List is configured for this build.")
            return
        self.signin_btn.config(state="disabled")
        self.ref_status_var.set("Starting Microsoft sign-in…")

        def run():
            try:
                pref.acquire_token(cfg["mslist"], interactive=True, printer=self._device_printer)
                self.after(0, self._on_sign_in_done, None)
            except Exception as exc:  # noqa: BLE001
                self.after(0, self._on_sign_in_done, str(exc))

        threading.Thread(target=run, daemon=True).start()

    def _device_printer(self, message):
        # Called from the worker thread; marshal the code prompt onto the UI thread.
        def show():
            try:
                webbrowser.open("https://microsoft.com/devicelogin")
            except Exception:
                pass
            messagebox.showinfo("Sign in to Microsoft", message)
        self.after(0, show)

    def _on_sign_in_done(self, error):
        self.signin_btn.config(state="normal")
        if error:
            self.ref_status_var.set("Sign-in failed — see dialog.")
            self.ref_label.config(fg="#8B4000")
            messagebox.showerror("Sign-in failed", error)
            return
        self.ref_status_var.set("✓  Microsoft List (Purchased Items) — signed in")
        self.ref_label.config(fg="#1F6B2E")

    def _browse_bom(self):
        messagebox.showinfo("Before you export from Inventor", REMINDER)
        path = filedialog.askopenfilename(
            title="Select a BOM export (Inventor or Vault)",
            filetypes=[("BOM export", "*.xls *.xlsx *.csv *.txt"), ("All files", "*.*")])
        if path:
            self.bom_var.set(path)
            if not self.asm_var.get():
                self.asm_var.set(os.path.splitext(os.path.basename(path))[0])

    def _browse_out(self):
        path = filedialog.askdirectory(title="Select Output Folder")
        if path:
            self.out_var.set(path)

    def _generate(self):
        bom_path = self.bom_var.get().strip()
        out_dir = self.out_var.get().strip() or os.path.join(os.path.expanduser("~"), "Downloads")
        asm = self.asm_var.get().strip()
        if not bom_path:
            messagebox.showwarning("Missing input", "Please select a BOM file.")
            return
        if not asm:
            messagebox.showwarning("Missing input", "Please enter an assembly / job number.")
            return
        self.btn.config(state="disabled")
        self.status_var.set("Generating…")
        ref = ""   # reference comes from the Microsoft List (or auto-found Excel)

        def run():
            try:
                result = bp.generate_from_file(bom_path, asm, out_dir, reference_path=ref)
            except Exception as exc:  # noqa: BLE001
                result = {"error": True, "message": str(exc)}
            self.after(0, self._on_done, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_done(self, result):
        self.btn.config(state="normal")
        if result.get("error"):
            self.status_var.set("Error — see dialog.")
            messagebox.showerror("Error", str(result.get("message")))
            return
        path = result.get("output_path", "")
        matched = result.get("matched_parts", 0)
        total = result.get("total_purchased_parts", 0)
        unmatched = result.get("unmatched_parts") or []
        warnings = result.get("warnings") or []
        self.status_var.set(f"Saved: {os.path.basename(path)}")
        lines = [f"Purchasing sheet saved to:\n{path}",
                 f"\nPurchased parts matched: {matched} of {total}"]
        if unmatched:
            lines.append(f"Unmatched (no price): {len(unmatched)} — "
                         + ", ".join(unmatched[:10]) + (" …" if len(unmatched) > 10 else ""))
        if warnings:
            lines.append("\n".join(str(w) for w in warnings))
        if messagebox.askyesno("Done", "\n".join(lines) + "\n\nOpen the output folder?"):
            try:
                os.startfile(os.path.dirname(path))  # type: ignore[attr-defined]
            except Exception:
                pass


if __name__ == "__main__":
    App().mainloop()
