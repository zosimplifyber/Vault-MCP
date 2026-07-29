"""Unit tests for the BOM → Wrike manufacturing task builder.

No network: Vault and Wrike are both faked. Workbooks are built in-test with
bom_purchasing.build_purchasing_sheet, so the fixtures exercise the real
writer rather than a hand-rolled imitation of it.
"""
import os
import sys
from datetime import date

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


# ----------------------------------------------------------------- grouping

def _resolved(title, supplier, kind=wmt.KIND_MAKE, lead=None, qty=1.0):
    part = wmt.OrderPart(title=title, sheet_vendor=supplier, kind=kind,
                         qty=qty, lead_time_days=lead)
    return wmt.ReconcileRow(part=part, status=wmt.STATUS_MATCHED,
                            proposal=supplier, chosen=supplier)


def test_one_supplier_yields_one_order_however_many_parts():
    rows = [_resolved(f"ISO-{n}", "McMaster-Carr", wmt.KIND_BUY)
            for n in range(11)]
    orders = wmt.group_orders(rows)

    assert len(orders) == 1
    assert len(orders[0].parts) == 11


def test_a_mixed_supplier_gets_one_order_with_a_manufacturing_stage():
    """One PO to MiSUMi means one set of tasks."""
    rows = [_resolved("CD-001366", "MiSUMi", wmt.KIND_MAKE),
            _resolved("ISO 4762", "MiSUMi", wmt.KIND_BUY),
            _resolved("ISO 4032", "MiSUMi", wmt.KIND_BUY)]
    orders = wmt.group_orders(rows)

    assert len(orders) == 1
    assert orders[0].has_make
    assert [p.title for p in orders[0].make_parts] == ["CD-001366"]


def test_a_buy_only_supplier_has_no_manufacturing_stage():
    rows = [_resolved("ISO 4762", "McMaster-Carr", wmt.KIND_BUY)]
    orders = wmt.group_orders(rows)

    assert not orders[0].has_make
    assert [s for s in orders[0].stages] == [wmt.STAGE_PURCHASING,
                                             wmt.STAGE_SHIPPING]


def test_supplier_spellings_collapse_to_one_order():
    rows = [_resolved("A", "Xometry"), _resolved("B", "xometry  ")]
    orders = wmt.group_orders(rows)

    assert len(orders) == 1
    assert orders[0].supplier == "Xometry"    # the first row's spelling


def test_excluded_and_unresolved_rows_contribute_to_no_order():
    keep = _resolved("A", "Xometry")
    dropped = _resolved("B", "Xometry")
    dropped.excluded = True
    blocked = wmt.ReconcileRow(part=wmt.OrderPart(title="C"),
                               status=wmt.STATUS_MISMATCH)
    orders = wmt.group_orders([keep, dropped, blocked])

    assert [p.title for p in orders[0].parts] == ["A"]


# --------------------------------------------------------------- scheduling

def test_business_days_skip_the_weekend():
    friday = date(2026, 8, 7)
    assert wmt.add_business_days(friday, 1) == date(2026, 8, 10)
    assert wmt.add_business_days(friday, 0) == friday


def test_a_weekend_start_snaps_forward():
    saturday = date(2026, 8, 8)
    assert wmt.add_business_days(saturday, 0) == date(2026, 8, 10)


def test_lead_time_drives_manufacturing_for_a_make_order():
    rows = [_resolved("A", "Xometry", wmt.KIND_MAKE, lead=15),
            _resolved("B", "Xometry", wmt.KIND_MAKE, lead=5)]
    orders = wmt.schedule_orders(wmt.group_orders(rows),
                                 start=date(2026, 8, 3),
                                 durations=wmt.Durations())

    stages = {s.stage: s for s in orders[0].schedule}
    assert stages[wmt.STAGE_PURCHASING].start == date(2026, 8, 3)
    assert stages[wmt.STAGE_PURCHASING].due == date(2026, 8, 4)
    assert stages[wmt.STAGE_MANUFACTURING].start == date(2026, 8, 5)
    assert stages[wmt.STAGE_MANUFACTURING].due == date(2026, 8, 25)
    assert stages[wmt.STAGE_SHIPPING].start == date(2026, 8, 26)
    assert stages[wmt.STAGE_SHIPPING].due == date(2026, 8, 28)


