# tests/test_release_workflow_gui.py
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

tk = pytest.importorskip("tkinter")


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
