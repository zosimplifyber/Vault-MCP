import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from supplier_pricing import models as m  # noqa: E402


class TestPriceResult:
    def test_ok_true_when_price_and_no_error(self):
        r = m.PriceResult(part_number="X", vendor="MiSUMi", unit_price=12.5)
        assert r.ok() is True

    def test_ok_false_when_no_price(self):
        r = m.PriceResult(part_number="X", vendor="MiSUMi", unit_price=None)
        assert r.ok() is False

    def test_ok_false_when_error_set_even_with_price(self):
        r = m.PriceResult(part_number="X", vendor="MiSUMi", unit_price=1.0,
                          error="stale")
        assert r.ok() is False

    def test_defaults(self):
        r = m.PriceResult(part_number="X", vendor="MiSUMi", unit_price=1.0)
        assert r.currency == "USD"
        assert r.price_breaks == []
        assert r.lead_time_days is None
        assert r.source == ""

    def test_to_dict_is_json_friendly(self):
        import json
        r = m.PriceResult(
            part_number="HFS5-2020-1000", vendor="MiSUMi", unit_price=9.99,
            price_breaks=[m.PriceBreak(min_qty=1, unit_price=9.99),
                          m.PriceBreak(min_qty=10, unit_price=8.5)],
            lead_time_days=3, source="misumi:web",
        )
        d = r.to_dict()
        json.dumps(d)  # must not raise
        assert d["unit_price"] == 9.99
        assert d["price_breaks"][1] == {"min_qty": 10, "unit_price": 8.5}


class TestQuoteLineItem:
    def test_minimal(self):
        li = m.QuoteLineItem(part_number="1078A331", unit_price=8.1)
        assert li.currency == "USD"
        assert li.lead_time_days is None

    def test_as_price_result_carries_fields(self):
        li = m.QuoteLineItem(part_number="1078A331", unit_price=8.1,
                             lead_time_days=5, vendor="McMaster-Carr",
                             source_file="q.pdf")
        r = li.as_price_result()
        assert isinstance(r, m.PriceResult)
        assert r.part_number == "1078A331"
        assert r.unit_price == 8.1
        assert r.lead_time_days == 5
        assert r.source.startswith("quote:")


class TestUpdateOutcome:
    def test_status_constants_exist(self):
        assert m.UpdateOutcome.MATCHED
        assert m.UpdateOutcome.NOT_FOUND
        assert m.UpdateOutcome.AMBIGUOUS
        assert m.UpdateOutcome.UNCHANGED
