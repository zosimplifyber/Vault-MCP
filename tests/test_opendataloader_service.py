"""The HTTP contract RAGFlow's client already speaks.

Every field name and status code here is dictated by
/ragflow/deepdoc/parser/opendataloader_parser.py in the running RAGFlow
container. This is a contract to implement, not a design to revisit.
"""
import asyncio
import time

import httpx
import pytest
from fastapi import HTTPException
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


def test_health_stays_open_even_when_a_key_is_configured():
    """Docker's HEALTHCHECK curls this with no Authorization header.

    Requiring the token here would leave the container permanently unhealthy
    while /file_parse worked fine. /health exposes a slot count, not document
    content, so it is deliberately open — this test exists to stop someone
    "hardening" it back.
    """
    c = client({"ODL_API_KEY": "secret"})
    assert c.get("/health").status_code == 200
    assert c.get("/health", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_health_reports_slot_availability():
    body = client({"ODL_MAX_CONCURRENCY": "3"}).get("/health").json()
    assert body["max_concurrency"] == 3
    assert body["slots_available"] == 3


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


# --- Timing and capacity -----------------------------------------------------
#
# These are the behaviours the module exists to guarantee, and the ones a
# synchronously-raising fake cannot exercise. `error=TimeoutError(...)` only
# proves the `except` clause is wired; it never lets asyncio.wait_for expire,
# which is why a real defect here needed a manual probe to find rather than
# showing up as a failing test.


def _sleeping_client(env, seconds, record=None):
    """A client whose conversion blocks in a REAL thread, as the JVM does."""
    application = app_mod.create_app(env)

    def slow_run(pdf_bytes, filename, tier, settings, options):
        time.sleep(seconds)
        if record is not None:
            record.append(filename)
        return ConvertResult(json_doc={"type": "document"}, md_text="# t")

    application.state.run_convert = slow_run
    return TestClient(application)


def test_a_conversion_that_overruns_is_a_504_and_returns_promptly():
    c = _sleeping_client({"ODL_TIMEOUT": "1"}, seconds=5)
    started = time.monotonic()
    response = c.post("/file_parse", files={"file": ("d.pdf", PDF, "application/pdf")})
    elapsed = time.monotonic() - started

    assert response.status_code == 504
    # The point of the timeout is to answer before RAGFlow's own 600 s ceiling.
    # If this waited for the thread it would take 5 s, not ~1 s.
    assert elapsed < 4, f"answered in {elapsed:.1f}s — the deadline is not being enforced"


async def _slow_asgi_client(env, seconds):
    """One app, one event loop, a conversion that blocks in a real thread.

    These use httpx+ASGITransport rather than TestClient on purpose: TestClient
    runs each request in its own event loop, and an abandoned conversion
    outlives the request that started it. Its release callback would then fire
    against a dead loop ("bound to a different event loop"). Uvicorn runs one
    loop for the life of the process, so a shared-loop client is what actually
    models production here.
    """
    application = app_mod.create_app(env)

    def slow_run(pdf_bytes, filename, tier, settings, options):
        time.sleep(seconds)
        return ConvertResult(json_doc={"type": "document"}, md_text="# t")

    application.state.run_convert = slow_run
    return application, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://odl.test"
    )


async def test_a_timed_out_conversion_still_frees_its_slot():
    """The slot outlives the request, then comes back on its own.

    This pins the fix for the defect where the slot was released when the
    *wait* ended rather than when the *work* did, letting real concurrency
    exceed ODL_MAX_CONCURRENCY without limit.
    """
    _, client_ = await _slow_asgi_client({"ODL_TIMEOUT": "1", "ODL_MAX_CONCURRENCY": "1"}, seconds=2)
    async with client_ as c:
        response = await c.post("/file_parse", files={"file": ("d.pdf", PDF, "application/pdf")})
        assert response.status_code == 504

        # The abandoned thread is still running, so the only slot is still held.
        assert (await c.get("/health")).json()["slots_available"] == 0

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (await c.get("/health")).json()["slots_available"] == 1:
                break
            await asyncio.sleep(0.05)
        assert (await c.get("/health")).json()["slots_available"] == 1, "slot never came back"


async def test_a_saturated_service_answers_503_not_504():
    """503 and 504 mean different things and RAGFlow should see the difference.

    504 says "your document was slow". 503 says "we never started" — the truth
    when no slot came free, and a distinct signal for an operator reading logs.
    """
    _, client_ = await _slow_asgi_client({"ODL_TIMEOUT": "1", "ODL_MAX_CONCURRENCY": "1"}, seconds=3)
    async with client_ as c:
        responses = await asyncio.gather(
            *(c.post("/file_parse", files={"file": ("d.pdf", PDF, "application/pdf")})
              for _ in range(3))
        )
    codes = [r.status_code for r in responses]
    assert 503 in codes, f"expected a saturation rejection, got {codes}"


def test_an_oversized_upload_is_rejected_with_413_before_conversion():
    """413 tells RAGFlow not to spend its three retries on it."""
    record = {}
    c = client({"ODL_MAX_UPLOAD_BYTES": "100"}, record=record)
    response = c.post(
        "/file_parse", files={"file": ("big.pdf", b"x" * 5000, "application/pdf")}
    )
    assert response.status_code == 413
    assert record == {}, "the upload was converted despite exceeding the limit"


def test_an_upload_within_the_limit_is_still_accepted():
    c = client({"ODL_MAX_UPLOAD_BYTES": "100000"})
    response = c.post("/file_parse", files={"file": ("ok.pdf", PDF, "application/pdf")})
    assert response.status_code == 200


def test_a_deliberate_status_code_is_not_relabelled_502():
    """Pins the `except HTTPException: raise` guard.

    Without it, a 4xx raised inside the conversion block came back as a 502
    with the real detail mangled into the body. Nothing else would notice if a
    refactor dropped that clause.
    """
    c = client(error=HTTPException(status_code=403, detail="distinguishable-403"))
    response = c.post("/file_parse", files={"file": ("d.pdf", PDF, "application/pdf")})
    assert response.status_code == 403
    assert response.json()["detail"] == "distinguishable-403"
