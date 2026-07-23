# Inventor BOM Import + Assembly Costing, and Item-Master Tool Flags — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make BOM → Purchasing work today by importing an Inventor BOM export (with real quantities), add per-assembly costing + an Assembly Costs summary sheet, surface unmatched parts, and flag the three Item-Master-dependent tools as broken in the launcher GUI.

**Architecture:** Extend the existing file-import path in `bom_purchasing.py` — auto-detect Inventor vs Vault-canonical headers, translate `BOM Structure`→`Source` values, and reuse the existing Excel-formula assembly roll-up (detecting assemblies by "has children" instead of `Category Name`). Add an "Assembly Costs" summary sheet and unmatched highlighting. The GUI flags are a small, isolated change to `gui/launcher.py`.

**Tech Stack:** Python 3.10+, pandas, openpyxl, tkinter (Tk GUI), pytest.

---

## File structure

- `bom_purchasing.py` (modify) — new `INVENTOR_FIELD_MAP` / `SOURCE_VALUE_MAP`; new `read_bom_file()` and `coerce_bom_dataframe()`; rewire `generate_from_file()`; add `Material Finish` column; assembly-by-children detection + unmatched highlight in `build_purchasing_sheet()`; new `_build_assembly_costs_tab()`.
- `gui/launcher.py` (modify) — `_tool_row(..., broken=False)`; store buttons in `self.tool_buttons`; flag three tools.
- `gui/purchasing.py` (modify) — accept `.txt`; reminder dialog before file pick.
- `mcp_server.py` (modify) — docstring update on `vault_generate_purchasing_sheet_from_file`.
- `tests/test_launcher_flags.py` (create) — headless assertion the three rows are disabled.
- `tests/test_bom_purchasing_inventor.py` (create) — unit + integration tests.
- `tests/fixtures/CD-001608-inventor-bom.txt` (create) — the real Inventor export sample.

Two phases: **Phase 1** (GUI flags — small, ship first) and **Phase 2** (Inventor import + costing).

---

# Phase 1 — Flag Item-Master tools as broken in the GUI

### Task 1: Add `broken` support to the launcher tool rows and flag three tools

**Files:**
- Modify: `gui/launcher.py` — `__init__` (add `self.tool_buttons`), `_tool_row` (~585-603), `_build_tools_panel` (~529-583)
- Test: `tests/test_launcher_flags.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_launcher_flags.py
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

tk = pytest.importorskip("tkinter")


def _make_gui():
    from gui.launcher import LauncherGUI
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    gui = LauncherGUI(root, cfg=cfg, auto_start_mcp=False)
    root.update_idletasks()
    return root, gui


def test_item_master_tools_are_flagged_broken():
    root, gui = _make_gui()
    try:
        for title in ("Release Workflow", "MFG Order Package", "Property Check (Lookup)"):
            btn = gui.tool_buttons[title]
            assert str(btn["state"]) == "disabled", f"{title} should be disabled"
    finally:
        root.destroy()


def test_working_tools_stay_enabled():
    root, gui = _make_gui()
    try:
        btn = gui.tool_buttons["BOM → Purchasing Sheet"]
        assert str(btn["state"]) != "disabled"
    finally:
        root.destroy()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_launcher_flags.py -v`
Expected: FAIL — `AttributeError: 'LauncherGUI' object has no attribute 'tool_buttons'`.

- [ ] **Step 3: Initialize the button registry**

In `gui/launcher.py`, `LauncherGUI.__init__`, next to the other persistent refs (near `self._logo_img = None`), add:

```python
        # Tool-row buttons, keyed by title — lets tests/status code find them
        self.tool_buttons: dict[str, tk.Button] = {}
```

- [ ] **Step 4: Add `broken` handling to `_tool_row`**

Replace `_tool_row` (currently ~585-603) with:

```python
    def _tool_row(self, parent, title, desc, btn_text, command, *,
                  primary, broken=False):
        row = tk.Frame(parent, bg=WHITE, pady=8)
        row.pack(fill="x")
        text = tk.Frame(row, bg=WHITE)
        text.pack(side="left", fill="x", expand=True)

        title_text = title if not broken else f"{title}   ⛔ BROKEN — Item Master retired"
        tk.Label(
            text, text=title_text, bg=WHITE,
            fg=(RUST_ORANGE if broken else DARK_BLUE),
            font=("Arial", 11, "bold"), anchor="w",
        ).pack(fill="x")

        shown_desc = desc if not broken else (
            "Disabled — depends on the retired Item Master. A CAD/iProperty "
            "rewrite is planned."
        )
        tk.Label(
            text, text=shown_desc, bg=WHITE,
            fg=(RUST_ORANGE if broken else DARK_GRAY),
            font=("Arial", 9), anchor="w", justify="left", wraplength=400,
        ).pack(fill="x", pady=(2, 0))

        btn = self._brand_button(row, f"  {btn_text}  ", command, primary=primary)
        if broken:
            btn.configure(state="disabled")
        btn.pack(side="right", padx=(12, 0))
        self.tool_buttons[title] = btn

        tk.Frame(parent, bg=GRAY_BDR, height=1).pack(fill="x", pady=(4, 0))
```

