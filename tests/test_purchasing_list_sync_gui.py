# tests/test_purchasing_list_sync_gui.py
"""The BOM → Purchased Parts List summary line.

"Already in list: 273" read as though 273 BOM parts were found; it is the size
of the list. The line has to separate the two counts.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

tk = pytest.importorskip("tkinter")

from gui.purchasing_list_sync import launch_bom_list_sync_gui, summary_line  # noqa: E402


def _widget_text(widget, out):
    for child in widget.winfo_children():
        try:
            out.append(str(child.cget("text")))
        except Exception:      # noqa: BLE001 — frames have no text
            pass
        _widget_text(child, out)
    return out


class TestWindowBuilds:
    def test_the_branded_chrome_and_controls_are_all_present(self):
        try:
            root = tk.Tk()
        except tk.TclError:
            pytest.skip("no display available")
        root.withdraw()
        try:
            launch_bom_list_sync_gui(parent=root)
            win = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
            texts = _widget_text(win, [])
            # header, section cards, actions and the status bar
            assert "BOM → Purchased Parts List" in texts
            assert "  BOM SOURCE" in texts and "  OPTIONS" in texts
            assert "  OUTPUT" in texts
            assert any("Scan" in t for t in texts)
            assert any("Add missing" in t for t in texts)
            assert "Close" in texts
            assert "Ready." in texts       # status bar wired to its variable
        finally:
            root.destroy()


def _report(**over):
    base = {"missing": [], "created": 0, "updated": 0, "checked": 18,
            "already_present": 18, "existing_count": 273, "by_source": {}}
    base.update(over)
    return base


def test_scan_line_separates_bom_parts_from_list_size():
    line = summary_line(_report(), applied=False)
    assert "Found 0 missing part(s) of 18 BOM part(s) checked" in line
    assert "18 already in the list" in line
    assert "holds 273" in line          # list size, clearly labelled as such


def test_scan_line_omits_the_empty_by_source_breakdown():
    assert "By source" not in summary_line(_report(), applied=False)


def test_scan_line_shows_the_by_source_breakdown_when_parts_are_missing():
    line = summary_line(
        _report(missing=["SF-1", "SF-2"], already_present=16,
                by_source={"Buy": 2}), applied=False)
    assert "Found 2 missing part(s) of 18 BOM part(s) checked" in line
    assert "By source: {'Buy': 2}" in line


def test_apply_line_reports_what_was_created():
    line = summary_line(_report(missing=["SF-1", "SF-2"], created=2,
                                already_present=16, by_source={"Buy": 2}),
                        applied=True)
    assert line.startswith("ADDED 2 missing part(s) of 18 BOM part(s) checked")


def test_update_clause_only_appears_when_updating_existing():
    r = _report(updated=3)
    assert "3 existing updated" in summary_line(r, applied=True,
                                                update_existing=True)
    assert "existing updated" not in summary_line(r, applied=True)
