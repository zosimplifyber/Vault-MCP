# tests/test_check_file_properties.py
"""Unit tests for the file-based property checker.

Runs against responses recorded from the live vault (``tests/fixtures/
vault_file_cd001659*.json``), so nothing here touches the network.
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

import check_file_properties as cfp  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture()
def search_payload():
    """A recorded /file-versions?q=CD-001659.iam&option[propDefIds]=all response."""
    return _fixture("vault_file_cd001659.json")


@pytest.fixture()
def uses_payload():
    """A recorded /file-versions/124814/uses response (CAD BOM children)."""
    return _fixture("vault_file_cd001659_uses.json")


@pytest.fixture()
def rules():
    return cfp.load_json(cfp.DEFAULT_RULES_PATH)


@pytest.fixture()
def properties(search_payload):
    record = search_payload["results"][0]
    defs = cfp.extract_definition_index(search_payload)
    return cfp.flatten_file_properties(record, defs)


# --------------------------------------------------------------------------- extraction

def test_definitions_come_from_the_response_itself(search_payload):
    """Vault embeds the property definitions, so no extra round-trip is needed."""
    defs = cfp.extract_definition_index(search_payload)
    assert defs, "the response should carry included.propertyDefinition"
    assert defs["77"]["displayName"] == "State"


def test_extract_definition_index_tolerates_a_missing_included_block():
    assert cfp.extract_definition_index({}) == {}
    assert cfp.extract_definition_index({"included": {}}) == {}
    assert cfp.extract_definition_index(None) == {}


def test_properties_resolve_to_their_display_names(properties):
    """The names have to match the keys in file_property_rules.json."""
    assert properties["Category Name"] == "Assembly - Engineering"
    assert properties["Description (File)"] == "bmw kft90 hot a adapter plate assembly"
    assert properties["Title"] == "CD-001659"
    assert properties["Source"] == "Make"
    assert properties["Engineer"] == "Alan Y."
    assert properties["Engr Approved By"] == "Zak O."
    assert properties["Project"] == "BMW"
    assert properties["Revision"] == "4"
    assert properties["State"] == "Released"
    assert properties["File Name"] == "CD-001659.iam"
    assert properties["CAD Category"] == ""


def test_historical_twins_do_not_shadow_the_live_value(properties):
    """Vault returns State and 'State (Historical)' as separate definitions."""
    assert properties["State"] == "Released"
    assert "State (Historical)" in properties


def test_unnamed_properties_are_skipped_not_invented():
    """A value whose definition didn't come back must not become a fake key."""
    record = {"name": "X.ipt", "properties": [
        {"propertyDefinitionId": "999", "value": "orphan"},
    ]}
    flat = cfp.flatten_file_properties(record, {})
    assert "orphan" not in flat.values()
    assert flat == {"File Name": "X.ipt"}


def test_a_populated_value_wins_over_an_empty_one():
    defs = {"1": {"displayName": "Vendor"}, "2": {"displayName": "Vendor"}}
    record = {"properties": [
        {"propertyDefinitionId": "1", "value": "MiSUMi"},
        {"propertyDefinitionId": "2", "value": ""},
    ]}
    assert cfp.flatten_file_properties(record, defs)["Vendor"] == "MiSUMi"

    record_reversed = {"properties": [
        {"propertyDefinitionId": "2", "value": ""},
        {"propertyDefinitionId": "1", "value": "MiSUMi"},
    ]}
    assert cfp.flatten_file_properties(record_reversed, defs)["Vendor"] == "MiSUMi"


# --------------------------------------------------------------------------- name matching

def test_exact_name_match_wins_over_the_first_result():
    records = [{"name": "CD-001659-BRACKET.ipt"}, {"name": "CD-001659.iam"}]
    record, note = cfp.select_file_record(records, "CD-001659.iam")
    assert record["name"] == "CD-001659.iam"
    assert note is None


def test_name_matching_ignores_case():
    records = [{"name": "CD-001659.iam"}]
    record, note = cfp.select_file_record(records, "cd-001659.IAM")
    assert record["name"] == "CD-001659.iam"
    assert note is None


