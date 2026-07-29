# tests/test_release_steps.py
import release_steps

# Exact hex values, not just "starts with #" — a mistyped hex during a pure
# copy/paste move is the single most likely defect here, and the palette is
# the one thing this file most needs to catch.
PALETTE_HEX = {
    "DARK_BLUE":   "#1F3864",
    "MID_BLUE":    "#2E75B6",
    "PALE_BLUE":   "#EAF3FB",
    "LIGHT_GRAY":  "#F2F2F2",
    "GRAY_BDR":    "#CCCCCC",
    "DARK_GRAY":   "#888888",
    "WHITE":       "#FFFFFF",
    "OLIVE_GREEN": "#D8E4BC",
    "RUST_ORANGE": "#C0504D",
    "WARN_AMBER":  "#B7791F",
}
PALETTE = list(PALETTE_HEX)


def test_theme_exports_the_exact_palette_hex_values():
    from gui import theme
    for name, expected in PALETTE_HEX.items():
        assert hasattr(theme, name), f"theme is missing {name}"
        assert getattr(theme, name) == expected, (
            f"theme.{name} == {getattr(theme, name)!r}, expected {expected!r}"
        )


def test_theme_exports_the_shared_helpers():
    from gui import theme
    for name in ("_resource_path", "_pil_available"):
        assert hasattr(theme, name), f"theme is missing {name}"


def test_release_workflow_still_re_exports_the_palette():
    """Six GUI modules import these from gui.release_workflow. Keep that working."""
    from gui import release_workflow, theme
    for name in PALETTE + ["_resource_path", "_pil_available"]:
        assert getattr(release_workflow, name) == getattr(theme, name)


def test_release_workflow_still_exposes_the_pil_names():
    """_build_header and _set_window_icon reference these; without them the
    logo and window icon fail inside a bare except and vanish silently."""
    from gui import release_workflow
    for name in ("PILImage", "ImageTk", "_pil_available"):
        assert hasattr(release_workflow, name), f"release_workflow lost {name}"


def test_outcome_defaults_to_no_pending_apply():
    out = release_steps.StepOutcome(ok=True, summary="done")
    assert out.pending_apply is None
    assert out.lines == []
    assert out.result is None


def test_outcome_needs_review_is_false_with_no_pending_apply():
    out = release_steps.StepOutcome(ok=True, summary="done")
    assert out.needs_review is False


def test_outcome_needs_review_is_true_with_a_pending_apply():
    """A preview may report problems (ok=False) and still offer Apply — step
    5 previewing drawing gaps is exactly that, so needs_review must take
    precedence over ok rather than derive from it."""
    out = release_steps.StepOutcome(
        ok=False, summary="preview",
        pending_apply=lambda: release_steps.StepOutcome(ok=True, summary="applied"),
    )
    assert out.needs_review is True


def test_all_tags_covers_the_named_constants_and_excludes_wizard_chrome():
    assert release_steps.ALL_TAGS == (
        release_steps.TAG_INFO, release_steps.TAG_PASS, release_steps.TAG_FAIL,
        release_steps.TAG_WARN, release_steps.TAG_DIM, release_steps.TAG_H2,
    )
    # Wizard chrome, not engine output — must never sneak in here.
    assert "h1" not in release_steps.ALL_TAGS
    assert "step_banner" not in release_steps.ALL_TAGS


def _compliance(top=("100", "10"), children=(), failed=0, children_error=None,
                 category="Part", file_name="CD-001659.iam"):
    """Build a compliance result shaped like check_file_name's return value.

    ``category=None`` mimics an unresolved category: ``evaluate_against_rules``
    (check_file_properties.py) leaves ``report`` as ``None`` in that case,
    never as an empty dict — that None-vs-empty-dict distinction is exactly
    what the gate's critical bug was about.

    ``file_name`` mirrors the real result's top-level key
    (check_file_properties.py:526), which Task 6's name lookup reads.
    """
    return {
        "file_name": file_name,
        "info": {"file_version_id": top[0], "file_id": top[1]},
        "category_raw": category or "Unrecognized",
        "category_resolved": category,
        "report": {"failed": failed} if category else None,
        "children_error": children_error,
        "children": [
            {"file_version_id": v, "file_id": m, "file_name": f"F{v}.ipt"}
            for v, m in children
        ],
    }


