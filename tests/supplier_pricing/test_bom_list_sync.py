import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bom_list_sync as bls  # noqa: E402


# internal -> display, mirroring the real "Engineering Purchased Parts" list
# after its 2026-07-28 rename: field_1 is the key (the file name, no extension)
# and the old part-number column is marked legacy.
DISP = {
    "Title": "OLDPt.2-Title",
    "LinkTitle": "OLDPt.2-Title",
    "field_1": "Title (Name)",
    "field_2": "Description",
    "field_3": "Material",
    "field_4": "Vendor",
    "field_5": "Vendor Number",
    "field_6": "Cost Per",
}

# already in the list — keyed on Title (Name), the file name without extension
EXISTING = [
    ("1", {"Title": "SF-000067", "field_1": "CD-000891",
           "field_2": "rectangular pull handle"}),
    ("2", {"Title": "SF-000105", "field_1": "CD-000902",
           "field_2": "threaded bumper"}),
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
    # CD-000891 already exists; two are new; one new is duplicated
    return pd.DataFrame({
        "Number": ["SF-000067", "SF-999001", "SF-999002", "SF-999002"],
        "Name": ["CD-000891", "CD-999001", "CD-999002", "CD-999002"],
        "Description (Item,CO)": ["rectangular pull handle", "widget bracket",
                                  "spacer", "spacer"],
        "Material": ["plastic", "aluminum", "nylon", "nylon"],
        "Source": ["Buy", "Buy", "Make", "Make"],
    })


class TestColumnCheck:
    """What the GUI shows when a BOM is picked: which fields are there."""

    # The export template, in its own column order.
    EXPORT = ["Part Number", "Filename", "Thumbnail", "BOM Structure",
              "Unit QTY", "QTY", "Description", "REV", "Vendor", "Web Link",
              "Material"]

    def test_the_export_template_reports_nothing_missing(self):
        result = bls.check_bom_columns(self.EXPORT)
        assert result["ok"] is True
        assert result["missing_required"] == []
        assert result["missing_optional"] == []

    def test_thumbnail_is_never_reported_as_missing(self):
        # It is in the export but nothing reads it.
        result = bls.check_bom_columns([c for c in self.EXPORT if c != "Thumbnail"])
        assert "Thumbnail" not in result["missing_optional"]

    def test_a_title_column_is_not_expected(self):
        # The export dropped Title; its absence must not be flagged.
        result = bls.check_bom_columns(self.EXPORT)
        assert "Title" not in result["missing_optional"]

    def test_an_older_export_with_item_and_title_still_passes(self):
        older = ["Item", "Part Number", "Title", "Thumbnail", "BOM Structure",
                 "Unit QTY", "QTY", "Description", "REV", "Material", "Vendor",
                 "Web Link", "Filename"]
        assert bls.check_bom_columns(older)["ok"] is True

    def test_a_missing_required_field_is_named(self):
        result = bls.check_bom_columns(["Item", "Description"])
        assert result["ok"] is False
        assert "QTY" in result["missing_required"]
        assert "Filename" in result["missing_required"]
        assert "Part Number" not in result["missing_required"]

    REQUIRED = ["Filename", "BOM Structure", "QTY",
                "Description", "REV", "Vendor", "Material"]

    def test_filename_is_required_because_it_is_the_lookup_key(self):
        result = bls.check_bom_columns(["Part Number", "QTY"])
        assert result["ok"] is False
        assert "Filename" in result["missing_required"]

    def test_every_field_the_list_row_needs_is_required(self):
        result = bls.check_bom_columns(["QTY", "Filename"])
        assert result["ok"] is False
        assert set(result["missing_required"]) == {
            "BOM Structure", "Description", "REV", "Vendor", "Material"}

    def test_part_number_is_optional_now_that_the_file_name_is_the_key(self):
        result = bls.check_bom_columns(self.REQUIRED)
        assert result["ok"] is True
        assert "Part Number" in result["missing_optional"]

    def test_optional_gaps_do_not_make_it_invalid(self):
        result = bls.check_bom_columns(self.REQUIRED)
        assert result["ok"] is True
        assert set(result["missing_optional"]) == {"Part Number", "Unit QTY",
                                                   "Web Link"}

    def test_vault_style_headers_satisfy_the_same_fields(self):
        result = bls.check_bom_columns(["Quantity", "File Name", "Source",
                                        "Desc", "Revision", "Supplier", "Material"])
        assert result["missing_required"] == []

    def test_matching_ignores_case_and_padding(self):
        result = bls.check_bom_columns(["qty", " FILENAME ",
                                        "bom structure", "DESCRIPTION", "rev",
                                        "vendor", " Material "])
        assert result["missing_required"] == []

    def test_reads_the_header_row_off_a_file(self, tmp_path):
        p = tmp_path / "bom.csv"
        p.write_text("Item,Part Number,QTY\n1,SF-1,2\n", encoding="utf-8")
        assert bls.bom_file_columns(str(p)) == ["Item", "Part Number", "QTY"]


class TestFileNameIsTheKey:
    """The list is keyed on Title (Name) — the file name without its extension."""

    def _bom(self, tmp_path, rows: str):
        p = tmp_path / "bom.csv"
        p.write_text("Part Number,QTY,BOM Structure,Filename\n" + rows,
                     encoding="utf-8")
        df, err = bls.bom_dataframe_from_file(str(p))
        assert err is None, err
        return df

    def test_the_key_is_the_file_stem(self, tmp_path):
        df = self._bom(tmp_path, "93501A112,8,Purchased,Lock-Washers-M6-Steel.ipt\n")
        assert df.loc[0, "Name"] == "Lock-Washers-M6-Steel"

    def test_only_the_last_extension_is_stripped(self, tmp_path):
        df = self._bom(tmp_path, "ISO 2338,7,Purchased,ISO 2338 - 5 h8 x 16 v2.ipt\n")
        assert df.loc[0, "Name"] == "ISO 2338 - 5 h8 x 16 v2"

    def test_the_key_reaches_the_list_as_title_name(self, tmp_path):
        df = self._bom(tmp_path, "93501A112,8,Purchased,Lock-Washers-M6-Steel.ipt\n")
        client = FakeClient()
        bls.add_missing_bom_rows(client, df, dry_run=False)
        assert client.created[0]["field_1"] == "Lock-Washers-M6-Steel"

    def test_a_row_with_no_file_name_is_skipped_not_guessed(self, tmp_path):
        p = tmp_path / "bom.csv"
        p.write_text("Part Number,QTY,BOM Structure\nSF-1,2,Purchased\n",
                     encoding="utf-8")
        df, err = bls.bom_dataframe_from_file(str(p))
        assert err is None
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, df, dry_run=False)
        assert client.created == []                 # nothing invented from the number
        assert report["skipped_no_name"] == 1

    def test_a_part_number_already_in_the_list_is_no_excuse(self):
        # SF-000067 is in the list under the legacy column, but its file name is
        # not — with no fallback, this counts as missing. Accepted by the owner.
        client = FakeClient()
        df = pd.DataFrame({"Number": ["SF-000067"], "Name": ["CD-999999"],
                           "Source": ["Buy"]})
        report = bls.add_missing_bom_rows(client, df, dry_run=True)
        assert report["missing"] == ["CD-999999"]


