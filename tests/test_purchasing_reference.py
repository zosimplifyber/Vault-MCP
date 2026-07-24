import os
import sys

import openpyxl
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bom_purchasing as bp  # noqa: E402
import purchasing_reference as pref  # noqa: E402


def test_mslist_is_configured():
    assert pref.mslist_is_configured(pref.DEFAULT_CONFIG) is True   # IDs are baked in
    assert pref.mslist_is_configured({"mslist": {"client_id": "", "tenant_id": ""}}) is False
    assert pref.mslist_is_configured({"mslist": {"client_id": "x", "tenant_id": ""}}) is False


def test_acquire_token_short_circuits_without_cache(tmp_path, monkeypatch):
    # No cache file -> must raise instantly, without importing msal or hitting the network.
    monkeypatch.setattr(pref, "_token_cache_path", lambda: str(tmp_path / "nope.bin"))
    with pytest.raises(RuntimeError, match="Not signed in"):
        pref.acquire_token(pref.DEFAULT_CONFIG["mslist"], interactive=False)


def test_load_mslist_dataframe_maps_fields(monkeypatch):
    def fake_get(token, url, params=None):
        if "/lists/LIST/columns" in url:
            return {"value": [
                {"name": "f1", "displayName": "Part Number"},
                {"name": "f2", "displayName": "Vendor"},
                {"name": "f3", "displayName": "Cost Per"},
                {"name": "f4", "displayName": "Material"},
            ]}
        if "/lists/LIST/items" in url:
            return {"value": [
                {"fields": {"f1": "SF-1", "f2": "Acme", "f3": 3.5, "f4": "Steel"}},
                {"fields": {"f1": "SF-2", "f2": "Globex", "f3": 1.25, "f4": "Al"}},
            ]}
        if url.endswith("/sites/SITE/lists"):
            return {"value": [{"id": "LIST", "displayName": "Purchased Items",
                               "name": "PurchasedItems"}]}
        if ":/sites/" in url:
            return {"id": "SITE"}
        raise AssertionError("unexpected url: " + url)

    monkeypatch.setattr(pref, "_graph_get", fake_get)
    ml = dict(pref.DEFAULT_CONFIG["mslist"])
    df = pref.load_mslist_dataframe(ml, token="T")   # token given -> no auth
    assert {"Number", "Vendor", "Cost Per", "Material"}.issubset(df.columns)
    assert list(df["Number"]) == ["SF-1", "SF-2"]
    assert list(df["Vendor"]) == ["Acme", "Globex"]
    assert list(df["Cost Per"]) == [3.5, 1.25]


def test_load_mslist_dataframe_follows_paging(monkeypatch):
    pages = {
        "p1": {"value": [{"fields": {"f1": "SF-1"}}],
               "@odata.nextLink": "https://graph.microsoft.com/v1.0/PAGE2"},
        "p2": {"value": [{"fields": {"f1": "SF-2"}}]},
    }

    def fake_get(token, url, params=None):
        if "/lists/LIST/columns" in url:
            return {"value": [{"name": "f1", "displayName": "Part Number"}]}
        if url.endswith("/PAGE2"):
            return pages["p2"]
        if "/lists/LIST/items" in url:
            return pages["p1"]
        if url.endswith("/sites/SITE/lists"):
            return {"value": [{"id": "LIST", "displayName": "Purchased Items"}]}
        if ":/sites/" in url:
            return {"id": "SITE"}
        raise AssertionError("unexpected url: " + url)

    monkeypatch.setattr(pref, "_graph_get", fake_get)
    df = pref.load_mslist_dataframe(dict(pref.DEFAULT_CONFIG["mslist"]), token="T")
    assert list(df["Number"]) == ["SF-1", "SF-2"]   # both pages collected


def test_enrich_falls_back_to_excel_when_mslist_fails(tmp_path, monkeypatch):
    # A configured List that fails to load must downgrade to the Excel file + warn.
    monkeypatch.setattr(pref, "resolve_reference_config", lambda override=None: {
        "source": "auto",
        "mslist": {"tenant_id": "T", "client_id": "C", "site_hostname": "h",
                   "site_path": "/sites/S", "list_name": "L", "list_id": ""},
        "column_map": {},
    })

    def boom(*a, **k):
        raise RuntimeError("not signed in")
    monkeypatch.setattr(pref, "load_mslist_dataframe", boom)

    ref = tmp_path / "ref.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "purchased parts"
    ws.append(["Number", "Vendor", "Cost Per"])
    ws.append(["SF-1", "Acme", 3.5])
    wb.save(ref)
    monkeypatch.setattr(bp, "find_purchased_items_file", lambda: str(ref))

    df = pd.DataFrame({"Number": ["SF-1"], "Source": ["Buy"]})
    out, matched, total, unmatched, warnings = bp._enrich_with_reference(df)
    assert any("Microsoft List" in w for w in warnings)   # fallback was warned
    assert out.loc[0, "Vendor"] == "Acme"                 # Excel data used
