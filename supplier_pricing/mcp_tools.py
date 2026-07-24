"""MCP tool registration for supplier pricing.

Kept as a standalone ``register(mcp)`` so wiring into ``mcp_server.py`` is a single
additive line (low merge risk with parallel work on that file).
"""
from __future__ import annotations

import json


def register(mcp) -> None:
    @mcp.tool()
    def purchasing_get_mcmaster_price(part_number: str, qty: int = 1,
                                      allow_scrape: bool = False) -> str:
        """
        Look up the current McMaster-Carr unit price for an exact part number.

        Uses the official McMaster API when a client certificate is configured. If
        no cert is configured, browser scraping is DISABLED by default (it can get
        your account banned) and this returns an error explaining how to enable the
        API; pass allow_scrape=True to deliberately use the browser fallback. Lead
        time is always reported as 1 business day. Check the returned `source`:
        "mcmaster:api" (safe) vs "mcmaster:web" (scraping) vs "mcmaster:disabled".

        Args:
            part_number: Exact McMaster part number (e.g. "1078A331").
            qty: Quantity, for quantity-break pricing (default 1).
            allow_scrape: Permit the browser fallback when no API cert is set.
        """
        from .config import supplier_pricing_block
        from .providers.mcmaster import make_mcmaster_provider
        provider = make_mcmaster_provider(supplier_pricing_block(),
                                          allow_scrape=allow_scrape)
        result = provider.get_price(part_number, qty=qty)
        close = getattr(provider, "close", None)
        if close:
            close()
        return json.dumps(result.to_dict(), indent=2, default=str)

    @mcp.tool()
    def purchasing_update_mcmaster_prices(dry_run: bool = True,
                                          only_missing: bool = False,
                                          limit: int | None = None,
                                          allow_scrape: bool = False) -> str:
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
                             limit=limit, allow_scrape=allow_scrape)
        return json.dumps(report, indent=2, default=str)

    @mcp.tool()
    def purchasing_add_bom_items_to_list(bom_file: str, dry_run: bool = True,
                                         buy_only: bool = False,
                                         update_existing: bool = False) -> str:
        """
        Add parts from an exported BOM to the "Engineering Purchased Parts"
        Microsoft List when they are not already present (matched by part number).

        Reads the BOM (.xlsx/.xls/.csv/.txt, same header-mapping as the purchasing
        sheet), compares each part number against the list, and creates a new list
        item (Number + Description + Material) for each missing part. SAFE BY
        DEFAULT: dry_run=True plans and writes NOTHING — review the returned rows,
        then call with dry_run=False to create them. Requires the app registration
        to have Sites.ReadWrite.All + a prior `probe` sign-in.

        Args:
            bom_file: Path to the exported BOM file.
            dry_run: When True (default) only plan; when False, create the rows.
            buy_only: Only add Buy/Other parts (skip Make/manufactured items).
        """
        import bom_list_sync
        from .cli import _connect_client
        df, err = bom_list_sync.bom_dataframe_from_file(bom_file)
        if err:
            return json.dumps({"error": err}, indent=2)
        sources = {"Buy", "Other"} if buy_only else None
        client = _connect_client()
        report = bom_list_sync.add_missing_bom_rows(
            client, df, dry_run=dry_run, sources=sources,
            update_existing=update_existing)
        return json.dumps(report, indent=2, default=str)