class TestWithoutAPartNumber:
    """The key is the file name, so a BOM with no Part Number still works."""

    def _bom(self, tmp_path):
        p = tmp_path / "bom.csv"
        p.write_text("QTY,BOM Structure,Filename\n2,Purchased,CD-999900.ipt\n",
                     encoding="utf-8")
        df, err = bls.bom_dataframe_from_file(str(p))
        assert err is None, err
        return df

    def test_it_parses_without_a_part_number_column(self, tmp_path):
        df = self._bom(tmp_path)
        assert df.loc[0, "Name"] == "CD-999900"

    def test_the_row_is_added_under_its_file_name(self, tmp_path):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, self._bom(tmp_path), dry_run=False)
        assert report["created"] == 1
        assert client.created[0]["field_1"] == "CD-999900"

    def test_the_legacy_title_falls_back_to_the_file_stem(self, tmp_path):
        # No Part Number in the export -> the file name stands in for it.
        client = FakeClient()
        bls.add_missing_bom_rows(client, self._bom(tmp_path), dry_run=False)
        assert client.created[0]["Title"] == "CD-999900"


class TestPlanMissing:
    def test_dry_run_finds_missing_and_writes_nothing(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=True)
        assert client.created == []
        assert set(report["missing"]) == {"CD-999001", "CD-999002"}
        assert report["created"] == 0

    def test_dedupes_repeated_keys(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=True)
        assert report["missing"].count("CD-999002") == 1

    def test_existing_parts_are_not_added(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=True)
        assert "CD-000891" not in report["missing"]

    def test_report_counts_bom_parts_checked_and_already_present(self):
        # existing_count is the SIZE OF THE LIST; the BOM-side counts are separate
        # so the GUI can say "x of y BOM parts" instead of quoting the list size.
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=True)
        assert report["checked"] == 3            # 4 rows, CD-999002 twice
        assert report["already_present"] == 1    # CD-000891
        assert report["existing_count"] == 2     # the list holds 2 items

    def test_checked_count_respects_the_source_filter(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=True,
                                          sources={"Buy"})
        assert report["checked"] == 2            # CD-000891 + CD-999001
        assert report["already_present"] == 1

    def test_source_filtered_rows_are_counted_not_just_dropped(self):
        """Nothing counted source-filtered rows before this — a caller with
        no way to see how many rows the filter ate cannot tell 'the list is
        current' apart from 'every row was filtered out before it could be
        checked' (e.g. a BOM export with no Source column, where every row's
        source reads as "" and matches nothing)."""
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=True,
                                          sources={"Buy"})
        # The two "Make" rows (CD-999002 appears twice) are filtered by
        # source before the dedupe check runs, so both are counted.
        assert report["skipped_source"] == 2

    def test_skipped_source_is_zero_with_no_filter(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=True)
        assert report["skipped_source"] == 0

    def test_a_bom_with_no_source_column_filters_every_row(self):
        """The shape behind C1: a BOM export with no Source column gives
        every row source="", which never matches a non-None sources set —
        every row is skipped_source, not missing, and checked stays 0."""
        client = FakeClient()
        df = pd.DataFrame({"Number": ["SF-999001", "SF-999002"],
                           "Name": ["CD-999001", "CD-999002"]})  # no Source
        report = bls.add_missing_bom_rows(client, df, dry_run=True,
                                          sources={"Buy", "Other"})
        assert report["checked"] == 0
        assert report["missing"] == []
        assert report["skipped_source"] == 2


