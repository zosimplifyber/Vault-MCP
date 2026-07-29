# tests/test_launcher_flags.py
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

tk = pytest.importorskip("tkinter")


def _make_gui():
    from tests.tk_helpers import make_tk_root
    from gui.launcher import LauncherGUI
    # config.json is gitignored, so it is absent in a fresh checkout and in CI.
    # Skipping says that plainly; reading it unguarded raised FileNotFoundError
    # and read as a real failure.
    cfg_path = os.path.join(ROOT, "config.json")
    if not os.path.isfile(cfg_path):
        pytest.skip("config.json not present (it is gitignored)")
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    root = make_tk_root()
    gui = LauncherGUI(root, cfg=cfg, auto_start_mcp=False)
    root.update_idletasks()
    return root, gui


def test_mfg_package_is_still_flagged_broken():
    """MFG Order Package still resolves parts through Vault items."""
    root, gui = _make_gui()
    try:
        btn = gui.tool_buttons["MFG Order Package"]
        assert str(btn["state"]) == "disabled"
    finally:
        root.destroy()


def test_release_workflow_is_enabled_again():
    """The wizard was rewritten onto files, so it is off the broken list."""
    root, gui = _make_gui()
    try:
        btn = gui.tool_buttons["Release Workflow"]
        assert str(btn["state"]) != "disabled"
    finally:
        root.destroy()


def test_working_tools_stay_enabled():
    root, gui = _make_gui()
    try:
        btn = gui.tool_buttons["BOM → Purchasing Sheet"]
        assert str(btn["state"]) != "disabled"
    finally:
        root.destroy()


def test_property_check_is_no_longer_flagged_broken():
    """Property Check was rewritten onto files, so it is off the broken list."""
    root, gui = _make_gui()
    try:
        assert "Property Check (Lookup)" not in gui.tool_buttons, (
            "the old item-based Property Check row should be gone"
        )
        btn = gui.tool_buttons["Property Check"]
        assert str(btn["state"]) != "disabled"
    finally:
        root.destroy()