- [ ] **Step 5: Flag the three tools in `_build_tools_panel`**

In `_build_tools_panel`, add `broken=True` to the three `_tool_row(...)` calls whose titles are `"Release Workflow"`, `"MFG Order Package"`, and `"Property Check (Lookup)"`. Example for the first:

```python
        self._tool_row(
            body,
            "Release Workflow",
            "Walk through compliance, sync properties, get files local, "
            "rebuild in Inventor, and release CAD + items.",
            "Open Workflow",
            self._on_open_workflow,
            primary=True,
            broken=True,
        )
```

Do the same (`broken=True`) for the `"MFG Order Package"` and `"Property Check (Lookup)"` rows. Leave `"BOM → Purchasing Sheet"`, `"Open Reports Folder"`, and `"Edit Property Rules"` unchanged.

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_launcher_flags.py -v`
Expected: PASS (or SKIP if no display — then verify manually with `python app.py`).

- [ ] **Step 7: Commit**

```bash
git add gui/launcher.py tests/test_launcher_flags.py
git commit -m "feat(gui): flag Item-Master tools (Release Workflow, MFG, Property Check) broken"
```

---

# Phase 2 — Inventor BOM import + assembly costing

### Task 2: Header/value maps + `coerce_bom_dataframe`

**Files:**
- Modify: `bom_purchasing.py` — add maps after `VAULT_FIELD_MAP` (~145) and `coerce_bom_dataframe` near `generate_from_file`
- Test: `tests/test_bom_purchasing_inventor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bom_purchasing_inventor.py
import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bom_purchasing as bp  # noqa: E402


def test_coerce_maps_inventor_headers_and_translates_source():
    df = pd.DataFrame({
        "Item": ["1", "2", "2.1"],
        "Part Number": ["SF-001580", "SF-001803", "SF-001885"],
        "BOM Structure": ["Normal", "Normal", "Purchased"],
        "Unit QTY": ["Each", "Each", "Each"],
        "QTY": [2, 1, 8],
        "Description": ["adapter plate", "bladder tool", "hex screw"],
        "REV": ["", "1", "1"],
        "Material": ["Aluminum", "", "Stainless Steel"],
        "Material Finish": ["", "", "Black Oxide"],
    })
    out, err = bp.coerce_bom_dataframe(df)
    assert err is None
    assert list(out["Number"]) == ["SF-001580", "SF-001803", "SF-001885"]
    assert list(out["Item Qty"]) == [2, 1, 8]
    assert list(out["Row Order"]) == ["1", "2", "2.1"]
    assert list(out["Source"]) == ["Make", "Make", "Buy"]      # translated
    assert list(out["Units"]) == ["Each", "Each", "Each"]
    assert out["Material Finish"].tolist() == ["", "", "Black Oxide"]
    # canonical columns that Inventor doesn't export exist and are blank/None
    for col in ("Category Name", "State", "Position Number", "Title (Item,CO)"):
        assert col in out.columns


def test_coerce_accepts_canonical_vault_headers_unchanged():
    df = pd.DataFrame({c: ["x"] for c in bp.BOM_COLUMNS})
    out, err = bp.coerce_bom_dataframe(df)
    assert err is None
    assert set(bp.BOM_COLUMNS).issubset(out.columns)


def test_coerce_errors_when_no_part_number():
    df = pd.DataFrame({"QTY": [1, 2], "Description": ["a", "b"]})
    out, err = bp.coerce_bom_dataframe(df)
    assert err is not None and "part number" in err.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bom_purchasing_inventor.py -v`
Expected: FAIL — `AttributeError: module 'bom_purchasing' has no attribute 'coerce_bom_dataframe'`.

- [ ] **Step 3: Add the maps and the function**

In `bom_purchasing.py`, after `VAULT_FIELD_MAP` (ends ~145) add:

```python
# Inventor BOM export headers → our canonical BOM columns (case-insensitive).
INVENTOR_FIELD_MAP: dict[str, str] = {
    "Item": "Row Order",
    "Part Number": "Number",
    "QTY": "Item Qty",
    "Unit QTY": "Units",
    "BOM Structure": "Source",
    "Description": "Description (Item,CO)",
    "REV": "Revision",
    "Material": "Material",
    "Material Finish": "Material Finish",
}