# --- property_check_blocked ----------------------------------------------
#
# | condition                        | blocks? | force overrides? |
# |-----------------------------------|---------|------------------|
# | no step 1 result                  | yes     | no               |
# | children_error set                | yes     | no               |
# | top file has no rule set          | yes     | yes              |
# | failing properties (top or child) | yes     | yes              |
# | child has no rule set of its own  | no      | n/a              |


def test_gate_blocks_when_step_one_has_not_run():
    reason = release_steps.property_check_blocked(None, force=False)
    assert reason is not None
    assert "step 1" in reason.lower()


def test_gate_blocks_missing_result_even_with_force():
    """Force overrides bad properties, not absent data. Steps 2 and 3 have no
    file list at all without step 1, so there is nothing to force past."""
    reason = release_steps.property_check_blocked(None, force=True)
    assert reason is not None
    assert "step 1" in reason.lower()


def test_gate_blocks_on_a_present_but_empty_dict_even_with_force():
    """An empty dict is still falsy — pin this boundary next to None."""
    assert release_steps.property_check_blocked({}, force=True) is not None


def test_gate_clear_when_everything_passes():
    assert release_steps.property_check_blocked(_compliance(), force=False) is None


def test_gate_blocks_on_top_level_failure():
    reason = release_steps.property_check_blocked(_compliance(failed=2), force=False)
    assert reason is not None
    assert "force" in reason.lower()


def test_gate_blocks_on_child_failure():
    c = _compliance(children=[("200", "20")])
    c["children"][0].update(category_resolved="Part", report={"failed": 1})
    reason = release_steps.property_check_blocked(c, force=False)
    assert reason is not None
    assert "force" in reason.lower()


def test_gate_blocks_on_child_error():
    c = _compliance(children=[("200", "20")])
    # Matches the real producer (check_file_properties.py:512): an errored
    # child gets "report": None, never "report": {}.
    c["children"][0].update(error="lookup failed", report=None)
    reason = release_steps.property_check_blocked(c, force=False)
    assert reason is not None
    assert "force" in reason.lower()


def test_gate_force_overrides_failures():
    c = _compliance(failed=2, children=[("200", "20")])
    c["children"][0].update(category_resolved="Part", report={"failed": 3})
    assert release_steps.property_check_blocked(c, force=True) is None


def test_gate_blocks_when_top_file_has_no_rule_set():
    """The critical fix: an unresolved top-level category leaves report=None,
    which must not silently read as zero failures. The message must say
    nothing was checked and must not imply a failure."""
    reason = release_steps.property_check_blocked(_compliance(category=None),
                                                    force=False)
    assert reason is not None
    assert "nothing was checked" in reason.lower()
    assert "fail" not in reason.lower()


def test_gate_force_overrides_no_rule_set():
    """Forceable: a category may legitimately have no rules, and an
    un-forceable block would make the wizard unusable for that work."""
    reason = release_steps.property_check_blocked(_compliance(category=None),
                                                    force=True)
    assert reason is None


def test_gate_blocks_on_children_error_even_with_force():
    """Not forceable: the child list is silently incomplete, so the user
    cannot consent to a partial release when they cannot know what is
    missing."""
    c = _compliance(children_error="Cannot walk the CAD BOM")
    reason_default = release_steps.property_check_blocked(c, force=False)
    reason_forced = release_steps.property_check_blocked(c, force=True)
    assert reason_default is not None
    assert reason_forced is not None
    assert "cannot be forced" in reason_forced.lower()


