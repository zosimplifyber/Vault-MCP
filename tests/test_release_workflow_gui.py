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