class TestApply:
    def test_apply_creates_items_with_mapped_fields(self):
        client = FakeClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=False)
        assert report["created"] == 2
        by_key = {c["field_1"]: c for c in client.created}
        assert set(by_key) == {"CD-999001", "CD-999002"}
        w = by_key["CD-999001"]
        assert w["field_1"] == "CD-999001"          # Title (Name) -> the key
        assert w["Title"] == "SF-999001"            # legacy column keeps the number
        assert w["field_2"] == "widget bracket"     # description
        assert w["field_3"] == "aluminum"           # material

    def test_the_name_and_vendor_number_come_from_the_bom(self):
        client = FakeClient()
        df = pd.DataFrame({
            "Number": ["SF-999001"],
            "Name": ["CD-001574"],                 # file stem, distinct from number
            "Description (Item,CO)": ["bmw bladder"],
            "Material": ["aluminum"],
            "Vendor": ["Acme"],
            "Vendor Number": ["https://example.com/p"],   # from BOM Web Link
            "Source": ["Buy"],
        })
        bls.add_missing_bom_rows(client, df, dry_run=False)
        f = client.created[0]
        assert f["field_1"] == "CD-001574"         # Title (Name) is the key
        assert f["Title"] == "SF-999001"           # legacy column, for provenance
        assert f["field_1"] != f["Title"]
        assert f["field_4"] == "Acme"
        assert f["field_5"] == "https://example.com/p"

    def test_nan_cells_are_skipped_and_output_is_json_safe(self):
        import json
        client = FakeClient()
        df = pd.DataFrame({
            "Number": ["SF-999003"],
            "Name": ["CD-999003"],
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
        keys = {c["field_1"] for c in client.created}
        assert keys == {"CD-999001"}                # CD-999002 is a Make part
        assert report["created"] == 1

    def test_update_existing_patches_present_rows_without_the_key(self):
        # SF-000067 is already in the list; with update_existing it should be
        # PATCHed (title/desc) but NOT re-created, and Title (key) is not patched.
        client = FakeClient()
        df = pd.DataFrame({
            "Number": ["SF-000067"],
            "Name": ["CD-000891"],                  # matches the list's Title (Name)
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
        assert "field_1" not in patch               # never patch the key field
        assert patch["field_2"] == "updated desc"

    def test_update_existing_off_by_default(self):
        client = FakeClient()
        df = pd.DataFrame({"Number": ["SF-000067"], "Name": ["CD-000891"],
                           "Description (Item,CO)": ["x"], "Source": ["Buy"]})
        bls.add_missing_bom_rows(client, df, dry_run=False)
        assert client.patched == []                 # default leaves existing alone

    def test_one_failing_create_does_not_abort_the_batch(self):
        class FlakyClient(FakeClient):
            def create_list_item(self, fields):
                if fields["field_1"] == "CD-999001":
                    raise RuntimeError("Graph POST item 403: access denied")
                return super().create_list_item(fields)

        client = FlakyClient()
        report = bls.add_missing_bom_rows(client, bom(), dry_run=False)
        assert report["created"] == 1              # CD-999002 still added
        assert len(report["errors"]) == 1
        assert report["errors"][0]["name"] == "CD-999001"
        by_key = {r["name"]: r for r in report["rows"]}
        assert by_key["CD-999001"]["status"] == "error"


class TestDefaultBomDir:
    def test_prefers_the_vault_production_equipment_folder(self, monkeypatch):
        import bom_purchasing as bp
        monkeypatch.setattr(bp.os.path, "isdir", lambda p: p == bp.DEFAULT_OUTPUT_DIR)
        assert bls.default_bom_dir() == bp.DEFAULT_OUTPUT_DIR

    def test_falls_back_when_the_workspace_is_not_there(self, monkeypatch):
        import bom_purchasing as bp
        monkeypatch.setattr(bp.os.path, "isdir", lambda p: False)
        assert bls.default_bom_dir().endswith("Downloads")
