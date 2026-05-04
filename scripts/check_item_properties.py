"""
Check the properties of a Vault item (part or assembly) against a rule set.

Loads rules from item_property_rules.json at the project root. Rules are keyed
by Category Name (e.g. "Part - Purchased", "Part - Engineering",
"Assembly - Engineering"). Edit that file to change/extend the checks — the
script re-reads it on every run.

Run:
    python scripts/check_item_properties.py CD-001234
    python scripts/check_item_properties.py MFG-00037 --category "Assembly - Engineering"
    python scripts/check_item_properties.py CD-001234 --rules my_rules.json --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

# Make project root importable so we can reuse VaultRestAPI
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from vault_rest_api import VaultRestAPI  # noqa: E402


CONFIG_PATH = PROJECT_ROOT / "config.json"
DEFAULT_RULES_PATH = PROJECT_ROOT / "item_property_rules.json"


# ---------------------------------------------------------------------------
# Property name normalisation
# ---------------------------------------------------------------------------
# Vault returns property names in many flavours: REST camelCase, display name,
# internal name. Map every known synonym onto the canonical names we use in
# item_property_rules.json (which match the Vault grid headers in the UI).

CANONICAL_ALIASES: dict[str, str] = {
    # Number / part number
    "number": "Number", "itemnumber": "Number", "partnumber": "Number",
    # Title
    "title": "Title (Item,CO)", "title (item,co)": "Title (Item,CO)",
    # NOTE: do NOT alias "name" -> "Title (Item,CO)". On Vault item REST
    # responses ``name`` is the part number (mirror of ``number``), not the
    # title. The bogus alias was clobbering real titles like "ISO 2338 5 h8
    # x 10" with the part number "SF-001407", which broke required_unless
    # exemptions and any rule that reads the title.
    # Description
    "description": "Description (Item,CO)",
    "description (item,co)": "Description (Item,CO)",
    "desc": "Description (Item,CO)",
    # Revision / state
    "revision": "Revision", "rev": "Revision", "revisionnumber": "Revision",
    "state": "State", "lifecyclestate": "State", "lifecycle state": "State",
    "status": "State",
    # Category
    "category name": "Category Name", "categoryname": "Category Name",
    "category": "Category Name", "itemcategory": "Category Name",
    # Source
    "source": "Source", "bomstructure": "Source", "bom structure": "Source",
    "itemsource": "Source",
    # Units
    "units": "Units", "unit": "Units", "uom": "Units",
    # Material
    "material": "Material",
    # Vendor
    "vendor": "Vendor",
    "vendor number": "Vendor Number", "vendornumber": "Vendor Number",
    "vendorpartnumber": "Vendor Number", "vendor part number": "Vendor Number",
    # Sign-off / ownership
    "engineer": "Engineer",
    "engr approved": "Engr Approved", "engineeringapproved": "Engr Approved",
    "engineering approved": "Engr Approved", "engr_approved": "Engr Approved",
    "designer": "Designer",
    "project": "Project",
}


def canonical_name(raw: str) -> str:
    """Return the canonical property name for a raw Vault label, or the raw label unchanged."""
    if not isinstance(raw, str):
        return ""
    return CANONICAL_ALIASES.get(raw.strip().lower(), raw.strip())


# ---------------------------------------------------------------------------
# Property extraction from Vault REST responses
# ---------------------------------------------------------------------------

def _flatten_properties(record: dict[str, Any]) -> dict[str, Any]:
    """Return a flat {canonical_name: value} dict from a Vault item / item-version.

    Vault wraps properties differently across endpoints — sometimes flat at the
    root, sometimes in a nested ``properties`` dict, sometimes in a list of
    ``{name, value}`` dicts, and (when ``propDefIds`` is passed) as a list of
    ``{propertyDefinitionId, value, definition: {displayName, systemName}}``.
    This handles all four.
    """
    flat: dict[str, Any] = {}

    def absorb(key: Any, value: Any) -> None:
        if not isinstance(key, str):
            return
        cname = canonical_name(key)
        # First win: don't overwrite a populated value with an empty one
        existing = flat.get(cname)
        if existing in (None, "") or cname not in flat:
            flat[cname] = value

    for k, v in record.items():
        if k == "properties" and isinstance(v, dict):
            for kk, vv in v.items():
                absorb(kk, vv)
        elif k == "properties" and isinstance(v, list):
            for entry in v:
                if not isinstance(entry, dict):
                    continue
                # Pull the human-readable name. Order:
                #   1. nested ``definition.displayName``  (UDP enrichment shape)
                #   2. nested ``definition.systemName``
                #   3. flat ``name`` / ``displayName`` / ``propDefName``
                defn = entry.get("definition") or {}
                if not isinstance(defn, dict):
                    defn = {}
                name = (
                    defn.get("displayName")
                    or defn.get("systemName")
                    or entry.get("name")
                    or entry.get("displayName")
                    or entry.get("propDefName")
                )
                val = entry.get("value")
                if val is None:
                    val = entry.get("displayValue")
                # Absorb under the displayName so it matches the rules JSON
                # keys ("Title (Item,CO)", "Source", "Material", …).
                absorb(name, val)
                # Also absorb under systemName so canonical-name aliases that
                # only know the system name (e.g. "ItemNumber") still resolve.
                sysn = defn.get("systemName")
                if sysn and sysn != name:
                    absorb(sysn, val)
        elif isinstance(v, dict):
            # Some endpoints return scalar properties wrapped: {"value": "..."}
            if set(v.keys()) <= {"value", "displayValue", "type"} and ("value" in v or "displayValue" in v):
                absorb(k, v.get("value", v.get("displayValue")))
            else:
                absorb(k, v)
        else:
            absorb(k, v)

    return flat


# ---------------------------------------------------------------------------
# Vault lookup
# ---------------------------------------------------------------------------

def _extract_collection(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("results", "items", "itemVersions", "data", "value", "records"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
        if data.get("id") or data.get("masterId"):
            return [data]
    return []


def _extract_id(record: dict[str, Any] | None) -> str:
    if not isinstance(record, dict):
        return ""
    for key in ("id", "itemVersionId", "masterId", "itemId"):
        v = record.get(key)
        if v:
            return str(v)
    return ""


def _pick_latest_version_record(item: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    for key in ("latestItemVersion", "latestVersion", "latest"):
        v = item.get(key)
        if isinstance(v, dict):
            vid = _extract_id(v)
            if vid:
                return vid, v
    for key in ("latestItemVersionId", "latestVersionId"):
        v = item.get(key)
        if v:
            return str(v), None
    return "", None


# Vault returns only system fields by default. To pull user-defined properties
# (Source / Units / Material / Vendor / Engineer / Designer / Project / …) we
# have to pass ``propDefIds=<csv>`` on every item-version fetch. Building the
# CSV requires one extra round-trip to ``/property-definitions``, so cache it
# per-vault for the lifetime of the process.
_PROPDEF_CACHE: dict[str, str] = {}


async def get_all_item_propdef_ids(api: VaultRestAPI, vault_id: str) -> str:
    """Return a comma-separated string of every property-definition ID in the vault.

    The Vault REST endpoint doesn't filter property defs by entity type, so
    we hand the full set to every item lookup — Vault ignores defs that
    don't apply to items. The result is cached per-vault.
    """
    cached = _PROPDEF_CACHE.get(vault_id)
    if cached is not None:
        return cached
    resp = await api.get_property_definitions(vault_id=vault_id, limit=500)
    if resp.get("error"):
        _PROPDEF_CACHE[vault_id] = ""
        return ""
    data = resp.get("data") or {}
    rows = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in ("results", "items", "records", "data", "value"):
            inner = data.get(key)
            if isinstance(inner, list):
                rows = inner
                break
    ids = [str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")]
    csv = ",".join(ids)
    _PROPDEF_CACHE[vault_id] = csv
    return csv


async def fetch_item(api: VaultRestAPI, vault_id: str, part_number: str) -> dict[str, Any]:
    """Look up an item by part number and return a dict with ``master``,
    ``item_version`` and ``properties`` (the merged, canonical-named props).
    Raises ``RuntimeError`` if the item cannot be found.
    """
    propdef_csv = await get_all_item_propdef_ids(api, vault_id)

    search = await api.search_items(
        vault_id=vault_id, query=part_number, limit=10,
        prop_def_ids=propdef_csv,
    )
    if search["error"]:
        raise RuntimeError(f"search_items failed: {search['data']}")

    items = _extract_collection(search.get("data"))
    if not items:
        raise RuntimeError(f"No items found matching '{part_number}'.")

    # Prefer an exact part-number match if there is one
    def _num(rec: dict[str, Any]) -> str:
        flat = _flatten_properties(rec)
        return str(flat.get("Number", "") or "").strip()

    exact = [it for it in items if _num(it).lower() == part_number.strip().lower()]
    master = exact[0] if exact else items[0]

    multiple_note = None
    if len(items) > 1 and not exact:
        multiple_note = (
            f"{len(items)} items matched '{part_number}'; using the first. "
            "Refine the part number to disambiguate."
        )

    item_id = _extract_id(master)
    item_version_id, item_version = _pick_latest_version_record(master)

    if not item_version_id:
        history = await api.get_item_version_history(
            vault_id=vault_id, item_id=item_id, limit=50,
            prop_def_ids=propdef_csv,
        )
        if not history["error"]:
            versions = _extract_collection(history.get("data"))
            if versions:
                item_version = versions[-1]
                item_version_id = _extract_id(item_version)

    # Always re-fetch the item-version with propDefIds so the UDPs come back
    # populated. The reference embedded in the master record is too thin.
    if item_version_id:
        ver_resp = await api.get_item_version_by_id(
            vault_id=vault_id, item_version_id=item_version_id,
            prop_def_ids=propdef_csv,
        )
        if not ver_resp["error"] and isinstance(ver_resp.get("data"), dict):
            item_version = ver_resp["data"]

    properties = _flatten_properties(master)
    if isinstance(item_version, dict):
        for k, v in _flatten_properties(item_version).items():
            existing = properties.get(k)
            if existing in (None, "") or k not in properties:
                properties[k] = v

    return {
        "master": master,
        "item_version": item_version,
        "item_version_id": item_version_id,
        "properties": properties,
        "note": multiple_note,
    }


async def fetch_bom_children(
    api: VaultRestAPI,
    vault_id: str,
    item_version_id: str,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return one entry per BOM row with merged-canonical properties.

    Each entry: ``{"item_version_id": str, "row_order": str, "properties": {...}}``.
    For each row we merge the BOM-row props with a fresh ``get_item_version_by_id``
    call so user-defined properties (Engineer, Vendor, Material, …) are present.
    The version fetches are run concurrently to keep latency manageable.
    """
    propdef_csv = await get_all_item_propdef_ids(api, vault_id)

    bom = await api.get_item_bom(
        vault_id=vault_id, item_version_id=item_version_id, limit=limit,
        prop_def_ids=propdef_csv,
    )
    if bom["error"]:
        raise RuntimeError(f"get_item_bom failed: {bom['data']}")

    rows = _extract_collection(bom.get("data"))
    if not rows:
        return []

    async def hydrate(row: dict[str, Any]) -> dict[str, Any]:
        flat = _flatten_properties(row)
        ivid = (
            str(row.get("itemVersionId") or "")
            or str(row.get("childItemVersionId") or "")
            or _extract_id(row)
        )
        # Re-fetch the item version with propDefIds so we get every UDP
        # populated — the BOM-row record alone often only carries system
        # fields.
        if ivid:
            ver = await api.get_item_version_by_id(
                vault_id=vault_id, item_version_id=ivid,
                prop_def_ids=propdef_csv,
            )
            if not ver["error"] and isinstance(ver.get("data"), dict):
                for k, v in _flatten_properties(ver["data"]).items():
                    if v not in (None, ""):
                        flat[k] = v

        return {
            "item_version_id": ivid,
            "row_order": str(flat.get("Row Order") or row.get("rowOrder") or ""),
            "properties": flat,
        }

    results = await asyncio.gather(
        *[hydrate(r) for r in rows], return_exceptions=True
    )

    children: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            children.append({
                "item_version_id": "",
                "row_order": "",
                "properties": {},
                "error": f"{type(r).__name__}: {r}",
            })
        else:
            children.append(r)
    return children


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------

