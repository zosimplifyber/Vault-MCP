"""Routing decides whether a document is worth the expensive tier.

The test PDFs are built here rather than committed as binaries so the intent
stays readable: one has a real text layer, one has none at all (the stand-in
for a scan, since the detector keys on exactly that absence).
"""
import io

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf._page import PageObject
from pypdf.errors import DependencyError
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from opendataloader.service.config import load_settings
from opendataloader.service.router import Tier, UnreadablePdf, chars_per_page, choose_tier
import opendataloader.service.router as router_module


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


def _encrypted_pdf(user_password, owner_password="owner-secret", algorithm="AES-128"):
    # PdfWriter can produce real encrypted PDFs, so encryption handling is
    # exercised for real rather than mocked away.
    reader = PdfReader(io.BytesIO(_text_pdf()))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password=user_password, owner_password=owner_password, algorithm=algorithm)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


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


def test_the_threshold_boundary_is_inclusive():
    # Pin >= rather than >: flipping the operator currently passes the whole
    # suite without a single failure, so nothing else catches a regression here.
    pdf_bytes = _text_pdf()
    density = int(chars_per_page(pdf_bytes, sample_pages=5))
    exact = load_settings({"ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE": str(density)})
    above = load_settings({"ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE": str(density + 1)})
    assert choose_tier(pdf_bytes, None, exact) is Tier.LOCAL
    assert choose_tier(pdf_bytes, None, above) is Tier.HYBRID


def test_min_chars_per_page_zero_is_the_never_route_to_hybrid_escape_hatch():
    # config.py documents 0 as "never route to hybrid" — nothing asserted it.
    settings = load_settings({"ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE": "0"})
    assert choose_tier(_drawing_only_pdf(), None, settings) is Tier.LOCAL


def test_an_owner_password_only_pdf_reads_normally_and_routes_local():
    # Plenty of shipped standards PDFs restrict printing/editing with an owner
    # password but leave the user password blank — pypdf's PdfReader retries
    # with an empty password automatically, so this must NOT regress into an
    # UnreadablePdf; that would misfile a perfectly readable document.
    pdf_bytes = _encrypted_pdf(user_password="")
    assert chars_per_page(pdf_bytes, sample_pages=5) > 50
    assert choose_tier(pdf_bytes, None, load_settings({})) is Tier.LOCAL


def test_a_user_password_pdf_raises_unreadable_pdf():
    # A real user password is a decisive failure — no tier can read this, so
    # it must not be silently reclassified as "no text layer" and sent to OCR.
    pdf_bytes = _encrypted_pdf(user_password="secret")
    with pytest.raises(UnreadablePdf):
        chars_per_page(pdf_bytes, sample_pages=5)
    with pytest.raises(UnreadablePdf):
        choose_tier(pdf_bytes, None, load_settings({}))


def test_a_missing_crypto_dependency_raises_unreadable_pdf_mentioning_the_extra(monkeypatch):
    # cryptography is installed on this dev host, so simulate the service
    # image that ships plain pypdf by making the read fail exactly as it
    # would there.
    def fake_pdf_reader(*args, **kwargs):
        raise DependencyError("cryptography>=3.1 is required for AES algorithm")

    monkeypatch.setattr(router_module, "PdfReader", fake_pdf_reader)

    with pytest.raises(UnreadablePdf, match=r"pypdf\[crypto\]"):
        chars_per_page(_text_pdf(), sample_pages=5)


def test_a_failed_page_extraction_is_logged_and_excluded_from_the_average(monkeypatch, caplog):
    # One bad page in the sample must not count as evidence of "no text" the
    # way a genuinely blank page does, and the failure must leave a trace —
    # this is the exact construct ("except Exception: continue") that hid the
    # AES-128 case before UnreadablePdf existed.
    single_page_density = chars_per_page(_text_pdf(pages=1), sample_pages=1)

    calls = {"n": 0}
    original_extract_text = PageObject.extract_text

    def flaky_extract_text(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated extraction failure")
        return original_extract_text(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", flaky_extract_text)

    with caplog.at_level("WARNING", logger=router_module.__name__):
        density = chars_per_page(_text_pdf(pages=3), sample_pages=3)

    assert density == pytest.approx(single_page_density)
    assert any(
        "could not extract text from a sampled page" in r.message for r in caplog.records
    )
