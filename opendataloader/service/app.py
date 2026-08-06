"""HTTP surface for the OpenDataLoader service.

The routes, field names and response body are dictated by RAGFlow's built-in
client (/ragflow/deepdoc/parser/opendataloader_parser.py). RAGFlow retries
three times and uses a 600 s timeout, so failures are reported as 5xx and this
service times out first at ODL_TIMEOUT.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Mapping

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from .config import Settings, load_settings
from .convert import ConvertError, run_convert
from .router import UnreadablePdf, choose_tier

logger = logging.getLogger(__name__)


def _authorise(settings: Settings, authorization: str | None) -> None:
    if not settings.api_key:
        return
    expected = f"Bearer {settings.api_key}"
    if (authorization or "").strip() != expected:
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def create_app(env: Mapping[str, str] | None = None) -> FastAPI:
    settings = load_settings(env)
    app = FastAPI(title="OpenDataLoader PDF service")
    app.state.settings = settings
    # Indirection so tests can substitute the conversion without a JVM.
    app.state.run_convert = run_convert
    # Each conversion spawns a JVM; unbounded concurrency would thrash the host.
    #
    # Known gap: this only bounds *requests inside the `async with` block*, not
    # the underlying threadpool work. When asyncio.wait_for times out below,
    # the `async with` releases this slot immediately, but the thread it
    # abandoned keeps running the JVM to completion (Python threads are not
    # cancellable) — it just stops being counted. Under sustained timeouts,
    # real concurrent JVM processes can exceed `max_concurrency`. Confirmed by
    # probe: with max_concurrency=1 and a fake run_convert that sleeps past a
    # 1 s ODL_TIMEOUT, two overlapping requests both landed in the "running"
    # section simultaneously (max_active == 2). Not fixed here — thread
    # cancellation/abandon-tracking is a bigger design change than this task.
    app.state.slots = asyncio.Semaphore(settings.max_concurrency)

    @app.get("/health")
    async def health(authorization: str | None = Header(default=None)):
        _authorise(settings, authorization)
        return {"status": "ok"}

    @app.post("/file_parse")
    async def file_parse(
        file: UploadFile = File(...),
        hybrid: str | None = Form(default=None),
        image_output: str | None = Form(default=None),
        sanitize: str | None = Form(default=None),
        authorization: str | None = Header(default=None),
    ):
        _authorise(settings, authorization)

        pdf_bytes = await file.read()
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="empty upload")

        options: dict = {}
        if image_output is not None:
            options["image_output"] = image_output
        if sanitize is not None:
            # Inconsistent with config._bool by design-not-yet-reconciled: this
            # accepts the same true-spellings ("1"/"true"/"yes"/"on") but,
            # unlike config._bool, treats every other value — including the
            # recognised false-spellings "0"/"false"/"no"/"off" *and* a plain
            # typo — as False with no warning logged. A misspelled
            # `sanitize=treu` would silently forward `False` instead of being
            # caught. Flagging, not fixing: the task asked me to note this
            # rather than unilaterally change RAGFlow-facing form parsing.
            options["sanitize"] = sanitize.strip().lower() in ("1", "true", "yes", "on")

        filename = file.filename or "input.pdf"

        # A decisively unreadable PDF must fail fast and loudly. Routing it to
        # the OCR tier instead would burn ~27 minutes of CPU (540 s timeout x
        # RAGFlow's 3 retries) on a document no tier can read.
        try:
            tier = choose_tier(pdf_bytes, hybrid, settings)
        except UnreadablePdf as exc:
            logger.error("[file_parse] %s is unreadable: %s", filename, exc)
            raise HTTPException(status_code=400, detail=str(exc))

        async with app.state.slots:
            try:
                result = await asyncio.wait_for(
                    run_in_threadpool(
                        app.state.run_convert,
                        pdf_bytes,
                        filename,
                        tier,
                        settings,
                        options,
                    ),
                    timeout=settings.timeout_seconds,
                )
            except HTTPException:
                # A 4xx raised by something inside this block (e.g. a future
                # size/type check moved in here, or a substituted
                # `run_convert` in tests) must reach the caller as itself —
                # not get relabelled 502 by the broad `except Exception`
                # below. Confirmed by probing with a fake run_convert that
                # raises HTTPException(403, ...): without this clause the
                # response comes back 502 with detail "403: ...", losing both
                # the real status code and the original detail.
                raise
            except (asyncio.TimeoutError, TimeoutError):
                logger.error("[file_parse] %s timed out after %ss", filename, settings.timeout_seconds)
                raise HTTPException(status_code=504, detail="conversion timed out")
            except ConvertError as exc:
                logger.error("[file_parse] %s failed: %s", filename, exc)
                raise HTTPException(status_code=502, detail=str(exc))
            except Exception as exc:
                logger.exception("[file_parse] %s failed unexpectedly", filename)
                raise HTTPException(status_code=502, detail=str(exc))

        return {"json_doc": result.json_doc, "md_text": result.md_text}

    return app


app = create_app()
