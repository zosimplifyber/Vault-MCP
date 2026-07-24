import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from supplier_pricing import cli  # noqa: E402
from supplier_pricing.providers.mcmaster import (  # noqa: E402
    McMasterApiProvider, McMasterBrowserProvider, make_mcmaster_provider,
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
    def test_no_cert_gives_browser_provider(self):
        assert isinstance(make_mcmaster_provider({}), McMasterBrowserProvider)

    def test_cert_gives_api_provider(self):
        prov = make_mcmaster_provider({"mcmaster": {"api_cert": "cert.pfx",
                                                    "api_user": "u",
                                                    "api_password": "p"}})
        assert isinstance(prov, McMasterApiProvider)


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
