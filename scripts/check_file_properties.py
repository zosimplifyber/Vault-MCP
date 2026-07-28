"""
Check a Vault FILE's properties (iProperties) against a rule set.

Type a file name — ``CD-001659.iam`` — and this pulls that file's properties
from Vault and reports which ones are out of compliance.

Rules come from ``file_property_rules.json`` at the project root, keyed by the
file's Category Name ("Assembly - Engineering", "Part - Engineering",
"Part - Purchased", …). That file is re-read on every run, so edits take effect
immediately.

This is the file-side twin of ``check_item_properties.py``. The rule engine is
shared — imported from that module, not duplicated — but the fetch layer and
property names are different, because Vault files and Vault items expose
different properties (files have ``Description (File)`` and ``CAD Category``;
items have ``Description (Item,CO)`` and ``Units``).

Run:
    python scripts/check_file_properties.py CD-001659.iam
    python scripts/check_file_properties.py CD-001659.iam --recursive
    python scripts/check_file_properties.py CD-001659.iam --json
    python scripts/check_file_properties.py            # launches the GUI

Exit codes: 0 = everything passed, 1 = one or more failures, 2 = no rule set
matched the file's category.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Make the project root and scripts/ importable so we can reuse VaultRestAPI
# and the item tool's rule engine.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from vault_rest_api import VaultRestAPI  # noqa: E402

# The rule engine is shared with the item checker. One implementation, one
# place to fix it. check_item_properties does not import this module, so there
# is no import cycle.
from check_item_properties import (  # noqa: E402
    check_properties,
    load_json,
    resolve_category,
)


CONFIG_PATH = PROJECT_ROOT / "config.json"
DEFAULT_RULES_PATH = PROJECT_ROOT / "file_property_rules.json"


# ---------------------------------------------------------------------------
# Property extraction from Vault file responses
# ---------------------------------------------------------------------------
# A file-version record carries its user properties as
#     "properties": [{"propertyDefinitionId": "77", "value": "Released"}, ...]
# with no names attached. The names live in the response's
#     "included": {"propertyDefinition": {"77": {"displayName": "State", ...}}}
# map. Everything below turns that pair into a flat {display name: value} dict
# keyed the same way file_property_rules.json is.

# System fields that appear at the top level of a file-version record. Mapped
# onto the display names the rules use so a file still reports a sane Category
# Name / Revision / State even if the properties array is missing.
_RECORD_FIELD_NAMES: dict[str, str] = {
    "name": "File Name",
    "category": "Category Name",
    "revision": "Revision",
    "state": "State",
}


def extract_definition_index(payload: Any) -> dict[str, dict[str, Any]]:
    """Return ``{propertyDefinitionId: definition}`` from a response's ``included``.

    Vault embeds the definition of every property it returned, so a single
    request yields both the values and their names.
    """
    if not isinstance(payload, dict):
        return {}
    included = payload.get("included")
    if not isinstance(included, dict):
        return {}
    defs = included.get("propertyDefinition")
    if not isinstance(defs, dict):
        return {}
    return {str(k): v for k, v in defs.items() if isinstance(v, dict)}


def flatten_file_properties(
    record: dict[str, Any],
    defs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return a flat ``{display name: value}`` dict for one file-version record.

    Values from the ``properties`` array win over the record's top-level system
    fields, and a populated value always wins over an empty one — Vault
    sometimes reports the same property twice (current and historical).
    """
    flat: dict[str, Any] = {}

    def absorb(name: Any, value: Any) -> None:
        if not isinstance(name, str) or not name.strip():
            return
        key = name.strip()
        if key in flat and _is_blank(value) and not _is_blank(flat[key]):
            return
        flat[key] = value

    for field, name in _RECORD_FIELD_NAMES.items():
        if field in record:
            absorb(name, record.get(field))

    for entry in record.get("properties") or []:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("propertyDefinitionId", ""))
        definition = defs.get(pid) or {}
        display = definition.get("displayName")
        if not display:
            # No definition came back for this id — skip rather than invent a
            # name. A rule keyed on it will report "missing", which is honest.
            continue
        # Historical twins ("State (Historical)") are separate definitions, so
        # they land under their own names and never shadow the live value.
        absorb(display, entry.get("value"))
        system = definition.get("systemName")
        if system and system != display and system not in flat:
            flat[system] = entry.get("value")

    return flat


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