def test_lead_time_drives_shipping_when_nothing_is_made():
    """A McMaster order's lead time IS its ship time. Putting it on a stage
    that does not exist would lose it."""
    rows = [_resolved("ISO", "McMaster-Carr", wmt.KIND_BUY, lead=3)]
    orders = wmt.schedule_orders(wmt.group_orders(rows),
                                 start=date(2026, 8, 3),
                                 durations=wmt.Durations())

    stages = {s.stage: s for s in orders[0].schedule}
    assert wmt.STAGE_MANUFACTURING not in stages
    assert stages[wmt.STAGE_SHIPPING].start == date(2026, 8, 5)
    assert stages[wmt.STAGE_SHIPPING].due == date(2026, 8, 7)


def test_a_blank_lead_time_falls_back_to_the_default():
    rows = [_resolved("A", "Xometry", wmt.KIND_MAKE, lead=None)]
    durations = wmt.Durations(purchasing=2, manufacturing=10, shipping=3)
    orders = wmt.schedule_orders(wmt.group_orders(rows),
                                 start=date(2026, 8, 3), durations=durations)

    stages = {s.stage: s for s in orders[0].schedule}
    assert stages[wmt.STAGE_MANUFACTURING].start == date(2026, 8, 5)
    assert stages[wmt.STAGE_MANUFACTURING].due == date(2026, 8, 18)


def test_the_order_span_covers_every_stage():
    rows = [_resolved("A", "Xometry", wmt.KIND_MAKE, lead=15)]
    orders = wmt.schedule_orders(wmt.group_orders(rows),
                                 start=date(2026, 8, 3),
                                 durations=wmt.Durations())

    assert orders[0].start == date(2026, 8, 3)
    assert orders[0].due == date(2026, 8, 28)


# ---------------------------------------------------------------- rendering

def _scheduled_order(parts, supplier="Xometry"):
    rows = [wmt.ReconcileRow(part=p, status=wmt.STATUS_MATCHED,
                             proposal=supplier, chosen=supplier)
            for p in parts]
    return wmt.schedule_orders(wmt.group_orders(rows),
                               start=date(2026, 8, 3),
                               durations=wmt.Durations())[0]


def test_the_parent_title_names_the_build_and_supplier():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    assert wmt.parent_title("CD-001608", order) == "CD-001608 - Xometry"


def test_a_stage_title_still_reads_alone_in_a_my_work_queue():
    """Subtasks show detached from their parent in list views."""
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    assert (wmt.stage_title("CD-001608", order, wmt.STAGE_MANUFACTURING)
            == "CD-001608 Xometry - 2. Manufacturing")
    assert (wmt.stage_title("CD-001608", order, wmt.STAGE_PURCHASING)
            == "CD-001608 Xometry - 1. Purchasing")


def test_a_buy_only_order_numbers_shipping_second():
    order = _scheduled_order([wmt.OrderPart(title="ISO", kind=wmt.KIND_BUY)],
                             supplier="McMaster-Carr")
    assert (wmt.stage_title("CD-001608", order, wmt.STAGE_SHIPPING)
            == "CD-001608 McMaster-Carr - 2. Shipping")


def test_the_parent_description_carries_every_part_and_the_total():
    order = _scheduled_order([
        wmt.OrderPart(title="CD-001200", description="adapter plate",
                      qty=2, unit_cost=40.0),
        wmt.OrderPart(title="CD-001201", description="bracket",
                      qty=1, unit_cost=10.0),
    ])
    html = wmt.render_description(order, wmt.STAGE_PARENT,
                                  source_name="CD-001608 Purchasing Sheet.xlsx")

    assert "CD-001200" in html and "CD-001201" in html
    assert "adapter plate" in html
    assert "CD-001608 Purchasing Sheet.xlsx" in html
    assert "90.00" in html                 # 2*40 + 1*10


def test_the_manufacturing_description_lists_only_the_made_parts():
    order = _scheduled_order([
        wmt.OrderPart(title="CD-001200", kind=wmt.KIND_MAKE,
                      material="6061-T6", revision="R3"),
        wmt.OrderPart(title="ISO 4762", kind=wmt.KIND_BUY),
    ])
    html = wmt.render_description(order, wmt.STAGE_MANUFACTURING,
                                  source_name="sheet.xlsx")

    assert "CD-001200" in html
    assert "6061-T6" in html
    assert "ISO 4762" not in html


def test_the_purchasing_description_has_costs_and_a_checklist():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200", qty=2,
                                            unit_cost=40.0)])
    html = wmt.render_description(order, wmt.STAGE_PURCHASING,
                                  source_name="sheet.xlsx")

    assert "80.00" in html
    assert "PO issued" in html


def test_descriptions_are_html_because_wrike_collapses_newlines():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    html = wmt.render_description(order, wmt.STAGE_SHIPPING,
                                  source_name="sheet.xlsx")

    assert "<table" in html or "<br" in html


