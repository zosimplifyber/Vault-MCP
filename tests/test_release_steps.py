# tests/test_release_steps.py
import pytest

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


# --- run_property_check (Task 5 — Step 1 engine) --------------------------


def test_property_check_reports_a_clean_assembly(monkeypatch):
    clean = {
        "file_name": "CD-001659.iam",
        "info": {"file_version_id": "100", "file_id": "10",
                 "properties": {"Revision": "A", "State": "Work in Progress"}},
        "report": {"total": 5, "passed": 5, "failed": 0, "results": []},
        "children": [], "children_error": None,
        "category_resolved": "Assembly",
    }
    monkeypatch.setattr(release_steps, "_check_file_name", lambda **kw: clean)

    out = release_steps.run_property_check("CD-001659.iam")

    assert out.ok is True
    assert out.pending_apply is None
    assert out.result is clean
    assert "5/5" in out.summary


def test_property_check_is_not_ok_when_rules_fail(monkeypatch):
    dirty = {
        "file_name": "CD-001659.iam",
        "info": {"file_version_id": "100", "file_id": "10", "properties": {}},
        "report": {"total": 5, "passed": 3, "failed": 2, "results": [
            {"property": "Revision", "passed": False, "value": "",
             "failures": ["must not be empty"]},
        ]},
        "children": [], "children_error": None,
        "category_resolved": "Assembly",
    }
    monkeypatch.setattr(release_steps, "_check_file_name", lambda **kw: dirty)

    out = release_steps.run_property_check("CD-001659.iam")

    assert out.ok is False
    # The failing property must be named, not just counted.
    assert any("Revision" in text for text, _tag in out.lines)
    # The result is still carried so steps 2/3 can run under Force.
    assert out.result is dirty


def test_property_check_surfaces_a_vault_error(monkeypatch):
    def boom(**_kw):
        raise RuntimeError("file 'CD-001659.iam' not found in Vault")
    monkeypatch.setattr(release_steps, "_check_file_name", boom)

    out = release_steps.run_property_check("CD-001659.iam")

    assert out.ok is False
    assert out.result is None
    assert "not found" in out.summary


def test_property_check_converts_a_raised_value_error_to_a_failed_outcome(
    monkeypatch,
):
    """check_file_name raises ValueError when exactly one of api/vault_id is
    supplied (a half-supplied session). run_property_check must convert that
    — like any other exception — into a failed outcome rather than letting
    it propagate out of the step."""
    def boom(**_kw):
        raise ValueError(
            "check_file_name: api and vault_id must be supplied together — "
            "got api=set, vault_id=''"
        )
    monkeypatch.setattr(release_steps, "_check_file_name", boom)

    out = release_steps.run_property_check("CD-001659.iam")

    assert out.ok is False
    assert out.result is None
    assert "must be supplied together" in out.summary


def test_property_check_counts_failing_children(monkeypatch):
    result = {
        "file_name": "CD-001659.iam",
        "info": {"file_version_id": "100", "file_id": "10", "properties": {}},
        "report": {"total": 5, "passed": 5, "failed": 0, "results": []},
        "children": [
            {"file_name": "A.ipt", "file_version_id": "200", "file_id": "20",
             "category_resolved": "Part",
             "report": {"failed": 1, "results": [
                 {"property": "Material", "passed": False, "value": "",
                  "failures": ["must not be empty"]}]}},
            {"file_name": "B.ipt", "file_version_id": "300", "file_id": "30",
             "category_resolved": "Part", "report": {"failed": 0, "results": []}},
        ],
        "children_error": None, "category_resolved": "Assembly",
    }
    monkeypatch.setattr(release_steps, "_check_file_name", lambda **kw: result)

    out = release_steps.run_property_check("CD-001659.iam")

    assert out.ok is False
    assert any("A.ipt" in text for text, _tag in out.lines)


