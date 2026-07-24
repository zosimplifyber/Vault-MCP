import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from supplier_pricing.providers.mcmaster import McMasterApiProvider  # noqa: E402


class FakeResp:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._j = json_data
        self.text = text

    def json(self):
        return self._j


class FakeHttp:
    """httpx.Client-shaped stub for the McMaster API."""
    def __init__(self, login=None, subscribe=None, price=None):
        self.login = login or FakeResp(200, {"AuthToken": "TOK",
                                             "ExpirationTS": "2099-01-01T00:00:00Z"})
        self.subscribe = subscribe or FakeResp(200, {})
        self.price = price or FakeResp(200, [{"Amount": 3.46, "MinimumQuantity": 1,
                                              "UnitOfMeasure": "Each"}])
        self.calls = []

    def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json, headers))
        return self.login

    def put(self, url, json=None, headers=None):
        self.calls.append(("PUT", url, json, headers))
        return self.subscribe

    def get(self, url, headers=None, params=None):
        self.calls.append(("GET", url, headers))
        return self.price

    def close(self):
        pass


def make(http):
    return McMasterApiProvider(user="u", password="p", client=http)


class TestGetPrice:
    def test_returns_price_lead_time_1_and_source(self):
        prov = make(FakeHttp())
        r = prov.get_price("1078A331")
        assert r.ok()
        assert r.unit_price == 3.46
        assert r.vendor == "McMaster-Carr"
        assert r.lead_time_days == 1
        assert r.source == "mcmaster:api"
        assert r.price_breaks[0].min_qty == 1
        assert r.price_breaks[0].unit_price == 3.46

    def test_login_happens_once_across_calls(self):
        http = FakeHttp()
        prov = make(http)
        prov.get_price("1078A331")
        prov.get_price("9557K11")
        logins = [c for c in http.calls if c[0] == "POST" and c[1].endswith("/login")]
        assert len(logins) == 1

    def test_subscribes_the_part_before_pricing(self):
        http = FakeHttp()
        make(http).get_price("1078A331")
        puts = [c for c in http.calls if c[0] == "PUT" and c[1].endswith("/products")]
        assert puts, "expected a PUT /products subscribe call"
        assert puts[0][2] == {"URL": "https://mcmaster.com/_1078A331_"}

    def test_quantity_break_selects_correct_tier(self):
        http = FakeHttp(price=FakeResp(200, [
            {"Amount": 10.0, "MinimumQuantity": 1, "UnitOfMeasure": "Each"},
            {"Amount": 8.5, "MinimumQuantity": 25, "UnitOfMeasure": "Each"},
        ]))
        prov = make(http)
        assert prov.get_price("X", qty=1).unit_price == 10.0
        assert prov.get_price("X", qty=30).unit_price == 8.5

    def test_already_subscribed_is_tolerated(self):
        http = FakeHttp(subscribe=FakeResp(400, text="already subscribed"))
        r = make(http).get_price("1078A331")
        assert r.ok()
        assert r.unit_price == 3.46

    def test_price_404_returns_error_not_exception(self):
        http = FakeHttp(price=FakeResp(404, text="Not Found"))
        r = make(http).get_price("BOGUS")
        assert not r.ok()
        assert r.unit_price is None
        assert r.error

    def test_empty_price_array_is_error(self):
        http = FakeHttp(price=FakeResp(200, []))
        r = make(http).get_price("X")
        assert not r.ok()

    def test_login_failure_returns_error_result(self):
        http = FakeHttp(login=FakeResp(401, text="bad creds"))
        r = make(http).get_price("X")
        assert not r.ok()
        assert r.error


class TestBrowserPriceParse:
    def test_parses_dollar_amount_from_text(self):
        from supplier_pricing.providers.mcmaster import parse_price_from_text
        assert parse_price_from_text("Each $3.46") == 3.46
        assert parse_price_from_text("$1,234.56 per pack") == 1234.56
        assert parse_price_from_text("no price here") is None
