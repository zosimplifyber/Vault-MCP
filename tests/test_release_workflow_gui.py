# tests/test_release_workflow_gui.py
import glob
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

tk = pytest.importorskip("tkinter")


def _new_root():
    """A fresh Tk root, retrying the way tests/test_purchasing_list_sync_gui
    does. Re-initialising Tk after another module tore its root down loses the
    Tcl/Tk library paths; without the retry that surfaces as a *skip*, which
    would quietly hide every assertion below it."""
    try:
        return tk.Tk()
    except tk.TclError:
        base = getattr(sys, "base_prefix", sys.prefix)
        for var, pattern in (("TCL_LIBRARY", "tcl8.*"), ("TK_LIBRARY", "tk8.*")):
            hits = [p for p in glob.glob(os.path.join(base, "tcl", pattern))
                    if os.path.isdir(p)]
            if hits:
                os.environ[var] = hits[0]
        try:
            return tk.Tk()
        except tk.TclError as exc:
            pytest.skip(f"no display available: {exc}")


def _make_gui():
    from gui.release_workflow import ReleaseWorkflowGUI
    root = _new_root()
    root.withdraw()
    gui = ReleaseWorkflowGUI(root)
    root.update_idletasks()
    return root, gui


def test_mfg_package_keeps_the_item_based_search_dialog():
    """MFG Order Package is out of scope for this rewrite. Its SearchDialog
    must stay item-based and keep calling parent.set_part_number."""
    from gui.search_dialog import SearchDialog
    from gui.mfg_package import MFGPackageGUI

    ids = [c[0] for c in SearchDialog.COLUMNS]
    assert "number" in ids, "MFG's dialog still searches items"
    # The duck-typed contract mfg_package implements for the dialog.
    for hook in ("_brand_button", "_ensure_signed_in", "set_part_number"):
        assert hasattr(MFGPackageGUI, hook), f"mfg_package lost {hook}"


def test_the_search_dialog_still_queries_items_not_files():
    """The extraction must not have quietly repointed MFG at search_files."""
    import inspect

    from gui.search_dialog import SearchDialog

    src = inspect.getsource(SearchDialog)
    assert "search_items" in src, "MFG's dialog stopped searching items"
    assert "search_files" not in src
    assert "set_part_number" in src, "MFG's dialog stopped handing back a PN"


# ---------------------------------------------------------------------------
# The file-driven wizard shell
# ---------------------------------------------------------------------------


def test_the_six_steps_are_the_file_driven_ones():
    from gui.release_workflow import STEPS
    names = [name for _num, name, _desc in STEPS]
    assert names == [
        "Property Check",
        "Sync Properties",
        "Release Files",
        "BOM → Purchased Parts List",
        "BOM → Publish Deliverables",
        "BOM → Purchasing Sheet",
    ]


def test_the_retired_item_steps_are_gone():
    from gui.release_workflow import STEPS
    names = " ".join(name for _num, name, _desc in STEPS)
    for retired in ("Readiness report", "Download local", "Inventor rebuild",
                    "Release items", "Compliance check"):
        assert retired not in names


def test_review_is_a_distinct_status():
    """A step waiting on a human must not look like one still calling Vault."""
    from gui.release_workflow import (
        STATUS_REVIEW, STATUS_RUNNING, STATUS_TAGS)
    assert STATUS_REVIEW in STATUS_TAGS
    assert STATUS_TAGS[STATUS_REVIEW] != STATUS_TAGS[STATUS_RUNNING]


def test_the_window_takes_both_inputs():
    root, gui = _make_gui()
    try:
        assert gui.top_file_var.get() == ""
        assert gui.bom_path_var.get() == ""
    finally:
        root.destroy()


