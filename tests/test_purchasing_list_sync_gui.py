# tests/test_purchasing_list_sync_gui.py
"""The BOM → Purchased Parts List window: chrome, column feedback, summary line.

Two things worth stating outright:
* "Already in list: 273" read as though 273 BOM parts were found; it is the size
  of the list, so the summary line has to separate the two counts.
* Picking a file must say which columns it carries BEFORE anything is scanned.
"""
import glob
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


def _entries(widget, out):
    for child in widget.winfo_children():
        if isinstance(child, tk.Entry):
            out.append(child)
        _entries(child, out)
    return out


@pytest.fixture(scope="module")
def root():
    """One Tk root for the module — repeatedly creating roots skips flakily."""
    try:
        r = tk.Tk()
    except tk.TclError:
        # Re-initialising Tk after another test module has torn its root down
        # loses the Tcl/Tk library paths here. Point them at the interpreter's
        # own copies and retry once.
        base = getattr(sys, "base_prefix", sys.prefix)
        for var, pattern in (("TCL_LIBRARY", "tcl8.*"), ("TK_LIBRARY", "tk8.*")):
            hits = [p for p in glob.glob(os.path.join(base, "tcl", pattern))
                    if os.path.isdir(p)]
            if hits:
                os.environ[var] = hits[0]
        try:
            r = tk.Tk()
        except tk.TclError as exc:
            pytest.skip(f"no display available: {exc}")
    r.withdraw()
    yield r
    r.destroy()


def _load(root, path):
    """Open the window, put `path` in its entry, return every label's text."""
    launch_bom_list_sync_gui(parent=root)
    win = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
    _entries(win, [])[0].insert(0, str(path))
    root.update()
    texts = _widget_text(win, [])
    win.destroy()
    return texts


class TestColumnFeedback:
    """Picking a file checks its header row and says what is missing."""

    def test_a_missing_required_column_is_called_out(self, root, tmp_path):
        bad = tmp_path / "bad.csv"
        bad.write_text("Item,Description\n1,a thing\n", encoding="utf-8")
        texts = _load(root, bad)
        assert any("Missing required column(s)" in t and "Part Number" in t
                   for t in texts)

    def test_a_complete_export_is_confirmed(self, root, tmp_path):
        good = tmp_path / "good.csv"
        good.write_text(          # the export template's own columns
            "Part Number,Filename,Thumbnail,BOM Structure,Unit QTY,QTY,"
            "Description,REV,Vendor,Web Link,Material\n"
            "SF-1,CD-1.ipt,,Purchased,Each,2,thing,1,Acme,123,Steel\n",
            encoding="utf-8")
        texts = _load(root, good)
        assert any("All required and optional columns found" in t for t in texts)

    def test_optional_gaps_are_listed_without_blocking(self, root, tmp_path):
        thin = tmp_path / "thin.csv"
        thin.write_text("Part Number,QTY\nSF-1,2\n", encoding="utf-8")
        texts = _load(root, thin)
        assert any("All required columns found" in t and "Filename" in t
                   for t in texts)


class TestWindowBuilds:
    def test_the_branded_chrome_and_controls_are_all_present(self, root):
        launch_bom_list_sync_gui(parent=root)
        win = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
        try:
            texts = _widget_text(win, [])
            # header, section cards, actions and the status bar
            assert "BOM → Purchased Parts List" in texts
            assert "  BOM SOURCE" in texts and "  OPTIONS" in texts
            assert "  OUTPUT" in texts
            assert any("Scan" in t for t in texts)
            assert any("Add missing" in t for t in texts)
            assert "Close" in texts
            assert "Ready." in texts       # status bar wired to its variable
            # the required / optional field lists are spelled out on the card
            assert "Required:" in texts and "Optional:" in texts
            assert any("Part Number, QTY" in t for t in texts)
            assert any("Filename" in t for t in texts)
        finally:
            win.destroy()


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