def test_property_check_is_not_ok_when_children_error_is_set(monkeypatch):
    """A failed CAD BOM walk means the child list is incomplete. Reporting a
    pass on a partial walk is the same 'absent data reads as success' bug
    already fixed once in property_check_blocked — this pins the same fix in
    the step engine itself, since the top file and every named child can pass
    while an unseen child (the walk never reached it) is hiding a failure."""
    result = {
        "file_name": "CD-001659.iam",
        "info": {"file_version_id": "100", "file_id": "10", "properties": {}},
        "report": {"total": 5, "passed": 5, "failed": 0, "results": []},
        "children": [], "children_error": "Cannot walk the CAD BOM",
        "category_resolved": "Assembly",
    }
    monkeypatch.setattr(release_steps, "_check_file_name", lambda **kw: result)

    out = release_steps.run_property_check("CD-001659.iam")

    assert out.ok is False
    # The result is still carried — this is forceable (property_check_blocked
    # governs whether steps 2/3 may proceed), so step 1 itself must not hide it.
    assert out.result is result


# --- run_sync_properties (Task 6 — Step 2 engine) -------------------------
#
# This is the first step with a pending_apply. The "preview writes nothing"
# test is the important one — the same pattern repeats for Task 7.


class RecordingAPI:
    """Fake VaultRestAPI that records job submissions instead of making them."""

    def __init__(self):
        self.submitted = []

    async def submit_job(self, **kwargs):
        self.submitted.append(kwargs)
        return {"error": False, "data": {"job": {"id": str(len(self.submitted))}}}


class NoWriteAPI:
    """Fails the test if anything tries to write during a preview."""

    async def submit_job(self, **kwargs):
        raise AssertionError("preview must not submit jobs")


def test_sync_preview_lists_files_and_writes_nothing():
    c = _compliance(children=[("200", "20"), ("300", "30")])
    out = release_steps.run_sync_properties(NoWriteAPI(), "V1", c)

    assert out.ok is True
    assert out.pending_apply is not None       # staged, not done
    assert "3" in out.summary


def test_sync_apply_submits_one_job_per_file_version():
    api = RecordingAPI()
    c = _compliance(children=[("200", "20"), ("300", "30")])

    applied = release_steps.run_sync_properties(api, "V1", c).pending_apply()

    assert applied.ok is True
    assert applied.pending_apply is None       # terminal
    assert [j["params"]["FileVersionId"] for j in api.submitted] == \
        ["100", "200", "300"]


def test_sync_uses_pascal_case_job_params():
    """Vault's /jobs Params keys are case-sensitive PascalCase; camelCase is
    accepted with a 200 and silently ignored."""
    api = RecordingAPI()
    release_steps.run_sync_properties(api, "V1", _compliance()).pending_apply()

    assert "FileVersionId" in api.submitted[0]["params"]
    assert api.submitted[0]["job_type"] == "Autodesk.Vault.SyncProperties"