def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _required_unless_matches(
    rule: dict[str, Any],
    all_properties: dict[str, Any] | None,
) -> bool:
    """Return True iff a ``required_unless`` clause is satisfied (and the
    `required` check should therefore be skipped).

    Schema:
        "required_unless": {
            "property": "Title (Item,CO)",
            "matches_pattern": "(?i)^\\s*(ISO|DIN|ANSI|...)\\b"
        }

    Reads ``all_properties[property]`` and checks whether the value matches
    the regex via re.search. If the lookup property is missing/empty or the
    pattern doesn't match, the exemption does NOT fire and the surrounding
    `required` check applies normally.
    """
    clause = rule.get("required_unless")
    if not isinstance(clause, dict) or not all_properties:
        return False
    prop = clause.get("property")
    pattern = clause.get("matches_pattern")
    if not isinstance(prop, str) or not isinstance(pattern, str) or not pattern:
        return False
    other_val = all_properties.get(prop)
    if _is_empty(other_val):
        return False
    try:
        return re.search(pattern, _coerce_str(other_val)) is not None
    except re.error:
        return False


def evaluate_rule(
    prop_name: str,
    rule: dict[str, Any],
    value: Any,
    all_properties: dict[str, Any] | None = None,
) -> list[str]:
    """Return a list of human-readable failure messages for one property rule.

    ``all_properties`` is the full property dict for the item, used for
    cross-property checks like ``required_unless``. Optional for back-compat
    with callers that only have a single (prop, value) pair.
    """
    failures: list[str] = []
    required = bool(rule.get("required", False))
    if required and _required_unless_matches(rule, all_properties):
        required = False

    if _is_empty(value):
        if required:
            failures.append("missing (required)")
        return failures

    sval = _coerce_str(value).strip()

    allowed = rule.get("allowed_values")
    if isinstance(allowed, list) and allowed:
        if sval not in [str(a) for a in allowed]:
            failures.append(f"value '{sval}' not in allowed_values {allowed}")

    forbidden = rule.get("forbidden_values")
    if isinstance(forbidden, list) and forbidden:
        if sval in [str(f) for f in forbidden]:
            failures.append(f"value '{sval}' is in forbidden_values {forbidden}")

    pattern = rule.get("pattern")
    if isinstance(pattern, str) and pattern:
        try:
            if not re.fullmatch(pattern, sval):
                failures.append(f"value '{sval}' does not match pattern /{pattern}/")
        except re.error as exc:
            failures.append(f"invalid regex pattern '{pattern}': {exc}")

    # Forbidden patterns — list of regexes; if ANY matches anywhere in the
    # value (re.search), it's a failure. Lets us layer specific guidance
    # (e.g. "no ISO/DIN words in descriptions") on top of the structural
    # `pattern` check, so the failure message names the actual violation.
    forbidden_patterns = rule.get("forbidden_patterns")
    if isinstance(forbidden_patterns, list):
        for fp in forbidden_patterns:
            if not isinstance(fp, str) or not fp:
                continue
            try:
                if re.search(fp, sval):
                    failures.append(
                        f"value '{sval}' contains forbidden pattern /{fp}/"
                    )
            except re.error as exc:
                failures.append(f"invalid forbidden_pattern '{fp}': {exc}")

    min_len = rule.get("min_length")
    if isinstance(min_len, int) and len(sval) < min_len:
        failures.append(f"length {len(sval)} < min_length {min_len}")

    max_len = rule.get("max_length")
    if isinstance(max_len, int) and len(sval) > max_len:
        failures.append(f"length {len(sval)} > max_length {max_len}")

    return failures


