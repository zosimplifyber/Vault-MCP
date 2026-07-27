import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from supplier_pricing import cli  # noqa: E402
from supplier_pricing.providers.mcmaster import (  # noqa: E402
    McMasterApiProvider, McMasterBrowserProvider, make_mcmaster_provider,
    _DisabledProvider,
)


class TestParser:
    def test_price_command(self):
        args = cli.build_parser().parse_args(["price", "1078A331"])
        assert args.command == "price"
        assert args.part_number == "1078A331"
        assert args.qty == 1

    def test_update_list_defaults_to_dry_run(self):
        args = cli.build_parser().parse_args(["update-list"])
        assert args.apply is False          # dry-run unless --apply
        assert args.only_missing is False

    def test_update_list_apply_and_limit(self):
        args = cli.build_parser().parse_args(["update-list", "--apply", "--limit", "5"])
        assert args.apply is True
        assert args.limit == 5


class TestProviderFactory:
    def test_no_cert_default_disables_scraping(self):
        # Safe by default: no cert + no opt-in => never scrapes.
        prov = make_mcmaster_provider({})
        assert isinstance(prov, _DisabledProvider)
        r = prov.get_price("1078A331")
        assert not r.ok()
        assert "scraping is DISABLED" in r.error
        assert r.source == "mcmaster:disabled"

    def test_no_cert_with_allow_scrape_gives_browser(self):
        prov = make_mcmaster_provider({}, allow_scrape=True)
        assert isinstance(prov, McMasterBrowserProvider)

    def test_config_allow_scrape_flag_gives_browser(self):
        prov = make_mcmaster_provider({"mcmaster": {"allow_scrape": True}})
        assert isinstance(prov, McMasterBrowserProvider)

    def test_cert_gives_api_provider(self):
        prov = make_mcmaster_provider({"mcmaster": {"api_cert": "cert.pfx",
                                                    "api_user": "u",
                                                    "api_password": "p"}})
        assert isinstance(prov, McMasterApiProvider)

    def test_mode_api_without_cert_refuses_to_scrape(self):
        prov = make_mcmaster_provider({"mcmaster": {"mode": "api"}})
        assert isinstance(prov, _DisabledProvider)


class TestAddReportOutput:
    def test_reports_bom_parts_checked_separately_from_list_size(self, capsys):
        # "already in list=273" alone read as 273 BOM parts; it is the list size.
        cli._print_add_report({
            "dry_run": True, "missing": ["SF-1"], "checked": 18,
            "already_present": 17, "existing_count": 273, "created": 0,
            "updated": 0, "errors": [], "by_source": {"Buy": 1}, "rows": [],
        })
        out = capsys.readouterr().out
        assert "checked=18" in out
        assert "already in list=17" in out
        assert "list size=273" in out


class FakeMcp:
    def __init__(self):
        self.registered = []

    def tool(self):
        def deco(fn):
            self.registered.append(fn.__name__)
            return fn
        return deco


class TestMcpRegistration:
    def test_registers_both_tools(self):
        import supplier_pricing.mcp_tools as mt
        fake = FakeMcp()
        mt.register(fake)
        assert "purchasing_get_mcmaster_price" in fake.registered
        assert "purchasing_update_mcmaster_prices" in fake.registered
