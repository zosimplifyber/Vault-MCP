import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bom_list_sync as bls  # noqa: E402


# internal -> display, mirroring the real "Engineering Purchased Parts" list
DISP = {
    "Title": "Title",
    "LinkTitle": "Number",
    "field_1": "Title (Item,CO)",
    "field_2": "Description (Item,CO)",
    "field_3": "Material",
    "field_4": "Vendor",
    "field_5": "Vendor Number",
    "field_6": "Cost Per",
}

# already in the list
EXISTING = [
    ("1", {"Title": "SF-000067", "field_2": "rectangular pull handle"}),
    ("2", {"Title": "SF-000105", "field_2": "threaded bumper"}),
]


class FakeClient:
    def __init__(self):
        self.created = []

    def column_display_map(self):
        return dict(DISP)

    def iter_rows(self):
        for item_id, fields in EXISTING:
            yield item_id, dict(fields)

    def create_list_item(self, fields):
        self.created.append(dict(fields))
        return {"id": str(100 + len(self.created)), "fields": fields}


def bom():
    # SF-000067 already exists; two are new; one new is duplicated
    return pd.DataFrame({
        "Number": ["SF-000067", "SF-999001", "SF-999002", "SF-999002"],
        "Description (Item,CO)": ["rectangular pull handle", "widget bracket",
                                  "spacer", "spacer"],
        "Material": ["plastic", "aluminum", "nylon", "nylon"],
        "Source": ["Buy", "Buy", "Make", "Make"],
    })


class TestPlanMissing:
    def test_dry_run_finds_missing_and_writes_nothing(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=True)
        assert client.created == []
        assert set(report["missing"]) == {"SF-999001", "SF-999002"}
        assert report["created"] == 0

    def test_dedupes_repeated_part_numbers(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=True)
        assert report["missing"].count("SF-999002") == 1

    def test_existing_parts_are_not_added(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=True)
        assert "SF-000067" not in report["missing"]


class TestApply:
    def test_apply_creates_items_with_mapped_fields(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=False)
        assert report["created"] == 2
        by_title = {c["Title"]: c for c in client.created}
        assert set(by_title) == {"SF-999001", "SF-999002"}
        w = by_title["SF-999001"]
        assert w["Title"] == "SF-999001"            # built-in key -> Number column
        assert w["field_2"] == "widget bracket"     # description
        assert w["field_3"] == "aluminum"           # material

    def test_source_filter_limits_to_buy(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=False,
                                          sources={"Buy"})
        titles = {c["Title"] for c in client.created}
        assert titles == {"SF-999001"}              # SF-999002 is a Make part
        assert report["created"] == 1
