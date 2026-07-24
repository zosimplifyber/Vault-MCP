"""Command-line interface for supplier pricing.

    python -m supplier_pricing price <part_number>
    python -m supplier_pricing update-list [--apply] [--only-missing] [--limit N]
    python -m supplier_pricing probe            # sign in (write scope) + list columns
    python -m supplier_pricing login-mcmaster   # browser-fallback sign-in
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import (load_config, supplier_pricing_block, update_list_name,
                     write_field_overrides)
from .providers.mcmaster import make_mcmaster_provider


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_price(args) -> int:
    sp = supplier_pricing_block()
    provider = make_mcmaster_provider(sp, allow_scrape=args.allow_scrape)
    result = provider.get_price(args.part_number, qty=args.qty)
    close = getattr(provider, "close", None)
    if close:
        close()
    _print_json(result.to_dict())
    return 0 if result.ok() else 1


def _connect_client(sp: dict | None = None):
    from purchasing_update import GraphListClient
    sp = sp if sp is not None else supplier_pricing_block()
    return GraphListClient.connect(cfg=None, list_name=update_list_name(sp),
                                   interactive=False)


def _run_update(*, apply: bool, only_missing: bool, limit: int | None,
                allow_scrape: bool = False) -> dict:
    from purchasing_update import update_mcmaster_prices
    cfg = load_config()
    sp = supplier_pricing_block(cfg)
    client = _connect_client(sp)
    provider = make_mcmaster_provider(sp, allow_scrape=allow_scrape)
    try:
        return update_mcmaster_prices(
            client, provider, dry_run=not apply, only_missing=only_missing,
            limit=limit, field_overrides=write_field_overrides(sp),
        )
    finally:
        close = getattr(provider, "close", None)
        if close:
            close()


def _print_report(report: dict) -> None:
    c = report["counts"]
    mode = "APPLIED" if not report["dry_run"] else "DRY-RUN (no writes)"
    print(f"\n=== McMaster price update - {mode} ===")
    print(f"scanned={c['scanned']} mcmaster={c['mcmaster']} priced={c['priced']} "
          f"applied={report['applied']} not_found={c['not_found']} "
          f"no_vendor_number={c['no_vendor_number']} "
          f"skipped_has_price={c['skipped_has_price']} skipped_limit={c['skipped_limit']}")
    for w in report.get("warnings", []):
        print(f"  ! {w}")
    print(f"{'item':>6}  {'vendor#':<16} {'was':>10} {'new':>10}  status")
    for r in report["rows"]:
        print(f"{str(r['item_id']):>6}  {str(r.get('vendor_number') or ''):<16} "
              f"{str(r.get('current_price') if r.get('current_price') is not None else ''):>10} "
              f"{str(r.get('new_price') if r.get('new_price') is not None else ''):>10}  "
              f"{r['status']}")


def cmd_update(args) -> int:
    report = _run_update(apply=args.apply, only_missing=args.only_missing,
                         limit=args.limit, allow_scrape=args.allow_scrape)
    _print_report(report)
    return 0


def _print_add_report(report: dict) -> None:
    mode = "APPLIED" if not report["dry_run"] else "DRY-RUN (nothing added)"
    print(f"\n=== BOM -> List add - {mode} ===")
    errs = report.get("errors", [])
    print(f"already in list={report['existing_count']}  missing={len(report['missing'])}  "
          f"added={report['created']}  updated={report.get('updated', 0)}  "
          f"errors={len(errs)}  by_source={report['by_source']}")
    for r in report["rows"]:
        print(f"  + {r['number']:<14} [{r['source'] or '-':<6}] "
              f"{str(r['description'] or '')[:44]}  ({r['status']})")
    for e in errs:
        print(f"  ! {e['number']}: {e['error'][:120]}")


def cmd_add_from_bom(args) -> int:
    import bom_list_sync
    df, err = bom_list_sync.bom_dataframe_from_file(args.bom_file)
    if err:
        print(f"BOM parse error: {err}")
        return 2
    sources = {"Buy", "Other"} if args.buy_only else None
    client = _connect_client()
    report = bom_list_sync.add_missing_bom_rows(
        client, df, dry_run=not args.apply, sources=sources,
        update_existing=args.update_existing)
    _print_add_report(report)
    return 0


def cmd_check_write(args) -> int:
    """Definitive write-access test: create one throwaway row, then delete it."""
    client = _connect_client()
    sentinel = {"Title": "ZZ-WRITE-CHECK (auto-delete)"}
    print("Creating a throwaway list item to test write access…")
    try:
        created = client.create_list_item(sentinel)
    except Exception as exc:
        msg = str(exc)
        print(f"WRITE FAILED: {msg}")
        if "403" in msg or "denied" in msg.lower() or "authoriz" in msg.lower():
            print("\n-> The app registration is missing delegated "
                  "Sites.ReadWrite.All (with admin consent). Add it, then re-run "
                  "`python -m supplier_pricing probe` and try again.")
        return 1
    item_id = str((created or {}).get("id", ""))
    print(f"  created item id={item_id}. Deleting it…")
    try:
        client.delete_item(item_id)
        print("WRITE OK - created and deleted a row. You have write access.")
        return 0
    except Exception as exc:
        print(f"Created id={item_id} but could NOT delete it: {exc}\n"
              f"-> Please remove the 'ZZ-WRITE-CHECK' row manually.")
        return 1


def cmd_probe(args) -> int:
    import purchasing_update
    return purchasing_update.probe()


def cmd_login_mcmaster(args) -> int:
    sp = supplier_pricing_block()
    user_data_dir = (sp.get("mcmaster", {}) or {}).get("user_data_dir") or ".mcmaster-profile"
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(f"playwright not available: {exc}")
        return 2
    print("Opening a browser. Sign in to mcmaster.com, then press Enter here.")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir, headless=False)
        page = ctx.new_page()
        page.goto("https://www.mcmaster.com/", wait_until="domcontentloaded")
        input("Press Enter once you're signed in to save the session... ")
        ctx.close()
    print(f"Session saved to {user_data_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="supplier_pricing",
                                description="McMaster price lookups + Microsoft List updates")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("price", help="look up one McMaster part's price")
    sp.add_argument("part_number")
    sp.add_argument("--qty", type=int, default=1)
    sp.add_argument("--allow-scrape", action="store_true",
                    help="allow the browser fallback if no API cert is configured "
                         "(scraping may violate McMaster's terms — off by default)")
    sp.set_defaults(func=cmd_price)

    su = sub.add_parser("update-list", help="update McMaster prices in the List")
    su.add_argument("--apply", action="store_true",
                    help="actually write (default is a dry run)")
    su.add_argument("--only-missing", action="store_true",
                    help="only price rows that have no cost yet")
    su.add_argument("--limit", type=int, default=None)
    su.add_argument("--allow-scrape", action="store_true",
                    help="allow the browser fallback if no API cert is configured "
                         "(scraping may violate McMaster's terms — off by default)")
    su.set_defaults(func=cmd_update)

    ab = sub.add_parser("add-from-bom",
                        help="add BOM parts that aren't already in the list")
    ab.add_argument("bom_file", help="exported BOM (.xlsx/.xls/.csv/.txt)")
    ab.add_argument("--apply", action="store_true",
                    help="actually create the rows (default is a dry run)")
    ab.add_argument("--buy-only", action="store_true",
                    help="only add Buy/Other parts (skip Make)")
    ab.add_argument("--update-existing", action="store_true",
                    help="also PATCH parts already in the list with the BOM's "
                         "Title/Description/Material/Vendor/Vendor Number "
                         "(leaves Cost/Lead untouched)")
    ab.set_defaults(func=cmd_add_from_bom)

    cw = sub.add_parser("check-write",
                        help="safely test write access (creates + deletes one row)")
    cw.set_defaults(func=cmd_check_write)

    pr = sub.add_parser("probe", help="sign in (write scope) and print list columns")
    pr.set_defaults(func=cmd_probe)

    lm = sub.add_parser("login-mcmaster", help="browser-fallback sign-in")
    lm.set_defaults(func=cmd_login_mcmaster)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
