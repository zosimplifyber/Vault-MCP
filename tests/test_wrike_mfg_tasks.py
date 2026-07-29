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


# ---------------------------------------------------------------- fake vault

class FakeVaultAPI:
    """Records calls and replays canned /file-versions responses.

    ``vendor_map`` maps a part title to the Vendor property value Vault should
    report. A title absent from the map returns no matching file at all.
    """

    def __init__(self, vendor_map=None, errors=(), raises=(), truncate=()):
        self.vendor_map = vendor_map or {}
        self.errors = set(errors)
        self.raises = set(raises)
        self.truncate = set(truncate)
        self.calls = []

    async def search_file_versions(self, vault_id=None, query=None, **kwargs):
        self.calls.append({"vault_id": vault_id, "query": query, **kwargs})
        if query in self.raises:
            raise RuntimeError(f"search blew up for {query}")
        if query in self.errors:
            return {"error": True, "status_code": 500, "data": "boom"}
        results = []
        if query in self.vendor_map:
            results.append(_file_hit(f"{query}.ipt", self.vendor_map[query]))
        if query in self.truncate:
            results = [_file_hit(f"OTHER-{n}.ipt", "")
                       for n in range(wmt.SEARCH_LIMIT)]
        return {"error": False, "status_code": 200,
                "data": {"results": results,
                         "included": {"propertyDefinition": {
                             "PD1": {"displayName": "Vendor"}}}}}


def _file_hit(name, vendor):
    return {"entityType": "FileVersion", "name": name, "id": "1",
            "properties": [{"propertyDefinitionId": "PD1", "value": vendor}]}


async def _reconcile(parts, api):
    return await wmt.reconcile_vendors(api, "1", parts)


def _part(title, vendor, kind=wmt.KIND_MAKE):
    return wmt.OrderPart(title=title, sheet_vendor=vendor, kind=kind)


# ----------------------------------------------------------------- statuses

async def test_agreeing_vendors_are_matched_and_chosen_automatically():
    api = FakeVaultAPI({"CD-001200": "Xometry"})
    rows = await _reconcile([_part("CD-001200", "Xometry")], api)

    assert rows[0].status == wmt.STATUS_MATCHED
    assert rows[0].chosen == "Xometry"
    assert rows[0].resolved


async def test_comparison_ignores_case_and_whitespace():
    """The reference BOM spells it McMASTER-CARR."""
    api = FakeVaultAPI({"SF-000067": "McMASTER-CARR"})
    rows = await _reconcile(
        [_part("SF-000067", "McMaster-Carr", wmt.KIND_BUY)], api)

    assert rows[0].status == wmt.STATUS_MATCHED


async def test_a_genuine_disagreement_blocks():
    api = FakeVaultAPI({"CD-001200": "Xometry"})
    rows = await _reconcile([_part("CD-001200", "Protolabs")], api)

    assert rows[0].status == wmt.STATUS_MISMATCH
    assert rows[0].proposal == ""
    assert not rows[0].resolved


async def test_one_blank_side_proposes_the_populated_one():
    api = FakeVaultAPI({"A": "", "B": "Fictiv"})
    rows = await _reconcile([_part("A", "Xometry"), _part("B", "")], api)

    assert rows[0].status == wmt.STATUS_SHEET_ONLY
    assert rows[0].proposal == "Xometry"
    assert rows[1].status == wmt.STATUS_VAULT_ONLY
    assert rows[1].proposal == "Fictiv"
    assert not rows[0].resolved          # a proposal still needs accepting


async def test_both_blank_blocks():
    api = FakeVaultAPI({"A": ""})
    rows = await _reconcile([_part("A", "")], api)

    assert rows[0].status == wmt.STATUS_BOTH_BLANK
    assert rows[0].proposal == ""


