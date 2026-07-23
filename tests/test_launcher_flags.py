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
    from gui.launcher import LauncherGUI
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    gui = LauncherGUI(root, cfg=cfg, auto_start_mcp=False)
    root.update_idletasks()
    return root, gui


def test_item_master_tools_are_flagged_broken():
    root, gui = _make_gui()
    try:
        for title in ("Release Workflow", "MFG Order Package", "Property Check (Lookup)"):
            btn = gui.tool_buttons[title]
            assert str(btn["state"]) == "disabled", f"{title} should be disabled"
    finally:
        root.destroy()


def test_working_tools_stay_enabled():
    root, gui = _make_gui()
    try:
        btn = gui.tool_buttons["BOM → Purchasing Sheet"]
        assert str(btn["state"]) != "disabled"
    finally:
        root.destroy()
