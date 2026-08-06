"""Does RAGFlow actually understand what we send it?

Every other test in this suite checks our code against our own expectations.
This one runs parse trees captured from the real OpenDataLoader engine through
RAGFlow's real element-walking functions (copied verbatim into
tests/fixtures/ragflow_element_walker.py).

If this passes, an ingest produces chunks with working citations. If it fails,
ingests would still return 200 and silently index nothing useful — which is
the failure this whole service exists to avoid, so do not weaken these
assertions to make them green.
"""
import json
from pathlib import Path

import pytest

from opendataloader.service.normalize import normalize_tables
from tests.fixtures.ragflow_element_walker import (
    _bbox_from_element,
    _element_html,
    _element_text,
    _iter_elements,
)

FIXTURES = Path(__file__).parent / "fixtures"
# Captured from the running container, not hand-written — see the commit that
# added them. odl_sample_doc is a text-rich standards PDF; odl_sample_table is
# a generated PDF whose 4x4 table both real documents happened to lack.
PROSE = FIXTURES / "odl_sample_doc.json"
TABLE = FIXTURES / "odl_sample_table.json"


@pytest.fixture(scope="module")
def prose_tree():
    return json.loads(PROSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def table_tree():
    return normalize_tables(json.loads(TABLE.read_text(encoding="utf-8")))


def test_ragflow_finds_elements_in_our_output(prose_tree):
    assert list(_iter_elements(prose_tree)), (
        "RAGFlow's walker found no elements — it looks for dicts carrying 'type' "
        "plus one of 'content'/'text'/'cells'"
    )


def test_ragflow_extracts_real_text(prose_tree):
    texts = [_element_text(el).strip() for el in _iter_elements(prose_tree)]
    assert any(texts), "no element yielded text; every chunk would be empty"


def test_ragflow_reads_bounding_boxes(prose_tree):
    found = [b for b in (_bbox_from_element(el) for el in _iter_elements(prose_tree)) if b]
    assert found, (
        "no bounding boxes parsed — source citations would not link to the page. "
        "RAGFlow wants 'bounding box' as [left, bottom, right, top] and 'page number'"
    )


def test_every_text_element_is_locatable_on_a_page(prose_tree):
    """A chunk without a bbox can be retrieved but never cited back to the PDF."""
    els = [el for el in _iter_elements(prose_tree) if _element_text(el).strip()]
    located = [el for el in els if _bbox_from_element(el) is not None]
    assert len(located) == len(els), (
        f"{len(els) - len(located)} of {len(els)} text elements have no usable bbox"
    )


def test_bounding_boxes_are_sane(prose_tree):
    for box in filter(None, (_bbox_from_element(el) for el in _iter_elements(prose_tree))):
        assert box.page_no >= 1
        assert box.x1 > box.x0
        assert box.y1 > box.y0


def test_elements_carry_types_ragflow_recognises(prose_tree):
    known = {
        "heading", "title", "paragraph", "text", "list", "list_item", "caption",
        "table", "image", "picture", "figure", "formula", "equation",
    }
    seen = {str(el.get("type", "")).lower() for el in _iter_elements(prose_tree)}
    assert seen & known, f"no recognised element types; saw {sorted(seen)}"


def test_a_table_reaches_ragflow_as_a_table(table_tree):
    tables = [el for el in _iter_elements(table_tree)
              if str(el.get("type", "")).lower() == "table"]
    assert tables, "RAGFlow's walker never visits the table node"
    assert _element_html(tables[0]), "the table carries no HTML for RAGFlow to index"


def test_a_table_is_croppable_and_citable(table_tree):
    table = next(el for el in _iter_elements(table_tree)
                 if str(el.get("type", "")).lower() == "table")
    box = _bbox_from_element(table)
    assert box is not None, "no bbox — RAGFlow cannot crop or cite the table"
    assert box.page_no >= 1 and box.x1 > box.x0 and box.y1 > box.y0


def test_table_rows_stay_intact_through_ragflows_reader(table_tree):
    """The values in a row must still be in that row once RAGFlow reads it."""
    table = next(el for el in _iter_elements(table_tree)
                 if str(el.get("type", "")).lower() == "table")
    rendered = _element_html(table) or _element_text(table)
    m10 = [chunk for chunk in rendered.split("<tr>") if "ISO 4014 M10" in chunk]
    assert m10, "the M10 designation is missing entirely"
    assert "16.0 mm" in m10[0], "the M10 head diameter is no longer beside its designation"
