"""MCP tool registration for supplier pricing.

Kept as a standalone ``register(mcp)`` so wiring into ``mcp_server.py`` is a single
additive line (low merge risk with parallel work on that file).
"""
from __future__ import annotations

import json


def register(mcp) -> None:
    @mcp.tool()
    def purchasing_get_mcmaster_price(part_number: str, qty: int = 1) -> str:
        """
        Look up the current McMaster-Carr unit price for an exact part number.

        Uses the official McMaster API when a client certificate is configured,
        otherwise a browser fallback. Lead time is always reported as 1 business
        day (McMaster ships same-day; their API exposes no lead time). Returns a
        JSON PriceResult (unit_price, price_breaks, source, error).

        Args:
            part_number: Exact McMaster part number (e.g. "1078A331").
            qty: Quantity, for quantity-break pricing (default 1).
        """
        from .config import supplier_pricing_block
        from .providers.mcmaster import make_mcmaster_provider
        provider = make_mcmaster_provider(supplier_pricing_block())
        return json.dumps(provider.get_price(part_number, qty=qty).to_dict(),
                          indent=2, default=str)

    @mcp.tool()
    def purchasing_update_mcmaster_prices(dry_run: bool = True,
                                          only_missing: bool = False,
                                          limit: int | None = None) -> str:
        """
        Update McMaster-Carr prices in the "Engineering Purchased Parts" Microsoft
        List. For every list row whose Vendor is McMaster and that has a Vendor
        Number, fetch the current price and write it to the Cost Per column and set
        Lead Time = 1 business day.

        SAFE BY DEFAULT: dry_run=True plans the changes and writes NOTHING — review
        the returned report, then call again with dry_run=False to apply. Writing
        requires the app registration to have the Sites.ReadWrite.All scope and a
        prior device-code sign-in (run `python -m supplier_pricing probe`).

        Args:
            dry_run: When True (default) only plan; when False, PATCH the list.
            only_missing: Only price rows that currently have no cost.
            limit: Cap how many rows are priced (useful for a first test run).

        Returns a JSON report: per-row {item_id, vendor_number, current_price,
        new_price, lead_time_days, status} plus counts and any warnings.
        """
        from .cli import _run_update
        report = _run_update(apply=not dry_run, only_missing=only_missing,
                             limit=limit)
        return json.dumps(report, indent=2, default=str)