async def test_a_missing_buy_part_proposes_the_sheet_but_a_make_part_blocks():
    """A catalogue screw that was never checked into Vault is routine, and its
    sheet vendor came from the Engineering Purchased Parts list. A missing
    CD-numbered Make part is not routine."""
    api = FakeVaultAPI({})
    rows = await _reconcile([
        _part("ISO 4762 M6", "McMaster-Carr", wmt.KIND_BUY),
        _part("CD-001200", "Xometry", wmt.KIND_MAKE),
    ], api)

    assert rows[0].status == wmt.STATUS_NOT_IN_VAULT
    assert rows[0].proposal == "McMaster-Carr"
    assert rows[1].status == wmt.STATUS_NOT_IN_VAULT
    assert rows[1].proposal == ""


async def test_a_search_error_degrades_only_that_row():
    api = FakeVaultAPI({"B": "Fictiv"}, errors=["A"])
    rows = await _reconcile([_part("A", "Xometry"), _part("B", "")], api)

    assert rows[0].status == wmt.STATUS_LOOKUP_FAILED
    assert rows[0].proposal == "Xometry"    # a transient error is not evidence
    assert rows[1].status == wmt.STATUS_VAULT_ONLY


async def test_a_raising_search_does_not_sink_the_reconcile():
    api = FakeVaultAPI({"B": "Fictiv"}, raises=["A"])
    rows = await _reconcile([_part("A", "Xometry"), _part("B", "")], api)

    assert rows[0].status == wmt.STATUS_LOOKUP_FAILED
    assert len(rows) == 2


async def test_a_full_page_without_the_file_reports_truncation():
    """Saying "not in Vault" when the cap was hit would send someone to fix
    data that is already correct."""
    api = FakeVaultAPI({}, truncate=["A"])
    rows = await _reconcile([_part("A", "Xometry")], api)

    assert rows[0].status == wmt.STATUS_TRUNCATED


async def test_only_exact_basename_matches_count():
    api = FakeVaultAPI({})

    async def search(vault_id=None, query=None, **kwargs):
        return {"error": False, "status_code": 200,
                "data": {"results": [_file_hit("CD-001200-BRACKET.ipt", "Wrong")],
                         "included": {"propertyDefinition": {
                             "PD1": {"displayName": "Vendor"}}}}}

    api.search_file_versions = search
    rows = await _reconcile([_part("CD-001200", "Xometry")], api)

    assert rows[0].vault_vendor == ""
    assert rows[0].status == wmt.STATUS_NOT_IN_VAULT


async def test_non_file_version_hits_are_ignored():
    api = FakeVaultAPI({})

    async def search(vault_id=None, query=None, **kwargs):
        hit = _file_hit("CD-001200.ipt", "Wrong")
        hit["entityType"] = "ItemVersion"
        return {"error": False, "status_code": 200,
                "data": {"results": [hit],
                         "included": {"propertyDefinition": {
                             "PD1": {"displayName": "Vendor"}}}}}

    api.search_file_versions = search
    rows = await _reconcile([_part("CD-001200", "Xometry")], api)

    assert rows[0].vault_vendor == ""


async def test_the_lookup_asks_for_properties():
    """Files ignore the bare propDefIds that items use — the wrong spelling
    returns 200 with the properties silently missing."""
    api = FakeVaultAPI({"CD-001200": "Xometry"})
    await _reconcile([_part("CD-001200", "Xometry")], api)

    assert api.calls[0]["prop_def_ids"] == "all"
    assert api.calls[0]["limit"] == wmt.SEARCH_LIMIT


def test_accept_proposals_resolves_every_amber_row():
    rows = [
        wmt.ReconcileRow(part=_part("A", "X"), status=wmt.STATUS_SHEET_ONLY,
                         proposal="X"),
        wmt.ReconcileRow(part=_part("B", ""), status=wmt.STATUS_MISMATCH,
                         proposal=""),
    ]
    wmt.accept_proposals(rows)

    assert rows[0].chosen == "X"
    assert rows[1].chosen == ""          # reds are never auto-resolved
    assert wmt.unresolved_count(rows) == 1
