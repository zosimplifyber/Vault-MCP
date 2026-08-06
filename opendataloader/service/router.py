"""Decide which OpenDataLoader tier a document deserves.

The local tier is a deterministic Java parser at roughly 0.015 s/page and no ML
model. The hybrid tier runs Docling and OCR, which on CPU is the very cost this
service exists to avoid. So a PDF that already carries a text layer must never
reach it.
"""
from __future__ import annotations

import io
import logging
from enum import Enum

from pypdf import PdfReader

from .config import Settings

logger = logging.getLogger(__name__)


class Tier(str, Enum):
    LOCAL = "local"
    HYBRID = "hybrid"


def chars_per_page(pdf_bytes: bytes, sample_pages: int) -> float:
    """Average extractable characters across the first `sample_pages` pages.

    Returns 0.0 for anything unreadable: a corrupt upload is a routing
    decision, not a crash.
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = reader.pages[: max(1, sample_pages)]
        if not pages:
            return 0.0
        total = 0
        for page in pages:
            try:
                total += len((page.extract_text() or "").strip())
            except Exception:
                continue
        return total / len(pages)
    except Exception as exc:
        logger.warning("[router] could not read the PDF for detection: %s", exc)
        return 0.0


def choose_tier(pdf_bytes: bytes, explicit_hybrid: str | None, settings: Settings) -> Tier:
    """Pick a tier. An explicit `hybrid` field from RAGFlow always wins."""
    if explicit_hybrid is not None:
        explicit = explicit_hybrid.strip().lower()
        if explicit in ("", "off", "none", "false"):
            return Tier.LOCAL
        return Tier.HYBRID

    if not settings.enable_hybrid:
        return Tier.LOCAL

    density = chars_per_page(pdf_bytes, settings.sample_pages)
    if density >= settings.min_chars_per_page:
        logger.info("[router] local tier (%.0f chars/page)", density)
        return Tier.LOCAL
    logger.info("[router] hybrid tier (%.0f chars/page — no usable text layer)", density)
    return Tier.HYBRID
