"""Copied verbatim from RAGFlow v0.26.4,
/ragflow/deepdoc/parser/opendataloader_parser.py.

These are the functions RAGFlow uses to walk our /file_parse response. They are
duplicated here on purpose: testing against our own idea of the schema would
prove nothing, and RAGFlow is not importable from this repo. If RAGFlow is
upgraded and the contract test starts failing, re-copy this file first — the
failure may be an upstream change rather than a bug in our service.

Extracted with:
  docker exec docker-ragflow-cpu-1 sh -c \
    'sed -n "/^class OpenDataLoaderContentType/,/^class OpenDataLoaderParser/p" \
     /ragflow/deepdoc/parser/opendataloader_parser.py'

Do not edit these functions to make a test pass.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Optional


class OpenDataLoaderContentType(str, Enum):
    IMAGE = "image"
    TABLE = "table"
    TEXT = "text"
    EQUATION = "equation"


@dataclass
class _BBox:
    page_no: int
    x0: float
    y0: float
    x1: float
    y1: float


_TEXT_TYPES = {"heading", "title", "paragraph", "text", "list", "list_item", "caption"}
_TABLE_TYPES = {"table"}
_IMAGE_TYPES = {"image", "picture", "figure"}
_FORMULA_TYPES = {"formula", "equation"}


def _as_float(v) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None


def _bbox_from_element(el: dict) -> Optional[_BBox]:
    bb = el.get("bounding box") or el.get("bounding_box") or el.get("bbox")
    pn = el.get("page number")
    if pn is None:
        pn = el.get("page_number")
    if pn is None:
        pn = el.get("page")
    if bb is None or pn is None:
        return None
    if not isinstance(bb, (list, tuple)) or len(bb) < 4:
        return None
    coords = [_as_float(x) for x in bb[:4]]
    if any(c is None for c in coords):
        return None
    try:
        page_no = int(pn)
    except Exception:
        return None
    # OpenDataLoader emits [left, bottom, right, top] in PDF points.
    left, bottom, right, top = coords
    x0, x1 = min(left, right), max(left, right)
    y0, y1 = min(bottom, top), max(bottom, top)
    return _BBox(page_no=page_no, x0=x0, y0=y0, x1=x1, y1=y1)


def _iter_elements(node: Any) -> Iterable[dict]:
    if isinstance(node, dict):
        if "type" in node and ("content" in node or "text" in node or "cells" in node):
            yield node
        for v in node.values():
            yield from _iter_elements(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_elements(item)


def _element_text(el: dict) -> str:
    content = el.get("content")
    if isinstance(content, str):
        return content
    text = el.get("text")
    if isinstance(text, str):
        return text
    # tables may expose cells; join row-wise if needed
    cells = el.get("cells")
    if isinstance(cells, list):
        rows: dict[int, list[str]] = {}
        for c in cells:
            if not isinstance(c, dict):
                continue
            row = c.get("row") or c.get("row_index") or 0
            rows.setdefault(int(row), []).append(str(c.get("content") or c.get("text") or ""))
        return "\n".join(" | ".join(v) for _, v in sorted(rows.items()))
    return ""


def _element_html(el: dict) -> str:
    for key in ("html", "html_content"):
        v = el.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""



