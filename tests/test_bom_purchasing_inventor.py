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
