# tests/test_formed_fiber_inventor.py
"""Inventor reader tests.

Every test here runs against a fake COM object. No test may require Inventor
or pywin32 to be installed -- the suite has to pass on a build machine.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import inventor_automation as inv


# ------------------------------------------------------------------- fakes

class FakeMassProperties:
    def __init__(self, mass, volume):
        self.Mass = mass          # Inventor reports database units: kg
        self.Volume = volume      # ... and cm³


class FakeComponentDefinition:
    def __init__(self, mass, volume):
        self.MassProperties = FakeMassProperties(mass, volume)


class FakeDoc:
    def __init__(self, mass=0.5, volume=100.0):
        self.ComponentDefinition = FakeComponentDefinition(mass, volume)
        self.FullFileName = "fake.ipt"
        self.close_calls = []

    def Close(self, skip_save):
        self.close_calls.append(skip_save)


class FakeDocuments:
    def __init__(self, doc):
        self._doc = doc
        self.open_calls = []

    def Open(self, path, visible):
        self.open_calls.append((path, visible))
        return self._doc


class FakeApp:
    def __init__(self, doc):
        self.Documents = FakeDocuments(doc)
        self.Visible = True


@pytest.fixture
def part_file(tmp_path):
    """open_document checks the path exists, so the fake needs a real file."""
    path = tmp_path / "CD-001660.ipt"
    path.write_bytes(b"not really an Inventor part")
    return path


# ------------------------------------------------------------ open_document

def test_open_document_is_visible_by_default(part_file):
    """The release workflow's behaviour must not change."""
    doc = FakeDoc()
    app = FakeApp(doc)
    with inv.open_document(app, part_file):
        pass
    assert app.Documents.open_calls[0][1] is True


def test_open_document_can_open_invisibly(part_file):
    doc = FakeDoc()
    app = FakeApp(doc)
    with inv.open_document(app, part_file, open_visible=False):
        pass
    assert app.Documents.open_calls[0][1] is False


def test_open_document_closes_without_saving_by_default(part_file):
    doc = FakeDoc()
    app = FakeApp(doc)
    with inv.open_document(app, part_file):
        pass
    # Close takes SkipSave, the inverse of save_on_close.
    assert doc.close_calls == [True]