def test_gate_child_with_no_rule_set_does_not_block():
    """Matches child_status (check_file_properties.py:541): an unresolved
    child category is SKIP, not FAIL, and must not block the release."""
    c = _compliance(children=[("200", "20")])
    c["children"][0].update(category_resolved=None, report=None)
    assert release_steps.property_check_blocked(c, force=False) is None


# --- file_version_ids / file_master_ids -----------------------------------


def test_version_ids_lead_with_the_top_file():
    c = _compliance(children=[("200", "20"), ("300", "30")])
    assert release_steps.file_version_ids(c) == ["100", "200", "300"]


def test_version_ids_dedupe_a_child_that_repeats_the_top():
    c = _compliance(children=[("100", "10"), ("200", "20")])
    assert release_steps.file_version_ids(c) == ["100", "200"]


def test_version_ids_skip_children_that_failed_to_resolve():
    c = _compliance(children=[("200", "20")])
    c["children"].append({"file_version_id": "", "file_id": "",
                          "error": "not found"})
    assert release_steps.file_version_ids(c) == ["100", "200"]


def test_version_ids_strip_whitespace_only_ids_as_blank():
    c = _compliance()
    c["children"] = [{"file_version_id": "   ", "file_id": "20",
                       "file_name": "F.ipt"}]
    assert release_steps.file_version_ids(c) == ["100"]


def test_master_ids_are_ints():
    c = _compliance(children=[("200", "20")])
    assert release_steps.file_master_ids(c) == [10, 20]


def test_master_ids_skip_blank_and_unparseable():
    c = _compliance(children=[("200", ""), ("300", "not-a-number")])
    assert release_steps.file_master_ids(c) == [10]


def test_master_ids_dedupe_a_child_that_shares_the_top_files_master_id():
    c = _compliance(children=[("200", "10")])
    assert release_steps.file_master_ids(c) == [10]


# --- unresolved_files -------------------------------------------------
#
# Returns (name, missing) so steps 2 and 3 can each filter to the kind of
# drop that actually affects them, rather than both crying wolf about a file
# that's only broken for one of them.


def test_unresolved_files_flags_a_child_missing_both_ids_as_both():
    c = _compliance(children=[("200", "20")])
    c["children"].append({"file_version_id": "", "file_id": "",
                          "file_name": "Bad.ipt", "error": "not found"})
    assert release_steps.unresolved_files(c) == [("Bad.ipt", "both")]


def test_unresolved_files_falls_back_to_unnamed():
    c = _compliance(children=[("200", "20")])
    c["children"].append({"file_version_id": "", "file_id": ""})
    assert release_steps.unresolved_files(c) == [("(unnamed)", "both")]


def test_unresolved_files_flags_a_child_missing_only_its_master_id():
    """Present in file_version_ids, absent from file_master_ids — step 2
    (Sync) handles this file fine, so only step 3 (Release) should treat it
    as skipped. Reporting "both" or a bare name here would make step 2's
    preview lie about a file it syncs correctly."""
    c = _compliance(children=[("200", "")])
    c["children"][0]["file_name"] = "NoMaster.ipt"
    assert release_steps.unresolved_files(c) == [("NoMaster.ipt", "master")]


def test_unresolved_files_flags_a_child_missing_only_its_version_id():
    """Mirror case: present in file_master_ids, absent from
    file_version_ids — only step 2 (Sync) should treat this as skipped."""
    c = _compliance(children=[("", "30")])
    c["children"][0]["file_name"] = "NoVersion.ipt"
    assert release_steps.unresolved_files(c) == [("NoVersion.ipt", "version")]


def test_unresolved_files_empty_when_everything_resolved():
    c = _compliance(children=[("200", "20")])
    assert release_steps.unresolved_files(c) == []


def test_derivation_handles_an_empty_result():
    assert release_steps.file_version_ids({}) == []
    assert release_steps.file_master_ids({}) == []
    assert release_steps.unresolved_files({}) == []
