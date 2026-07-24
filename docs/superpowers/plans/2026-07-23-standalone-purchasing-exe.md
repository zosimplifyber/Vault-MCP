# Standalone BOM → Purchasing `.exe` — Implementation Plan

> **For agentic workers:** implement task-by-task; steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the canonical `bom_purchasing.py` runnable/compilable as a standalone Windows `.exe` (Tk GUI) that teammates use without `app.py`, the MCP server, or a Vault connection.

**Architecture:** A thin Tk GUI (`purchasing_standalone.py`) that imports only `bom_purchasing` and calls `generate_from_file(...)`; a `reference_path` override added to the engine; a PyInstaller one-file build script; user docs. Single source of truth = `bom_purchasing.py`.

**Tech Stack:** Python 3.10+, tkinter, pandas, openpyxl, PyInstaller, Pillow (for the embedded logo), xlrd (legacy `.xls`).

## File structure
- `bom_purchasing.py` (modify) — `reference_path` on `generate_from_file` + `_enrich_with_reference`.
- `purchasing_standalone.py` (create) — the Tk GUI entry point.
- `build_purchasing_exe.bat` (create) — PyInstaller one-file build.
- `PURCHASING_STANDALONE_HOW_TO_USE.txt` (create) — build + team-use docs.
- `tests/test_purchasing_standalone.py` (create) — engine override test, import-isolation test, headless GUI construct.

---

### Task 1: `reference_path` override on the engine (TDD)

**Files:** Modify `bom_purchasing.py` (`_enrich_with_reference` ~607, `generate_from_file`); Test: `tests/test_purchasing_standalone.py`

- [ ] **Step 1 — failing tests**
```python
# tests/test_purchasing_standalone.py
import os, sys
import openpyxl
import pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
import bom_purchasing as bp


def _ref_workbook(path):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "purchased parts"
    ws.append(["Number", "Vendor", "Cost Per"]); ws.append(["SF-1", "Acme", 3.5])
    wb.save(path)


def test_reference_path_override_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "find_purchased_items_file", lambda: None)  # auto-find finds nothing
    ref = tmp_path / "ref.xlsx"; _ref_workbook(ref)
    bom = tmp_path / "bom.txt"
    bom.write_text("Item\tPart Number\tBOM Structure\tQTY\tDescription\n"
                   "1\tSF-1\tPurchased\t2\tpart\n", encoding="utf-8")
    result = bp.generate_from_file(str(bom), "ASM", str(tmp_path), reference_path=str(ref))
    assert not result.get("error"), result
    ws = openpyxl.load_workbook(result["output_path"])["Purchasing"]
    header = [c.value for c in ws[3]]
    num, ven = header.index("Number") + 1, header.index("Vendor") + 1
    vendors = {ws.cell(r, num).value: ws.cell(r, ven).value for r in range(4, 5)}
    assert vendors.get("SF-1") == "Acme"   # came from the override, not OneDrive


def test_no_reference_path_falls_back_to_autofind(tmp_path, monkeypatch):
    hits = {}
    monkeypatch.setattr(bp, "find_purchased_items_file", lambda: hits.setdefault("called", True))
    bom = tmp_path / "bom.txt"
    bom.write_text("Item\tPart Number\tQTY\tDescription\n1\tSF-1\t2\tp\n", encoding="utf-8")
    result = bp.generate_from_file(str(bom), "ASM", str(tmp_path))
    assert not result.get("error"), result
    assert hits.get("called")   # default path still auto-discovers
```

- [ ] **Step 2 — run, expect FAIL** (`generate_from_file` has no `reference_path` kwarg).
  Run: `python -m pytest tests/test_purchasing_standalone.py -k reference -v`

- [ ] **Step 3 — implement.** In `_enrich_with_reference`, change the signature and the ref-path selection:
```python
def _enrich_with_reference(df: pd.DataFrame, reference_path: str = "") -> tuple[pd.DataFrame, int, int, list[str], list[str]]:
    """Try to fill purchasing columns from the reference file.

    reference_path, when a real file, overrides OneDrive auto-discovery.
    Returns (df, matched, total, unmatched_part_numbers, warnings).
    """
    warnings: list[str] = []
    matched = total = 0
    unmatched: list[str] = []

    ref_path = reference_path if (reference_path and os.path.isfile(reference_path)) \
        else find_purchased_items_file()
    if not ref_path:
        warnings.append(
            "Purchased items reference file not found. "
            "Ensure OneDrive is syncing the Purchasing folder, or the columns "
            "Material/Vendor/Cost Per will be left blank."
        )
        return df, matched, total, unmatched, warnings
    # ... (rest unchanged: load_reference_file, lookup_purchased_data, unmatched) ...
```
  And in `generate_from_file`, add `reference_path: str = ""` to the signature and pass it through:
```python
    df, matched, total, unmatched, warnings = _enrich_with_reference(df, reference_path=reference_path)
```
  (Keep the Material-precedence lines around it unchanged.)

