import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import purchasing_update as pu  # noqa: E402
from supplier_pricing.models import PriceResult  # noqa: E402


# ---- fakes -----------------------------------------------------------------

class FakeClient:
    """Stands in for the Graph list client (no network)."""

    def __init__(self, disp_map, rows):
        # disp_map: internal name -> display name
        self._disp = disp_map
        self._rows = rows            # list of (item_id, fields-by-internal-name)
        self.patches = []            # (item_id, fields) recorded on patch

    def column_display_map(self):
        return dict(self._disp)

    def iter_rows(self):
        for item_id, fields in self._rows:
            yield item_id, dict(fields)

    def patch_fields(self, item_id, fields):
        self.patches.append((item_id, dict(fields)))


class FakeProvider:
    vendor_key = "mcmaster"

    def __init__(self, prices):
        self.prices = prices          # part_number -> unit_price (or None)
        self.calls = []

    def get_price(self, part_number, qty=1):
        self.calls.append(part_number)
        p = self.prices.get(part_number)
        return PriceResult(
            part_number=part_number, vendor="McMaster-Carr",
            unit_price=p, lead_time_days=1, source="mcmaster:test",
            error=None if p is not None else "not found",
        )


# Internal-name -> display-name for a representative list.
DISP = {
    "Title": "Number",
    "Vendor": "Vendor",
    "field_vn": "Vendor Number",
    "field_cost": "Cost Per",
    "field_lead": "Lead Time (Business Days)",
}

ROWS = [
    # McMaster row with a vendor number and no price -> should be priced
    ("1", {"Title": "SF-01", "Vendor": "McMaster Carr", "field_vn": "1078A331"}),
    # MiSUMi row -> skipped (unsupported vendor)
    ("2", {"Title": "SF-02", "Vendor": "MiSUMi", "field_vn": "HFS5-2020"}),
    # McMaster row that already has a price
    ("3", {"Title": "SF-03", "Vendor": "McMaster-Carr", "field_vn": "9557K11",
           "field_cost": 12.34}),
    # McMaster row with no vendor number -> reported, not priced
    ("4", {"Title": "SF-04", "Vendor": "McMASTER-CARR"}),
]


class TestFieldIndex:
    def test_resolves_roles_from_display_names(self):
        idx = pu.build_field_index(DISP)
        assert idx["vendor"] == "Vendor"
        assert idx["vendor_number"] == "field_vn"
        assert idx["cost"] == "field_cost"
        assert idx["lead"] == "field_lead"

    def test_override_wins(self):
        idx = pu.build_field_index(DISP, overrides={"Cost Per": "custom_cost"})
        assert idx["cost"] == "custom_cost"


class TestUpdateDryRun:
    def test_plans_only_mcmaster_rows_and_writes_nothing(self):
        client = FakeClient(DISP, ROWS)
        prov = FakeProvider({"1078A331": 8.10, "9557K11": 20.0})
        report = pu.update_mcmaster_prices(client, prov, dry_run=True)

        assert client.patches == []                      # dry-run writes nothing
        assert prov.calls == ["1078A331", "9557K11"]     # both McMaster w/ vendor#
        by_item = {r["item_id"]: r for r in report["rows"]}
        assert by_item["1"]["status"] == "priced"
        assert by_item["1"]["new_price"] == 8.10
        assert by_item["1"]["lead_time_days"] == 1
        assert by_item["4"]["status"] == "no_vendor_number"
        assert "2" not in by_item                        # MiSUMi not planned

    def test_only_missing_skips_rows_that_already_have_a_price(self):
        client = FakeClient(DISP, ROWS)
        prov = FakeProvider({"1078A331": 8.10, "9557K11": 20.0})
        report = pu.update_mcmaster_prices(client, prov, dry_run=True,
                                           only_missing=True)
        assert prov.calls == ["1078A331"]                # 9557K11 already priced
        by_item = {r["item_id"]: r for r in report["rows"]}
        assert by_item["3"]["status"] == "skipped_has_price"

    def test_limit_caps_number_priced(self):
        client = FakeClient(DISP, ROWS)
        prov = FakeProvider({"1078A331": 8.10, "9557K11": 20.0})
        pu.update_mcmaster_prices(client, prov, dry_run=True, limit=1)
        assert prov.calls == ["1078A331"]


class TestUpdateApply:
    def test_apply_patches_price_and_lead_time_by_internal_name(self):
        client = FakeClient(DISP, ROWS)
        prov = FakeProvider({"1078A331": 8.10, "9557K11": 20.0})
        report = pu.update_mcmaster_prices(client, prov, dry_run=False)

        patched = {item_id: fields for item_id, fields in client.patches}
        assert patched["1"] == {"field_cost": 8.10, "field_lead": 1}
        assert patched["3"] == {"field_cost": 20.0, "field_lead": 1}
        assert "4" not in patched                        # nothing to write
        assert report["applied"] == 2

    def test_provider_miss_is_reported_not_written(self):
        client = FakeClient(DISP, ROWS)
        prov = FakeProvider({"1078A331": None, "9557K11": 20.0})   # first not found
        report = pu.update_mcmaster_prices(client, prov, dry_run=False)
        patched = {item_id for item_id, _ in client.patches}
        assert "1" not in patched
        by_item = {r["item_id"]: r for r in report["rows"]}
        assert by_item["1"]["status"] == "not_found"