def test_changing_the_top_file_resets_the_vault_steps_only():
    from gui.release_workflow import STATUS_OK, STATUS_PENDING
    root, gui = _make_gui()
    try:
        for num in ("1", "2", "3", "4", "5", "6"):
            gui._update_step_label(num, STATUS_OK)
        gui.top_file_var.set("CD-001659.iam")
        root.update_idletasks()
        assert gui.statuses["1"] == STATUS_PENDING
        assert gui.statuses["3"] == STATUS_PENDING
        assert gui.statuses["5"] == STATUS_OK      # BOM steps untouched
    finally:
        root.destroy()


def test_changing_the_bom_resets_the_bom_steps_only():
    from gui.release_workflow import STATUS_OK, STATUS_PENDING
    root, gui = _make_gui()
    try:
        for num in ("1", "2", "3", "4", "5", "6"):
            gui._update_step_label(num, STATUS_OK)
        gui.bom_path_var.set("C:/bom.xlsx")
        root.update_idletasks()
        assert gui.statuses["4"] == STATUS_PENDING
        assert gui.statuses["6"] == STATUS_PENDING
        assert gui.statuses["1"] == STATUS_OK      # Vault steps untouched
    finally:
        root.destroy()


def test_changing_the_top_file_drops_a_stale_property_check():
    """Steps 2-3 read self.compliance. A result from the previous file must
    never survive the file name changing under it."""
    root, gui = _make_gui()
    try:
        gui.compliance = {"file_name": "OLD.iam"}
        gui.top_file_var.set("CD-001659.iam")
        root.update_idletasks()
        assert gui.compliance is None
    finally:
        root.destroy()


def test_wizard_styles_every_tag_the_engines_emit():
    """Tk renders an unconfigured tag as plain text without raising, so a
    dropped tag_configure has no symptom other than looking wrong."""
    import release_steps
    from gui.release_workflow import TAG_STYLES
    for tag in release_steps.ALL_TAGS:
        assert tag in TAG_STYLES, f"engines emit {tag!r}, wizard never styles it"


def test_the_text_widget_really_configures_every_styled_tag():
    """TAG_STYLES is only a promise until _build_body applies it; ask Tk."""
    root, gui = _make_gui()
    try:
        from gui.release_workflow import TAG_STYLES
        configured = set(gui.text.tag_names())
        missing = sorted(set(TAG_STYLES) - configured)
        assert not missing, f"never handed to tag_configure: {missing}"
    finally:
        root.destroy()


def test_the_brand_assets_still_render():
    """Regression guard: a previous refactor dropped PILImage/ImageTk and both
    call sites swallowed the NameError, so the header logo and the window icon
    vanished on a fully green suite."""
    from gui import theme
    if not theme._pil_available:
        pytest.skip("Pillow not installed")
    root, gui = _make_gui()
    try:
        assert gui._logo_img is not None, "header logo silently failed to load"
        assert gui._icon_img is not None, "window icon silently failed to load"
    finally:
        root.destroy()


# ---------------------------------------------------------------------------
# Dispatch and the apply gate
#
# Steps 2-5 do not write when they run: they compute a preview and hand back a
# ``pending_apply`` closure holding the write. Everything below exists to prove
# that closure is never called without a human clicking Apply.
# ---------------------------------------------------------------------------


def test_a_staged_step_moves_to_review_and_relabels_the_button():
    from gui.release_workflow import STATUS_REVIEW, WorkerSignal
    import release_steps

    root, gui = _make_gui()
    try:
        staged = release_steps.StepOutcome(
            ok=True, summary="2 file(s) ready", lines=[("preview", "info")],
            pending_apply=lambda: release_steps.StepOutcome(
                ok=True, summary="done"),
        )
        gui._handle_signal(WorkerSignal("step_done", ("2", staged, False)))
        root.update_idletasks()

        assert gui.statuses["2"] == STATUS_REVIEW
        assert gui.pending_step == "2"
        assert "Apply" in gui.btn_run["text"]
    finally:
        root.destroy()