def resolve_category(raw_category: str, rules: dict[str, Any]) -> str | None:
    """Map a Vault category name (or alias) to a rule-set key, or None if unknown."""
    if not raw_category:
        return None
    cats = rules.get("categories", {}) or {}
    if raw_category in cats:
        return raw_category
    aliases = rules.get("category_aliases", {}) or {}
    mapped = aliases.get(raw_category)
    if mapped and mapped in cats:
        return mapped
    # Case-insensitive fallback
    low = raw_category.strip().lower()
    for k in cats:
        if k.lower() == low:
            return k
    for alias, target in aliases.items():
        if alias.lower() == low and target in cats:
            return target
    return None


def check_properties(properties: dict[str, Any], category_rules: dict[str, Any]) -> dict[str, Any]:
    """Run every rule for the resolved category and return a structured report."""
    rules = category_rules.get("properties", {}) or {}
    results: list[dict[str, Any]] = []
    for prop_name, rule in rules.items():
        value = properties.get(prop_name)
        failures = evaluate_rule(prop_name, rule, value, all_properties=properties)
        results.append({
            "property": prop_name,
            "value": value,
            "rule": rule,
            "passed": len(failures) == 0,
            "failures": failures,
        })

    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]
    return {
        "total": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    return sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _supports_color():
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def print_report(part_number: str, info: dict[str, Any], category: str | None,
                 report: dict[str, Any] | None) -> None:
    props = info["properties"]
    print()
    print(_c("1;36", f"Item: {part_number}"))
    print(f"  Number       : {props.get('Number', '(unknown)')}")
    print(f"  Title        : {props.get('Title (Item,CO)', '(empty)')}")
    print(f"  Category     : {props.get('Category Name', '(unknown)')}")
    print(f"  Revision/State: {props.get('Revision', '?')} / {props.get('State', '?')}")
    if info.get("note"):
        print(_c("33", f"  Note: {info['note']}"))
    print()

    if not category:
        print(_c("31", "No matching rule set found for this category."))
        print("  Edit item_property_rules.json or pass --category to override.")
        return

    print(_c("1", f"Checking against rules: {category}"))
    print()

    if report is None:
        return

    name_w = max(len(r["property"]) for r in report["results"]) if report["results"] else 20
    name_w = max(name_w, 18)

    for r in report["results"]:
        passed = r["passed"]
        mark = _c("32", "PASS") if passed else _c("31", "FAIL")
        value = r["value"]
        if value is None or (isinstance(value, str) and value.strip() == ""):
            value_str = _c("90", "(empty)")
        else:
            value_str = str(value)
        print(f"  [{mark}]  {r['property']:<{name_w}}  {value_str}")
        for f in r["failures"]:
            print(f"          {_c('31', '> ' + f)}")

    print()
    summary = f"{report['passed']}/{report['total']} properties passed"
    print(_c("1;32" if report["failed"] == 0 else "1;31", summary))


# ---------------------------------------------------------------------------
# Markdown report (release-readiness gate)
# ---------------------------------------------------------------------------

def _short_value(value: Any, *, width: int = 40) -> str:
    if value is None:
        return "_(empty)_"
    s = str(value).strip()
    if not s:
        return "_(empty)_"
    if len(s) > width:
        return s[: width - 1] + "…"
    return s.replace("|", "\\|")


def _row_status(child: dict[str, Any]) -> str:
    if child.get("error"):
        return "ERROR"
    if not child.get("category_resolved"):
        return "SKIP"
    return "PASS" if (child.get("report") or {}).get("failed", 0) == 0 else "FAIL"


def format_markdown_report(result: dict[str, Any]) -> str:
    """Render a release-readiness report as Markdown.

    Designed to be the first thing the user sees when starting the release
    workflow — it surfaces every non-compliant property across the top item
    and its BOM children so the user knows what to fix before letting the
    sync-properties / lifecycle-release steps run.
    """
    pn = result.get("part_number", "(unknown)")
    info = result.get("info") or {}
    props = info.get("properties") or {}
    top_report = result.get("report") or {}
    children = result.get("children") or []
    children_error = result.get("children_error")
    category = result.get("category_resolved") or result.get("category_raw") or "(unresolved)"

    lines: list[str] = []
    lines.append(f"# Release Readiness Report — `{pn}`")
    lines.append("")
    lines.append(
        "_Pre-flight property compliance check. Fix every **FAIL** below before "
        "starting the sync-properties / release steps — those steps will propagate "
        "whatever values are present right now._"
    )
    lines.append("")

    # ---- Top-level item summary
    lines.append("## Top-level item")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Number | `{props.get('Number', '(unknown)')}` |")
    lines.append(f"| Title | {_short_value(props.get('Title (Item,CO)'), width=80)} |")
    lines.append(f"| Description | {_short_value(props.get('Description (Item,CO)'), width=80)} |")
    lines.append(f"| Category | {_short_value(category, width=40)} |")
    lines.append(f"| Revision / State | `{props.get('Revision', '?')}` / `{props.get('State', '?')}` |")
    if info.get("note"):
        lines.append(f"| Note | {info['note']} |")
    lines.append("")

    # ---- Top-level rule check
    if not result.get("category_resolved"):
        lines.append("> No rule set matched this category. Update `item_property_rules.json` "
                     "or pass an explicit category to the workflow.")
        lines.append("")
    elif top_report:
        if top_report.get("failed", 0) == 0:
            lines.append(f"**Top-level item: PASS** ({top_report.get('passed', 0)}/{top_report.get('total', 0)})")
            lines.append("")
        else:
            lines.append(
                f"**Top-level item: FAIL** ({top_report.get('passed', 0)}/{top_report.get('total', 0)} pass)"
            )
            lines.append("")
            lines.append("| Property | Current Value | Failure |")
            lines.append("|---|---|---|")
            for r in top_report.get("results") or []:
                if r.get("passed"):
                    continue
                fail_msg = "; ".join(r.get("failures") or []) or "non-compliant"
                lines.append(
                    f"| `{r['property']}` | {_short_value(r.get('value'))} | {fail_msg} |"
                )
            lines.append("")

    # ---- Children
    lines.append("## BOM children")
    lines.append("")
    if children_error:
        lines.append(f"> BOM walk failed: {children_error}")
        lines.append("")
    elif not result.get("recursive"):
        lines.append("_(BOM children not checked — re-run with recursive mode for the full report.)_")
        lines.append("")
    elif not children:
        lines.append("_(No BOM children found.)_")
        lines.append("")
    else:
        # Summary line
        statuses = [_row_status(c) for c in children]
        pass_n = statuses.count("PASS")
        fail_n = statuses.count("FAIL")
        skip_n = statuses.count("SKIP")
        err_n = statuses.count("ERROR")
        lines.append(
            f"**{len(children)} child item(s):** {pass_n} pass, "
            f"{fail_n} fail, {skip_n} skipped (no rule set), {err_n} errored."
        )
        lines.append("")

        # Roll-up per child
        lines.append("| Status | Number | Category | Score |")
        lines.append("|---|---|---|---|")
        for c, st in zip(children, statuses):
            rep = c.get("report") or {}
            score = (
                f"{rep.get('passed', 0)}/{rep.get('total', 0)}"
                if st in ("PASS", "FAIL") else "—"
            )
            cat = c.get("category_resolved") or c.get("category_raw") or "(no rule set)"
            lines.append(
                f"| {st} | `{c.get('part_number', '?')}` | {cat} | {score} |"
            )
        lines.append("")

        # Per-child failure detail
        offenders = [c for c, st in zip(children, statuses) if st in ("FAIL", "ERROR")]
        if offenders:
            lines.append("### Child failures (must fix)")
            lines.append("")
            for c in offenders:
                pn_c = c.get("part_number", "?")
                if c.get("error"):
                    lines.append(f"- **`{pn_c}`** — error: {c['error']}")
                    continue
                rep = c.get("report") or {}
                bad = [r for r in (rep.get("results") or []) if not r.get("passed")]
                if not bad:
                    continue
                lines.append(f"- **`{pn_c}`** ({c.get('category_resolved') or '?'})")
                for r in bad:
                    fail_msg = "; ".join(r.get("failures") or []) or "non-compliant"
                    lines.append(
                        f"    - `{r['property']}` = {_short_value(r.get('value'))} → {fail_msg}"
                    )
            lines.append("")

    # ---- Verdict / next-step gate
    top_failed = bool(top_report and top_report.get("failed", 0) > 0)
    kids_failed = any(_row_status(c) in ("FAIL", "ERROR") for c in children)
    lines.append("## Verdict")
    lines.append("")
    if not top_failed and not kids_failed and result.get("category_resolved"):
        lines.append("**READY** — all checked items pass. Safe to proceed to property sync and release.")
    else:
        bits = []
        if top_failed:
            bits.append("the top-level item")
        if kids_failed:
            bits.append("one or more BOM children")
        lines.append(
            "**NOT READY** — " + " and ".join(bits) +
            " failed compliance. Fix the values listed above (in Vault Explorer or Inventor), "
            "then re-run this report before continuing the workflow."
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reusable pipeline (used by both CLI and GUI)
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    try:
        return json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse {path}: {exc}") from exc


def evaluate_against_rules(
    properties: dict[str, Any],
    raw_category: str,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the rule set for ``raw_category`` and run it against ``properties``.

    Returns ``{"category_raw", "category_resolved", "report"}``. ``report`` is
    None when no rule set matches the category.
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


async def check_part_number(
    part_number: str,
    *,
    config_path: Path = CONFIG_PATH,
    rules_path: Path = DEFAULT_RULES_PATH,
    category_override: str = "",
    recursive: bool = False,
    bom_limit: int = 500,
) -> dict[str, Any]:
    """Sign in, look up the item, run the rules. Returns a result dict.

    Result keys: ``part_number``, ``info``, ``rules``, ``category_raw``,
    ``category_resolved``, ``report``, ``children`` (list when recursive),
    ``children_error`` (str or None). Raises ``RuntimeError`` for any
    fatal Vault / config error so the caller can surface the message.
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

    info = await fetch_item(api, vault_id, part_number)

    raw_category = category_override or info["properties"].get("Category Name") or ""
    eval_top = evaluate_against_rules(info["properties"], raw_category, rules)

    children: list[dict[str, Any]] = []
    children_error: str | None = None
    if recursive:
        if not info.get("item_version_id"):
            children_error = (
                "Cannot walk BOM — no item-version ID was resolved for the top item."
            )
        else:
            try:
                rows = await fetch_bom_children(
                    api, vault_id, info["item_version_id"], limit=bom_limit
                )
            except RuntimeError as exc:
                children_error = str(exc)
                rows = []

            top_iv = str(info.get("item_version_id") or "")
            top_num = str(info["properties"].get("Number") or "").strip().lower()

            for row in rows:
                # Skip the top item if Vault echoes it as the root BOM row
                row_num = str(row["properties"].get("Number") or "").strip().lower()
                if row.get("item_version_id") == top_iv:
                    continue
                if top_num and row_num and row_num == top_num:
                    continue

                if row.get("error"):
                    children.append({
                        "part_number": row["properties"].get("Number") or "(unknown)",
                        "row_order": row.get("row_order", ""),
                        "properties": row["properties"],
                        "category_raw": row["properties"].get("Category Name") or "",
                        "category_resolved": None,
                        "report": None,
                        "error": row["error"],
                    })
                    continue

                child_raw_cat = row["properties"].get("Category Name") or ""
                child_eval = evaluate_against_rules(row["properties"], child_raw_cat, rules)
                children.append({
                    "part_number": row["properties"].get("Number") or "(unknown)",
                    "row_order": row.get("row_order", ""),
                    "properties": row["properties"],
                    "item_version_id": row.get("item_version_id", ""),
                    **child_eval,
                    "error": None,
                })

    return {
        "part_number": part_number,
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
# CLI mode
# ---------------------------------------------------------------------------

def _child_status(child: dict[str, Any]) -> str:
    if child.get("error"):
        return "ERROR"
    if not child.get("category_resolved"):
        return "SKIP"
    rep = child.get("report") or {}
    return "PASS" if rep.get("failed", 0) == 0 else "FAIL"


def print_children_report(children: list[dict[str, Any]], show_all: bool) -> None:
    if not children:
        print(_c("33", "No BOM children found."))
        return

    failed = sum(1 for c in children if _child_status(c) == "FAIL")
    skipped = sum(1 for c in children if _child_status(c) == "SKIP")
    errored = sum(1 for c in children if _child_status(c) == "ERROR")
    passed = len(children) - failed - skipped - errored

    print()
    print(_c("1", f"Children ({len(children)} items)"))
    print()

    for c in children:
        status = _child_status(c)
        tag = {"PASS": "32", "FAIL": "31", "SKIP": "33", "ERROR": "31"}[status]
        rep = c.get("report") or {}
        score = (
            f"{rep.get('passed', 0)}/{rep.get('total', 0)}"
            if status in ("PASS", "FAIL") else ""
        )
        cat = c.get("category_resolved") or c.get("category_raw") or "(no rule set)"
        row = c.get("row_order", "")
        prefix = f"  [{_c(tag, status):<14}] {c['part_number']:<16} {cat:<26} {score:>7}"
        if row:
            prefix += f"  row {row}"
        print(prefix)

        if c.get("error"):
            print(f"          {_c('31', '> ' + c['error'])}")
            continue

        if status == "FAIL" or show_all:
            for r in (rep.get("results") or []):
                if r["passed"] and not show_all:
                    continue
                value = r["value"]
                value_str = (
                    "(empty)"
                    if value is None or (isinstance(value, str) and value.strip() == "")
                    else str(value)
                )
                inner_tag = "32" if r["passed"] else "31"
                inner_mark = "PASS" if r["passed"] else "FAIL"
                print(f"          [{_c(inner_tag, inner_mark)}] {r['property']:<24} {value_str}")
                for f in r["failures"]:
                    print(f"                 {_c('31', '> ' + f)}")

    print()
    summary = (
        f"Children summary: {passed} pass, {failed} fail, "
        f"{skipped} skipped (no rule set), {errored} errored."
    )
    print(_c("1;31" if failed or errored else "1;32", summary))


async def run_cli(args: argparse.Namespace) -> int:
    try:
        result = await check_part_number(
            args.part_number,
            config_path=args.config,
            rules_path=args.rules,
            category_override=args.category,
            recursive=args.recursive,
        )
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        sys.exit(f"[ERROR] {exc}")

    info = result["info"]
    category = result["category_resolved"]
    report = result["report"]

    if args.json:
        print(json.dumps({
            "part_number": args.part_number,
            "category_raw": result["category_raw"],
            "category_resolved": category,
            "properties": info["properties"],
            "report": report,
            "children": [
                {k: v for k, v in c.items() if k != "properties" or args.show_all_props}
                for c in result.get("children", [])
            ],
            "children_error": result.get("children_error"),
            "available_categories": sorted((result["rules"].get("categories") or {}).keys()),
        }, indent=2, default=str))
    else:
        print_report(args.part_number, info, category, report)
        if args.show_all_props:
            print()
            print(_c("1", "All properties returned by Vault:"))
            for k in sorted(info["properties"]):
                print(f"  {k:<28} = {info['properties'][k]}")

        if args.recursive:
            if result.get("children_error"):
                print()
                print(_c("31", f"BOM walk failed: {result['children_error']}"))
            else:
                print_children_report(result.get("children", []), args.show_all_props)

    failed_top = bool(report and report["failed"] > 0)
    failed_kids = any(_child_status(c) in ("FAIL", "ERROR") for c in result.get("children", []))
    if failed_top or failed_kids:
        return 1
    if not category:
        return 2
    return 0


# ---------------------------------------------------------------------------
# GUI mode (Tkinter)
# ---------------------------------------------------------------------------

def run_gui(default_config: Path = CONFIG_PATH, default_rules: Path = DEFAULT_RULES_PATH) -> None:
    """Launch the property-checker GUI. Blocks until the window is closed."""
    import threading
    import tkinter as tk
    from tkinter import ttk, messagebox

    # Pre-load the rules so we can populate the category dropdown. If this
    # fails, fall through to a minimal list — the actual check will surface
    # the real error later.
    try:
        rules_for_dropdown = load_json(default_rules)
        category_keys = sorted((rules_for_dropdown.get("categories") or {}).keys())
    except Exception:
        category_keys = []

    root = tk.Tk()
    root.title("Vault Item Property Checker")
    root.geometry("780x620")
    root.minsize(640, 480)

    # ---- Top input bar --------------------------------------------------
    top = ttk.Frame(root, padding=(10, 10, 10, 6))
    top.pack(fill="x")

    ttk.Label(top, text="Part Number:").grid(row=0, column=0, sticky="w", padx=(0, 6))
    pn_var = tk.StringVar()
    pn_entry = ttk.Entry(top, textvariable=pn_var, width=28)
    pn_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))

    check_btn = ttk.Button(top, text="Check")
    check_btn.grid(row=0, column=2, padx=(0, 4))

    ttk.Label(top, text="Category:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
    cat_var = tk.StringVar(value="(auto-detect)")
    cat_combo = ttk.Combobox(
        top,
        textvariable=cat_var,
        values=["(auto-detect)"] + category_keys,
        state="readonly",
        width=26,
    )
    cat_combo.grid(row=1, column=1, sticky="ew", pady=(8, 0))

    show_all_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        top, text="Show all Vault properties", variable=show_all_var
    ).grid(row=1, column=2, sticky="w", padx=(8, 0), pady=(8, 0))

    recursive_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        top, text="Check children (walk BOM)", variable=recursive_var
    ).grid(row=2, column=1, sticky="w", pady=(4, 0))

    show_all_kids_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        top, text="Show passing child details", variable=show_all_kids_var
    ).grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(4, 0))

    top.columnconfigure(1, weight=1)

    # ---- Results pane ---------------------------------------------------
    body = ttk.Frame(root, padding=(10, 0, 10, 6))
    body.pack(fill="both", expand=True)

    text = tk.Text(body, wrap="word", font=("Consolas", 10), background="#1e1e1e",
                   foreground="#dcdcdc", insertbackground="#dcdcdc", borderwidth=0)
    yscroll = ttk.Scrollbar(body, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=yscroll.set, state="disabled")
    text.pack(side="left", fill="both", expand=True)
    yscroll.pack(side="right", fill="y")

    # Color tags
    text.tag_configure("h1",      foreground="#4ec9b0", font=("Consolas", 11, "bold"))
    text.tag_configure("h2",      foreground="#dcdcdc", font=("Consolas", 10, "bold"))
    text.tag_configure("dim",     foreground="#808080")
    text.tag_configure("pass",    foreground="#6a9955", font=("Consolas", 10, "bold"))
    text.tag_configure("fail",    foreground="#f48771", font=("Consolas", 10, "bold"))
    text.tag_configure("warn",    foreground="#dcdcaa")
    text.tag_configure("err",     foreground="#f48771")
    text.tag_configure("summary_ok",   foreground="#6a9955", font=("Consolas", 11, "bold"))
    text.tag_configure("summary_fail", foreground="#f48771", font=("Consolas", 11, "bold"))

    # ---- Status bar -----------------------------------------------------
    status_var = tk.StringVar(value="Ready.")
    status = ttk.Label(root, textvariable=status_var, relief="sunken", anchor="w",
                       padding=(8, 3))
    status.pack(fill="x", side="bottom")

    # ---- Helpers --------------------------------------------------------
    def write(line: str = "", tag: str | None = None) -> None:
        text.configure(state="normal")
        if tag:
            text.insert("end", line + "\n", tag)
        else:
            text.insert("end", line + "\n")
        text.configure(state="disabled")
        text.see("end")

    def clear_output() -> None:
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.configure(state="disabled")

    def render_result(result: dict[str, Any]) -> None:
        clear_output()
        info = result["info"]
        props = info["properties"]
        category = result["category_resolved"]
        report = result["report"]
        part_number = result["part_number"]

        write(f"Item: {part_number}", "h1")
        write(f"  Number        : {props.get('Number', '(unknown)')}")
        write(f"  Title         : {props.get('Title (Item,CO)', '(empty)')}")
        write(f"  Category      : {props.get('Category Name', '(unknown)')}")
        write(f"  Revision/State: {props.get('Revision', '?')} / {props.get('State', '?')}")
        if info.get("note"):
            write(f"  Note: {info['note']}", "warn")
        write()

        if not category:
            write("No matching rule set found for this category.", "err")
            write("  Edit item_property_rules.json or pick a category above.", "dim")
        else:
            write(f"Checking against rules: {category}", "h2")
            write()
            results = report["results"]
            name_w = max((len(r["property"]) for r in results), default=18)
            name_w = max(name_w, 18)

            for r in results:
                tag = "pass" if r["passed"] else "fail"
                mark = "PASS" if r["passed"] else "FAIL"
                value = r["value"]
                value_str = (
                    "(empty)"
                    if value is None or (isinstance(value, str) and value.strip() == "")
                    else str(value)
                )

                # Mixed-tag line: use multiple inserts
                text.configure(state="normal")
                text.insert("end", "  [")
                text.insert("end", mark, tag)
                text.insert("end", f"]  {r['property']:<{name_w}}  ")
                text.insert("end", value_str + "\n",
                            "dim" if value_str == "(empty)" else None)
                for f in r["failures"]:
                    text.insert("end", f"          > {f}\n", "fail")
                text.configure(state="disabled")
            text.see("end")

            write()
            summary = f"{report['passed']}/{report['total']} properties passed"
            write(summary, "summary_ok" if report["failed"] == 0 else "summary_fail")
            status_var.set(summary)

        if show_all_var.get():
            write()
            write("All properties returned by Vault:", "h2")
            for k in sorted(props):
                write(f"  {k:<28} = {props[k]}")

        if not category:
            status_var.set("No rule set matched — pick a category to override.")

        # ---- Children section ------------------------------------------
        if result.get("recursive"):
            write()
            write("─" * 70, "dim")
            if result.get("children_error"):
                write(f"BOM walk failed: {result['children_error']}", "err")
            else:
                children = result.get("children") or []
                if not children:
                    write("No BOM children found.", "warn")
                else:
                    pf = sf = ff = ef = 0
                    for c in children:
                        st = _child_status(c)
                        if st == "PASS":  pf += 1
                        elif st == "FAIL": ff += 1
                        elif st == "SKIP": sf += 1
                        else:              ef += 1

                    write(f"Children ({len(children)} items)", "h2")
                    write()

                    show_passing = show_all_kids_var.get()
                    for c in children:
                        st = _child_status(c)
                        st_tag = {"PASS": "pass", "FAIL": "fail",
                                  "SKIP": "warn", "ERROR": "fail"}[st]
                        rep = c.get("report") or {}
                        score = (
                            f"{rep.get('passed', 0)}/{rep.get('total', 0)}"
                            if st in ("PASS", "FAIL") else ""
                        )
                        cat = c.get("category_resolved") or c.get("category_raw") or "(no rule set)"
                        row = c.get("row_order", "")

                        text.configure(state="normal")
                        text.insert("end", "  [")
                        text.insert("end", f"{st:<5}", st_tag)
                        text.insert("end", f"]  {c['part_number']:<16}  {cat:<26}  {score:>7}")
                        if row:
                            text.insert("end", f"   row {row}", "dim")
                        text.insert("end", "\n")
                        text.configure(state="disabled")

                        if c.get("error"):
                            write(f"          > {c['error']}", "fail")
                            continue

                        if st == "FAIL" or (show_passing and st in ("PASS", "FAIL")):
                            for r in (rep.get("results") or []):
                                if r["passed"] and not show_passing:
                                    continue
                                value = r["value"]
                                value_str = (
                                    "(empty)"
                                    if value is None or (isinstance(value, str) and value.strip() == "")
                                    else str(value)
                                )
                                inner_tag = "pass" if r["passed"] else "fail"
                                inner_mark = "PASS" if r["passed"] else "FAIL"
                                text.configure(state="normal")
                                text.insert("end", "          [")
                                text.insert("end", inner_mark, inner_tag)
                                text.insert("end", f"]  {r['property']:<24}  ")
                                text.insert("end", value_str + "\n",
                                            "dim" if value_str == "(empty)" else None)
                                for f in r["failures"]:
                                    text.insert("end", f"                  > {f}\n", "fail")
                                text.configure(state="disabled")

                    write()
                    summary = (
                        f"Children: {pf} pass, {ff} fail, "
                        f"{sf} skipped, {ef} errored."
                    )
                    write(summary, "summary_ok" if (ff == 0 and ef == 0) else "summary_fail")
                    # Update status bar to include child stats
                    top_summary = status_var.get()
                    status_var.set(f"{top_summary} | {summary}")
            text.see("end")

    def on_done(result: dict[str, Any] | None, error: str | None) -> None:
        check_btn.configure(state="normal")
        pn_entry.configure(state="normal")
        if error:
            clear_output()
            write("Error:", "err")
            write(f"  {error}", "err")
            status_var.set("Error.")
            return
        render_result(result)

    def do_check() -> None:
        part_number = pn_var.get().strip()
        if not part_number:
            messagebox.showwarning("Missing part number", "Enter a part number to check.")
            return

        category_override = "" if cat_var.get() == "(auto-detect)" else cat_var.get()
        recursive = recursive_var.get()

        clear_output()
        write(f"Looking up '{part_number}' in Vault…", "dim")
        if recursive:
            write("Walking BOM and checking children — this may take a moment.", "dim")
        status_var.set("Checking…")
        check_btn.configure(state="disabled")
        pn_entry.configure(state="disabled")

        def worker() -> None:
            try:
                result = asyncio.run(check_part_number(
                    part_number,
                    config_path=default_config,
                    rules_path=default_rules,
                    category_override=category_override,
                    recursive=recursive,
                ))
                root.after(0, on_done, result, None)
            except (RuntimeError, FileNotFoundError, ValueError) as exc:
                root.after(0, on_done, None, str(exc))
            except Exception as exc:  # noqa: BLE001 — surface anything unexpected to the GUI
                root.after(0, on_done, None, f"{type(exc).__name__}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    check_btn.configure(command=do_check)
    pn_entry.bind("<Return>", lambda _e: do_check())
    pn_entry.focus_set()

    write("Enter a part number above and press Check (or hit Enter).", "dim")

    root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Check a Vault item's properties against rules in item_property_rules.json.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("part_number", nargs="?", default="",
                   help="Vault part number (e.g. CD-001234, MFG-00037). Omit to launch the GUI.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to config.json.")
    p.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH, help="Path to rules JSON.")
    p.add_argument("--category", default="",
                   help="Override the item's Category Name (e.g. 'Part - Engineering').")
    p.add_argument("--json", action="store_true", help="Emit a machine-readable JSON report.")
    p.add_argument("--show-all-props", action="store_true",
                   help="After the rule report, dump every property Vault returned.")
    p.add_argument("--recursive", "-r", action="store_true",
                   help="Walk the BOM and run the rules against every child item too.")
    p.add_argument("--gui", action="store_true",
                   help="Launch the GUI even if a part number is supplied.")
    p.add_argument("--no-gui", action="store_true",
                   help="Force CLI mode — error out if no part number is given.")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.gui or (not args.part_number and not args.no_gui):
        run_gui(default_config=args.config, default_rules=args.rules)
        sys.exit(0)
    if not args.part_number:
        sys.exit("[ERROR] part_number is required in --no-gui mode.")
    sys.exit(asyncio.run(run_cli(args)))
