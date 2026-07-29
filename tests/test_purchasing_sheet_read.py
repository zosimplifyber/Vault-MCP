# tests/test_purchasing_sheet_read.py
"""Reading a generated purchasing workbook back in.

The headline test is a round trip: build a sheet with build_purchasing_sheet,
read it with read_purchasing_sheet, and check the rows survive. That is the
whole reason the reader lives beside the writer — it fails the day a column
moves.
"""
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bom_purchasing as bp  # noqa: E402

BUY = {"Number": "SF-000067", "Title": "SF-000067", "Row Order": "1",
       "Item Qty": 2, "Units": "Each", "Description (Item,CO)": "pull handle",
       "Source": "Buy", "Vendor": "Acme", "Cost Per": 1.5,
       "Lead Time (Business Days)": 5}
MAKE = {"Number": "CD-001200", "Title": "CD-001200", "Row Order": "2",
        "Item Qty": 1, "Units": "Each", "Description (Item,CO)": "adapter plate",
        "Source": "Make", "Vendor": "Machine Shop", "Cost Per": 40.0,
        "Lead Time (Business Days)": 15}


def _df(rows):
    df, err = bp.coerce_bom_dataframe(pd.DataFrame(rows))
    assert err is None, err
    return df


def _sheet(tmp_path, rows, assembly="CD-001608"):
    """Write a purchasing workbook and return its path."""
    out = tmp_path / "CD-001608 Purchasing Sheet.xlsx"
    bp.build_purchasing_sheet(_df(rows), str(out), assembly)
    return str(out)


def test_round_trip_preserves_the_rows(tmp_path):
    path = _sheet(tmp_path, [BUY, MAKE])
    df, assembly, error = bp.read_purchasing_sheet(path)

    assert error is None
    assert assembly == "CD-001608"
    assert len(df) == 2

    by_title = {r["Title"]: r for _i, r in df.iterrows()}
    assert set(by_title) == {"SF-000067", "CD-001200"}

    make = by_title["CD-001200"]
    assert make["Source"] == "Make"
    assert make["Vendor"] == "Machine Shop"
    assert make["Description (Item,CO)"] == "adapter plate"
    assert float(make["Item Qty"]) == 1.0
    assert float(make["Cost Per"]) == 40.0
    assert float(make["Lead Time (Business Days)"]) == 15.0


def test_the_canonical_column_names_come_back(tmp_path):
    """The sheet heads them "Name" and "Description"; the reader restores the
    internal names the rest of the codebase keys on."""
    path = _sheet(tmp_path, [BUY])
    df, _assembly, error = bp.read_purchasing_sheet(path)

    assert error is None
    assert "Title" in df.columns
    assert "Description (Item,CO)" in df.columns
    assert "Name" not in df.columns