def test_an_inexact_match_is_flagged_rather_than_checked_silently():
    records = [{"name": "CD-001659-BRACKET.ipt"}, {"name": "CD-001659-PLATE.ipt"}]
    record, note = cfp.select_file_record(records, "CD-001659")
    assert record["name"] == "CD-001659-BRACKET.ipt"
    assert note and "CD-001659-BRACKET.ipt" in note


def test_no_results_raises():
    with pytest.raises(RuntimeError, match="No files found"):
        cfp.select_file_record([], "NOPE.ipt")


# --------------------------------------------------------------------------- rules

def test_cd001659_reports_its_three_real_failures(properties, rules):
    """The end-to-end expectation for the worked example.

    Title only repeats the part number, the description carries a customer
    name and digits, and CAD Category is empty.
    """
    result = cfp.evaluate_against_rules(
        properties, properties["Category Name"], rules)
    assert result["category_resolved"] == "Assembly - Engineering"

    failed = {r["property"] for r in result["report"]["results"] if not r["passed"]}
    assert failed == {"Title", "Description (File)", "CAD Category"}
    assert result["report"]["failed"] == 3
    assert result["report"]["passed"] == result["report"]["total"] - 3


def test_the_compliant_properties_pass(properties, rules):
    result = cfp.evaluate_against_rules(
        properties, properties["Category Name"], rules)
    passed = {r["property"] for r in result["report"]["results"] if r["passed"]}
    assert {"Source", "Engineer", "Engr Approved By", "Project",
            "Revision", "State", "Category Name", "File Name"} <= passed


def test_a_title_that_is_only_the_part_number_fails(properties, rules):
    result = cfp.evaluate_against_rules(
        properties, "Assembly - Engineering", rules)
    title = next(r for r in result["report"]["results"] if r["property"] == "Title")
    assert not title["passed"]
    assert "forbidden pattern" in " ".join(title["failures"])


def test_a_descriptive_title_passes(properties, rules):
    props = dict(properties, **{"Title": "hot press adapter plate"})
    result = cfp.evaluate_against_rules(props, "Assembly - Engineering", rules)
    title = next(r for r in result["report"]["results"] if r["property"] == "Title")
    assert title["passed"]


def test_a_clean_description_passes(properties, rules):
    props = dict(properties, **{"Description (File)": "adapter plate assembly"})
    result = cfp.evaluate_against_rules(props, "Assembly - Engineering", rules)
    desc = next(r for r in result["report"]["results"]
                if r["property"] == "Description (File)")
    assert desc["passed"]


def test_cad_category_must_agree_with_the_vault_category(properties, rules):
    props = dict(properties, **{"CAD Category": "Part - Engineering"})
    result = cfp.evaluate_against_rules(props, "Assembly - Engineering", rules)
    cad = next(r for r in result["report"]["results"]
               if r["property"] == "CAD Category")
    assert not cad["passed"]

    props["CAD Category"] = "Assembly - Engineering"
    result = cfp.evaluate_against_rules(props, "Assembly - Engineering", rules)
    cad = next(r for r in result["report"]["results"]
               if r["property"] == "CAD Category")
    assert cad["passed"]


def test_an_iso_standard_purchased_part_is_exempt_from_a_vendor_number(rules):
    """A generic ISO screw has no single supplier SKU — but it still has a supplier."""
    props = {
        "File Name": "ISO 4762 M6x20.ipt", "Title": "ISO 4762 M6x20",
        "Description (File)": "socket head cap screw",
        "Revision": "1", "State": "Released", "Source": "Buy",
        "Vendor": "McMASTER-CARR", "Engineer": "Zak O.",
        "Engr Approved By": "Zak O.", "Designer": "Zak O.", "Project": "General",
        "Category Name": "Part - Purchased", "CAD Category": "Part - Purchased",
    }
    result = cfp.evaluate_against_rules(props, "Part - Purchased", rules)
    assert result["report"]["failed"] == 0, [
        (r["property"], r["failures"])
        for r in result["report"]["results"] if not r["passed"]
    ]


