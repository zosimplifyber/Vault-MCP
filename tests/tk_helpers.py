# tests/tk_helpers.py
"""Shared Tk bootstrap for the GUI tests.

Lives here rather than in ``conftest.py`` because plain helper functions —
``test_launcher_flags._make_gui``, for one — need to import it, and pytest's
conftest is not importable by bare name from inside a test package.
"""
import glob
import os
import sys

import pytest


def make_tk_root():
    """Return a withdrawn ``tk.Tk``, retrying once before giving up.

    The naive form of this — ``try: tk.Tk() except TclError: pytest.skip(...)``
    — is a trap. Re-initialising Tk after another test module has torn its root
    down loses the Tcl/Tk library paths, raising a *transient* ``TclError`` that
    has nothing to do with display availability. The test then skips, and every
    assertion below it silently never runs while the suite still reports green.

    That is the same "absent data reads as success" failure this codebase keeps
    hitting, living inside the harness: at the summary line a skipped GUI test
    is indistinguishable from a passing one. Point the library vars at the
    interpreter's own copies and retry, so a skip means what it says.
    """
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError:
        base = getattr(sys, "base_prefix", sys.prefix)
        for var, pattern in (("TCL_LIBRARY", "tcl8.*"), ("TK_LIBRARY", "tk8.*")):
            hits = [p for p in glob.glob(os.path.join(base, "tcl", pattern))
                    if os.path.isdir(p)]
            if hits:
                os.environ[var] = hits[0]
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            pytest.skip(f"no display available: {exc}")
    root.withdraw()
    return root