def test_run_all_halts_at_a_pending_apply():
    """A release must never write to Vault or SharePoint unattended."""
    from gui.release_workflow import STATUS_PENDING, STATUS_REVIEW, WorkerSignal
    import release_steps

    root, gui = _make_gui()
    try:
        staged = release_steps.StepOutcome(
            ok=True, summary="staged",
            pending_apply=lambda: release_steps.StepOutcome(ok=True, summary="x"),
        )
        # run_all_after=True — the sequence must still stop here.
        gui._handle_signal(WorkerSignal("step_done", ("2", staged, True)))
        root.update_idletasks()

        assert gui.statuses["2"] == STATUS_REVIEW
        assert gui.statuses["3"] == STATUS_PENDING   # never started
    finally:
        root.destroy()


def test_a_preview_that_reports_problems_still_offers_apply():
    """``needs_review`` must be tested BEFORE ``ok``. Step 5 previews Make
    parts with no drawing: ok=False *and* a staged write. Checking ``ok``
    first would fail the step and bin the write the user was about to
    approve."""
    from gui.release_workflow import STATUS_REVIEW, WorkerSignal
    import release_steps

    root, gui = _make_gui()
    try:
        staged = release_steps.StepOutcome(
            ok=False, summary="3 Make part(s) have no drawing",
            pending_apply=lambda: release_steps.StepOutcome(ok=True, summary="x"),
        )
        gui._handle_signal(WorkerSignal("step_done", ("5", staged, True)))
        root.update_idletasks()

        assert gui.statuses["5"] == STATUS_REVIEW
        assert gui.pending_step == "5"
        assert "Apply" in gui.btn_run["text"]
    finally:
        root.destroy()


def test_skipping_a_staged_step_discards_the_write():
    from gui.release_workflow import STATUS_SKIPPED, WorkerSignal
    import release_steps

    fired = []
    root, gui = _make_gui()
    try:
        staged = release_steps.StepOutcome(
            ok=True, summary="staged",
            pending_apply=lambda: fired.append(1),
        )
        gui._handle_signal(WorkerSignal("step_done", ("2", staged, False)))
        gui._on_skip()
        root.update_idletasks()

        assert fired == []                       # nothing was written
        assert gui.statuses["2"] == STATUS_SKIPPED
        assert gui.pending_apply is None
        assert "Apply" not in gui.btn_run["text"]
    finally:
        root.destroy()


def test_apply_is_the_only_thing_that_performs_the_write():
    from gui.release_workflow import STATUS_OK, WorkerSignal
    import release_steps

    fired = []
    root, gui = _make_gui()
    try:
        def write() -> release_steps.StepOutcome:
            fired.append(1)
            return release_steps.StepOutcome(ok=True, summary="queued 2 of 2")

        staged = release_steps.StepOutcome(
            ok=True, summary="staged", pending_apply=write)
        gui._handle_signal(WorkerSignal("step_done", ("2", staged, False)))
        root.update_idletasks()
        assert fired == [], "the preview wrote"

        gui._on_run_next()                       # the button now says Apply
        gui.worker_thread.join(timeout=15)
        gui._drain_queue()
        root.update_idletasks()

        assert fired == [1]
        assert gui.statuses["2"] == STATUS_OK
        assert gui.pending_apply is None
        assert "Apply" not in gui.btn_run["text"]
    finally:
        root.destroy()


def test_run_all_refuses_to_walk_past_a_step_awaiting_review(monkeypatch):
    """'Run all remaining' must not step over a staged write either — doing so
    would strand the step at REVIEW and silently drop what it staged."""
    from gui import release_workflow as rw
    from gui.release_workflow import STATUS_PENDING, STATUS_REVIEW, WorkerSignal
    import release_steps

    fired = []
    root, gui = _make_gui()
    try:
        warned = []
        monkeypatch.setattr(rw.messagebox, "showwarning",
                            lambda *a, **k: warned.append(a))
        staged = release_steps.StepOutcome(
            ok=True, summary="staged", pending_apply=lambda: fired.append(1))
        gui._handle_signal(WorkerSignal("step_done", ("2", staged, False)))
        gui._on_run_all()
        root.update_idletasks()

        assert fired == []
        assert warned, "no warning when Run all hit a step awaiting review"
        assert gui.statuses["2"] == STATUS_REVIEW
        assert gui.statuses["3"] == STATUS_PENDING
        assert gui.pending_step == "2"
    finally:
        root.destroy()


