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


def test_coerce_errors_when_number_mixes_none_and_blank():
    df = pd.DataFrame({
        "Part Number": [None, "", None],
        "QTY": [1, 2, 3],
        "Description": ["a", "b", "c"],
    })
    out, err = bp.coerce_bom_dataframe(df)
    assert err is not None and "part number" in err.lower()


def test_coerce_does_not_crash_on_both_number_and_part_number():
    # Inventor branch triggers (missing most canonical cols) but Number already
    # exists alongside its synonym Part Number -> must not create a duplicate col.
    df = pd.DataFrame({
        "Number": ["SF-1", "SF-2"],
        "Part Number": ["X-1", "X-2"],
        "QTY": [1, 2],
        "Description": ["a", "b"],
    })
    out, err = bp.coerce_bom_dataframe(df)
    assert err is None
    assert list(out["Number"]) == ["SF-1", "SF-2"]      # canonical wins
    assert list(out["Item Qty"]) == [1, 2]


def test_coerce_errors_when_all_quantities_blank():
    df = pd.DataFrame({
        "Part Number": ["A", "B"],
        "QTY": ["", ""],
        "Description": ["a", "b"],
    })
    out, err = bp.coerce_bom_dataframe(df)
    assert err is not None and "quantit" in err.lower()


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

    import openpyxl
    wb = openpyxl.load_workbook(result["output_path"])
    ws = wb["Purchasing"]
    header = [c.value for c in ws[3]]
    mat_col = header.index("Material") + 1
    num_col = header.index("Number") + 1
    mats = {ws.cell(r, num_col).value: ws.cell(r, mat_col).value
            for r in range(4, 4 + 3)}
    assert mats["SF-001580"] == "Aluminum"       # from export
    assert mats["SF-001803"] == "REF-MATERIAL"   # blank in export → ref
