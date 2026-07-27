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
        self.patched = []

    def column_display_map(self):
        return dict(DISP)

    def iter_rows(self):
        for item_id, fields in EXISTING:
            yield item_id, dict(fields)

    def create_list_item(self, fields):
        self.created.append(dict(fields))
        return {"id": str(100 + len(self.created)), "fields": fields}

    def patch_fields(self, item_id, fields):
        self.patched.append((item_id, dict(fields)))


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

    def test_report_counts_bom_parts_checked_and_already_present(self):
        # existing_count is the SIZE OF THE LIST; the BOM-side counts are separate
        # so the GUI can say "x of y BOM parts" instead of quoting the list size.
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=True)
        assert report["checked"] == 3            # 4 rows, SF-999002 twice
        assert report["already_present"] == 1    # SF-000067
        assert report["existing_count"] == 2     # the list holds 2 items

    def test_checked_count_respects_the_source_filter(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=True,
                                          sources={"Buy"})
        assert report["checked"] == 2            # SF-000067 + SF-999001
        assert report["already_present"] == 1


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

    def test_title_and_vendor_number_come_from_bom_not_the_number(self):
        client = FakeClient()
        df = pd.DataFrame({
            "Number": ["SF-999001"],
            "Title (Item,CO)": ["CD-001574"],      # BOM Title, distinct from number
            "Description (Item,CO)": ["bmw bladder"],
            "Material": ["aluminum"],
            "Vendor": ["Acme"],
            "Vendor Number": ["https://example.com/p"],   # from BOM Web Link
            "Source": ["Buy"],
        })
        bls.add_missing_bom_rows(client, df, dry_run=False)
        f = client.created[0]
        assert f["Title"] == "SF-999001"           # part number -> built-in key
        assert f["field_1"] == "CD-001574"         # Title (Item,CO) != number
        assert f["field_1"] != f["Title"]
        assert f["field_4"] == "Acme"
        assert f["field_5"] == "https://example.com/p"

    def test_nan_cells_are_skipped_and_output_is_json_safe(self):
        import json
        client = FakeClient()
        df = pd.DataFrame({
            "Number": ["SF-999003"],
            "Description (Item,CO)": [float("nan")],   # empty cell -> pandas NaN
            "Material": ["steel"],
            "Source": ["Buy"],
        })
        bls.add_missing_bom_rows(client, df, dry_run=False)
        fields = client.created[0]
        assert "field_2" not in fields          # NaN description not written
        assert fields["field_3"] == "steel"
        json.dumps(fields)                       # must not raise on NaN

    def test_source_filter_limits_to_buy(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=False,
                                          sources={"Buy"})
        titles = {c["Title"] for c in client.created}
        assert titles == {"SF-999001"}              # SF-999002 is a Make part
        assert report["created"] == 1

    def test_update_existing_patches_present_rows_without_the_key(self):
        # SF-000067 is already in the list; with update_existing it should be
        # PATCHed (title/desc) but NOT re-created, and Title (key) is not patched.
        client = FakeClient()
        df = pd.DataFrame({
            "Number": ["SF-000067"],
            "Title (Item,CO)": ["CD-000900"],
            "Description (Item,CO)": ["updated desc"],
            "Material": ["plastic"],
            "Source": ["Buy"],
        })
        report = bls.add_missing_bom_rows(client, df, dry_run=False,
                                          update_existing=True)
        assert client.created == []                 # not re-added
        assert report["updated"] == 1
        item_id, patch = client.patched[0]
        assert item_id == "1"                       # matched existing item id
        assert "Title" not in patch                 # never patch the key field
        assert patch["field_1"] == "CD-000900"
        assert patch["field_2"] == "updated desc"

    def test_update_existing_off_by_default(self):
        client = FakeClient()
        df = pd.DataFrame({"Number": ["SF-000067"],
                           "Description (Item,CO)": ["x"], "Source": ["Buy"]})
        bls.add_missing_bom_rows(client, df, dry_run=False)
        assert client.patched == []                 # default leaves existing alone

    def test_one_failing_create_does_not_abort_the_batch(self):
        class FlakyClient(FakeClient):
            def create_list_item(self, fields):
                if fields["Title"] == "SF-999001":
                    raise RuntimeError("Graph POST item 403: access denied")
                return super().create_list_item(fields)

        client = FlakyClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=False)
        assert report["created"] == 1              # SF-999002 still added
        assert len(report["errors"]) == 1
        assert report["errors"][0]["number"] == "SF-999001"
        by_item = {r["number"]: r for r in report["rows"]}
        assert by_item["SF-999001"]["status"] == "error"