- [ ] **Step 4 — run, expect PASS**: `python -m pytest tests/test_purchasing_standalone.py -k reference -v`; then `python -m pytest -q` (full suite green).
- [ ] **Step 5 — commit**: `git add bom_purchasing.py tests/test_purchasing_standalone.py && git commit` (message: `feat(purchasing): optional reference_path override on generate_from_file`, with the Co-Authored-By trailer).

---

### Task 2: `purchasing_standalone.py` Tk GUI + isolation tests

**Files:** Create `purchasing_standalone.py`; add tests to `tests/test_purchasing_standalone.py`

- [ ] **Step 1 — failing tests** (append):
```python
def test_standalone_does_not_import_app_or_vault():
    import ast
    src = open(os.path.join(ROOT, "purchasing_standalone.py"), encoding="utf-8").read()
    mods = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    assert not (mods & {"app", "mcp_server", "vault_rest_api", "gui"}), mods
    assert "bom_purchasing" in mods


def test_standalone_gui_constructs(monkeypatch):
    tk = pytest.importorskip("tkinter")
    monkeypatch.setattr(bp, "find_purchased_items_file", lambda: None)
    import purchasing_standalone as ps
    try:
        app = ps.App()
    except tk.TclError:
        pytest.skip("no display")
    app.withdraw(); app.update_idletasks()
    assert "Simplifyber" in app.title()
    app.destroy()
```

- [ ] **Step 2 — run, expect FAIL** (`purchasing_standalone.py` missing).
- [ ] **Step 3 — create `purchasing_standalone.py`** with exactly the module in Appendix A below.
- [ ] **Step 4 — run, expect PASS**: `python -m pytest tests/test_purchasing_standalone.py -v`; then `python -c "import purchasing_standalone"`; then `python -m pytest -q`.
- [ ] **Step 5 — commit**: `git add purchasing_standalone.py tests/test_purchasing_standalone.py && git commit` (`feat: standalone Tk GUI for BOM -> Purchasing (no Vault/MCP)`).

---

### Task 3: build script + user docs, and build the `.exe`

**Files:** Create `build_purchasing_exe.bat`, `PURCHASING_STANDALONE_HOW_TO_USE.txt`

- [ ] **Step 1 — create `build_purchasing_exe.bat`** (Appendix B).
- [ ] **Step 2 — create `PURCHASING_STANDALONE_HOW_TO_USE.txt`** (Appendix C).
- [ ] **Step 3 — build & verify**: run `cmd //c build_purchasing_exe.bat` (or double-click). Confirm `Simplifyber_BOM_Purchasing.exe` is produced in the repo root and is a non-trivial size (>10 MB). If PyInstaller isn't installed it will `pip install` it.
- [ ] **Step 4 — smoke test the produced code path**: `python -c "import bom_purchasing as bp; print(bp.generate_from_file('tests/fixtures/CD-001608-inventor-bom.txt','CD-001608','.')['output_path'])"` — confirm a workbook path is returned (this exercises the exact engine the .exe bundles).
- [ ] **Step 5 — commit**: add the `.bat`, the `.txt`, and `.gitignore` the build artifacts (`build/`, `*.exe`, `build_purchasing_log.txt`) so the large binary isn't committed. `git add build_purchasing_exe.bat PURCHASING_STANDALONE_HOW_TO_USE.txt .gitignore && git commit` (`build: PyInstaller one-file build + docs for standalone purchasing exe`).

---

## Appendix A — `purchasing_standalone.py`
```python
"""
Simplifyber — standalone BOM -> Purchasing Sheet tool.

A self-contained Tk GUI that turns an Inventor or Vault BOM export into a
Simplifyber purchasing workbook using the shared bom_purchasing engine. Needs NO
Vault connection, MCP server, or app.py — only a BOM export file (and optionally
the purchased-items reference file). Build to a one-file .exe with
build_purchasing_exe.bat.
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import bom_purchasing as bp

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
        self._ref_path = None
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
        tk.Button(ref, text="Browse for a different file…", command=self._browse_ref,
                  bg=LIGHT_GRAY, fg=DARK_BLUE, relief="flat", font=("Arial", 8),
                  cursor="hand2").grid(row=2, column=0, sticky="w")

        self.bom_var = tk.StringVar()
        self._label(body, "BOM File (Inventor or Vault export):").grid(row=1, column=0, sticky="w")
        self._browse_row(body, self.bom_var, 2, self._browse_bom)

        self.out_var = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop"))
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
        self._set_reference_file(bp.find_purchased_items_file())

    def _set_reference_file(self, path):
        self._ref_path = path
        if path:
            self.ref_status_var.set(f"✓  {os.path.basename(path)}  (found automatically)")
            self.ref_label.config(fg="#1F6B2E")
        else:
            self.ref_status_var.set(
                "⚠  Not found automatically. Cost columns will be blank unless you "
                "browse for the file below.")
            self.ref_label.config(fg="#8B4000")

    def _browse_ref(self):
        path = filedialog.askopenfilename(
            title="Select Purchased Items Reference File",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")])
        if path:
            self._set_reference_file(path)

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
        out_dir = self.out_var.get().strip() or os.path.join(os.path.expanduser("~"), "Desktop")
        asm = self.asm_var.get().strip()
        if not bom_path:
            messagebox.showwarning("Missing input", "Please select a BOM file.")
            return
        if not asm:
            messagebox.showwarning("Missing input", "Please enter an assembly / job number.")
            return
        self.btn.config(state="disabled")
        self.status_var.set("Generating…")
        ref = self._ref_path or ""

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
```