def test_a_non_standard_purchased_part_needs_a_vendor_number(rules):
    props = {
        "File Name": "CD-002000.ipt", "Title": "linear rail block",
        "Description (File)": "linear rail block",
        "Revision": "1", "State": "Released", "Source": "Buy",
        "Vendor": "MiSUMi", "Engineer": "Zak O.", "Engr Approved By": "Zak O.",
        "Designer": "Zak O.", "Project": "General",
        "Category Name": "Part - Purchased", "CAD Category": "Part - Purchased",
    }
    result = cfp.evaluate_against_rules(props, "Part - Purchased", rules)
    number = next(r for r in result["report"]["results"]
                  if r["property"] == "Vendor Number")
    assert not number["passed"]


def test_an_unknown_category_skips_rather_than_passes(properties, rules):
    result = cfp.evaluate_against_rules(properties, "Documents", rules)
    assert result["category_resolved"] is None
    assert result["report"] is None


def test_category_aliases_resolve(rules):
    assert cfp.resolve_category("Assembly-Engineering", rules) == "Assembly - Engineering"
    assert cfp.resolve_category("Purchased Part", rules) == "Part - Purchased"


# --------------------------------------------------------------------------- rules file

def test_every_rule_set_is_well_formed(rules):
    """A bad regex in the JSON should fail here, not mid-report."""
    assert rules["categories"], "there should be at least one rule set"
    for category, spec in rules["categories"].items():
        for prop, rule in (spec.get("properties") or {}).items():
            where = f"{category}.{prop}"
            assert isinstance(rule, dict), where
            for key in ("pattern",):
                if rule.get(key):
                    re.compile(rule[key])
            for pattern in rule.get("forbidden_patterns") or []:
                re.compile(pattern)
            clause = rule.get("required_unless")
            if clause:
                assert clause.get("property"), where
                re.compile(clause["matches_pattern"])
                # The property the exemption reads must be one this rule set
                # actually checks, or the exemption can never fire.
                assert clause["property"] in spec["properties"], where


# The Vault grid columns that must be filled in on every file, in every
# category. Dropping one of these from a rule set silently weakens the gate,
# so it fails here instead.
ALWAYS_REQUIRED = (
    "State", "Revision", "Project", "Designer", "Engineer",
    "Engr Approved By", "Source",
)

PART_CATEGORIES = ("Part - Engineering", "Part - Purchased", "Part - Content Center")


@pytest.mark.parametrize("prop", ALWAYS_REQUIRED)
def test_every_category_requires_the_core_columns(rules, prop):
    for category, spec in rules["categories"].items():
        rule = (spec.get("properties") or {}).get(prop)
        assert rule is not None, f"{category} has no rule for {prop}"
        assert rule.get("required") is True, f"{category}.{prop} is not required"


@pytest.mark.parametrize("prop", ALWAYS_REQUIRED)
def test_a_missing_core_column_is_reported(rules, prop):
    """Blank it out and the rule set has to catch it."""
    for category in rules["categories"]:
        props = {p: "placeholder" for p in rules["categories"][category]["properties"]}
        props[prop] = ""
        result = cfp.evaluate_against_rules(props, category, rules)
        failed = {r["property"] for r in result["report"]["results"] if not r["passed"]}
        assert prop in failed, f"{category} did not flag a blank {prop}"


def test_engr_approved_by_rejects_not_reviewed_everywhere(rules):
    for category, spec in rules["categories"].items():
        rule = spec["properties"]["Engr Approved By"]
        assert "NOT REVIEWED" in (rule.get("forbidden_values") or []), category
        assert "NOT REVIEWED" not in (rule.get("allowed_values") or []), category

        props = {p: "placeholder" for p in spec["properties"]}
        props["Engr Approved By"] = "NOT REVIEWED"
        result = cfp.evaluate_against_rules(props, category, rules)
        approved = next(r for r in result["report"]["results"]
                        if r["property"] == "Engr Approved By")
        assert not approved["passed"], f"{category} accepted NOT REVIEWED"