def test_sync_reports_a_failed_submission():
    class HalfBrokenAPI:
        def __init__(self):
            self.calls = 0

        async def submit_job(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                return {"error": True, "data": "queue is disabled"}
            return {"error": False, "data": {"job": {"id": "1"}}}

    c = _compliance(children=[("200", "20")])
    applied = release_steps.run_sync_properties(
        HalfBrokenAPI(), "V1", c).pending_apply()

    assert applied.ok is False
    assert "1 failed" in applied.summary


def test_sync_with_no_files_is_ok_and_stages_nothing():
    out = release_steps.run_sync_properties(NoWriteAPI(), "V1", {})
    assert out.ok is True
    assert out.pending_apply is None


def test_sync_preview_names_a_file_with_no_version_id_as_unsyncable():
    """unresolved_files reports both 'version' and 'master' drops; step 2
    (sync) only needs a version ID, so only 'version'/'both' drops are its
    problem. A file that's merely missing a master ID syncs fine and is
    step 3's concern — naming it here would make this preview lie."""
    c = _compliance(children=[("", "30")])
    c["children"][0]["file_name"] = "NoVersion.ipt"

    out = release_steps.run_sync_properties(NoWriteAPI(), "V1", c)

    assert any("NoVersion.ipt" in text for text, _tag in out.lines)


def test_sync_preview_does_not_flag_a_file_missing_only_its_master_id():
    """This file has a version ID, so it syncs fine — it belongs in the
    'will be synced' list (untagged as a problem), not in a skipped/warning
    line. Only a missing version ID is step 2's concern; a missing master ID
    is step 3's."""
    c = _compliance(children=[("200", "")])
    c["children"][0]["file_name"] = "NoMaster.ipt"

    out = release_steps.run_sync_properties(NoWriteAPI(), "V1", c)

    assert not any("NoMaster.ipt" in text and tag == release_steps.TAG_WARN
                   for text, tag in out.lines)


# --- run_release_files (Task 7 — Step 3 engine) ---------------------------


@pytest.fixture
def fake_sdk(monkeypatch):
    """Stub the vault_sdk bridge — the real one shells out to PowerShell."""
    calls = {"updated": []}

    def lookup_file(master_id):
        return {"found": True, "masterId": master_id}

    def find_state_id_for_file(record, name):
        return 42 if name == "Released" else None

    def update_file_lifecycle_states(masters, state_id, comment=""):
        calls["updated"].append((list(masters), state_id, comment))
        return {"updated": len(masters)}

    class VaultSDKError(Exception):
        pass

    monkeypatch.setattr(release_steps, "_sdk", lambda: type("SDK", (), {
        "lookup_file": staticmethod(lookup_file),
        "find_state_id_for_file": staticmethod(find_state_id_for_file),
        "update_file_lifecycle_states": staticmethod(update_file_lifecycle_states),
        "VaultSDKError": VaultSDKError,
    }))
    return calls


def test_release_preview_resolves_the_state_and_writes_nothing(fake_sdk):
    c = _compliance(children=[("200", "20")])
    out = release_steps.run_release_files(None, "V1", c, target_state="Released")

    assert out.ok is True
    assert out.pending_apply is not None
    assert fake_sdk["updated"] == []           # nothing moved
    assert "42" in out.summary                 # resolved state id is visible


def test_release_apply_promotes_every_master_id(fake_sdk):
    c = _compliance(children=[("200", "20")])
    applied = release_steps.run_release_files(
        None, "V1", c, target_state="Released").pending_apply()

    assert applied.ok is True
    masters, state_id, _comment = fake_sdk["updated"][0]
    assert masters == [10, 20]
    assert state_id == 42


def test_release_honours_an_explicit_state_id_override(fake_sdk):
    release_steps.run_release_files(
        None, "V1", _compliance(), target_state="Anything", state_id=99,
    ).pending_apply()

    _masters, state_id, _comment = fake_sdk["updated"][0]
    assert state_id == 99


def test_release_fails_when_the_state_cannot_be_resolved(fake_sdk):
    out = release_steps.run_release_files(
        None, "V1", _compliance(), target_state="Nonexistent")

    assert out.ok is False
    assert out.pending_apply is None
    assert fake_sdk["updated"] == []


def test_release_with_no_files_is_ok_and_stages_nothing(fake_sdk):
    out = release_steps.run_release_files(
        None, "V1", {}, target_state="Released")
    assert out.ok is True
    assert out.pending_apply is None


def test_release_preview_names_a_file_with_no_master_id_as_unreleasable(fake_sdk):
    """unresolved_files reports both 'master' and 'version' drops; step 3
    (release) only needs a master ID, so only 'master'/'both' drops are its
    problem. A file merely missing a version ID synced fine in step 2 and is
    not this step's concern — naming it here would make this preview lie."""
    c = _compliance(children=[("200", "")])
    c["children"][0]["file_name"] = "NoMaster.ipt"

    out = release_steps.run_release_files(None, "V1", c, target_state="Released")

    assert any("NoMaster.ipt" in text for text, _tag in out.lines)


def test_release_preview_does_not_flag_a_file_missing_only_its_version_id(
    fake_sdk,
):
    """This file has a master ID, so it releases fine — a missing version ID
    only mattered to step 2 (sync), which already ran. Step 3 must not flag
    it as a problem."""
    c = _compliance(children=[("", "30")])
    c["children"][0]["file_name"] = "NoVersion.ipt"

    out = release_steps.run_release_files(None, "V1", c, target_state="Released")

    assert not any("NoVersion.ipt" in text and tag == release_steps.TAG_WARN
                   for text, tag in out.lines)


# ===========================================================================
# Stage-2 review fixes: three Criticals (absent data reading as success,
# the fourth/fifth/sixth instance of that pattern in this project) plus
# three Improvements and a set of minors.
# ===========================================================================


# --- C1: an unchecked top file must never render as a green PASS ----------


def test_property_check_is_not_ok_when_the_top_file_has_no_rule_set(monkeypatch):
    """``report`` is None — not {} — when no rule set matches the file's
    category (check_file_properties.evaluate_against_rules / result_exit_code
    returns 2 for this case). That must not read as 0 failures. This is the
    same bug already fixed once in property_check_blocked; a green PASS here
    is worse than a silent one, because it is what makes ticking 'Force past
    compliance gate' at step 2 feel safe."""
    unchecked = {
        "file_name": "CD-001659.iam",
        "info": {"file_version_id": "100", "file_id": "10", "properties": {}},
        "report": None,
        "children": [], "children_error": None,
        "category_resolved": None, "category_raw": "Unrecognized",
    }
    monkeypatch.setattr(release_steps, "_check_file_name", lambda **kw: unchecked)

    out = release_steps.run_property_check("CD-001659.iam")

    assert out.ok is False
    assert "nothing was checked" in out.summary.lower()
    # Nothing about this outcome may look like a pass.
    assert not any(tag == release_steps.TAG_PASS for _text, tag in out.lines)


def test_property_check_unchecked_still_carries_the_result_for_force(monkeypatch):
    """Unchecked is forceable (property_check_blocked already treats a
    missing rule set as forceable) — step 1 must still hand the result
    through so steps 2/3 can run under Force."""
    unchecked = {
        "file_name": "CD-001659.iam",
        "info": {"file_version_id": "100", "file_id": "10", "properties": {}},
        "report": None,
        "children": [], "children_error": None,
        "category_resolved": None, "category_raw": "Unrecognized",
    }
    monkeypatch.setattr(release_steps, "_check_file_name", lambda **kw: unchecked)

    out = release_steps.run_property_check("CD-001659.iam")

    assert out.result is unchecked


# --- C2: step 3's apply must reflect what the bridge actually confirmed ---


def _sdk_stub_with_update_response(monkeypatch, update_return):
    """Minimal vault_sdk stub whose update_file_lifecycle_states returns
    exactly ``update_return`` — for pinning C2's success math against every
    shape the real PowerShell bridge can hand back."""
    def lookup_file(master_id):
        return {"found": True, "masterId": master_id}

    def find_state_id_for_file(record, name):
        return 42 if name == "Released" else None

    def update_file_lifecycle_states(masters, state_id, comment=""):
        return update_return

    class VaultSDKError(Exception):
        pass

    monkeypatch.setattr(release_steps, "_sdk", lambda: type("SDK", (), {
        "lookup_file": staticmethod(lookup_file),
        "find_state_id_for_file": staticmethod(find_state_id_for_file),
        "update_file_lifecycle_states": staticmethod(update_file_lifecycle_states),
        "VaultSDKError": VaultSDKError,
    }))


@pytest.mark.parametrize(
    "bridge_response, expect_ok, expect_in_summary",
    [
        ({"updated": 2}, True, "2 of 2"),        # full match — genuine success
        ({"updated": 0}, False, "0 of 2"),       # nothing actually moved
        ({}, False, "did not report"),           # no 'updated' key at all
        ({"updated": None}, False, "did not report"),  # key present but None
    ],
    ids=["full-match", "zero-moved", "empty-response", "updated-is-none"],
)
def test_release_apply_ok_reflects_what_the_bridge_actually_confirmed(
    monkeypatch, bridge_response, expect_ok, expect_in_summary,
):
    _sdk_stub_with_update_response(monkeypatch, bridge_response)
    c = _compliance(children=[("200", "20")])   # masters [10, 20] -> requested=2

    applied = release_steps.run_release_files(
        None, "V1", c, target_state="Released").pending_apply()

    assert applied.ok is expect_ok
    assert expect_in_summary in applied.summary
    # The denominator must always be visible — "2 moved" alone gives the
    # engineer no baseline to judge it against.
    assert "2" in applied.summary


# --- C3: a total drop must fail, not read as "nothing to do" --------------


def test_sync_reports_a_total_drop_as_a_failure_not_nothing_to_do():
    """3 files were found by step 1, but every one of them lacks a usable
    file-version ID. That is not the same as an assembly with zero CAD
    files — it must not share that outcome's green tick."""
    c = _compliance(top=("", "10"), children=[("", "20"), ("", "30")])

    out = release_steps.run_sync_properties(NoWriteAPI(), "V1", c)

    assert out.ok is False
    assert out.pending_apply is None
    assert "3" in out.summary


def test_release_reports_a_total_drop_as_a_failure_not_nothing_to_do(fake_sdk):
    c = _compliance(top=("100", ""), children=[("200", ""), ("300", "")])

    out = release_steps.run_release_files(None, "V1", c, target_state="Released")

    assert out.ok is False
    assert out.pending_apply is None
    assert "3" in out.summary
    assert fake_sdk["updated"] == []


def test_sync_with_truly_no_files_stays_ok_unlike_a_total_drop():
    """Pin the boundary next to the total-drop case: zero entries at all
    (nothing found) is still 'nothing to do', not a failure."""
    out = release_steps.run_sync_properties(NoWriteAPI(), "V1", {})
    assert out.ok is True
    assert out.pending_apply is None


# --- I1: step 2's apply must survive a mid-batch exception ----------------


def test_sync_apply_survives_a_mid_batch_exception_and_still_reports_every_file():
    class FlakyAPI:
        def __init__(self):
            self.calls = 0

        async def submit_job(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise ConnectionError("transport reset")
            return {"error": False, "data": {"job": {"id": str(self.calls)}}}

    c = _compliance(children=[("200", "20"), ("300", "30")])
    applied = release_steps.run_sync_properties(
        FlakyAPI(), "V1", c).pending_apply()

    assert applied.ok is False
    # One line per file — the raise on file 2 must not swallow the record of
    # files 1 and 3, or two real submissions become invisible to the user.
    assert len(applied.lines) == 3
    assert any("transport reset" in text for text, _tag in applied.lines)
    assert sum(1 for _t, tag in applied.lines
               if tag == release_steps.TAG_PASS) == 2


def test_sync_apply_does_not_crash_on_a_response_with_no_error_key():
    """resp['error'] raises KeyError on a response missing that key; must
    use resp.get('error') instead."""
    class BareAPI:
        async def submit_job(self, **kwargs):
            return {"data": {"job": {"id": "9"}}}  # no "error" key at all

    applied = release_steps.run_sync_properties(
        BareAPI(), "V1", _compliance()).pending_apply()

    assert applied.ok is True


# --- I2: every bridge call in step 3 must be caught, not just the first ---


def test_release_preview_converts_a_find_state_error_without_escaping(
    monkeypatch,
):
    """find_state_id_for_file reaches the same PowerShell bridge as
    lookup_file — a non-zero exit, timeout, or bad JSON there must convert
    to a failed outcome exactly like a lookup_file failure does, not
    escape out of run_release_files."""
    class VaultSDKError(Exception):
        pass

    def lookup_file(master_id):
        return {"found": True, "masterId": master_id}

    def find_state_id_for_file(record, name):
        raise VaultSDKError("vault_sdk.ps1 exited non-zero")

    monkeypatch.setattr(release_steps, "_sdk", lambda: type("SDK", (), {
        "lookup_file": staticmethod(lookup_file),
        "find_state_id_for_file": staticmethod(find_state_id_for_file),
        "update_file_lifecycle_states": staticmethod(
            lambda *a, **k: {"updated": 0}),
        "VaultSDKError": VaultSDKError,
    }))

    out = release_steps.run_release_files(
        None, "V1", _compliance(), target_state="Released")

    assert out.ok is False
    assert out.pending_apply is None
    assert "non-zero" in out.summary


def test_release_preview_converts_an_sdk_import_error_without_escaping(
    monkeypatch,
):
    """_sdk() itself can raise (e.g. the bridge module is missing) — that
    must convert to a failed outcome too, never escape."""
    def boom():
        raise ImportError("no module named vault_sdk")
    monkeypatch.setattr(release_steps, "_sdk", boom)

    out = release_steps.run_release_files(
        None, "V1", _compliance(), target_state="Released")

    assert out.ok is False
    assert out.pending_apply is None


def test_release_apply_converts_an_sdk_write_exception_to_a_failed_outcome(
    monkeypatch,
):
    """Pins step 3's apply try/except directly — deleting it entirely
    survived the mutation pass because nothing exercised this path."""
    def lookup_file(master_id):
        return {"found": True, "masterId": master_id}

    def find_state_id_for_file(record, name):
        return 42

    def update_file_lifecycle_states(masters, state_id, comment=""):
        raise RuntimeError("vault_sdk.ps1 exited non-zero")

    monkeypatch.setattr(release_steps, "_sdk", lambda: type("SDK", (), {
        "lookup_file": staticmethod(lookup_file),
        "find_state_id_for_file": staticmethod(find_state_id_for_file),
        "update_file_lifecycle_states": staticmethod(update_file_lifecycle_states),
        "VaultSDKError": Exception,
    }))

    applied = release_steps.run_release_files(
        None, "V1", _compliance(), target_state="Released").pending_apply()

    assert applied.ok is False
    assert "exited non-zero" in applied.summary


# --- I3: the top file must never read as "(unnamed)" ----------------------


def test_release_preview_names_the_top_file_by_its_real_name_not_unnamed(
    fake_sdk,
):
    """fetch_file's info dict carries no file_name of its own — that key
    lives one level up, at compliance['file_name']. Without borrowing it,
    the top file is the one file whose own drop always reads as
    '(unnamed)' while every CAD BOM child gets its real name."""
    c = _compliance(top=("100", ""), children=[("200", "20")])

    out = release_steps.run_release_files(None, "V1", c, target_state="Released")

    assert not any("(unnamed)" in text for text, _tag in out.lines)
    assert any("CD-001659.iam" in text for text, _tag in out.lines)


def test_sync_preview_names_the_top_file_by_its_real_name_not_unnamed():
    c = _compliance(top=("", "10"), children=[("200", "20")])

    out = release_steps.run_sync_properties(NoWriteAPI(), "V1", c)

    assert not any("(unnamed)" in text for text, _tag in out.lines)
    assert any("CD-001659.iam" in text for text, _tag in out.lines)


# --- Minors -----------------------------------------------------------


def test_sync_apply_refuses_a_second_call():
    """The GUI prevents a double-click, but the engine is what's under
    test and Tasks 8-10 will copy this shape — it must guard itself."""
    api = RecordingAPI()
    c = _compliance(children=[("200", "20")])
    pending = release_steps.run_sync_properties(api, "V1", c).pending_apply

    pending()
    submitted_after_first = len(api.submitted)
    second = pending()

    assert len(api.submitted) == submitted_after_first   # nothing submitted again
    assert second.ok is False
    assert second.pending_apply is None


def test_release_apply_refuses_a_second_call(fake_sdk):
    c = _compliance(children=[("200", "20")])
    pending = release_steps.run_release_files(
        None, "V1", c, target_state="Released").pending_apply

    pending()
    second = pending()

    assert len(fake_sdk["updated"]) == 1     # only wrote once
    assert second.ok is False
    assert second.pending_apply is None


def test_release_preview_warns_that_the_change_cannot_be_undone(fake_sdk):
    """A safety affordance on the only irreversible step — a mutation
    deleting this sentence survived, so pin it directly."""
    out = release_steps.run_release_files(
        None, "V1", _compliance(), target_state="Released")
    assert any("cannot be undone" in text.lower() for text, _tag in out.lines)


def test_master_ids_logs_a_warning_for_unparseable_ids(caplog):
    """logger.warning -> logger.debug survived the mutation pass; pin the
    level directly."""
    c = _compliance(children=[("200", "not-a-number")])
    with caplog.at_level("WARNING", logger="release_steps"):
        release_steps.file_master_ids(c)
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_property_check_only_emits_known_tags(monkeypatch):
    monkeypatch.setattr(release_steps, "_check_file_name", lambda **kw: {
        "file_name": "CD-001659.iam",
        "info": {"file_version_id": "100", "file_id": "10", "properties": {}},
        "report": {"total": 5, "passed": 3, "failed": 2, "results": [
            {"property": "Revision", "passed": False, "value": "",
             "failures": ["must not be empty"]}]},
        "children": [{"file_name": "A.ipt", "file_version_id": "200",
                      "file_id": "20", "category_resolved": "Part",
                      "report": {"failed": 1, "results": [
                          {"property": "Material", "passed": False,
                           "value": "", "failures": ["x"]}]}}],
        "children_error": None, "category_resolved": "Assembly",
    })
    out = release_steps.run_property_check("CD-001659.iam")
    for _text, tag in out.lines:
        assert tag in release_steps.ALL_TAGS, f"unknown tag {tag!r}"


def test_sync_only_emits_known_tags():
    c = _compliance(children=[("200", "20")])
    preview = release_steps.run_sync_properties(RecordingAPI(), "V1", c)
    applied = preview.pending_apply()
    for outcome in (preview, applied):
        for _text, tag in outcome.lines:
            assert tag in release_steps.ALL_TAGS, f"unknown tag {tag!r}"


def test_release_only_emits_known_tags(fake_sdk):
    c = _compliance(children=[("200", "20")])
    preview = release_steps.run_release_files(
        None, "V1", c, target_state="Released")
    applied = preview.pending_apply()
    for outcome in (preview, applied):
        for _text, tag in outcome.lines:
            assert tag in release_steps.ALL_TAGS, f"unknown tag {tag!r}"


def test_file_version_ids_handles_none_the_same_way_unresolved_files_does():
    """The None guard lived only in unresolved_files; file_version_ids(None)
    and file_master_ids(None) raised. Move the guard into _entries so all
    three behave the same way instead of inviting a wrong assumption."""
    assert release_steps.file_version_ids(None) == []
    assert release_steps.file_master_ids(None) == []
    assert release_steps.unresolved_files(None) == []


# --- run_purchased_parts_list (Task 8 — Step 4 engine) --------------------


def _install_list_sync(monkeypatch, *, report=None, connect_error=None):
    """Stub the BOM parse, the SharePoint connect, and the sync call."""
    seen = {"dry_runs": []}

    def fake_connect():
        if connect_error:
            raise RuntimeError(connect_error)
        return object()

    def fake_add(client, df, *, dry_run=True, sources=None,
                 update_existing=False):
        seen["dry_runs"].append(dry_run)
        base = {"missing": ["SF-001902", "SF-001905"], "checked": 42,
                "already_present": 40, "existing_count": 900, "created": 0,
                "updated": 0, "errors": [], "by_source": {"Buy": 2},
                "rows": [], "dry_run": dry_run}
        out = dict(base, **(report or {}))
        if not dry_run:
            out["created"] = len(out["missing"])
        return out

    monkeypatch.setattr(release_steps, "_list_sync_deps",
                        lambda: (fake_connect, fake_add,
                                 lambda _p: ("DF", None)))
    return seen


def test_purchased_parts_preview_is_a_dry_run(monkeypatch):
    seen = _install_list_sync(monkeypatch)
    out = release_steps.run_purchased_parts_list("C:/bom.xlsx")

    assert out.ok is True
    assert out.pending_apply is not None
    assert seen["dry_runs"] == [True]          # nothing written
    # Each addition is named, so ISO/DIN fasteners that never match are
    # visible before they are added.
    assert any("SF-001902" in text for text, _tag in out.lines)


def test_purchased_parts_preview_never_writes_to_sharepoint(monkeypatch):
    """Distinct from the dry-run-flag check above: the underlying add call
    raises if it is ever invoked with dry_run=False during a preview, so a
    bug that reaches the write path is caught even if nothing inspects the
    dry_run flag it was handed."""
    calls = {"connects": 0}

    def fake_connect():
        calls["connects"] += 1
        return object()

    def fake_add(client, df, *, dry_run=True, sources=None,
                 update_existing=False):
        if not dry_run:
            raise AssertionError("preview must not perform a live write")
        return {"missing": ["SF-001902"], "checked": 1, "already_present": 0,
                "existing_count": 0, "created": 0, "updated": 0,
                "errors": [], "by_source": {"Buy": 1}, "rows": [],
                "dry_run": True}

    monkeypatch.setattr(release_steps, "_list_sync_deps",
                        lambda: (fake_connect, fake_add,
                                 lambda _p: ("DF", None)))
    out = release_steps.run_purchased_parts_list("C:/bom.xlsx")

    assert out.ok is True
    assert calls["connects"] == 1


def test_purchased_parts_apply_writes(monkeypatch):
    seen = _install_list_sync(monkeypatch)
    applied = release_steps.run_purchased_parts_list("C:/bom.xlsx").pending_apply()

    assert applied.ok is True
    assert seen["dry_runs"] == [True, False]
    assert "2 of 2" in applied.summary


def test_purchased_parts_apply_reports_against_the_expected_count(monkeypatch):
    """R2: 'created' must be judged against how many the preview said were
    missing — a partial write (1 of 2 rows actually created, no errors
    reported) must not read as success just because the errors list happens
    to be empty."""
    def fake_add(client, df, *, dry_run=True, sources=None,
                 update_existing=False):
        base = {"missing": ["SF-001902", "SF-001905"], "checked": 2,
                "already_present": 0, "existing_count": 0, "updated": 0,
                "errors": [], "by_source": {"Buy": 2}, "rows": [],
                "dry_run": dry_run}
        base["created"] = 0 if dry_run else 1     # only one really landed
        return base

    monkeypatch.setattr(release_steps, "_list_sync_deps",
                        lambda: (lambda: object(), fake_add,
                                 lambda _p: ("DF", None)))
    applied = release_steps.run_purchased_parts_list("C:/bom.xlsx").pending_apply()

    assert applied.ok is False
    assert "1 of 2" in applied.summary


def test_purchased_parts_apply_failure_does_not_lose_the_expected_count(
    monkeypatch,
):
    """R4: the apply body must not let an exception escape, and the count of
    what the preview expected must survive it — losing that record is worse
    than the failure itself."""
    def fake_add(client, df, *, dry_run=True, sources=None,
                 update_existing=False):
        if dry_run:
            return {"missing": ["SF-001902", "SF-001905"], "checked": 2,
                    "already_present": 0, "existing_count": 0, "created": 0,
                    "updated": 0, "errors": [], "by_source": {"Buy": 2},
                    "rows": [], "dry_run": True}
        raise RuntimeError("SharePoint connection dropped mid-batch")

    monkeypatch.setattr(release_steps, "_list_sync_deps",
                        lambda: (lambda: object(), fake_add,
                                 lambda _p: ("DF", None)))
    applied = release_steps.run_purchased_parts_list("C:/bom.xlsx").pending_apply()

    assert applied.ok is False
    assert "2" in applied.summary      # the expected count survives the raise


def test_purchased_parts_apply_refuses_a_second_call(monkeypatch):
    seen = _install_list_sync(monkeypatch)
    pending = release_steps.run_purchased_parts_list("C:/bom.xlsx").pending_apply

    pending()
    second = pending()

    assert seen["dry_runs"] == [True, False]   # nothing submitted again
    assert second.ok is False
    assert second.pending_apply is None


def test_purchased_parts_stages_nothing_when_list_is_current(monkeypatch):
    _install_list_sync(monkeypatch, report={"missing": [], "by_source": {}})
    out = release_steps.run_purchased_parts_list("C:/bom.xlsx")

    assert out.ok is True
    assert out.pending_apply is None           # nothing to apply
    assert "0" in out.summary or "up to date" in out.summary.lower()


def test_purchased_parts_explains_a_sharepoint_auth_failure(monkeypatch):
    _install_list_sync(monkeypatch, connect_error="not signed in")
    out = release_steps.run_purchased_parts_list("C:/bom.xlsx")

    assert out.ok is False
    assert "supplier_pricing probe" in out.summary


def test_purchased_parts_surfaces_a_bom_parse_error(monkeypatch):
    monkeypatch.setattr(
        release_steps, "_list_sync_deps",
        lambda: (lambda: object(), lambda *a, **k: {},
                 lambda _p: (None, "Unsupported file type: .foo")))
    out = release_steps.run_purchased_parts_list("C:/bom.foo")

    assert out.ok is False
    assert "Unsupported file type" in out.summary


def test_purchased_parts_only_emits_known_tags(monkeypatch):
    _install_list_sync(monkeypatch)
    preview = release_steps.run_purchased_parts_list("C:/bom.xlsx")
    applied = preview.pending_apply()
    for outcome in (preview, applied):
        for _text, tag in outcome.lines:
            assert tag in release_steps.ALL_TAGS, f"unknown tag {tag!r}"
