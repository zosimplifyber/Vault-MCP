import ast
import os
import sys

import openpyxl
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bom_purchasing as bp  # noqa: E402


def _ref_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "purchased parts"
    ws.append(["Number", "Vendor", "Cost Per"])
    ws.append(["SF-1", "Acme", 3.5])
    wb.save(path)


def test_reference_path_override_is_used(tmp_path, monkeypatch):
    # Auto-discovery finds nothing, so only the override can supply vendor data.
    monkeypatch.setattr(bp, "find_purchased_items_file", lambda: None)
    ref = tmp_path / "ref.xlsx"
    _ref_workbook(ref)
    bom = tmp_path / "bom.txt"
    bom.write_text(
        "Item\tPart Number\tBOM Structure\tQTY\tDescription\n"
        "1\tSF-1\tPurchased\t2\tpart\n",
        encoding="utf-8",
    )
    result = bp.generate_from_file(str(bom), "ASM", str(tmp_path), reference_path=str(ref))
    assert not result.get("error"), result
    ws = openpyxl.load_workbook(result["output_path"])["Purchasing"]
    header = [c.value for c in ws[3]]
    num, ven = header.index("Number") + 1, header.index("Vendor") + 1
    vendors = {ws.cell(r, num).value: ws.cell(r, ven).value for r in range(4, 5)}
    assert vendors.get("SF-1") == "Acme"   # from the override, not OneDrive


def test_no_reference_path_falls_back_to_autofind(tmp_path, monkeypatch):
    hits = {}
    monkeypatch.setattr(
        bp, "find_purchased_items_file", lambda: hits.setdefault("called", True) and None
    )
    bom = tmp_path / "bom.txt"
    bom.write_text("Item\tPart Number\tQTY\tDescription\n1\tSF-1\t2\tp\n", encoding="utf-8")
    result = bp.generate_from_file(str(bom), "ASM", str(tmp_path))  # no reference_path
    assert not result.get("error"), result
    assert hits.get("called")   # default path still auto-discovers