def test_the_whole_dispatch_path_stops_before_a_write(monkeypatch):
    """End-to-end through the real ``_run_step`` worker thread, with a staged
    write that raises if anything ever calls it unattended."""
    from gui.release_workflow import (
        STATUS_OK, STATUS_PENDING, STATUS_REVIEW)
    import release_steps

    def must_not_fire():
        raise AssertionError("wrote to Vault without asking")

    monkeypatch.setattr(
        release_steps, "run_sync_properties",
        lambda *a, **k: release_steps.StepOutcome(
            ok=True, summary="2 of 2 file(s) staged",
            lines=[("  would queue SyncProperties for 2 of 2 file(s)", "info")],
            pending_apply=must_not_fire),
    )

    root, gui = _make_gui()
    try:
        gui.top_file_var.set("CD-001659.iam")     # invalidates 1-3, clears compliance
        gui.api, gui.vault_id = object(), "1"
        gui.compliance = {"category_resolved": "Engineering",
                          "report": {"failed": 0}}
        gui._update_step_label("1", STATUS_OK)    # step 1 already ran clean

        gui._on_run_all()                         # unattended sequence
        gui.worker_thread.join(timeout=15)
        gui._drain_queue()
        root.update_idletasks()

        assert gui.statuses["2"] == STATUS_REVIEW
        assert gui.statuses["3"] == STATUS_PENDING
        assert gui.pending_step == "2"
        assert gui.pending_apply is must_not_fire
        assert "Apply" in gui.btn_run["text"]
        assert gui.busy is False
    finally:
        root.destroy()


def test_a_finished_step_goes_straight_to_ok():
    from gui.release_workflow import STATUS_OK, WorkerSignal
    import release_steps

    root, gui = _make_gui()
    try:
        done = release_steps.StepOutcome(ok=True, summary="clean")
        gui._handle_signal(WorkerSignal("step_done", ("1", done, False)))
        root.update_idletasks()

        assert gui.statuses["1"] == STATUS_OK
        assert gui.pending_step is None
    finally:
        root.destroy()


def test_step_1_hands_its_result_to_the_steps_that_read_it():
    from gui.release_workflow import WorkerSignal
    import release_steps

    root, gui = _make_gui()
    try:
        payload = {"file_name": "CD-001659.iam", "category_resolved": "Eng"}
        gui._handle_signal(WorkerSignal("step_done", (
            "1", release_steps.StepOutcome(
                ok=True, summary="clean", result=payload), False)))
        root.update_idletasks()

        assert gui.compliance is payload
        assert str(gui.btn_save_report["state"]) == "normal"
    finally:
        root.destroy()


def test_a_step_1_that_produced_nothing_never_leaves_a_stale_result_behind():
    """'Ran but produced nothing' must not read as the previous run's clean
    result — that is the absent-data-means-success bug this branch keeps
    finding."""
    from gui.release_workflow import WorkerSignal
    import release_steps

    root, gui = _make_gui()
    try:
        gui.compliance = {"file_name": "OLD.iam", "category_resolved": "Eng"}
        gui._handle_signal(WorkerSignal("step_done", (
            "1", release_steps.StepOutcome(
                ok=False, summary="lookup failed", result=None), False)))
        root.update_idletasks()

        assert gui.compliance is None
        assert str(gui.btn_save_report["state"]) == "disabled"
    finally:
        root.destroy()


def test_vault_steps_need_a_top_file_and_bom_steps_need_a_bom():
    root, gui = _make_gui()
    try:
        assert gui._missing_input_for("2") is not None   # no top file yet
        assert gui._missing_input_for("5") is not None   # no BOM yet
        gui.top_file_var.set("CD-001659.iam")
        gui.bom_path_var.set("C:/bom.xlsx")
        assert gui._missing_input_for("2") is None
        assert gui._missing_input_for("5") is None
    finally:
        root.destroy()


