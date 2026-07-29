"""Unit tests for the BOM → Wrike manufacturing task builder.

No network: Vault and Wrike are both faked. Workbooks are built in-test with
bom_purchasing.build_purchasing_sheet, so the fixtures exercise the real
writer rather than a hand-rolled imitation of it.
"""
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bom_purchasing as bp  # noqa: E402
import wrike_mfg_tasks as wmt  # noqa: E402


def _row(**over):
    base = {"Number": "CD-001200", "Title": "CD-001200", "Row Order": "1",
            "Item Qty": 1, "Units": "Each",
            "Description (Item,CO)": "adapter plate", "Source": "Make",
            "Vendor": "Xometry", "Cost Per": 40.0, "Shipping": 0.0,
            "Tax/Tariff": 0.0, "Lead Time (Business Days)": 15}
    base.update(over)
    return base


def _sheet(tmp_path, rows, assembly="CD-001608"):
    df, err = bp.coerce_bom_dataframe(pd.DataFrame(rows))
    assert err is None, err
    out = tmp_path / "CD-001608 Purchasing Sheet.xlsx"
    bp.build_purchasing_sheet(df, str(out), assembly)
    return str(out)


# --------------------------------------------------------------------- load

def test_load_reads_parts_and_the_assembly_number(tmp_path):
    path = _sheet(tmp_path, [
        _row(Number="CD-001200", Title="CD-001200"),
        _row(Number="SF-000067", Title="SF-000067", Source="Buy",
             Vendor="McMaster-Carr", **{"Item Qty": 4, "Cost Per": 1.5,
                                        "Lead Time (Business Days)": 3,
                                        "Row Order": "2"}),
    ])
    parts, assembly, error = wmt.load_order_parts(path)

    assert error is None
    assert assembly == "CD-001608"
    by_title = {p.title: p for p in parts}
    assert set(by_title) == {"CD-001200", "SF-000067"}

    make = by_title["CD-001200"]
    assert make.kind == wmt.KIND_MAKE
    assert make.sheet_vendor == "Xometry"
    assert make.lead_time_days == 15
    assert make.qty == 1.0
    assert make.line_total == 40.0

    buy = by_title["SF-000067"]
    assert buy.kind == wmt.KIND_BUY
    assert buy.qty == 4.0
    assert buy.line_total == 6.0


def test_line_total_adds_shipping_and_tax(tmp_path):
    path = _sheet(tmp_path, [_row(**{"Item Qty": 2, "Cost Per": 10.0,
                                     "Shipping": 5.0, "Tax/Tariff": 1.5})])
    parts, _assembly, error = wmt.load_order_parts(path)
    assert error is None
    assert parts[0].line_total == 26.5


def test_roll_up_rows_are_excluded_and_their_children_survive(tmp_path):
    """An assembly's Sub Total is a SUM of its children. Ordering both the
    assembly and its children puts the same metal on the PO twice."""
    path = _sheet(tmp_path, [
        _row(Number="CD-001613", Title="CD-001613", **{"Row Order": "1"}),
        _row(Number="CD-001612", Title="CD-001612", **{"Row Order": "1.1"}),
        _row(Number="CD-001577", Title="CD-001577", **{"Row Order": "1.2"}),
    ])
    parts, _assembly, error = wmt.load_order_parts(path)

    assert error is None
    assert {p.title for p in parts} == {"CD-001612", "CD-001577"}


def test_duplicate_titles_collapse_with_summed_qty_and_longest_lead(tmp_path):
    """A sheet lists a part once per place it appears in the BOM. One line
    item per part per order."""
    path = _sheet(tmp_path, [
        _row(**{"Row Order": "1", "Item Qty": 2,
                "Lead Time (Business Days)": 10}),
        _row(**{"Row Order": "2", "Item Qty": 3,
                "Lead Time (Business Days)": 20}),
    ])
    parts, _assembly, error = wmt.load_order_parts(path)

    assert error is None
    assert len(parts) == 1
    assert parts[0].qty == 5.0
    assert parts[0].lead_time_days == 20


def test_a_blank_title_row_is_dropped(tmp_path):
    path = _sheet(tmp_path, [
        _row(**{"Row Order": "1"}),
        _row(Number=None, Title=None, **{"Row Order": "2",
                                         "Description (Item,CO)": "ghost"}),
    ])
    parts, _assembly, error = wmt.load_order_parts(path)

    assert error is None
    assert [p.title for p in parts] == ["CD-001200"]


def test_a_non_numeric_qty_counts_as_one(tmp_path):
    path = _sheet(tmp_path, [_row(**{"Item Qty": "-"})])
    parts, _assembly, error = wmt.load_order_parts(path)

    assert error is None
    assert parts[0].qty == 1.0


def test_anything_other_than_make_is_treated_as_bought(tmp_path):
    path = _sheet(tmp_path, [
        _row(Number="A", Title="A", Source="Buy", **{"Row Order": "1"}),
        _row(Number="B", Title="B", Source="Other", **{"Row Order": "2"}),
    ])
    parts, _assembly, error = wmt.load_order_parts(path)

    assert error is None
    assert {p.kind for p in parts} == {wmt.KIND_BUY}


def test_a_reader_error_is_returned_not_raised(tmp_path):
    path = tmp_path / "CD-001608 BOM.xlsx"
    pd.DataFrame([{"Item": "1"}]).to_excel(path, index=False)

    parts, assembly, error = wmt.load_order_parts(str(path))

    assert error is not None
    assert parts == []
    assert assembly == ""