# ---------------------------------------------------------------------------
# Vault lookup
# ---------------------------------------------------------------------------

def _results(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("results")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    return []


def select_file_record(
    records: list[dict[str, Any]],
    file_name: str,
) -> tuple[dict[str, Any], str | None]:
    """Pick the record matching ``file_name`` and return ``(record, note)``.

    An exact case-insensitive name match always wins. Failing that, the first
    result is used and ``note`` explains the ambiguity so the report can say so
    rather than quietly checking the wrong file.
    """
    if not records:
        raise RuntimeError(f"No files found matching '{file_name}'.")

    wanted = file_name.strip().lower()
    exact = [r for r in records if str(r.get("name", "")).strip().lower() == wanted]
    if exact:
        note = None
        if len(exact) > 1:
            note = (
                f"{len(exact)} versions matched '{file_name}' exactly; "
                "checking the first (latest)."
            )
        return exact[0], note

    note = (
        f"No file is named exactly '{file_name}'. Checking "
        f"'{records[0].get('name')}'"
        + (f" ({len(records)} results matched)." if len(records) > 1 else ".")
    )
    return records[0], note


async def fetch_file(
    api: VaultRestAPI,
    vault_id: str,
    file_name: str,
) -> dict[str, Any]:
    """Look up a file by name and return its resolved properties.

    Returns ``{"record", "file_version_id", "file_id", "properties", "note"}``.
    Raises ``RuntimeError`` when the file cannot be found or Vault errors.
    """
    resp = await api.search_file_versions(
        vault_id=vault_id, query=file_name, prop_def_ids="all", limit=25,
    )
    if resp["error"]:
        raise RuntimeError(f"File search failed: {resp['data']}")

    payload = resp.get("data") or {}
    record, note = select_file_record(_results(payload), file_name)
    defs = extract_definition_index(payload)

    if not defs and (record.get("properties") or []):
        # Vault returned values without their definitions. Fall back to the
        # standalone definitions endpoint rather than report a file whose
        # properties we cannot name as compliant.
        defs = await fetch_definition_index(api, vault_id)

    return {
        "record": record,
        "file_version_id": str(record.get("id") or ""),
        "file_id": str((record.get("file") or {}).get("id") or ""),
        "properties": flatten_file_properties(record, defs),
        "note": note,
    }


_DEFS_CACHE: dict[str, dict[str, dict[str, Any]]] = {}


async def fetch_definition_index(
    api: VaultRestAPI, vault_id: str
) -> dict[str, dict[str, Any]]:
    """Fallback ``{id: definition}`` map from ``/property-definitions``, cached.

    Only used when a response omits ``included.propertyDefinition``.
    """
    cached = _DEFS_CACHE.get(vault_id)
    if cached is not None:
        return cached
    resp = await api.get_property_definitions(vault_id=vault_id, limit=500)
    if resp["error"]:
        raise RuntimeError(
            f"Could not load property definitions, so file properties cannot "
            f"be named: {resp['data']}"
        )
    rows = _results(resp.get("data") or {})
    index = {str(r["id"]): r for r in rows if r.get("id")}
    _DEFS_CACHE[vault_id] = index
    return index


async def fetch_cad_children(
    api: VaultRestAPI,
    vault_id: str,
    file_version_id: str,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return one entry per child file in the CAD BOM.

    ``/uses`` enriches every child with its properties in the same call when
    ``prop_def_ids`` is passed, so this is a single request no matter how many
    children there are.

    Each entry: ``{"file_name", "file_version_id", "properties"}``.
    """
    resp = await api.get_file_uses(
        vault_id=vault_id,
        file_version_id=file_version_id,
        prop_def_ids="all",
        limit=limit,
    )
    if resp["error"]:
        raise RuntimeError(f"CAD BOM walk failed: {resp['data']}")

    payload = resp.get("data") or {}
    defs = extract_definition_index(payload)
    rows = _results(payload)
    if rows and not defs:
        defs = await fetch_definition_index(api, vault_id)

    children: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        child = row.get("childFile")
        if not isinstance(child, dict):
            continue
        cid = str(child.get("id") or "")
        # A child used more than once in the assembly comes back per
        # occurrence; check it once.
        if cid and cid in seen:
            continue
        if cid:
            seen.add(cid)
        children.append({
            "file_name": str(child.get("name") or "(unknown)"),
            "file_version_id": cid,
            "assoc_type": str(row.get("fileAssocType") or ""),
            "properties": flatten_file_properties(child, defs),
        })

    children.sort(key=lambda c: c["file_name"].lower())
    return children


# ---------------------------------------------------------------------------
# Pipeline (shared by CLI and GUI)
# ---------------------------------------------------------------------------

def evaluate_against_rules(
    properties: dict[str, Any],
    raw_category: str,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the rule set for ``raw_category`` and run it against ``properties``.

    ``report`` is None when no rule set matches — which reports as SKIP, never
    as a pass.
    """
    category = resolve_category(raw_category, rules)
    report = None
    if category:
        report = check_properties(properties, rules["categories"][category])
    return {
        "category_raw": raw_category,
        "category_resolved": category,
        "report": report,
    }


async def check_file_name(
    file_name: str,
    *,
    config_path: Path = CONFIG_PATH,
    rules_path: Path = DEFAULT_RULES_PATH,
    category_override: str = "",
    recursive: bool = False,
    bom_limit: int = 500,
) -> dict[str, Any]:
    """Sign in, look up the file, run the rules. Returns a result dict.

    Keys: ``file_name``, ``info``, ``rules``, ``category_raw``,
    ``category_resolved``, ``report``, ``children``, ``children_error``,
    ``recursive``. Raises ``RuntimeError`` for any fatal Vault / config error
    so the caller can surface the message.
    """
    cfg = load_json(config_path)
    rules = load_json(rules_path)

    vault_cfg = cfg.get("vault") or {}
    for key in ("servername", "username", "password", "database"):
        if not vault_cfg.get(key):
            raise RuntimeError(f"config.json is missing vault.{key}")

    api = VaultRestAPI(servername=vault_cfg["servername"])
    sign_in = await api.create_session(
        database=vault_cfg["database"],
        username=vault_cfg["username"],
        password=vault_cfg["password"],
    )
    if sign_in["error"]:
        raise RuntimeError(f"Vault sign-in failed: {sign_in['data']}")

    vault_id = str(
        (sign_in["data"].get("vaultInformation") or {}).get("id", "")
        or sign_in["data"].get("vaultId", "")
        or ""
    )

    info = await fetch_file(api, vault_id, file_name)

    raw_category = category_override or info["properties"].get("Category Name") or ""
    eval_top = evaluate_against_rules(info["properties"], raw_category, rules)

    children: list[dict[str, Any]] = []
    children_error: str | None = None
    if recursive:
        if not info["file_version_id"]:
            children_error = (
                "Cannot walk the CAD BOM — Vault returned no file-version ID."
            )
        else:
            try:
                rows = await fetch_cad_children(
                    api, vault_id, info["file_version_id"], limit=bom_limit
                )
            except RuntimeError as exc:
                children_error = str(exc)
                rows = []

            for row in rows:
                child_cat = row["properties"].get("Category Name") or ""
                children.append({
                    "file_name": row["file_name"],
                    "file_version_id": row["file_version_id"],
                    "assoc_type": row["assoc_type"],
                    "properties": row["properties"],
                    **evaluate_against_rules(row["properties"], child_cat, rules),
                    "error": None,
                })

    return {
        "file_name": file_name,
        "info": info,
        "rules": rules,
        "category_raw": eval_top["category_raw"],
        "category_resolved": eval_top["category_resolved"],
        "report": eval_top["report"],
        "children": children,
        "children_error": children_error,
        "recursive": recursive,
    }


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def child_status(child: dict[str, Any]) -> str:
    if child.get("error"):
        return "ERROR"
    if not child.get("category_resolved"):
        return "SKIP"
    return "PASS" if (child.get("report") or {}).get("failed", 0) == 0 else "FAIL"


def result_exit_code(result: dict[str, Any]) -> int:
    report = result.get("report")
    if report and report.get("failed", 0) > 0:
        return 1
    if any(child_status(c) in ("FAIL", "ERROR") for c in result.get("children") or []):
        return 1
    if not result.get("category_resolved"):
        return 2
    return 0


def _display(value: Any) -> str:
    return "(empty)" if _is_blank(value) else str(value)


# ---------------------------------------------------------------------------
# Terminal reporting
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    return sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _supports_color():
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def print_report(result: dict[str, Any]) -> None:
    info = result["info"]
    props = info["properties"]
    report = result["report"]

    print()
    print(_c("1;36", f"File: {result['file_name']}"))
    print(f"  Name          : {props.get('File Name', '(unknown)')}")
    print(f"  Title         : {_display(props.get('Title'))}")
    print(f"  Description   : {_display(props.get('Description (File)'))}")
    print(f"  Category      : {props.get('Category Name', '(unknown)')}")
    print(f"  Revision/State: {props.get('Revision', '?')} / {props.get('State', '?')}")
    if info.get("note"):
        print(_c("33", f"  Note: {info['note']}"))
    print()

    if not result["category_resolved"]:
        print(_c("31", "No rule set matched this file's category "
                       f"({result['category_raw'] or 'none'})."))
        print("  Edit file_property_rules.json or pass --category to override.")
        return

    print(_c("1", f"Checking against rules: {result['category_resolved']}"))
    print()

    results = report["results"]
    name_w = max((len(r["property"]) for r in results), default=18)
    name_w = max(name_w, 18)

    for r in results:
        mark = _c("32", "PASS") if r["passed"] else _c("31", "FAIL")
        value_str = _display(r["value"])
        if value_str == "(empty)":
            value_str = _c("90", value_str)
        print(f"  [{mark}]  {r['property']:<{name_w}}  {value_str}")
        for f in r["failures"]:
            print(f"          {_c('31', '> ' + f)}")

    print()
    summary = f"{report['passed']}/{report['total']} properties passed"
    print(_c("1;32" if report["failed"] == 0 else "1;31", summary))


def print_children_report(children: list[dict[str, Any]], show_all: bool) -> None:
    if not children:
        print(_c("33", "No CAD BOM children found."))
        return

    statuses = [child_status(c) for c in children]
    print()
    print(_c("1", f"Children ({len(children)} files)"))
    print()

    for c, status in zip(children, statuses):
        tag = {"PASS": "32", "FAIL": "31", "SKIP": "33", "ERROR": "31"}[status]
        rep = c.get("report") or {}
        score = (
            f"{rep.get('passed', 0)}/{rep.get('total', 0)}"
            if status in ("PASS", "FAIL") else ""
        )
        cat = c.get("category_resolved") or c.get("category_raw") or "(no rule set)"
        # Pad before colouring — ANSI escapes count toward a format width and
        # would blow the column alignment apart.
        print(f"  [{_c(tag, f'{status:<5}')}] {c['file_name']:<24} "
              f"{cat:<24} {score:>7}")

        if c.get("error"):
            print(f"          {_c('31', '> ' + c['error'])}")
            continue

        if status == "FAIL" or show_all:
            for r in rep.get("results") or []:
                if r["passed"] and not show_all:
                    continue
                inner_tag = "32" if r["passed"] else "31"
                inner_mark = "PASS" if r["passed"] else "FAIL"
                print(f"          [{_c(inner_tag, inner_mark)}] "
                      f"{r['property']:<24} {_display(r['value'])}")
                for f in r["failures"]:
                    print(f"                 {_c('31', '> ' + f)}")

    print()
    summary = (
        f"Children: {statuses.count('PASS')} pass, {statuses.count('FAIL')} fail, "
        f"{statuses.count('SKIP')} skipped (no rule set), "
        f"{statuses.count('ERROR')} errored."
    )
    failed = statuses.count("FAIL") + statuses.count("ERROR")
    print(_c("1;31" if failed else "1;32", summary))


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def _escape(text: str) -> str:
    """Escape pipes so a value or message can't break out of a table cell.

    Failure messages quote the rule that fired, and alternation regexes like
    ``(CD|SF|MFG|DT)`` are full of pipes.
    """
    return str(text).replace("|", "\\|")


def _short(value: Any, *, width: int = 40) -> str:
    if _is_blank(value):
        return "_(empty)_"
    s = str(value).strip()
    if len(s) > width:
        s = s[: width - 1] + "…"
    return _escape(s)


def _problem(rule_result: dict[str, Any]) -> str:
    return _escape("; ".join(rule_result.get("failures") or []) or "non-compliant")


def format_markdown_report(result: dict[str, Any]) -> str:
    """Render the file compliance check as Markdown."""
    info = result.get("info") or {}
    props = info.get("properties") or {}
    report = result.get("report") or {}
    children = result.get("children") or []
    category = (
        result.get("category_resolved")
        or result.get("category_raw")
        or "(unresolved)"
    )

    lines: list[str] = []
    lines.append(f"# File Property Compliance — `{result.get('file_name')}`")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Name | `{props.get('File Name', '(unknown)')}` |")
    lines.append(f"| Title | {_short(props.get('Title'), width=80)} |")
    lines.append(f"| Description | {_short(props.get('Description (File)'), width=80)} |")
    lines.append(f"| Category | {_short(category, width=40)} |")
    lines.append(f"| Revision / State | `{props.get('Revision', '?')}` / "
                 f"`{props.get('State', '?')}` |")
    if info.get("note"):
        lines.append(f"| Note | {info['note']} |")
    lines.append("")

    if not result.get("category_resolved"):
        lines.append("> No rule set matched this file's category. Add one to "
                     "`file_property_rules.json` or pass an explicit category.")
        lines.append("")
    elif report.get("failed", 0) == 0:
        lines.append(f"**PASS** — {report.get('passed', 0)}/{report.get('total', 0)} "
                     "properties compliant.")
        lines.append("")
    else:
        lines.append(f"**FAIL** — {report.get('passed', 0)}/{report.get('total', 0)} "
                     "properties compliant.")
        lines.append("")
        lines.append("| Property | Current Value | Problem |")
        lines.append("|---|---|---|")
        for r in report.get("results") or []:
            if r.get("passed"):
                continue
            lines.append(f"| `{r['property']}` | {_short(r.get('value'))} "
                         f"| {_problem(r)} |")
        lines.append("")

    if result.get("recursive"):
        lines.append("## CAD BOM children")
        lines.append("")
        if result.get("children_error"):
            lines.append(f"> {result['children_error']}")
            lines.append("")
        elif not children:
            lines.append("_(No child files found.)_")
            lines.append("")
        else:
            statuses = [child_status(c) for c in children]
            lines.append(
                f"**{len(children)} child file(s):** {statuses.count('PASS')} pass, "
                f"{statuses.count('FAIL')} fail, "
                f"{statuses.count('SKIP')} skipped (no rule set), "
                f"{statuses.count('ERROR')} errored."
            )
            lines.append("")
            lines.append("| Status | File | Category | Score |")
            lines.append("|---|---|---|---|")
            for c, st in zip(children, statuses):
                rep = c.get("report") or {}
                score = (
                    f"{rep.get('passed', 0)}/{rep.get('total', 0)}"
                    if st in ("PASS", "FAIL") else "—"
                )
                cat = c.get("category_resolved") or c.get("category_raw") or "(no rule set)"
                lines.append(f"| {st} | `{c.get('file_name')}` | {cat} | {score} |")
            lines.append("")

            offenders = [c for c, st in zip(children, statuses) if st in ("FAIL", "ERROR")]
            if offenders:
                lines.append("### Child failures")
                lines.append("")
                for c in offenders:
                    if c.get("error"):
                        lines.append(f"- **`{c['file_name']}`** — error: {c['error']}")
                        continue
                    bad = [r for r in ((c.get("report") or {}).get("results") or [])
                           if not r.get("passed")]
                    if not bad:
                        continue
                    lines.append(f"- **`{c['file_name']}`** "
                                 f"({c.get('category_resolved') or '?'})")
                    for r in bad:
                        lines.append(f"    - `{r['property']}` = "
                                     f"{_short(r.get('value'))} → {_problem(r)}")
                lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def run_cli(args: argparse.Namespace) -> int:
    try:
        result = await check_file_name(
            args.file_name,
            config_path=args.config,
            rules_path=args.rules,
            category_override=args.category,
            recursive=args.recursive,
        )
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        sys.exit(f"[ERROR] {exc}")

    if args.markdown:
        print(format_markdown_report(result))
    elif args.json:
        print(json.dumps({
            "file_name": args.file_name,
            "category_raw": result["category_raw"],
            "category_resolved": result["category_resolved"],
            "properties": result["info"]["properties"],
            "note": result["info"].get("note"),
            "report": result["report"],
            "children": [
                {k: v for k, v in c.items()
                 if k != "properties" or args.show_all_props}
                for c in result["children"]
            ],
            "children_error": result["children_error"],
            "available_categories": sorted(
                (result["rules"].get("categories") or {}).keys()
            ),
        }, indent=2, default=str))
    else:
        print_report(result)
        if args.show_all_props:
            print()
            print(_c("1", "All properties returned by Vault:"))
            for k in sorted(result["info"]["properties"]):
                print(f"  {k:<34} = {result['info']['properties'][k]}")
        if args.recursive:
            if result["children_error"]:
                print()
                print(_c("31", result["children_error"]))
            else:
                print_children_report(result["children"], args.show_all_props)

    return result_exit_code(result)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Check a Vault file's properties against "
                    "file_property_rules.json.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("file_name", nargs="?", default="",
                   help="Vault file name, e.g. CD-001659.iam. "
                        "Omit to launch the GUI.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH,
                   help="Path to config.json.")
    p.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH,
                   help="Path to the rules JSON.")
    p.add_argument("--category", default="",
                   help="Override the file's Category Name "
                        "(e.g. 'Assembly - Engineering').")
    p.add_argument("--json", action="store_true",
                   help="Emit a machine-readable JSON report.")
    p.add_argument("--markdown", "-m", action="store_true",
                   help="Emit a Markdown report.")
    p.add_argument("--show-all-props", action="store_true",
                   help="Also dump every property Vault returned.")
    p.add_argument("--recursive", "-r", action="store_true",
                   help="Walk the CAD BOM and check every child file too.")
    p.add_argument("--gui", action="store_true",
                   help="Launch the GUI even if a file name is supplied.")
    p.add_argument("--no-gui", action="store_true",
                   help="Force CLI mode — error out if no file name is given.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.gui or (not args.file_name and not args.no_gui):
        from gui.file_property_check import run_gui  # noqa: PLC0415
        run_gui(default_config=args.config, default_rules=args.rules)
        sys.exit(0)
    if not args.file_name:
        sys.exit("[ERROR] file_name is required in --no-gui mode.")
    sys.exit(asyncio.run(run_cli(args)))


if __name__ == "__main__":
    main()
