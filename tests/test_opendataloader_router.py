"""Routing decides whether a document is worth the expensive tier.

The test PDFs are built here rather than committed as binaries so the intent
stays readable: one has a real text layer, one has none at all (the stand-in
for a scan, since the detector keys on exactly that absence).
"""
import io

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from opendataloader.service.config import load_settings
from opendataloader.service.router import Tier, chars_per_page, choose_tier


def _text_pdf(pages=2, line="The quick brown fox jumps over the lazy dog. " * 4):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for _ in range(pages):
        c.drawString(72, 720, line)
        c.showPage()
    c.save()
    return buf.getvalue()


def _drawing_only_pdf(pages=2):
    # No text operators at all — what a scanned page looks like to pypdf.
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for _ in range(pages):
        c.rect(72, 72, 400, 400, fill=1)
        c.showPage()
    c.save()
    return buf.getvalue()


def test_a_text_pdf_reports_many_characters_per_page():
    assert chars_per_page(_text_pdf(), sample_pages=5) > 50


def test_a_pdf_without_text_reports_zero():
    assert chars_per_page(_drawing_only_pdf(), sample_pages=5) == 0


def test_unreadable_bytes_report_zero_rather_than_raising():
    # A corrupt upload must become a routing decision, not a 500.
    assert chars_per_page(b"this is not a pdf", sample_pages=5) == 0


def test_a_text_pdf_routes_to_the_local_tier():
    assert choose_tier(_text_pdf(), None, load_settings({})) is Tier.LOCAL


def test_a_pdf_without_text_routes_to_hybrid():
    assert choose_tier(_drawing_only_pdf(), None, load_settings({})) is Tier.HYBRID


def test_an_explicit_hybrid_field_overrides_detection():
    # RAGFlow's dataset setting is a policy; our detection is only a default.
    settings = load_settings({})
    assert choose_tier(_text_pdf(), "docling-fast", settings) is Tier.HYBRID


def test_an_explicit_off_forces_the_local_tier():
    settings = load_settings({})
    assert choose_tier(_drawing_only_pdf(), "off", settings) is Tier.LOCAL


def test_hybrid_is_never_chosen_when_disabled():
    settings = load_settings({"ODL_ENABLE_HYBRID": "false"})
    assert choose_tier(_drawing_only_pdf(), None, settings) is Tier.LOCAL


def test_the_threshold_is_configurable():
    # Raise the bar above what the text PDF provides and it becomes "scanned".
    settings = load_settings({"ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE": "100000"})
    assert choose_tier(_text_pdf(), None, settings) is Tier.HYBRID