## Appendix B — `build_purchasing_exe.bat`
```bat
@echo off
REM  Simplifyber -- Standalone BOM -> Purchasing Sheet builder
cd /d "%~dp0"
set LOG=%~dp0build_purchasing_log.txt
echo Build started: %DATE% %TIME% > "%LOG%"

set PYTHON=
where py >nul 2>&1 && ( set PYTHON=py & goto :found )
where python >nul 2>&1 && ( set PYTHON=python & goto :found )
where python3 >nul 2>&1 && ( set PYTHON=python3 & goto :found )
echo ERROR: Python not found. Install Python 3.10+ and tick "Add Python to PATH". & pause & exit /b 1

:found
echo Using Python: %PYTHON% >> "%LOG%"
echo Installing required packages...
%PYTHON% -m pip install pandas openpyxl xlrd pyinstaller pillow >> "%LOG%" 2>&1
if %ERRORLEVEL% neq 0 ( echo ERROR: pip install failed. See build_purchasing_log.txt & pause & exit /b 1 )

echo Building .exe -- this takes 60-120 seconds...
%PYTHON% -m PyInstaller --onefile --windowed --name "Simplifyber_BOM_Purchasing" --clean --distpath "." --add-data "Simplifyber_Logo.png;." --add-data "Simplifyber_Logo_White.png;." purchasing_standalone.py >> "%LOG%" 2>&1
if %ERRORLEVEL% neq 0 ( echo ERROR: PyInstaller build failed. See build_purchasing_log.txt & pause & exit /b 1 )
if not exist "%~dp0Simplifyber_BOM_Purchasing.exe" ( echo ERROR: .exe not found after build. & pause & exit /b 1 )

if exist "%~dp0build" rmdir /s /q "%~dp0build"
if exist "%~dp0Simplifyber_BOM_Purchasing.spec" del /q "%~dp0Simplifyber_BOM_Purchasing.spec"
echo.
echo SUCCESS. Your .exe: %~dp0Simplifyber_BOM_Purchasing.exe
echo Share it via SharePoint; teammates do NOT need Python.
explorer "%~dp0"
pause
```

## Appendix C — `PURCHASING_STANDALONE_HOW_TO_USE.txt`
Plain-text instructions covering: (1) FIRST-TIME BUILD — install Python 3.10+ (Add to PATH), double-click `build_purchasing_exe.bat`, share the produced `Simplifyber_BOM_Purchasing.exe`; (2) EXPORT THE BOM FROM INVENTOR — Structured/All-Levels view, sort by Description desc + renumber, include Item/Part Number/QTY (+ recommended Description/Unit QTY/BOM Structure/REV/Material/Material Finish), save `.xlsx`/`.txt`/`.csv`; (3) RUN — open the `.exe`, Browse the BOM file, (optional) Browse the reference file, enter the assembly number, pick output folder, Generate; (4) WHAT IT PRODUCES — `{Assembly}-PurchasingExport.xlsx` with Purchasing, By Vendor, and Assembly Costs sheets (per-assembly cost-to-make-one + grand total; `*` marks totals that include unpriced parts); (5) TROUBLESHOOTING — SmartScreen "unknown publisher" → More info → Run anyway; reference file not found → check OneDrive Purchasing sync or Browse manually; nothing happens on double-click → check antivirus/Run as admin.

---

## Self-review
- **Spec coverage:** Deliverable 1 (GUI) → Task 2; Deliverable 2 (build.bat) → Task 3; Deliverable 3 (how-to) → Task 3; Deliverable 4 (`reference_path`) → Task 1. Testing (unit override, import isolation, headless construct, build, smoke) → Tasks 1-3 steps. ✅
- **Placeholders:** none — Appendix C is a content outline for a plain-text doc (acceptable; it's prose, not code).
- **Consistency:** the GUI calls `bp.generate_from_file(..., reference_path=ref)` defined in Task 1; `bp.find_purchased_items_file` / `bp._resource_path` exist in `bom_purchasing.py`; build.bat entry script `purchasing_standalone.py` matches Task 2; `.gitignore` keeps the large `.exe`/`build/` out of git.
