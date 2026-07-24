# Design — Standalone BOM → Purchasing executable (built from `bom_purchasing.py`)

**Date:** 2026-07-23
**Status:** Approved (design self-approved per owner delegation — owner out of office)

## Background & motivation

Engineering teammates who have Autodesk Inventor / the Vault client (to export a
BOM) but **not** `app.py`, the Vault MCP server, or a live Vault session need to
turn a BOM export into a Simplifyber purchasing sheet on their own machine.

A previous standalone tool exists (`SF - Engineering Management/BOM_Purchasing_Tool/`:
a Tk GUI `bom_to_purchasing.py` packaged as a one-file `.exe` via `build_exe.bat`),
but it has **drifted behind** the enhanced `bom_purchasing.py` in this repo — it
lacks Inventor-export auto-detection, assembly costing, unpriced-descendant
markers, and reference-file auto-fill. This design makes the **canonical**
`bom_purchasing.py` directly buildable into a standalone `.exe`, so the standalone
and the MCP tool can never diverge again.

`bom_purchasing.py` is already self-contained (imports only `glob/os/sys/datetime/
pandas/openpyxl`; `_resource_path` is `sys._MEIPASS`-aware). It only lacks an
entry point and a build.

## Scope

- **In:** a standalone Tk GUI wrapper + PyInstaller build + user docs, all in the
  Vault-MCP repo, building from `bom_purchasing.py`; a backward-compatible
  `reference_path` override on the engine.
- **Out:** the "part number → pull BOM from Vault" path (needs a live Vault/MCP
  session — stays exclusive to `app.py`/MCP). The standalone is **file-import
  only** (Inventor *or* Vault BOM export → purchasing sheet).

---

## Deliverable 1 — `purchasing_standalone.py` (Tk GUI entry point)

A single-window Tk app modeled on the existing `BOM_Purchasing_Tool` `App`, so the
team's existing `HOW_TO_USE` flow still applies. **Imports only** `bom_purchasing`
+ `tkinter`/`threading`/`os`/`sys` — no `app.py`, `mcp_server`, `gui.*`, or Vault.

Fields / behavior:
- **BOM file** — a Browse button (`filedialog.askopenfilename`, filetypes
  `*.xls *.xlsx *.csv *.txt`). On browse, first show the Inventor export reminder
  (the same text used in `gui/purchasing.py`: sort by Description desc + renumber;
  Structured/All-Levels view; required Item/Part Number/QTY + recommended cols;
  export `.xlsx`/`.txt`/`.csv`). Selecting a file auto-fills the Assembly field
  from the filename stem when that field is empty.
- **Reference file (optional)** — a Browse button; when set, overrides OneDrive
  auto-discovery of `purchased items.xlsx`. Empty ⇒ auto-discover.
- **Assembly / Job number** — a text entry (used for the output filename
  `{assembly}-PurchasingExport.xlsx`).
- **Save output to** — a Browse (directory); defaults to the user's Desktop.
- **Generate Purchasing Sheet** — a button that runs
  `bom_purchasing.generate_from_file(bom_path, assembly, output_dir,
  reference_path=ref_or_empty)` on a **worker thread** (so the UI doesn't freeze),
  marshaling the result back to the UI via a queue/`after`. On completion, show a
  summary: output path, `matched_parts`, count of `unmatched_parts`, and any
  `warnings`; plus an **Open folder** button. On error (the result dict's `error`
  key), show the message in a dialog.
- Branding: reuse the Simplifyber palette; set the window icon from
  `Simplifyber_Logo.png` if present (best-effort, wrapped in try/except).
- `if __name__ == "__main__": App().mainloop()`.

Validation: Generate requires a BOM file and a non-empty assembly number
(otherwise a friendly warning dialog); output dir defaults to Desktop if blank.

## Deliverable 2 — `build_purchasing_exe.bat`

Mirrors the proven existing `build_exe.bat`:
- `cd /d "%~dp0"`; log to `build_purchasing_log.txt`.
- Locate Python (`py` → `python` → `python3`); clear error + `pause` if absent.
- `pip install pandas openpyxl xlrd pyinstaller pillow` (pillow is required by
  openpyxl's `XLImage` for the embedded logo).
- `PyInstaller --onefile --windowed --name "Simplifyber_BOM_Purchasing" --clean
  --distpath "." --add-data "Simplifyber_Logo.png;." --add-data
  "Simplifyber_Logo_White.png;." purchasing_standalone.py`.
- Verify the `.exe` exists; clean up `build/` and the generated `.spec`; print the
  output path; `pause`.

Both logo PNGs already exist in the repo root, so `--add-data` bundles them and
`_resource_path` finds them at runtime.

## Deliverable 3 — `PURCHASING_STANDALONE_HOW_TO_USE.txt`

Build-once + team-use instructions (modeled on the existing `HOW_TO_USE.txt`),
updated for: Inventor **or** Vault export input; the Structured/All-Levels export
guidance; assembly costing + the `*`/unpriced footnote; and reference auto-fill
(with the optional Browse override). States teammates need no Python/Vault/MCP to
run the `.exe`.

## Deliverable 4 — engine change: `reference_path` override

Backward-compatible signature additions in `bom_purchasing.py`:
- `generate_from_file(bom_file_path, assembly_number, output_dir="", reference_path="")`
  — pass `reference_path` through to enrichment.
- `_enrich_with_reference(df, reference_path="")` — if `reference_path` is a
  non-empty existing file, load it via `load_reference_file`; else fall back to
  `find_purchased_items_file()` (current behavior). Default `""` ⇒ unchanged.

Every existing caller (the MCP tools, `generate_from_vault_bom`) keeps working
unchanged because the new parameter defaults to `""`.

---

## Testing / verification

- **Unit:** a test that `generate_from_file(..., reference_path=<temp xlsx>)` uses
  the supplied reference (its Vendor/Cost values appear) rather than OneDrive; and
  that omitting it preserves current behavior (falls back to
  `find_purchased_items_file`, monkeypatched). Full suite stays green.
- **Import isolation:** `python -c "import purchasing_standalone"` succeeds, and an
  automated check asserts the module does **not** import `app`, `mcp_server`,
  `vault_rest_api`, or `gui` (grep/AST) — proving standalone decoupling.
- **Build:** run `build_purchasing_exe.bat` on this machine; confirm
  `Simplifyber_BOM_Purchasing.exe` is produced.
- **Smoke:** headless-construct the GUI (withdrawn Tk root) to confirm it builds
  without error; and drive `generate_from_file` on `tests/fixtures/
  CD-001608-inventor-bom.txt` (already in the repo) to confirm the standalone code
  path produces the workbook.

## Out of scope / notes

- Code-signing the `.exe` (teammates may hit a SmartScreen prompt — documented in
  HOW_TO_USE).
- The old `BOM_Purchasing_Tool/` is left as-is (superseded); optionally note it as
  deprecated in its folder later.