@pytest.mark.parametrize("category", PART_CATEGORIES)
def test_every_part_requires_a_vendor(rules, category):
    rule = rules["categories"][category]["properties"]["Vendor"]
    assert rule.get("required") is True, f"{category} does not require Vendor"
    assert "required_unless" not in rule, (
        f"{category} lets Vendor off the hook — it should be required outright"
    )


@pytest.mark.parametrize("category", PART_CATEGORIES)
def test_a_blank_vendor_is_reported_on_every_part(rules, category):
    props = {p: "placeholder"
             for p in rules["categories"][category]["properties"]}
    props["Vendor"] = ""
    result = cfp.evaluate_against_rules(props, category, rules)
    vendor = next(r for r in result["report"]["results"] if r["property"] == "Vendor")
    assert not vendor["passed"], f"{category} accepted a blank Vendor"


def test_an_iso_standard_part_still_needs_a_vendor(rules):
    """The standard-part exemption applies to Vendor Number, never to Vendor."""
    props = {
        "File Name": "ISO 4762 M6x20.ipt", "Title": "ISO 4762 M6x20",
        "Description (File)": "socket head cap screw",
        "Revision": "1", "State": "Released", "Source": "Buy",
        "Vendor": "", "Engineer": "Zak O.", "Engr Approved By": "Zak O.",
        "Designer": "Zak O.", "Project": "General",
        "Category Name": "Part - Purchased", "CAD Category": "Part - Purchased",
    }
    result = cfp.evaluate_against_rules(props, "Part - Purchased", rules)
    by_name = {r["property"]: r for r in result["report"]["results"]}
    assert not by_name["Vendor"]["passed"], "Vendor has no standard-part exemption"
    assert by_name["Vendor Number"]["passed"], "Vendor Number keeps its exemption"


def test_file_rules_do_not_use_item_only_property_names(rules):
    """Guards against copy-paste drift back toward item_property_rules.json."""
    item_only = {"Title (Item,CO)", "Description (Item,CO)", "Number", "Units"}
    for category, spec in rules["categories"].items():
        overlap = item_only & set(spec.get("properties") or {})
        assert not overlap, f"{category} uses item-side property names: {overlap}"


# --------------------------------------------------------------------------- children

def test_cad_bom_children_come_back_enriched(uses_payload, monkeypatch):
    """/uses returns children with their properties, so no extra fetch per child."""
    defs = cfp.extract_definition_index(uses_payload)
    children = []
    for row in uses_payload["results"]:
        child = row["childFile"]
        children.append(cfp.flatten_file_properties(child, defs))

    assert len(children) == 3
    names = sorted(c["File Name"] for c in children)
    assert names == ["CD-001162.ipt", "CD-001171.ipt", "CD-001624.ipt"]
    for props in children:
        assert props["Category Name"] == "Part - Engineering"
        assert props["Description (File)"]


def test_children_are_deduplicated_by_file_version(uses_payload):
    """A part used twice in an assembly is checked once."""
    import asyncio

    doubled = dict(uses_payload)
    doubled["results"] = list(uses_payload["results"]) + [uses_payload["results"][0]]

    class FakeAPI:
        async def get_file_uses(self, **_kwargs):
            return {"error": False, "status_code": 200, "data": doubled}

    children = asyncio.run(cfp.fetch_cad_children(FakeAPI(), "1", "124814"))
    assert len(children) == 3
    assert len({c["file_version_id"] for c in children}) == 3


def test_a_failed_bom_walk_raises_with_the_vault_message():
    import asyncio

    class FakeAPI:
        async def get_file_uses(self, **_kwargs):
            return {"error": True, "status_code": 403,
                    "data": {"message": "forbidden"}}

    with pytest.raises(RuntimeError, match="CAD BOM walk failed"):
        asyncio.run(cfp.fetch_cad_children(FakeAPI(), "1", "124814"))


# --------------------------------------------------------------------------- exit codes

def _result(report=None, category="Assembly - Engineering", children=None):
    return {
        "report": report, "category_resolved": category,
        "children": children or [],
    }


def test_exit_code_zero_when_everything_passes():
    assert cfp.result_exit_code(_result({"failed": 0, "passed": 5, "total": 5})) == 0