def test_the_two_input_groups_do_not_block_each_other():
    root, gui = _make_gui()
    try:
        gui.top_file_var.set("CD-001659.iam")
        assert gui._missing_input_for("1") is None
        assert gui._missing_input_for("4") is not None   # still needs a BOM
        gui.top_file_var.set("")
        gui.bom_path_var.set("C:/bom.xlsx")
        assert gui._missing_input_for("6") is None
        assert gui._missing_input_for("3") is not None   # still needs a file
    finally:
        root.destroy()


def test_a_step_missing_its_input_never_starts_a_worker(monkeypatch):
    from gui import release_workflow as rw
    root, gui = _make_gui()
    try:
        warned = []
        monkeypatch.setattr(rw.messagebox, "showwarning",
                            lambda *a, **k: warned.append(a))
        gui._run_step("2", run_all_after=False)
        root.update_idletasks()

        assert warned, "no warning for the missing top file"
        assert gui.worker_thread is None
        assert gui.busy is False
    finally:
        root.destroy()


def test_a_vault_step_without_a_session_never_starts_a_worker(monkeypatch):
    from gui import release_workflow as rw
    root, gui = _make_gui()
    try:
        warned = []
        monkeypatch.setattr(rw.messagebox, "showwarning",
                            lambda *a, **k: warned.append(a))
        gui.top_file_var.set("CD-001659.iam")
        gui.api, gui.vault_id = None, ""
        gui._run_step("2", run_all_after=False)
        root.update_idletasks()

        assert warned, "no warning for the missing Vault session"
        assert gui.worker_thread is None
    finally:
        root.destroy()


def test_steps_2_and_3_refuse_to_run_without_a_clean_property_check():
    """The gate is release_steps.property_check_blocked — a module-level
    function, not a GUI method."""
    root, gui = _make_gui()
    try:
        gui.compliance = None            # step 1 never ran
        gui.api, gui.vault_id = object(), "1"
        for num in ("2", "3"):
            outcome = gui._step_runner(num)()
            assert outcome.ok is False, f"step {num} ran with no property check"
            assert "step 1" in outcome.summary.lower()
            assert outcome.pending_apply is None, "blocked step staged a write"
    finally:
        root.destroy()


def test_the_bom_steps_are_not_gated_on_the_property_check(monkeypatch):
    """A purchasing sheet is useful while properties are still being fixed."""
    import release_steps
    seen = []
    monkeypatch.setattr(
        release_steps, "run_purchasing_sheet",
        lambda *a, **k: seen.append((a, k)) or release_steps.StepOutcome(
            ok=True, summary="wrote it"))

    root, gui = _make_gui()
    try:
        gui.compliance = None
        gui.bom_path_var.set("C:/bom.xlsx")
        outcome = gui._step_runner("6")()
        assert outcome.ok is True
        assert seen, "step 6 never reached its engine"
    finally:
        root.destroy()


def test_every_step_has_a_runner():
    from gui.release_workflow import STEPS
    root, gui = _make_gui()
    try:
        for num, *_ in STEPS:
            assert callable(gui._step_runner(num)), f"step {num} has no runner"
    finally:
        root.destroy()


def test_the_retired_item_runners_are_gone():
    from gui.release_workflow import ReleaseWorkflowGUI
    for name in ("_run_step_1_compliance", "_run_step_2_report",
                 "_run_step_3_sync", "_run_step_4_download",
                 "_run_step_5_inventor", "_run_step_6_release_cad",
                 "_run_step_7_release_items", "_log_compliance_summary",
                 "_child_status", "_guess_top_assembly", "_compliance_blocked",
                 "set_part_number"):
        assert not hasattr(ReleaseWorkflowGUI, name), f"{name} survived"
