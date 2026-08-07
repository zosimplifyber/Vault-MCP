# tests/test_formed_fiber_gui.py
"""Form tests for the Formed Fiber handoff tool.

Constructed with api=None throughout: the form must open and be usable
without a Vault session, because a handoff written entirely by hand is a
legitimate thing to want.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

tk = pytest.importorskip("tkinter")


def _make_gui(**kwargs):
    from tests.tk_helpers import make_tk_root
    from gui.formed_fiber_handoff import HandoffGUI

    root = make_tk_root()
    gui = HandoffGUI(parent=root, api=None, vault_id="", cfg={}, **kwargs)
    root.update_idletasks()
    return root, gui


def test_form_opens_without_a_vault_session():
    root, gui = _make_gui()
    try:
        assert gui.win.winfo_exists()
    finally:
        root.destroy()


def test_every_document_field_has_an_entry():
    import formed_fiber_handoff as engine

    root, gui = _make_gui()
    try:
        for name, _ in engine.MACHINE_FIELDS:
            assert name in gui.vars, f"{name} has no entry"
        for name, _ in engine.PRODUCTION_FIELDS:
            assert name in gui.vars, f"{name} has no entry"
            assert name in gui.target_vars, f"{name} has no target checkbox"
        assert "material" in gui.vars
        assert "volume" in gui.vars
        # Material and volume are not measurements -- no target checkbox.
        assert "material" not in gui.target_vars
        assert "volume" not in gui.target_vars
    finally:
        root.destroy()


def test_standard_dry_weight_tracks_bone_dry_weight():
    root, gui = _make_gui()
    try:
        gui.vars["bone_dry_weight"].set("100")
        root.update_idletasks()
        assert gui.vars["standard_dry_weight"].get() == "105.26"
    finally:
        root.destroy()


def test_standard_dry_weight_stops_tracking_once_typed_in():
    """Setting the var IS the trigger now -- no need to call the hook by hand.

    The earlier version called gui.on_standard_dry_weight_edited() directly,
    which meant it never exercised the real trigger and could not have caught
    the over-broad <Key> binding this replaced.
    """
    root, gui = _make_gui()
    try:
        gui.vars["bone_dry_weight"].set("100")
        root.update_idletasks()
        gui.vars["standard_dry_weight"].set("999.99")   # the user edit
        gui.vars["bone_dry_weight"].set("250")
        root.update_idletasks()
        assert gui.vars["standard_dry_weight"].get() == "999.99"
    finally:
        root.destroy()


def test_the_derivation_detaches_on_value_change_not_on_keypress():
    """No key binding may drive the detach.

    `entry.bind("<Key>", ...)` fires for arrow keys and Tab as well as typing,
    so clicking into the field to read the number and pressing Left silently
    and permanently stopped it tracking -- no error, no visual cue, just a
    number that quietly goes stale on a manufacturing document.

    This asserts structurally rather than by driving real key events, because
    a behavioural version of this test PASSES VACUOUSLY: event_generate does
    not deliver key events to a widget in a withdrawn window, so the
    assertion never exercises the binding at all. Verified by mutation --
    restoring the <Key> binding left a behavioural version green.
    """
    root, gui = _make_gui()
    try:
        entry = gui.entries["standard_dry_weight"]
        bound = entry.bind()
        key_bindings = [seq for seq in bound if "Key" in seq]
        assert not key_bindings, (
            f"detach is driven by {key_bindings}, which fire on cursor "
            "movement as well as typing")

        # And the trace-based detach it was replaced with does work.
        gui.vars["bone_dry_weight"].set("100")
        root.update_idletasks()
        assert gui.vars["standard_dry_weight"].get() == "105.26"
        assert gui._derived_tracking["standard_dry_weight"] is True
        gui.vars["standard_dry_weight"].set("999.99")
        assert gui._derived_tracking["standard_dry_weight"] is False
    finally:
        root.destroy()


def test_a_stale_inventor_read_cannot_overwrite_a_newer_one():
    """Click part A, click part B, A's slower read lands last.

    Opening Inventor takes seconds and clicking down a BOM comparing parts is
    the normal way to use this, so the slow result for a part the user has
    moved on from must be dropped -- otherwise another part's mass lands on
    this document, silently.
    """
    from inventor_automation import PhysicalProperties

    root, gui = _make_gui()
    try:
        gui.part = {"file_name": "A.ipt", "folder_path": "$/X"}
        gui._read_physical_properties()
        stale = gui._inventor_generation

        gui.part = {"file_name": "B.ipt", "folder_path": "$/X"}
        gui._read_physical_properties()
        current = gui._inventor_generation

        # B's read returns first, then A's arrives late.
        gui._handle("inventor", (current, PhysicalProperties(200.0, 20.0)))
        gui._handle("inventor", (stale, PhysicalProperties(999.0, 99.0)))

        assert gui.vars["bone_dry_weight"].get() == "200.00"
        assert gui.vars["volume"].get() == "20.00"
    finally:
        root.destroy()


def test_a_stale_inventor_error_does_not_clobber_a_good_read():
    from inventor_automation import PhysicalProperties

    root, gui = _make_gui()
    try:
        gui.part = {"file_name": "A.ipt", "folder_path": "$/X"}
        gui._read_physical_properties()
        stale = gui._inventor_generation
        gui.part = {"file_name": "B.ipt", "folder_path": "$/X"}
        gui._read_physical_properties()
        current = gui._inventor_generation

        gui._handle("inventor", (current, PhysicalProperties(200.0, 20.0)))
        gui._handle("inventor_error", (stale, "Inventor not installed"))

        assert gui.vars["bone_dry_weight"].get() == "200.00"
        assert "Could not read" not in gui.inventor_note_var.get()
    finally:
        root.destroy()


def test_target_flag_mirrors_bone_dry_weight_while_tracking():
    """A value derived from a target is itself a target."""
    root, gui = _make_gui()
    try:
        gui.vars["bone_dry_weight"].set("100")
        gui.target_vars["bone_dry_weight"].set(True)
        root.update_idletasks()
        assert gui.target_vars["standard_dry_weight"].get() is True
    finally:
        root.destroy()


def test_collect_builds_handoff_data_with_target_markers():
    import formed_fiber_handoff as engine

    root, gui = _make_gui()
    try:
        gui.vars["machine"].set("Beckwood 150T")
        gui.vars["material"].set("Cellulose Fibre")
        gui.vars["volume"].set("512.50")
        gui.vars["dry_thickness"].set("2.4")
        gui.target_vars["dry_thickness"].set(True)
        gui.vars["wet_thickness"].set("6.1")

        data = gui.collect()

        assert data.machine == "Beckwood 150T"
        assert data.material == "Cellulose Fibre"
        assert data.volume == "512.50"
        assert data.dry_thickness == engine.Value("2.4", True)
        assert data.wet_thickness == engine.Value("6.1", False)
    finally:
        root.destroy()


def test_picking_a_machine_fills_both_pressures(tmp_path):
    import json

    library = tmp_path / "machines.json"
    library.write_text(json.dumps({"machines": [
        {"name": "Beckwood 150T", "vacuum_pressure": "-0.9 barg",
         "press_force": "1200000 N", "characterized": True},
    ]}), encoding="utf-8")

    root, gui = _make_gui(machines_path=library)
    try:
        gui.vars["machine"].set("Beckwood 150T")
        gui.on_machine_selected()
        assert gui.vars["vacuum_pressure"].get() == "-0.9 barg"
        assert gui.vars["press_force"].get() == "1200000 N"
        assert gui.machine_warning_var.get() == ""
    finally:
        root.destroy()


def test_uncharacterized_machine_warns_without_blocking(tmp_path):
    import json

    library = tmp_path / "machines.json"
    library.write_text(json.dumps({"machines": [
        {"name": "New Press", "characterized": False},
    ]}), encoding="utf-8")

    root, gui = _make_gui(machines_path=library)
    try:
        gui.vars["machine"].set("New Press")
        gui.on_machine_selected()
        assert "characterized" in gui.machine_warning_var.get().lower()
    finally:
        root.destroy()


def test_a_broken_machine_library_does_not_stop_the_form(tmp_path):
    library = tmp_path / "machines.json"
    library.write_text("{not json", encoding="utf-8")

    root, gui = _make_gui(machines_path=library)
    try:
        assert gui.machines == []
        assert gui.win.winfo_exists()
    finally:
        root.destroy()


def test_generate_writes_a_pdf(tmp_path, monkeypatch):
    root, gui = _make_gui()
    try:
        gui.vars["machine"].set("Beckwood 150T")
        gui.vars["material"].set("Cellulose Fibre")
        gui.out_dir_var.set(str(tmp_path))
        gui.out_name_var.set("CD-001659-DesignToProcessHandoff.pdf")
        # Skip the "some fields are blank" dialog.
        monkeypatch.setattr(gui, "confirm_blank_fields", lambda missing: True)

        written = gui.generate()

        assert written is not None
        assert written.is_file()
    finally:
        root.destroy()


def test_the_bom_table_shows_the_file_description():
    """The description is what tells you which child is the pressed part.

    Asserts the rendered row, not just the column definition: the tree is
    populated by looking each column's key up on the child dict, so a key
    that does not match what formed_fiber_vault produces yields a silently
    blank column rather than an error.
    """
    root, gui = _make_gui()
    try:
        gui.assembly = {"file_name": "CD-001478.iam", "revision": "2",
                        "state": "Released", "folder_path": "$/DESIGNS"}
        gui.children = [{
            "file_name": "CD-001488.iam", "revision": "2", "state": "Released",
            "material": "Aluminum 6061", "description": "forming tool",
            "folder_path": "$/DESIGNS", "category": "Assembly - Engineering",
        }]
        gui._populate_bom("")
        root.update_idletasks()

        assert "description" in [key for key, *_ in gui.BOM_COLUMNS]
        row = gui.bom_tree.item(gui.bom_tree.get_children()[0])["values"]
        assert "forming tool" in row
        # Sits beside the file name, which is how the table is read.
        assert row.index("forming tool") == 1
    finally:
        root.destroy()


def test_a_machine_with_no_recorded_pressures_does_not_wipe_typed_ones(tmp_path):
    """Selecting a press must not clear values already entered by hand.

    KFT 90 and Lab Former ship without recorded pressures, so a blind
    overwrite would erase what the user had just typed simply by picking
    the machine.
    """
    import json

    library = tmp_path / "machines.json"
    library.write_text(json.dumps({"machines": [
        {"name": "KFT 90", "vacuum_pressure": "", "press_force": ""},
    ]}), encoding="utf-8")

    root, gui = _make_gui(machines_path=library)
    try:
        gui.vars["vacuum_pressure"].set("-0.85 barg")
        gui.vars["press_force"].set("950000 N")
        gui.vars["machine"].set("KFT 90")
        gui.on_machine_selected()
        assert gui.vars["vacuum_pressure"].get() == "-0.85 barg"
        assert gui.vars["press_force"].get() == "950000 N"
    finally:
        root.destroy()


def test_the_shipped_library_offers_the_real_presses():
    import formed_fiber_handoff as engine

    names = [m.name for m in engine.load_machines(engine.MACHINES_PATH)]
    assert "KFT 90" in names
    assert "Lab Former" in names


def test_wet_weight_is_derived_from_bone_dry_weight():
    """Wet weight is the part at 15% moisture, so bone dry fibre is 85%."""
    root, gui = _make_gui()
    try:
        gui.vars["bone_dry_weight"].set("3660.11")
        root.update_idletasks()
        assert gui.vars["wet_weight"].get() == "4306.01"
        assert gui.vars["standard_dry_weight"].get() == "3852.75"
    finally:
        root.destroy()


def test_overriding_one_derived_field_leaves_the_other_tracking():
    """The two detach independently.

    They share one mechanism now, so the mistake to guard against is a single
    tracking flag: typing a wet weight by hand must not also freeze standard
    dry weight.
    """
    root, gui = _make_gui()
    try:
        gui.vars["bone_dry_weight"].set("100")
        root.update_idletasks()

        gui.vars["wet_weight"].set("999.99")        # override just this one
        gui.vars["bone_dry_weight"].set("200")
        root.update_idletasks()

        assert gui.vars["wet_weight"].get() == "999.99"
        assert gui.vars["standard_dry_weight"].get() == "210.53"
        assert gui._derived_tracking["wet_weight"] is False
        assert gui._derived_tracking["standard_dry_weight"] is True
    finally:
        root.destroy()


def test_both_derived_fields_mirror_the_bone_dry_target_flag():
    root, gui = _make_gui()
    try:
        gui.vars["bone_dry_weight"].set("100")
        gui.target_vars["bone_dry_weight"].set(True)
        root.update_idletasks()
        assert gui.target_vars["wet_weight"].get() is True
        assert gui.target_vars["standard_dry_weight"].get() is True
    finally:
        root.destroy()