def test_values_are_escaped():
    order = _scheduled_order([wmt.OrderPart(title="A<b>",
                                            description="1 < 2 & 3")])
    html = wmt.render_description(order, wmt.STAGE_PARENT,
                                  source_name="sheet.xlsx")

    assert "A&lt;b&gt;" in html
    assert "1 &lt; 2 &amp; 3" in html


# ------------------------------------------------------------- fake wrike

class FakeWrike:
    """Records calls and hands back sequential task ids.

    ``existing`` is the list of task dicts already in the folder.
    ``fail_titles`` are titles whose create should fail.
    """

    def __init__(self, existing=(), fail_titles=(), dependency_fails=False):
        self.existing = list(existing)
        self.fail_titles = set(fail_titles)
        self.dependency_fails = dependency_fails
        self.created = []
        self.dependencies = []
        self.search_calls = []
        self._next = 0

    async def search_tasks(self, title=None, status=None, folder_id=None,
                           page_size=100):
        self.search_calls.append({"title": title, "status": status,
                                  "folder_id": folder_id})
        rows = self.existing
        if title:
            rows = [r for r in rows if title.lower() in r["title"].lower()]
        return {"error": False, "status_code": 200,
                "data": {"data": rows, "count": len(rows)}}

    async def create_task(self, folder_id, title, description=None,
                          start_date=None, due_date=None, responsibles=None,
                          super_task_ids=None, **kwargs):
        self.created.append({
            "folder_id": folder_id, "title": title, "description": description,
            "start_date": start_date, "due_date": due_date,
            "responsibles": responsibles, "super_task_ids": super_task_ids,
        })
        if title in self.fail_titles:
            return {"error": True, "status_code": 400, "data": "nope"}
        self._next += 1
        return {"error": False, "status_code": 200,
                "data": {"data": [{"id": f"IEAA{self._next}"}]}}

    async def add_dependency(self, task_id, predecessor_id,
                             relation_type="FinishToStart"):
        self.dependencies.append({"task_id": task_id,
                                  "predecessor_id": predecessor_id,
                                  "relation_type": relation_type})
        if self.dependency_fails:
            return {"error": True, "status_code": 400, "data": "no link"}
        return {"error": False, "status_code": 200, "data": {"data": []}}


OWNERS = {wmt.STAGE_PURCHASING: "KUAAP", wmt.STAGE_MANUFACTURING: "KUAAM",
          wmt.STAGE_SHIPPING: "KUAAS"}


async def _create(orders, wrike, build="CD-001608"):
    return await wmt.create_orders(
        wrike, folder_id="IEAF1", build=build, orders=orders,
        owners=OWNERS, source_name="CD-001608 Purchasing Sheet.xlsx")


# ----------------------------------------------------------------- creation

async def test_a_make_order_creates_a_parent_and_three_subtasks():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200", lead_time_days=15)])
    wrike = FakeWrike()
    result = await _create([order], wrike)

    assert result.orders_created == 1
    assert [c["title"] for c in wrike.created] == [
        "CD-001608 - Xometry",
        "CD-001608 Xometry - 1. Purchasing",
        "CD-001608 Xometry - 2. Manufacturing",
        "CD-001608 Xometry - 3. Shipping",
    ]
    assert wrike.created[0]["super_task_ids"] is None
    assert wrike.created[1]["super_task_ids"] == ["IEAA1"]


async def test_a_buy_only_order_creates_two_subtasks():
    order = _scheduled_order(
        [wmt.OrderPart(title="ISO", kind=wmt.KIND_BUY, lead_time_days=3)],
        supplier="McMaster-Carr")
    wrike = FakeWrike()
    await _create([order], wrike)

    assert len(wrike.created) == 3          # parent + 2 stages
    assert "Manufacturing" not in " ".join(c["title"] for c in wrike.created)


async def test_stages_are_chained_finish_to_start():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike()
    await _create([order], wrike)

    # subtasks are IEAA2, IEAA3, IEAA4 under parent IEAA1
    assert wrike.dependencies == [
        {"task_id": "IEAA3", "predecessor_id": "IEAA2",
         "relation_type": "FinishToStart"},
        {"task_id": "IEAA4", "predecessor_id": "IEAA3",
         "relation_type": "FinishToStart"},
    ]


async def test_a_buy_only_order_chains_purchasing_straight_to_shipping():
    order = _scheduled_order(
        [wmt.OrderPart(title="ISO", kind=wmt.KIND_BUY)],
        supplier="McMaster-Carr")
    wrike = FakeWrike()
    await _create([order], wrike)

    assert len(wrike.dependencies) == 1
    assert wrike.dependencies[0]["predecessor_id"] == "IEAA2"
    assert wrike.dependencies[0]["task_id"] == "IEAA3"


