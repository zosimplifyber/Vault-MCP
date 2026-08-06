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


# ------------------------------------------------- read_part_physical_properties

class FakePythoncom:
    """Records COM apartment bookkeeping so the tests can assert on it."""

    def __init__(self):
        self.init_calls = 0
        self.uninit_calls = 0

    def CoInitialize(self):
        self.init_calls += 1

    def CoUninitialize(self):
        self.uninit_calls += 1


@pytest.fixture
def fake_com(monkeypatch):
    """Patch the COM boundary. Returns (pythoncom, make_app) for assertions."""
    pythoncom = FakePythoncom()
    monkeypatch.setattr(inv, "_import_win32", lambda: (pythoncom, None))
    return pythoncom


def test_mass_converts_kilograms_to_grams(part_file, fake_com, monkeypatch):
    """Inventor's API reports database units -- kg -- regardless of what the
    document displays, so grams is an exact *1000 with no unit parsing."""
    doc = FakeDoc(mass=0.10526, volume=512.5)
    monkeypatch.setattr(inv, "get_inventor_app", lambda **_: FakeApp(doc))

    props = inv.read_part_physical_properties(part_file)

    assert props.mass_g == pytest.approx(105.26)


def test_volume_is_already_in_cubic_centimetres(part_file, fake_com, monkeypatch):
    doc = FakeDoc(mass=0.5, volume=512.5)
    monkeypatch.setattr(inv, "get_inventor_app", lambda **_: FakeApp(doc))

    props = inv.read_part_physical_properties(part_file)

    assert props.volume_cm3 == pytest.approx(512.5)


def test_the_part_is_opened_invisibly_and_not_saved(part_file, fake_com, monkeypatch):
    doc = FakeDoc()
    app = FakeApp(doc)
    monkeypatch.setattr(inv, "get_inventor_app", lambda **_: app)

    inv.read_part_physical_properties(part_file)

    assert app.Documents.open_calls[0][1] is False
    assert doc.close_calls == [True]


def test_com_is_initialised_and_released(part_file, fake_com, monkeypatch):
    """The GUI reads on a worker thread, where COM must be initialised or
    every call fails with an error pointing nowhere near the cause."""
    monkeypatch.setattr(inv, "get_inventor_app", lambda **_: FakeApp(FakeDoc()))

    inv.read_part_physical_properties(part_file)

    assert fake_com.init_calls == 1
    assert fake_com.uninit_calls == 1


def test_com_is_released_even_when_the_read_fails(part_file, fake_com, monkeypatch):
    class Exploding(FakeDoc):
        # No mass/volume to assign -- FakeDoc.__init__ would try to set
        # ComponentDefinition, which collides with the read-only property
        # below (property has no setter -> AttributeError on construction).
        def __init__(self):
            self.FullFileName = "fake.ipt"
            self.close_calls = []

        @property
        def ComponentDefinition(self):
            raise RuntimeError("no component definition")

    monkeypatch.setattr(inv, "get_inventor_app", lambda **_: FakeApp(Exploding()))

    with pytest.raises(inv.InventorAutomationError):
        inv.read_part_physical_properties(part_file)

    assert fake_com.uninit_calls == 1


def test_missing_pywin32_raises_unavailable(part_file, monkeypatch):
    def _boom():
        raise inv.InventorUnavailableError("pywin32 is not installed.")

    monkeypatch.setattr(inv, "_import_win32", _boom)

    with pytest.raises(inv.InventorUnavailableError):
        inv.read_part_physical_properties(part_file)


def test_a_missing_part_file_is_an_automation_error(tmp_path, fake_com, monkeypatch):
    monkeypatch.setattr(inv, "get_inventor_app", lambda **_: FakeApp(FakeDoc()))

    with pytest.raises(inv.InventorAutomationError):
        inv.read_part_physical_properties(tmp_path / "not-there.ipt")
