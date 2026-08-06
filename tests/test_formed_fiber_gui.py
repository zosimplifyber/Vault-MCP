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
    root, gui = _make_gui()
    try:
        gui.vars["bone_dry_weight"].set("100")
        root.update_idletasks()
        gui.on_standard_dry_weight_edited()      # what the <Key> binding calls
        gui.vars["standard_dry_weight"].set("999.99")
        gui.vars["bone_dry_weight"].set("250")
        root.update_idletasks()
        assert gui.vars["standard_dry_weight"].get() == "999.99"
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
         "press_pressure": "120 bar", "characterized": True},
    ]}), encoding="utf-8")

    root, gui = _make_gui(machines_path=library)
    try:
        gui.vars["machine"].set("Beckwood 150T")
        gui.on_machine_selected()
        assert gui.vars["vacuum_pressure"].get() == "-0.9 barg"
        assert gui.vars["press_pressure"].get() == "120 bar"
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
