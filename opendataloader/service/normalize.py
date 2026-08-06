"""Make OpenDataLoader's tables legible to RAGFlow.

RAGFlow's client reads a parse tree that its own converter shapes, and for
text that shape happens to match what OpenDataLoader emits — headings,
paragraphs, captions and their bounding boxes all pass through untouched.
Tables do not, in two separate ways:

  * An OpenDataLoader `table` node carries its content under `rows`. RAGFlow's
    `_iter_elements` only yields a node that has `content`, `text` or `cells`,
    so it never visits the table node and never records a table at all.
  * An OpenDataLoader `table row` *does* carry `cells`, so RAGFlow does visit
    it — but the text sits in each cell's `kids`, while RAGFlow reads
    `cells[].content`. The row therefore renders as `'|  |  |'`.

Measured against a real 4x4 table, RAGFlow produced zero tables and sixteen
single-word sections: "ISO 4014 M10" and "16.0 mm" landed in different chunks
with nothing associating them, which is precisely the question a fastener
table exists to answer.

So each table is rewritten into the one shape RAGFlow does understand: an
`html` string plus a flat `cells` list, with the now-redundant `rows` removed
so the same text is not also scattered across loose paragraph sections. The
table keeps its own bounding box and page number, which is what RAGFlow needs
to crop the region and cite it.

Nothing else in the tree is touched.
"""
from __future__ import annotations

import copy
import logging
from html import escape
from typing import Any

logger = logging.getLogger(__name__)


def _cell_text(cell: Any) -> str:
    """Gather a cell's text, wherever the engine nested it.

    Cell content arrives as paragraph `kids` rather than a `content` string,
    and a cell may hold several paragraphs.
    """
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            value = node.get("content")
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(cell)
    return " ".join(parts)


def _rows_as_text(table: dict) -> list[list[str]]:
    rows = table.get("rows")
    if not isinstance(rows, list):
        return []
    out: list[list[str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cells = row.get("cells")
        if not isinstance(cells, list):
            continue
        out.append([_cell_text(c) if isinstance(c, dict) else "" for c in cells])
    return out


def _as_html(grid: list[list[str]]) -> str:
    # A header row is worth marking: RAGFlow indexes the HTML as-is, and a
    # retrieved chunk reads far better when the column names are identifiable.
    lines = ["<table>"]
    for index, row in enumerate(grid):
        tag = "th" if index == 0 else "td"
        cells = "".join(f"<{tag}>{escape(text)}</{tag}>" for text in row)
        lines.append(f"<tr>{cells}</tr>")
    lines.append("</table>")
    return "".join(lines)


def _normalise_table(table: dict) -> None:
    grid = _rows_as_text(table)
    if not grid:
        # A table with no readable rows — common for the grid lines in a
        # drawing's title block. Leave it alone rather than emitting an empty
        # table that would only add a blank chunk.
        table.pop("rows", None)
        return

    table["html"] = _as_html(grid)
    # `cells` is what makes RAGFlow visit the node at all, and it doubles as
    # the text fallback if the HTML is ever dropped.
    table["cells"] = [
        {"row": r + 1, "column": c + 1, "content": text}
        for r, row in enumerate(grid)
        for c, text in enumerate(row)
    ]
    # Drop the original rows: their cell paragraphs would otherwise be yielded
    # separately, duplicating every value as its own one-word section.
    table.pop("rows", None)


def normalize_tables(json_doc: Any) -> Any:
    """Return a copy of the parse tree with every table made legible.

    The input is left untouched — convert.py still holds it for the markdown
    path and for debugging.
    """
    if not isinstance(json_doc, (dict, list)):
        return json_doc

    tree = copy.deepcopy(json_doc)
    count = 0

    def walk(node: Any) -> None:
        nonlocal count
        if isinstance(node, dict):
            if str(node.get("type", "")).lower() == "table":
                try:
                    _normalise_table(node)
                    count += 1
                except Exception as exc:
                    # A malformed table must not cost us the whole document.
                    logger.warning("[normalize] leaving a table as-is: %s", exc)
                return
            for child in list(node.values()):
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(tree)
    if count:
        logger.info("[normalize] rewrote %d table(s) into RAGFlow's shape", count)
    return tree
