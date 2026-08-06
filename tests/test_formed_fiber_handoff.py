# tests/test_formed_fiber_handoff.py
"""Engine tests for the Formed Fiber design-to-process handoff."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import formed_fiber_handoff as engine


# --------------------------------------------------------------- standard dry

def test_standard_dry_weight_is_wet_basis():
    """A standard dry part is 5% water BY MASS OF THE FINISHED PART.

    So the bone dry fibre is the other 95% and the divisor is 0.95. The
    tempting alternative -- dry-basis regain, `* 1.05`, the textile
    convention -- gives 105.00 here. This test exists to stop someone
    "correcting" the formula to it.
    """
    assert engine.standard_dry_weight("100") == "105.26"
    assert engine.standard_dry_weight("250") == "263.16"


def test_standard_dry_weight_rejects_unusable_input():
    # "nan", "inf" and "Infinity" are the interesting ones: float() accepts
    # them all, and a positivity test alone lets them through -- NaN compares
    # false against everything and +inf is not <= 0 -- so they would print as
    # a weight of "nan" or "inf" on the document.
    for bad in ("", "   ", "abc", "0", "-5", None,
                "nan", "inf", "Infinity", "-inf"):
        assert engine.standard_dry_weight(bad) == "", f"{bad!r} should give ''"


def test_standard_dry_weight_accepts_decimals_and_whitespace():
    assert engine.standard_dry_weight(" 47.5 ") == "50.00"


# ---------------------------------------------------------------- data model

from datetime import date  # noqa: E402


def test_value_defaults_are_blank_and_not_targets():
    v = engine.Value()
    assert v.text == ""
    assert v.is_target is False


def test_handoff_data_defaults_are_usable():
    """A bare HandoffData must be constructible -- the GUI builds one before
    anything has been picked, and the renderer must cope with it."""
    data = engine.HandoffData()
    assert data.machine == ""
    assert data.material == ""
    assert data.volume == ""
    assert data.bone_dry_weight == engine.Value()
    assert data.generated_on == date.today()


# --------------------------------------------------------------- row builders

def test_blank_value_renders_an_em_dash():
    """A blank must be visibly blank, not ambiguous whitespace."""
    assert engine.render_value(engine.Value()) == engine.EM_DASH
    assert engine.render_text("") == engine.EM_DASH
    assert engine.render_text("   ") == engine.EM_DASH


def test_target_values_are_marked_and_others_are_not():
    assert engine.render_value(engine.Value("2.4", True)) == "2.4 (TARGET)"
    assert engine.render_value(engine.Value("2.4", False)) == "2.4"


def test_production_section_prints_eight_rows_in_order():
    """Material and the new Part Volume row lead, then the six measured
    fields. Volume is not on the paper form -- it was added deliberately."""
    rows = engine.production_rows(engine.HandoffData())
    assert [label for label, _ in rows] == [
        "Final Pressed Part Material",
        "Part Volume [cm³]",
        "Dry Part Thickness [mm]",
        "Wet Part Thickness [mm] – Or Transfer GAPS",
        "Wet Weight [g]",
        "Bone Dry Weight [g]",
        "Standard Dry Weight [g]",
        "Dryness [%]",
    ]


def test_machine_and_file_sections_print_their_rows():
    data = engine.HandoffData(machine="Beckwood 150T", ga_filename="CD-1.iam")
    assert engine.machine_rows(data)[0] == ("Machine – Brand and Model", "Beckwood 150T")
    assert engine.file_rows(data)[0] == ("General Assembly Filename", "CD-1.iam")
    assert len(engine.machine_rows(data)) == 3
    assert len(engine.file_rows(data)) == 2


def test_machine_and_file_labels_are_pinned():
    """Sections 1 and 3 pin their literal text the way section 2 does.

    These strings print on a customer-facing document, and the whole point of
    building rows in the engine is that the wording is provable without
    opening a PDF. Asserting only row [0] and a count would let a typo in the
    other labels ship silently.
    """
    data = engine.HandoffData()
    assert [label for label, _ in engine.machine_rows(data)] == [
        "Machine – Brand and Model",
        "Vacuum Pressure [bar or barg]",
        "Hot Press Pressing Pressure [bar]",
    ]
    assert [label for label, _ in engine.file_rows(data)] == [
        "General Assembly Filename",
        "Final Pressed Part Filename",
    ]


def test_render_text_does_not_treat_a_legitimate_zero_as_blank():
    """The `str(text or "")` shorthand would render 0 as an em dash."""
    assert engine.render_text("0") == "0"
    assert engine.render_text(0) == "0"
    assert engine.render_text(None) == engine.EM_DASH


def test_missing_fields_lists_every_blank_row_by_label():
    data = engine.HandoffData(machine="Beckwood 150T")
    missing = engine.missing_fields(data)
    assert "Machine – Brand and Model" not in missing
    assert "Part Volume [cm³]" in missing
    assert "Dryness [%]" in missing
    # 3 machine + 8 production + 2 file = 13 rows, one of which is filled.
    assert len(missing) == 12


# ------------------------------------------------------------ machine library

import json  # noqa: E402


def _write_machines(tmp_path, payload):
    path = tmp_path / "machines.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_machines_reads_every_profile(tmp_path):
    path = _write_machines(tmp_path, {"machines": [
        {"name": "Beckwood 150T", "vacuum_pressure": "-0.9 barg",
         "press_pressure": "120 bar", "characterized": True},
        {"name": "Wabash 50T", "vacuum_pressure": "-0.8 barg",
         "press_pressure": "60 bar", "characterized": False},
    ]})
    machines = engine.load_machines(path)
    assert [m.name for m in machines] == ["Beckwood 150T", "Wabash 50T"]
    assert machines[0].vacuum_pressure == "-0.9 barg"
    assert machines[0].press_pressure == "120 bar"
    assert machines[1].characterized is False


def test_load_machines_never_raises(tmp_path):
    """A broken library must not stop a handoff being written by hand."""
    missing = tmp_path / "nope.json"
    assert engine.load_machines(missing) == []

    malformed = tmp_path / "bad.json"
    malformed.write_text("{not json", encoding="utf-8")
    assert engine.load_machines(malformed) == []

    wrong_shape = _write_machines(tmp_path, {"machines": "not a list"})
    assert engine.load_machines(wrong_shape) == []


def test_load_machines_skips_unusable_rows(tmp_path):
    path = _write_machines(tmp_path, {"machines": [
        {"name": ""},               # no name -- cannot appear in a dropdown
        "not a dict",
        {"name": "Real Press"},
    ]})
    assert [m.name for m in engine.load_machines(path)] == ["Real Press"]


def test_machines_default_to_characterized(tmp_path):
    """Absent flag means characterized. Warning on a machine nobody has
    flagged either way would cry wolf on every existing entry."""
    path = _write_machines(tmp_path, {"machines": [{"name": "Press"}]})
    assert engine.load_machines(path)[0].characterized is True


def test_a_quoted_false_still_means_not_characterized(tmp_path):
    """machines.json is hand-edited, so `"characterized": "false"` happens.

    bool("false") is True, so coercing would silently suppress the
    uncharacterized-press warning -- a safety notice vanishing because of a
    pair of quotes.
    """
    for falsey in ("false", "False", "FALSE", "no", "0", "off"):
        path = _write_machines(tmp_path, {"machines": [
            {"name": "Press", "characterized": falsey}]})
        assert engine.load_machines(path)[0].characterized is False, falsey

    # Real booleans and genuine truthy text still read as characterized.
    for truthy in (True, "true", "yes"):
        path = _write_machines(tmp_path, {"machines": [
            {"name": "Press", "characterized": truthy}]})
        assert engine.load_machines(path)[0].characterized is True, truthy


def test_shipped_machines_json_is_loadable():
    """The file we ship must actually parse."""
    machines = engine.load_machines(engine.MACHINES_PATH)
    assert isinstance(machines, list)


def test_find_machine_matches_on_exact_name():
    machines = [engine.Machine(name="Beckwood 150T", press_pressure="120 bar"),
                engine.Machine(name="Wabash 50T")]
    assert engine.find_machine(machines, "Beckwood 150T").press_pressure == "120 bar"
    assert engine.find_machine(machines, "  Beckwood 150T  ") is not None
    assert engine.find_machine(machines, "Nonexistent") is None
    assert engine.find_machine([], "Beckwood 150T") is None
    assert engine.find_machine(machines, "") is None


# -------------------------------------------------------------------- paths

from pathlib import Path  # noqa: E402


def test_vault_folder_maps_onto_the_local_workspace():
    got = engine.vault_folder_to_local("$/DESIGNS/PRODUCTION EQUIPMENT/Mold 12",
                                       r"C:\Vault Workspace")
    assert got == Path(r"C:\Vault Workspace") / "DESIGNS" / "PRODUCTION EQUIPMENT" / "Mold 12"


def test_vault_folder_mapping_tolerates_shapes_vault_actually_returns():
    root = r"C:\WS"
    assert engine.vault_folder_to_local("$/A/B", root) == Path(root) / "A" / "B"
    assert engine.vault_folder_to_local("/A/B", root) == Path(root) / "A" / "B"
    assert engine.vault_folder_to_local("$\\A\\B", root) == Path(root) / "A" / "B"
    assert engine.vault_folder_to_local("$/", root) == Path(root)
    assert engine.vault_folder_to_local("", root) == Path(root)


def test_part_local_path_uses_the_parts_own_folder():
    """The pressed part need not live beside its assembly."""
    got = engine.part_local_path("$/DESIGNS/Parts", "CD-001660.ipt", workspace_root=r"C:\WS")
    assert got == Path(r"C:\WS") / "DESIGNS" / "Parts" / "CD-001660.ipt"


def test_handoff_filename_derives_from_the_assembly():
    assert engine.handoff_filename("CD-001659.iam") == "CD-001659-DesignToProcessHandoff.pdf"
    assert engine.handoff_filename("CD-001659") == "CD-001659-DesignToProcessHandoff.pdf"
    # Only the final extension is dropped.
    assert engine.handoff_filename("CD.1659.iam") == "CD.1659-DesignToProcessHandoff.pdf"
    assert engine.handoff_filename("") == "Handoff-DesignToProcessHandoff.pdf"


def test_resolve_output_dir_uses_the_mapped_folder_when_it_exists(tmp_path):
    (tmp_path / "A" / "B").mkdir(parents=True)
    directory, note = engine.resolve_output_dir(
        "$/A/B", workspace_root=str(tmp_path), fallback=str(tmp_path / "nope"))
    assert directory == tmp_path / "A" / "B"
    assert note == ""


def test_resolve_output_dir_falls_back_and_explains(tmp_path):
    fallback = tmp_path / "Downloads"
    fallback.mkdir()
    directory, note = engine.resolve_output_dir(
        "$/Nowhere", workspace_root=str(tmp_path), fallback=str(fallback))
    assert directory == fallback
    assert "Nowhere" in note and str(fallback) in note


def test_resolve_output_dir_does_not_invent_the_folder(tmp_path):
    """A directory conjured inside the Vault workspace is a path Vault does
    not know about, so the mapped folder is never created."""
    fallback = tmp_path / "Downloads"
    fallback.mkdir()
    engine.resolve_output_dir(
        "$/Nowhere", workspace_root=str(tmp_path), fallback=str(fallback))
    assert not (tmp_path / "Nowhere").exists()


def test_resolve_output_dir_says_when_no_folder_was_supplied(tmp_path):
    """Blank folder path is the no-Vault-session route, not a missing folder.

    Saying "<workspace root> is not on this machine" there sends the reader
    hunting for a directory that was never named.
    """
    fallback = tmp_path / "Downloads"
    fallback.mkdir()
    _, note = engine.resolve_output_dir(
        "", workspace_root=str(tmp_path), fallback=str(fallback))
    assert "No Vault folder" in note
    assert "is not on this machine" not in note


def test_resolve_output_dir_defaults_to_the_purchasing_output_dir(monkeypatch, tmp_path):
    """The fallback=None branch is the one production actually takes."""
    import bom_purchasing

    monkeypatch.setattr(bom_purchasing, "default_output_dir",
                        lambda: str(tmp_path / "Downloads"))
    directory, note = engine.resolve_output_dir(
        "$/Nowhere", workspace_root=str(tmp_path))
    assert directory == tmp_path / "Downloads"
    assert "Nowhere" in note


def test_file_reference_carries_the_revision():
    assert engine.format_file_reference("CD-001659.iam", "3") == "CD-001659.iam (Rev 3)"
    assert engine.format_file_reference("CD-001659.iam", "") == "CD-001659.iam"
    assert engine.format_file_reference("", "3") == ""


def test_workspace_root_from_config_falls_back_to_the_default():
    assert engine.workspace_root_from_config(None) == engine.DEFAULT_WORKSPACE_ROOT
    assert engine.workspace_root_from_config({}) == engine.DEFAULT_WORKSPACE_ROOT
    assert engine.workspace_root_from_config(
        {"handoff": {"workspace_root": r"D:\WS"}}) == r"D:\WS"


# ------------------------------------------------------------- vault lookup

import formed_fiber_vault as vault_lookup  # noqa: E402


def _props(**overrides):
    base = {
        "File Name": "CD-001659.iam", "Revision": "3", "State": "Released",
        "Material": "", "Folder Path": "$/DESIGNS/Mold 12",
        "Category Name": "Assembly - Engineering",
    }
    base.update(overrides)
    return base


async def test_load_assembly_summarises_the_assembly_and_its_children(monkeypatch):
    async def fake_fetch_file(api, vault_id, file_name):
        return {"properties": _props(), "file_version_id": "v-1"}

    async def fake_fetch_children(api, vault_id, version_id, **kwargs):
        assert version_id == "v-1"
        return [{"properties": _props(
            **{"File Name": "CD-001660.ipt", "Revision": "2",
               "Material": "Cellulose Fibre", "Folder Path": "$/DESIGNS/Parts",
               "Category Name": "Part - Engineering"})}]

    monkeypatch.setattr(vault_lookup, "fetch_file", fake_fetch_file)
    monkeypatch.setattr(vault_lookup, "fetch_cad_children", fake_fetch_children)

    result = await vault_lookup.load_assembly(object(), "1", "CD-001659.iam")

    assert result["assembly"]["file_name"] == "CD-001659.iam"
    assert result["assembly"]["revision"] == "3"
    assert result["assembly"]["folder_path"] == "$/DESIGNS/Mold 12"
    assert result["children_error"] == ""
    child = result["children"][0]
    assert child["file_name"] == "CD-001660.ipt"
    assert child["material"] == "Cellulose Fibre"
    assert child["folder_path"] == "$/DESIGNS/Parts"


async def test_load_assembly_reports_a_bom_walk_failure_without_raising(monkeypatch):
    """A failed BOM walk must still return the assembly -- the filenames are
    half the document, and they are already in hand."""
    async def fake_fetch_file(api, vault_id, file_name):
        return {"properties": _props(), "file_version_id": "v-1"}

    async def fake_fetch_children(api, vault_id, version_id, **kwargs):
        raise RuntimeError("CAD BOM walk failed: boom")

    monkeypatch.setattr(vault_lookup, "fetch_file", fake_fetch_file)
    monkeypatch.setattr(vault_lookup, "fetch_cad_children", fake_fetch_children)

    result = await vault_lookup.load_assembly(object(), "1", "CD-001659.iam")

    assert result["assembly"]["file_name"] == "CD-001659.iam"
    assert result["children"] == []
    assert "boom" in result["children_error"]


async def test_load_assembly_without_a_version_id_cannot_walk_the_bom(monkeypatch):
    async def fake_fetch_file(api, vault_id, file_name):
        return {"properties": _props(), "file_version_id": ""}

    monkeypatch.setattr(vault_lookup, "fetch_file", fake_fetch_file)

    result = await vault_lookup.load_assembly(object(), "1", "CD-001659.iam")

    assert result["children"] == []
    assert "file-version ID" in result["children_error"]
