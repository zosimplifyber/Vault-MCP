"""The HTTP contract RAGFlow's client already speaks.

Every field name and status code here is dictated by
/ragflow/deepdoc/parser/opendataloader_parser.py in the running RAGFlow
container. This is a contract to implement, not a design to revisit.
"""
import pytest
from fastapi.testclient import TestClient

from opendataloader.service import app as app_mod
from opendataloader.service.convert import ConvertError, ConvertResult
from opendataloader.service.router import Tier

PDF = b"%PDF-1.4 pretend"


def client(env=None, result=None, error=None, record=None):
    application = app_mod.create_app(env or {})

    def fake_run(pdf_bytes, filename, tier, settings, options):
        if record is not None:
            record.update({"filename": filename, "tier": tier, "options": options})
        if error is not None:
            raise error
        return result or ConvertResult(json_doc={"type": "document"}, md_text="# t")

    application.state.run_convert = fake_run
    return TestClient(application)


def test_health_reports_ok():
    response = client().get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_requires_the_bearer_token_when_one_is_configured():
    c = client({"ODL_API_KEY": "secret"})
    assert c.get("/health").status_code == 401
    assert c.get("/health", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_a_wrong_bearer_token_is_rejected():
    c = client({"ODL_API_KEY": "secret"})
    response = c.post(
        "/file_parse",
        files={"file": ("d.pdf", PDF, "application/pdf")},
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_file_parse_returns_the_contract_shape():
    response = client().post("/file_parse", files={"file": ("d.pdf", PDF, "application/pdf")})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"json_doc", "md_text"}
    assert body["json_doc"] == {"type": "document"}
    assert body["md_text"] == "# t"


def test_a_missing_file_is_rejected():
    assert client().post("/file_parse", data={}).status_code == 422


def test_the_hybrid_form_field_reaches_the_router():
    record = {}
    client(record=record).post(
        "/file_parse",
        files={"file": ("d.pdf", PDF, "application/pdf")},
        data={"hybrid": "docling-fast"},
    )
    assert record["tier"] is Tier.HYBRID


def test_sanitize_and_image_output_are_forwarded():
    record = {}
    client(record=record).post(
        "/file_parse",
        files={"file": ("d.pdf", PDF, "application/pdf")},
        data={"sanitize": "true", "image_output": "none"},
    )
    assert record["options"] == {"sanitize": True, "image_output": "none"}


def test_sanitize_false_is_forwarded_as_false():
    record = {}
    client(record=record).post(
        "/file_parse",
        files={"file": ("d.pdf", PDF, "application/pdf")},
        data={"sanitize": "false"},
    )
    assert record["options"]["sanitize"] is False


def test_a_conversion_failure_is_a_502():
    # RAGFlow retries three times on its own, so a 5xx is the right signal.
    c = client(error=ConvertError("nothing usable"))
    response = c.post("/file_parse", files={"file": ("d.pdf", PDF, "application/pdf")})
    assert response.status_code == 502


def test_an_unexpected_error_is_also_a_502():
    c = client(error=RuntimeError("surprise"))
    response = c.post("/file_parse", files={"file": ("d.pdf", PDF, "application/pdf")})
    assert response.status_code == 502


def test_a_timeout_is_a_504():
    c = client(error=TimeoutError("too slow"))
    response = c.post("/file_parse", files={"file": ("d.pdf", PDF, "application/pdf")})
    assert response.status_code == 504


def test_an_empty_upload_is_rejected_before_conversion():
    response = client().post("/file_parse", files={"file": ("d.pdf", b"", "application/pdf")})
    assert response.status_code == 400


def test_a_decisively_unreadable_pdf_is_a_fast_400(monkeypatch):
    """Password-protected and crypto-unsupported PDFs must fail immediately.

    Routing them to the OCR tier would cost ~27 minutes of CPU (540 s timeout
    times RAGFlow's three retries) on a document no tier can read.
    """
    from opendataloader.service.router import UnreadablePdf

    def refuse(pdf_bytes, explicit_hybrid, settings):
        raise UnreadablePdf("password-protected; no tier can read it")

    monkeypatch.setattr(app_mod, "choose_tier", refuse)
    response = client().post("/file_parse", files={"file": ("d.pdf", PDF, "application/pdf")})
    assert response.status_code == 400
    assert "password" in response.json()["detail"]