# Inventor "BOM Structure" values → our canonical "Source" values.
SOURCE_VALUE_MAP: dict[str, str] = {
    "Purchased": "Buy",
    "Normal": "Make",
    "Phantom": "Make",
    "Inseparable": "Make",
    "Reference": "Make",
}
```

Then, just above `def generate_from_file(` (~686), add:

```python
def coerce_bom_dataframe(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, str | None]:
    """Normalize a raw BOM DataFrame (Vault-canonical OR Inventor export).

    Returns (normalized_df, error_message). error_message is None on success.
    Guarantees the result carries every BOM_COLUMNS entry plus 'Material' and
    'Material Finish' (missing ones filled with None), a positional index, and
    translated Source values. Errors if the critical Number/Item Qty are absent.
    """
    df = df.rename(columns={c: str(c).strip() for c in df.columns})

    if not set(BOM_COLUMNS).issubset(df.columns):
        # Inventor export — map its headers case-insensitively.
        lower = {c.lower(): c for c in df.columns}
        rename: dict[str, str] = {}
        for inv_name, canon in INVENTOR_FIELD_MAP.items():
            src = lower.get(inv_name.lower())
            if src is not None and canon not in rename.values():
                rename[src] = canon
        df = df.rename(columns=rename)
        if "Source" in df.columns:
            df["Source"] = df["Source"].map(
                lambda v: SOURCE_VALUE_MAP.get(str(v).strip(), v)
            )

    for col in BOM_COLUMNS + ["Material", "Material Finish"]:
        if col not in df.columns:
            df[col] = None

    df = df.reset_index(drop=True)

    if df["Number"].isna().all() or (df["Number"].astype(str).str.strip() == "").all():
        return df, ("No part numbers found — the BOM needs a 'Part Number' "
                    "(Inventor) or 'Number' (Vault) column.")
    if df["Item Qty"].isna().all():
        return df, ("No quantities found — the BOM needs a 'QTY' (Inventor) "
                    "or 'Item Qty' (Vault) column.")
    return df, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bom_purchasing_inventor.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add bom_purchasing.py tests/test_bom_purchasing_inventor.py
git commit -m "feat(purchasing): map Inventor BOM headers + translate Source values"
```

---

### Task 3: `read_bom_file` — support `.txt`(tab), `.csv`, `.xls`, `.xlsx`

**Files:**
- Modify: `bom_purchasing.py` — add `read_bom_file` above `generate_from_file`
- Test: `tests/test_bom_purchasing_inventor.py`

- [ ] **Step 1: Write the failing test**

```python
def test_read_bom_file_reads_tab_delimited_txt(tmp_path):
    p = tmp_path / "bom.txt"
    p.write_text(
        "Item\tPart Number\tQTY\tDescription\n"
        "1\tSF-001580\t2\tadapter plate\n"
        "2.1\tSF-001885\t8\thex screw\n",
        encoding="utf-8",
    )
    df = bp.read_bom_file(str(p))
    assert list(df.columns[:4]) == ["Item", "Part Number", "QTY", "Description"]
    assert len(df) == 2
    assert str(df.iloc[1]["Part Number"]) == "SF-001885"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bom_purchasing_inventor.py::test_read_bom_file_reads_tab_delimited_txt -v`
Expected: FAIL — `AttributeError: ... has no attribute 'read_bom_file'`.

- [ ] **Step 3: Implement `read_bom_file`**

Add above `def generate_from_file(`:

```python
def read_bom_file(bom_file_path: str) -> pd.DataFrame:
    """Read a BOM export into a DataFrame by extension.

    .csv → comma; .txt → tab-delimited (Inventor's text export); .xls/.xlsx →
    first sheet. Raises ValueError on an unsupported extension.
    """
    ext = os.path.splitext(bom_file_path)[1].lower()
    if ext == ".csv":
        return pd.read_csv(bom_file_path)
    if ext == ".txt":
        return pd.read_csv(bom_file_path, sep="\t")
    if ext in (".xls", ".xlsx"):
        return pd.read_excel(bom_file_path, sheet_name=0)
    raise ValueError(f"Unsupported file type: {ext}. Use .xlsx, .xls, .csv, or .txt.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bom_purchasing_inventor.py::test_read_bom_file_reads_tab_delimited_txt -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bom_purchasing.py tests/test_bom_purchasing_inventor.py
git commit -m "feat(purchasing): read tab-delimited .txt BOM exports"
```

---

### Task 4: Rewire `generate_from_file` (auto-detect + Material precedence)

**Files:**
- Modify: `bom_purchasing.py` — `generate_from_file` (686-735)
- Test: `tests/test_bom_purchasing_inventor.py`

- [ ] **Step 1: Write the failing test**

`_enrich_with_reference` reads a real network reference file; monkeypatch it so the test is hermetic and asserts Material precedence (export wins).

```python
def test_generate_from_file_inventor_export_populates(tmp_path, monkeypatch):
    p = tmp_path / "bom.txt"
    p.write_text(
        "Item\tPart Number\tBOM Structure\tUnit QTY\tQTY\tDescription\tREV\tMaterial\tMaterial Finish\n"
        "1\tSF-001580\tNormal\tEach\t2\tadapter plate\t\tAluminum\t\n"
        "2\tSF-001803\tNormal\tEach\t1\tbladder tool\t1\t\t\n"
        "2.1\tSF-001885\tPurchased\tEach\t8\thex screw\t1\tSteel\tBlack Oxide\n",
        encoding="utf-8",
    )

    # Reference "enrichment" that would overwrite Material — prove export wins.
    def fake_enrich(df):
        df = df.copy()
        df["Material"] = "REF-MATERIAL"
        df["Vendor"] = "Acme"
        df["Cost Per"] = 1.5
        return df, 1, 1, [], []
    monkeypatch.setattr(bp, "_enrich_with_reference", fake_enrich)

    out_dir = tmp_path / "out"
    result = bp.generate_from_file(str(p), "CD-001608", str(out_dir))
    assert not result.get("error"), result
    assert os.path.isfile(result["output_path"])

    # export Material wins where present; falls back to ref where blank
    wb = __import__("openpyxl").load_workbook(result["output_path"])
    ws = wb["Purchasing"]
    header = [c.value for c in ws[3]]
    mat_col = header.index("Material") + 1
    num_col = header.index("Number") + 1
    mats = {ws.cell(r, num_col).value: ws.cell(r, mat_col).value
            for r in range(4, 4 + 3)}
    assert mats["SF-001580"] == "Aluminum"       # from export
    assert mats["SF-001803"] == "REF-MATERIAL"   # blank in export → ref
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bom_purchasing_inventor.py::test_generate_from_file_inventor_export_populates -v`
Expected: FAIL — current `generate_from_file` rejects the file (missing canonical columns) and doesn't do Material precedence.

- [ ] **Step 3: Rewrite `generate_from_file`**

Replace the body of `generate_from_file` (686-735) with:

```python
def generate_from_file(
    bom_file_path: str,
    assembly_number: str,
    output_dir: str = "",
) -> dict[str, Any]:
    """Build a purchasing sheet from an exported BOM file.

    Accepts a Vault BOM export (canonical columns) or an Inventor BOM export
    (auto-detected + header-mapped). Supports .xlsx/.xls/.csv/.txt(tab).
    """
    if not os.path.isfile(bom_file_path):
        return {"error": True, "message": f"BOM file not found: {bom_file_path}"}

    try:
        raw = read_bom_file(bom_file_path)
    except ValueError as exc:
        return {"error": True, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": True, "message": f"Could not read BOM file: {exc}"}

    df, err = coerce_bom_dataframe(raw)
    if err:
        return {"error": True, "message": err}

    # Material precedence: the export's Material wins where non-blank; the
    # reference file only fills blanks.
    export_material = df["Material"].copy()
    df, matched, total, unmatched, warnings = _enrich_with_reference(df)
    keep = export_material.notna() & (export_material.astype(str).str.strip() != "")
    df.loc[keep, "Material"] = export_material[keep]

    if not output_dir:
        output_dir = os.path.dirname(bom_file_path)
    os.makedirs(output_dir, exist_ok=True)
    out_file = os.path.join(output_dir, f"{assembly_number}-PurchasingExport.xlsx")

    build_purchasing_sheet(df, out_file, assembly_number)

    return {
        "output_path": out_file,
        "matched_parts": matched,
        "total_purchased_parts": total,
        "unmatched_parts": unmatched,
        "warnings": warnings,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bom_purchasing_inventor.py::test_generate_from_file_inventor_export_populates -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bom_purchasing.py tests/test_bom_purchasing_inventor.py
git commit -m "feat(purchasing): auto-detect Inventor exports in generate_from_file + Material precedence"
```

---

### Task 5: Assembly-by-children detection + `Material Finish` column

**Files:**
- Modify: `bom_purchasing.py` — `PURCHASE_COLUMNS`/`COLUMN_WIDTHS` (~59-78), `build_purchasing_sheet` (435-450)
- Test: `tests/test_bom_purchasing_inventor.py`

- [ ] **Step 1: Write the failing test**

```python
def _hier_df():
    # 2 is an assembly (has child 2.1); 1 is a leaf part.
    return pd.DataFrame({
        "Number": ["SF-1", "SF-2", "SF-21"],
        "Row Order": ["1", "2", "2.1"],
        "Position Number": [None, None, None],
        "Item Qty": [2, 1, 8],
        "Units": ["Each", "Each", "Each"],
        "Category Name": [None, None, None],   # Inventor exports have none
        "Revision": [None, "1", "1"],
        "State": [None, None, None],
        "Title (Item,CO)": [None, None, None],
        "Description (Item,CO)": ["leaf", "assy", "screw"],
        "Source": ["Buy", "Make", "Buy"],
        "Material": ["Al", None, "Steel"],
        "Material Finish": [None, None, "Black Oxide"],
        "Vendor": ["Acme", None, "Bolts Inc"],
        "Cost Per": [1.0, None, 0.25],
        "Vendor Number": [None, None, None],
        "HS/HTS Code": [None, None, None],
        "Shipping": [None, None, None],
        "Tax/Tariff": [None, None, None],
        "Lead Time (Business Days)": [None, None, None],
    })


def test_material_finish_is_a_column_and_populates(tmp_path):
    out = tmp_path / "s.xlsx"
    bp.build_purchasing_sheet(_hier_df(), str(out), "ASM")
    ws = __import__("openpyxl").load_workbook(str(out))["Purchasing"]
    header = [c.value for c in ws[3]]
    assert "Material Finish" in header
    mf_col = header.index("Material Finish") + 1
    assert ws.cell(6, mf_col).value == "Black Oxide"  # row 4+2 = the screw


def test_assembly_detected_by_children_without_category(tmp_path):
    out = tmp_path / "s.xlsx"
    bp.build_purchasing_sheet(_hier_df(), str(out), "ASM")
    ws = __import__("openpyxl").load_workbook(str(out))["Purchasing"]
    header = [c.value for c in ws[3]]
    cost_col = header.index("Cost Per") + 1
    # Row 5 is "2" (assembly) → Cost Per is a SUM formula over its child rows.
    assert str(ws.cell(5, cost_col).value).startswith("=SUM(")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bom_purchasing_inventor.py -k "material_finish or assembly_detected" -v`
Expected: FAIL — no `Material Finish` column; assembly not detected (Category blank → `is_assembly` False → no SUM formula).

- [ ] **Step 3: Add the `Material Finish` column**

In `bom_purchasing.py`, change `PURCHASE_COLUMNS` (59-62) to insert `"Material Finish"` right after `"Material"`:

```python
PURCHASE_COLUMNS = [
    "Material", "Material Finish", "Vendor", "Vendor Number", "Cost Per",
    "HS/HTS Code", "Shipping", "Tax/Tariff", "Sub Total",
    "Lead Time (Business Days)",
]
```

Add a width in `COLUMN_WIDTHS` (inside the dict, after the `"Material": 22,` entry):

```python
    "Material Finish": 18,
```

- [ ] **Step 4: Detect assemblies by children and render `Material Finish`**

In `build_purchasing_sheet`, in the per-row loop (435-450), reorder so `child_indices` is computed first and fold it into `is_assembly`; and add a render branch for `Material Finish`. Replace:

```python
    for ri, (df_idx, row) in enumerate(df.iterrows(), start=FIRST_DATA_ROW):
        category = str(row.get("Category Name", "")).strip().lower()
        is_assembly = "assembly" in category
        child_indices = children_map.get(df_idx, [])
```

with:

```python
    for ri, (df_idx, row) in enumerate(df.iterrows(), start=FIRST_DATA_ROW):
        category = str(row.get("Category Name", "")).strip().lower()
        child_indices = children_map.get(df_idx, [])
        # Inventor exports carry no Category Name — a row with children IS an
        # assembly, so detect by structure as well as category.
        is_assembly = ("assembly" in category) or bool(child_indices)
```

Then, in the column render chain, add a `Material Finish` branch. Change the `elif col_name in LOOKUP_COLUMNS:` block start (452) to be preceded by:

```python
            elif col_name == "Material Finish":
                val = row.get("Material Finish")
                c.value = None if pd.isna(val) else val

            elif col_name in LOOKUP_COLUMNS:
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_bom_purchasing_inventor.py -k "material_finish or assembly_detected" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bom_purchasing.py tests/test_bom_purchasing_inventor.py
git commit -m "feat(purchasing): detect assemblies by BOM structure + add Material Finish column"
```

---

### Task 6: "Assembly Costs" summary sheet (Option A)

**Files:**
- Modify: `bom_purchasing.py` — call new tab from `build_purchasing_sheet` (before `wb.save`, ~498); add `_build_assembly_costs_tab`
- Test: `tests/test_bom_purchasing_inventor.py`

- [ ] **Step 1: Write the failing test**

```python
def test_assembly_costs_sheet_lists_assemblies_and_grand_total(tmp_path):
    out = tmp_path / "s.xlsx"
    bp.build_purchasing_sheet(_hier_df(), str(out), "ASM")
    wb = __import__("openpyxl").load_workbook(str(out))
    assert "Assembly Costs" in wb.sheetnames
    ws = wb["Assembly Costs"]
    cells = [c.value for col in ws.iter_cols() for c in col]
    text = "\n".join(str(v) for v in cells if v is not None)
    assert "SF-2" in text                     # the one assembly is listed
    assert "GRAND TOTAL" in text
    # grand total is a formula referencing the Purchasing sheet
    assert any(isinstance(v, str) and v.startswith("=") and "Purchasing!" in v
               for v in cells)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bom_purchasing_inventor.py::test_assembly_costs_sheet_lists_assemblies_and_grand_total -v`
Expected: FAIL — no "Assembly Costs" sheet.

- [ ] **Step 3: Call and implement the tab**

In `build_purchasing_sheet`, change (498):

```python
    _build_vendor_tab(wb, df)
    wb.save(output_path)
```

to:

```python
    _build_vendor_tab(wb, df)
    _build_assembly_costs_tab(wb, df)
    wb.save(output_path)
```

Add this function after `_build_vendor_tab` (after ~601):

```python
def _build_assembly_costs_tab(wb: Workbook, df: pd.DataFrame) -> None:
    """Add the 'Assembly Costs' summary — each sub-assembly's cost-to-make-one
    (references the Purchasing sheet's roll-up formulas) plus a grand total for
    the whole build."""
    children_map = build_children_map(df)
    all_children = {ci for kids in children_map.values() for ci in kids}
    assemblies = [i for i in range(len(df)) if children_map.get(i)]
    top_level = [i for i in range(len(df)) if i not in all_children]

    # Purchasing-sheet geometry (must match build_purchasing_sheet).
    HDR_ROW = 3
    FIRST_DATA_ROW = HDR_ROW + 1
    cost_col = get_column_letter(ALL_COLUMNS.index("Cost Per") + 1)
    sub_col = get_column_letter(ALL_COLUMNS.index("Sub Total") + 1)

    ws = wb.create_sheet("Assembly Costs")
    bdr = _border()
    DOLLAR_FMT = '"$"#,##0.00'
    cols = ["Item", "Part #", "Description", "Cost to Make One"]
    last_col = get_column_letter(len(cols))

    ws.merge_cells(f"A1:{last_col}1")
    c = ws["A1"]
    c.value = "Assembly Costs"
    c.font = Font(name="Arial", bold=True, color=WHITE, size=11)
    c.fill = PatternFill("solid", fgColor=DARK_BLUE)
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 40

    HDR = 2
    for ci, name in enumerate(cols, 1):
        cc = ws.cell(row=HDR, column=ci, value=name)
        cc.font = Font(name="Arial", bold=True, color=WHITE, size=10)
        cc.fill = PatternFill("solid", fgColor=DARK_BLUE)
        cc.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cc.border = bdr

    alt_fill = PatternFill("solid", fgColor=PALE_BLUE)
    white_fill = PatternFill("solid", fgColor=WHITE)
    for n, i in enumerate(assemblies):
        ri = HDR + 1 + n
        row = df.iloc[i]
        purch_row = FIRST_DATA_ROW + i
        vals = [
            None if pd.isna(row.get("Row Order")) else row.get("Row Order"),
            None if pd.isna(row.get("Number")) else row.get("Number"),
            None if pd.isna(row.get("Description (Item,CO)")) else row.get("Description (Item,CO)"),
            f"=Purchasing!{cost_col}{purch_row}",   # cost to make one
        ]
        for ci, val in enumerate(vals, 1):
            cc = ws.cell(row=ri, column=ci, value=val)
            cc.font = Font(name="Arial", size=10)
            cc.fill = alt_fill if n % 2 == 0 else white_fill
            cc.border = bdr
            cc.alignment = Alignment(horizontal="left", vertical="center")
            if ci == 4:
                cc.number_format = DOLLAR_FMT

    total_row = HDR + 1 + len(assemblies)
    ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=3)
    tc = ws.cell(row=total_row, column=1, value="GRAND TOTAL — whole build")
    tc.font = Font(name="Arial", bold=True, color=DARK_BLUE, size=10)
    tc.alignment = Alignment(horizontal="right", vertical="center")
    grand = ws.cell(row=total_row, column=4)
    if top_level:
        refs = "+".join(f"Purchasing!{sub_col}{FIRST_DATA_ROW + i}" for i in top_level)
        grand.value = f"={refs}"
    else:
        grand.value = 0
    grand.number_format = DOLLAR_FMT
    grand.font = Font(name="Arial", bold=True, color=DARK_BLUE, size=10)

    widths = {"Item": 12, "Part #": 16, "Description": 46, "Cost to Make One": 18}
    for ci, name in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(ci)].width = widths[name]
    ws.freeze_panes = "A3"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bom_purchasing_inventor.py::test_assembly_costs_sheet_lists_assemblies_and_grand_total -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bom_purchasing.py tests/test_bom_purchasing_inventor.py
git commit -m "feat(purchasing): add Assembly Costs summary sheet (cost-to-make-one + grand total)"
```

---

### Task 7: Surface unmatched parts (highlight + list)

**Files:**
- Modify: `bom_purchasing.py` — `build_purchasing_sheet` data loop + a trailing note
- Test: `tests/test_bom_purchasing_inventor.py`

- [ ] **Step 1: Write the failing test**

```python
def test_unmatched_parts_are_highlighted_and_listed(tmp_path):
    df = _hier_df()
    # SF-21 is a Buy part with no Vendor → unmatched.
    df.loc[df["Number"] == "SF-21", "Vendor"] = None
    df.loc[df["Number"] == "SF-21", "Cost Per"] = None
    out = tmp_path / "s.xlsx"
    bp.build_purchasing_sheet(df, str(out), "ASM")
    ws = __import__("openpyxl").load_workbook(str(out))["Purchasing"]
    header = [c.value for c in ws[3]]
    num_col = header.index("Number") + 1
    # the screw row (4+2=6) Number cell is filled with the unmatched color
    fill = ws.cell(6, num_col).fill
    assert fill.fgColor.rgb and fill.fgColor.rgb.endswith(bp.UNMATCHED_FILL)
    # an "Unmatched" note listing SF-21 appears somewhere below the table
    text = "\n".join(str(c.value) for col in ws.iter_cols() for c in col if c.value)
    assert "Unmatched" in text and "SF-21" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bom_purchasing_inventor.py::test_unmatched_parts_are_highlighted_and_listed -v`
Expected: FAIL — no `UNMATCHED_FILL`, no highlight, no note.

- [ ] **Step 3: Add the constant, highlight, and note**

In `bom_purchasing.py`, near the other color constants (after `OLIVE_GREEN = "D8E4BC"`, ~35), add:

```python
UNMATCHED_FILL = "FCE4D6"  # light orange — a Buy part with no price found
```

In `build_purchasing_sheet`, inside the per-row loop, compute an unmatched flag and override the fill. After the existing `if is_assembly: fill = olive_fill else: ...` block (440-443), add:

```python
        is_unmatched = (
            not is_assembly
            and str(row.get("Source", "")).strip() in {"Buy", "Other"}
            and pd.isna(row.get("Vendor"))
        )
        if is_unmatched:
            fill = PatternFill("solid", fgColor=UNMATCHED_FILL)
```

Then, immediately before `# Column widths & freeze` (~483), add the note block:

```python
    # Unmatched note — list Buy parts with no reference match so a $0 line is
    # clearly "no price found", not "free".
    unmatched_nums = [
        str(r.get("Number"))
        for _, r in df.iterrows()
        if str(r.get("Source", "")).strip() in {"Buy", "Other"}
        and pd.isna(r.get("Vendor")) and pd.notna(r.get("Number"))
    ]
    if unmatched_nums:
        note_row = FIRST_DATA_ROW + len(df) + 1
        ws.merge_cells(start_row=note_row, start_column=1,
                       end_row=note_row, end_column=n_cols)
        nc = ws.cell(row=note_row, column=1)
        nc.value = (f"Unmatched ({len(unmatched_nums)}) — no price in reference: "
                    + ", ".join(unmatched_nums))
        nc.font = Font(name="Arial", size=9, italic=True, color=DARK_GRAY)
        nc.fill = PatternFill("solid", fgColor=UNMATCHED_FILL)
        nc.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bom_purchasing_inventor.py::test_unmatched_parts_are_highlighted_and_listed -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bom_purchasing.py tests/test_bom_purchasing_inventor.py
git commit -m "feat(purchasing): highlight and list unmatched (unpriced) parts"
```

---

### Task 8: GUI reminder + `.txt` filetype + MCP docstring

**Files:**
- Modify: `gui/purchasing.py` — `_on_browse_bom` (604-614), the toggle label (275), `_on_generate` filetype message (652)
- Modify: `mcp_server.py` — `vault_generate_purchasing_sheet_from_file` docstring (~1369-1393)
- Test: manual (GUI) — no unit test; verify launch

- [ ] **Step 1: Add the reminder + `.txt` to the file browser**

In `gui/purchasing.py`, replace `_on_browse_bom` (604-614) with:

```python
    def _on_browse_bom(self) -> None:
        messagebox.showinfo(
            "Before you export from Inventor",
            "1. Sort the BOM by Description (descending), then renumber the items.\n"
            "2. Use a Structured / All-Levels BOM view (needed for per-assembly costs).\n"
            "3. Include columns —\n"
            "     Required:    Item, Part Number, QTY\n"
            "     Recommended: Description, Unit QTY, BOM Structure, REV,\n"
            "                  Material, Material Finish\n"
            "4. Export as .xlsx (preferred), tab-delimited .txt, or .csv.",
            parent=self,
        )
        path = filedialog.askopenfilename(
            title="Select a BOM export (Inventor or Vault)",
            filetypes=[("BOM export", "*.xls *.xlsx *.csv *.txt"),
                       ("All files", "*.*")],
            parent=self,
        )
        if path:
            self.bom_path_var.set(path)
```

> Note: confirm the variable name written on select matches the existing code
> (it is `self.bom_path_var` in the current `_on_browse_bom`; keep whatever the
> current line uses).

- [ ] **Step 2: Update the toggle label and the generate-flow message**

Change the import toggle label (275) from `"Import BOM file (.xlsx / .csv)"` to:

```python
            toggles, text="Import BOM file (Inventor or Vault: .xlsx/.xls/.csv/.txt)",
```

Change the `_on_generate` guidance string (652) from `"Select a Vault BOM export (.xlsx, .xls or .csv)."` to:

```python
                    "Select a BOM export (.xlsx, .xls, .csv, or .txt).",
```

- [ ] **Step 3: Update the MCP tool docstring**

In `mcp_server.py`, in `vault_generate_purchasing_sheet_from_file`'s docstring, replace the file-type sentence with:

```python
        Accepts a Vault BOM export or an Inventor BOM export (auto-detected):
        .xlsx, .xls, .csv, or tab-delimited .txt. For Inventor, use a
        Structured / All-Levels view and include at least Item, Part Number,
        and QTY (Description, Unit QTY, BOM Structure, REV, Material, and
        Material Finish are used when present).
```

- [ ] **Step 4: Verify the GUI launches**

Run: `python app.py --gui` (or `python -c "import gui.purchasing"` to confirm it imports).
Expected: no import/syntax errors; the purchasing tool's Import mode shows the new label; browsing shows the reminder then a picker listing `.txt`.

- [ ] **Step 5: Commit**

```bash
git add gui/purchasing.py mcp_server.py
git commit -m "feat(purchasing): Inventor export reminder + .txt support in GUI and MCP tool"
```

---

### Task 9: End-to-end verification with the real CD-001608 export

**Files:**
- Create: `tests/fixtures/CD-001608-inventor-bom.txt` (paste the user's export verbatim)
- Test: `tests/test_bom_purchasing_inventor.py`

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/CD-001608-inventor-bom.txt` with the exact tab-separated
content the user provided (header row `Item	Part Number	Thumbnail	BOM Structure	Unit QTY	QTY	Stock Number	Description	REV` followed by all 44 data rows). Preserve tabs and empty cells.

- [ ] **Step 2: Write the integration test**

```python
def test_cd001608_end_to_end(tmp_path, monkeypatch):
    fixture = os.path.join(ROOT, "tests", "fixtures", "CD-001608-inventor-bom.txt")

    # Hermetic: no reference file → everything unmatched, costs blank.
    monkeypatch.setattr(bp, "find_purchased_items_file", lambda: None)

    result = bp.generate_from_file(fixture, "CD-001608", str(tmp_path))
    assert not result.get("error"), result

    wb = __import__("openpyxl").load_workbook(result["output_path"])
    assert set(["Purchasing", "By Vendor", "Assembly Costs"]).issubset(wb.sheetnames)

    ws = wb["Purchasing"]
    header = [c.value for c in ws[3]]
    num_col = header.index("Number") + 1
    qty_col = header.index("Item Qty") + 1
    numbers = [ws.cell(r, num_col).value for r in range(4, 4 + 44)]
    qtys = [ws.cell(r, qty_col).value for r in range(4, 4 + 44)]

    assert "SF-001580" in numbers                 # part numbers populated
    assert 120 in qtys                            # a real quantity from QTY
    # a library part with no SF number still appears (will be unmatched)
    assert any(isinstance(n, str) and n.startswith("ISO 4762") for n in numbers)

    # assemblies present in the summary (e.g. item 14 "CD-001621")
    acost = "\n".join(
        str(c.value) for col in wb["Assembly Costs"].iter_cols()
        for c in col if c.value
    )
    assert "CD-001621" in acost and "GRAND TOTAL" in acost
```

- [ ] **Step 3: Run the integration test**

Run: `pytest tests/test_bom_purchasing_inventor.py::test_cd001608_end_to_end -v`
Expected: PASS.

- [ ] **Step 4: Run the whole suite (regression)**

Run: `pytest tests/ -v`
Expected: all pass (existing wrike/smoke tests unaffected).

- [ ] **Step 5: Manual real-run (with reference file available)**

Run the purchasing GUI, import `tests/fixtures/CD-001608-inventor-bom.txt` (or a fresh Inventor export), generate, and open the workbook. Confirm: quantities populate, Assembly Costs shows per-assembly costs + grand total, and library/Make parts appear highlighted in the unmatched list.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/CD-001608-inventor-bom.txt tests/test_bom_purchasing_inventor.py
git commit -m "test(purchasing): end-to-end Inventor CD-001608 BOM import"
```

---

## Self-review

**Spec coverage:**
- §1.1 import/auto-detect/filetypes → Tasks 2, 3, 4. ✅
- §1.1 header map + Source translation → Task 2. ✅
- §1.1 Material precedence + Material Finish column → Tasks 4, 5. ✅
- §1.2 per-assembly cost + extended qty (via existing formula roll-up) → Task 5 (detection) + Task 6 (summary). ✅
- §1.2 cost source & $0 limitation → surfaced by Task 7. ✅
- §1.3 Assembly Costs summary sheet (Option A) → Task 6. ✅
- §1.3 unmatched shown (highlight + list) → Task 7. ✅
- §1.4 export reminder (GUI + tool description) → Task 8. ✅
- §1.5 touch points (bom_purchasing/mcp_server/gui.purchasing) → Tasks 2-8. ✅
- Deliverable 2 GUI flags → Task 1. ✅
- Testing/verification (CD-001608, regression, GUI headless) → Tasks 1, 9. ✅
- Deferred CAD/iProperty rewrite → intentionally out of scope; no task. ✅

**Placeholder scan:** No TBD/TODO; all code steps show complete code. The one advisory note in Task 8 Step 1 (confirm the existing var name) is a safety check, not a missing implementation.

**Type/name consistency:** `coerce_bom_dataframe`, `read_bom_file`, `_build_assembly_costs_tab`, `UNMATCHED_FILL`, `INVENTOR_FIELD_MAP`, `SOURCE_VALUE_MAP`, `self.tool_buttons` are used consistently across tasks. Sheet geometry constants (`HDR_ROW=3`, `FIRST_DATA_ROW=4`, `Cost Per`/`Sub Total` via `ALL_COLUMNS.index`) match `build_purchasing_sheet`. `PURCHASE_COLUMNS` change keeps `ALL_COLUMNS = BOM_COLUMNS + PURCHASE_COLUMNS` valid (dynamic `.index()` lookups absorb the inserted column).

**Note for the implementer:** `build_children_map` and the roll-up formulas assume a positional (0..n-1) index; `coerce_bom_dataframe` calls `reset_index(drop=True)` to guarantee it. Keep that when touching the data path.
