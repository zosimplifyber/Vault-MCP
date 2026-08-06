"""Tables must survive the trip into RAGFlow.

Measured against the real engine (see tests/fixtures/odl_sample_table.json,
captured from a 4x4 PDF table), RAGFlow's own converter produced **zero
tables** and 21 sections: 16 single-word paragraphs plus four `'|  |  |'`
rows. "ISO 4014 M10" and "16.0 mm" ended up in separate chunks with nothing
tying them together, so a question about that bolt's head diameter could not
be answered from the index.

Two schema mismatches cause it, and both are one-sided — RAGFlow reads what
its own converter emits, not what OpenDataLoader emits:

  * an OpenDataLoader `table` node carries `rows`, but RAGFlow's
    `_iter_elements` only yields nodes having `content`/`text`/`cells`, so the
    table node is never visited at all;
  * an OpenDataLoader `table row` *does* carry `cells`, so it IS visited, but
    the cell text lives in each cell's `kids` while RAGFlow reads
    `cells[].content` — hence the empty `'|  |  |'`.

This module normalises tables on the way out so RAGFlow sees a real table.
"""
import json
from pathlib import Path

import pytest

from opendataloader.service.normalize import normalize_tables

FIXTURE = Path(__file__).parent / "fixtures" / "odl_sample_table.json"


# --- RAGFlow v0.26.4 deepdoc/parser/opendataloader_parser.py, verbatim -------
# Duplicated here on purpose: asserting against our own idea of the schema
# would prove nothing, and RAGFlow is not importable from this repo.
# Do not edit these to make a test pass.

_TABLE_TYPES = {"table"}
_IMAGE_TYPES = {"image", "picture", "figure"}
_FORMULA_TYPES = {"formula", "equation"}


def _iter_elements(node):
    if isinstance(node, dict):
        if "type" in node and ("content" in node or "text" in node or "cells" in node):
            yield node
        for v in node.values():
            yield from _iter_elements(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_elements(item)


def _element_text(el):
    content = el.get("content")
    if isinstance(content, str):
        return content
    text = el.get("text")
    if isinstance(text, str):
        return text
    cells = el.get("cells")
    if isinstance(cells, list):
        rows = {}
        for c in cells:
            if not isinstance(c, dict):
                continue
            row = c.get("row") or c.get("row_index") or 0
            rows.setdefault(int(row), []).append(str(c.get("content") or c.get("text") or ""))
        return "\n".join(" | ".join(v) for _, v in sorted(rows.items()))
    return ""


def _element_html(el):
    for key in ("html", "html_content"):
        v = el.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _classify(el_type):
    t = (el_type or "").lower()
    if t in _TABLE_TYPES:
        return "table"
    if t in _IMAGE_TYPES:
        return "image"
    if t in _FORMULA_TYPES:
        return "equation"
    return t or "text"


def ragflow_split(tree):
    """What RAGFlow's _transfer_from_json would make of this tree."""
    sections, tables = [], []
    for el in _iter_elements(tree):
        kind = _classify(el.get("type", ""))
        if kind == "table":
            tables.append(_element_html(el) or _element_text(el))
            continue
        if kind in _IMAGE_TYPES or kind == "image":
            continue
        txt = _element_text(el).strip()
        if txt:
            sections.append(txt)
    return sections, tables


# -----------------------------------------------------------------------------


@pytest.fixture
def real_tree():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_captured_fixture_really_does_defeat_ragflow(real_tree):
    """Guards the premise. If this ever fails, the fix may be unnecessary."""
    sections, tables = ragflow_split(real_tree)
    assert tables == [], "RAGFlow already sees tables — re-check whether normalising is still needed"
    assert any("|" in s and s.replace("|", "").strip() == "" for s in sections), (
        "expected the empty '|  |  |' rows that motivated this module"
    )


def test_ragflow_sees_a_real_table_after_normalising(real_tree):
    _, tables = ragflow_split(normalize_tables(real_tree))
    assert len(tables) == 1, f"expected exactly one table, got {len(tables)}"


def test_every_cell_value_survives_in_one_place(real_tree):
    _, tables = ragflow_split(normalize_tables(real_tree))
    html = tables[0]
    for value in ("Designation", "Thread", "Head dia", "Tensile",
                  "ISO 4014 M8", "M8x1.25", "13.0 mm",
                  "ISO 4014 M10", "M10x1.5", "16.0 mm",
                  "ISO 4014 M12", "M12x1.75", "18.0 mm", "10.9"):
        assert value in html, f"{value!r} was lost from the table"


def test_a_row_stays_a_row(real_tree):
    """The whole point: the M10 row's values must stay associated."""
    _, tables = ragflow_split(normalize_tables(real_tree))
    html = tables[0]
    m10_row = [r for r in html.split("<tr>") if "ISO 4014 M10" in r]
    assert m10_row, "no row contained the M10 designation"
    assert "16.0 mm" in m10_row[0], "the M10 head diameter is no longer in the M10 row"


def test_the_empty_pipe_rows_are_gone(real_tree):
    sections, _ = ragflow_split(normalize_tables(real_tree))
    junk = [s for s in sections if s.replace("|", "").strip() == ""]
    assert junk == [], f"still emitting content-free rows: {junk}"


def test_cell_text_is_not_also_scattered_across_loose_sections(real_tree):
    """One coherent table beats sixteen one-word chunks plus a table."""
    sections, _ = ragflow_split(normalize_tables(real_tree))
    assert "M8x1.25" not in sections, "cell text is duplicated as a loose section"


def test_the_table_keeps_its_bounding_box(real_tree):
    """Without page number and bbox RAGFlow cannot crop or cite the table."""
    normalised = normalize_tables(real_tree)
    table = next(el for el in _iter_elements(normalised) if _classify(el.get("type", "")) == "table")
    assert table.get("page number") is not None
    bbox = table.get("bounding box")
    assert isinstance(bbox, list) and len(bbox) >= 4


def test_a_tree_without_tables_is_left_alone():
    tree = {"type": "document", "kids": [{"type": "paragraph", "content": "hello", "page number": 1}]}
    assert normalize_tables(tree) == tree


def test_prose_in_a_real_document_is_untouched():
    """Normalising tables must not disturb the text path that already works."""
    doc = json.loads((Path(__file__).parent / "fixtures" / "odl_sample_doc.json").read_text(encoding="utf-8"))
    before, _ = ragflow_split(doc)
    after, _ = ragflow_split(normalize_tables(doc))
    assert after == before


def test_html_is_escaped():
    tree = {
        "type": "table", "page number": 1, "bounding box": [0, 0, 10, 10],
        "number of rows": 1, "number of columns": 1,
        "rows": [{"type": "table row", "row number": 1, "cells": [
            {"type": "table cell", "row number": 1, "column number": 1,
             "kids": [{"type": "paragraph", "content": "a < b & c"}]}]}],
    }
    _, tables = ragflow_split(normalize_tables(tree))
    assert "&lt;" in tables[0] and "&amp;" in tables[0]
    assert "a < b & c" not in tables[0]


def test_the_input_tree_is_not_mutated(real_tree):
    """convert.py may still want the original for md_text or debugging."""
    before = json.dumps(real_tree, sort_keys=True)
    normalize_tables(real_tree)
    assert json.dumps(real_tree, sort_keys=True) == before


def test_a_malformed_table_does_not_raise():
    for broken in (
        {"type": "table"},
        {"type": "table", "rows": None},
        {"type": "table", "rows": [{"cells": None}]},
        {"type": "table", "rows": [{"cells": [None, 5, "x"]}]},
    ):
        normalize_tables(broken)  # must not raise