async def test_each_stage_carries_its_own_owner():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike()
    await _create([order], wrike)

    assert wrike.created[1]["responsibles"] == ["KUAAP"]
    assert wrike.created[2]["responsibles"] == ["KUAAM"]
    assert wrike.created[3]["responsibles"] == ["KUAAS"]


async def test_the_parent_spans_the_order_and_belongs_to_purchasing():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200",
                                            lead_time_days=15)])
    wrike = FakeWrike()
    await _create([order], wrike)

    parent = wrike.created[0]
    assert parent["start_date"] == "2026-08-03"
    assert parent["due_date"] == "2026-08-28"
    assert parent["responsibles"] == ["KUAAP"]


async def test_dates_are_sent_as_plain_iso_dates():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike()
    await _create([order], wrike)

    assert wrike.created[1]["start_date"] == "2026-08-03"
    assert wrike.created[1]["due_date"] == "2026-08-04"


# ------------------------------------------------------------------ re-runs

async def test_an_existing_order_is_skipped_and_reported():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike(existing=[{"id": "OLD", "title": "CD-001608 - Xometry",
                                 "status": "Active"}])
    result = await _create([order], wrike)

    assert result.orders_created == 0
    assert result.orders_skipped == 1
    assert wrike.created == []
    assert "CD-001608 - Xometry" in result.skipped_titles


async def test_a_completed_order_still_counts_as_existing():
    """Wrike's folder listing can filter completed tasks out. Sending no
    status param is what keeps a finished order from being recreated."""
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike(existing=[{"id": "OLD", "title": "CD-001608 - Xometry",
                                 "status": "Completed"}])
    result = await _create([order], wrike)

    assert result.orders_skipped == 1
    assert all(c["status"] is None for c in wrike.search_calls)


async def test_a_substring_title_match_is_not_treated_as_existing():
    """Wrike's title filter is a substring match; the comparison is exact."""
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike(existing=[{"id": "OLD",
                                 "title": "CD-001608 - Xometry Rework",
                                 "status": "Active"}])
    result = await _create([order], wrike)

    assert result.orders_created == 1


async def test_a_new_supplier_is_created_while_an_existing_one_is_skipped():
    made = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    bought = _scheduled_order([wmt.OrderPart(title="ISO", kind=wmt.KIND_BUY)],
                              supplier="McMaster-Carr")
    wrike = FakeWrike(existing=[{"id": "OLD", "title": "CD-001608 - Xometry",
                                 "status": "Active"}])
    result = await _create([made, bought], wrike)

    assert result.orders_created == 1
    assert result.orders_skipped == 1
    assert wrike.created[0]["title"] == "CD-001608 - McMaster-Carr"


# --------------------------------------------------------------- failures

async def test_a_failed_subtask_reports_what_was_created_and_moves_on():
    made = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    bought = _scheduled_order([wmt.OrderPart(title="ISO", kind=wmt.KIND_BUY)],
                              supplier="McMaster-Carr")
    wrike = FakeWrike(fail_titles=["CD-001608 Xometry - 2. Manufacturing"])
    result = await _create([made, bought], wrike)

    assert result.failures
    assert any("Manufacturing" in f for f in result.failures)
    # the next supplier still ran
    assert any(c["title"] == "CD-001608 - McMaster-Carr" for c in wrike.created)


async def test_a_failed_parent_skips_its_subtasks_entirely():
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike(fail_titles=["CD-001608 - Xometry"])
    result = await _create([order], wrike)

    assert len(wrike.created) == 1
    assert result.orders_created == 0
    assert result.failures


async def test_an_unscheduled_order_is_never_linked():
    """Wrike rejects a dependency between undated tasks outright, so calling
    add_dependency for one would be a guaranteed error rather than a link."""
    rows = [wmt.ReconcileRow(part=wmt.OrderPart(title="CD-001200"),
                             status=wmt.STATUS_MATCHED, proposal="Xometry",
                             chosen="Xometry")]
    order = wmt.group_orders(rows)[0]      # grouped but never scheduled
    wrike = FakeWrike()
    result = await _create([order], wrike)

    assert wrike.dependencies == []
    assert result.dependency_failures == []


async def test_a_dependency_failure_leaves_the_tasks_in_place():
    """The tasks are the product; the link is the garnish."""
    order = _scheduled_order([wmt.OrderPart(title="CD-001200")])
    wrike = FakeWrike(dependency_fails=True)
    result = await _create([order], wrike)

    assert result.orders_created == 1
    assert len(wrike.created) == 4
    assert result.dependency_failures