def test_exit_code_one_when_the_file_fails():
    assert cfp.result_exit_code(_result({"failed": 2, "passed": 3, "total": 5})) == 1


def test_exit_code_one_when_only_a_child_fails():
    child = {"error": None, "category_resolved": "Part - Engineering",
             "report": {"failed": 1}}
    result = _result({"failed": 0, "passed": 5, "total": 5}, children=[child])
    assert cfp.result_exit_code(result) == 1


def test_exit_code_two_when_no_rule_set_matched():
    assert cfp.result_exit_code(_result(None, category=None)) == 2


def test_a_skipped_child_is_not_a_failure():
    child = {"error": None, "category_resolved": None, "report": None}
    result = _result({"failed": 0, "passed": 5, "total": 5}, children=[child])
    assert cfp.child_status(child) == "SKIP"
    assert cfp.result_exit_code(result) == 0


# --------------------------------------------------------------------------- reporting

def test_markdown_report_names_every_failure(properties, rules):
    evaluated = cfp.evaluate_against_rules(
        properties, properties["Category Name"], rules)
    md = cfp.format_markdown_report({
        "file_name": "CD-001659.iam",
        "info": {"properties": properties, "note": None},
        "children": [], "children_error": None, "recursive": False,
        **evaluated,
    })
    assert "# File Property Compliance — `CD-001659.iam`" in md
    assert "**FAIL**" in md
    for prop in ("Title", "Description (File)", "CAD Category"):
        assert f"`{prop}`" in md


def test_markdown_escapes_pipes_so_tables_do_not_break(properties, rules):
    """Alternation regexes like (CD|SF|MFG|DT) appear verbatim in failures."""
    evaluated = cfp.evaluate_against_rules(
        properties, properties["Category Name"], rules)
    md = cfp.format_markdown_report({
        "file_name": "CD-001659.iam",
        "info": {"properties": properties, "note": None},
        "children": [], "children_error": None, "recursive": False,
        **evaluated,
    })
    title_row = next(line for line in md.splitlines()
                     if line.startswith("| `Title`"))
    # Three columns means four pipes; any extra is an unescaped one leaking in.
    assert title_row.count("|") - title_row.count("\\|") == 4, title_row


def test_gui_renders_a_report_without_blowing_up(properties, rules):
    """The GUI's render path is only exercised at runtime — pin it here."""
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    try:
        from gui.file_property_check import run_gui
        run_gui(parent=root)
        root.update_idletasks()

        window = next(w for w in root.winfo_children()
                      if isinstance(w, tk.Toplevel))
        text = _find_text_widget(window)
        assert text is not None, "the report pane should exist"

        evaluated = cfp.evaluate_against_rules(
            properties, properties["Category Name"], rules)
        window.render_for_test({
            "file_name": "CD-001659.iam",
            "info": {"properties": properties, "note": None},
            "children": [], "children_error": None, "recursive": False,
            **evaluated,
        })
        root.update_idletasks()

        rendered = text.get("1.0", "end")
        assert "CD-001659.iam" in rendered
        assert "Assembly - Engineering" in rendered
        for prop in ("Title", "Description (File)", "CAD Category"):
            assert prop in rendered
        assert "13/16 properties passed" in rendered
    finally:
        root.destroy()


def _find_text_widget(widget):
    import tkinter as tk
    for child in widget.winfo_children():
        if isinstance(child, tk.Text):
            return child
        found = _find_text_widget(child)
        if found is not None:
            return found
    return None


def test_markdown_report_says_pass_when_clean(properties, rules):
    clean = dict(properties, **{
        "Title": "hot press adapter plate",
        "Description (File)": "adapter plate assembly",
        "CAD Category": "Assembly - Engineering",
    })
    evaluated = cfp.evaluate_against_rules(clean, "Assembly - Engineering", rules)
    md = cfp.format_markdown_report({
        "file_name": "CD-001659.iam",
        "info": {"properties": clean, "note": None},
        "children": [], "children_error": None, "recursive": False,
        **evaluated,
    })
    assert "**PASS**" in md
