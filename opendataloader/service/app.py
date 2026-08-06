"""HTTP surface for the OpenDataLoader service.

The routes, field names and response body are dictated by RAGFlow's built-in
client (/ragflow/deepdoc/parser/opendataloader_parser.py). RAGFlow retries
three times and uses a 600 s timeout, so failures are reported as 5xx and this
service times out first at ODL_TIMEOUT.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
from typing import Any, Mapping

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from .config import Settings, _is_unset, load_settings
from .config import _bool as _parse_bool
from .convert import ConvertError, run_convert
from .router import UnreadablePdf, choose_tier

logger = logging.getLogger(__name__)

# A request that can never get a slot must fail fast rather than pile onto the
# queue: total wall time is queue-wait + conversion, and RAGFlow's own client
# gives up (with a retry) at 600s. Budgeting the wait at a fraction of
# ODL_TIMEOUT keeps queue-wait + conversion under that ceiling even in the
# worst case (540s default -> 54s budget -> 594s worst case).
_QUEUE_BUDGET_FRACTION = 0.1


def _authorise(settings: Settings, authorization: str | None) -> None:
    if not settings.api_key:
        return
    expected = f"Bearer {settings.api_key}"
    provided = (authorization or "").strip()
    # Constant-time: a bearer token is a secret, and string == would let a
    # timing difference leak how many leading characters an attacker guessed.
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def create_app(env: Mapping[str, str] | None = None) -> FastAPI:
    settings = load_settings(env)
    app = FastAPI(title="OpenDataLoader PDF service")
    app.state.settings = settings
    # Indirection so tests can substitute the conversion without a JVM.
    app.state.run_convert = run_convert
    # Each conversion spawns a JVM; unbounded concurrency would thrash the
    # host. A slot is held until the underlying thread actually finishes (see
    # _release_slot below), not merely until this request stops waiting for
    # it — Python threads have no cancellation hook, so a hung JVM keeps
    # running after ODL_TIMEOUT regardless of what this service does. Holding
    # the slot for the thread's real lifetime turns that into visible
    # backpressure (further requests queue, then 503) instead of unbounded
    # concurrency invisible to /health. It does not stop the hung JVM itself;
    # that needs out-of-process execution with a hard kill, deliberately
    # deferred to a later task.
    app.state.slots = asyncio.Semaphore(settings.max_concurrency)
    app.state.max_concurrency = settings.max_concurrency
    # Slots currently held, including ones whose request already answered
    # (504/timeout) but whose thread is still running — this is what makes
    # /health's slot count honest rather than mirroring the fast-but-wrong
    # "async with" accounting.
    app.state.in_flight = 0

    @app.get("/health")
    async def health():
        # Deliberately unauthenticated even when ODL_API_KEY is set: Docker's
        # HEALTHCHECK calls this with no Authorization header, and requiring
        # one would leave the container permanently "unhealthy" while
        # /file_parse works fine. Nothing here is worth protecting — it is a
        # slot count, not document content — so do not add auth back later.
        return {
            "status": "ok",
            "slots_available": app.state.max_concurrency - app.state.in_flight,
            "max_concurrency": app.state.max_concurrency,
        }

    @app.post("/file_parse")
    async def file_parse(
        file: UploadFile = File(...),
        hybrid: str | None = Form(default=None),
        image_output: str | None = Form(default=None),
        sanitize: str | None = Form(default=None),
        authorization: str | None = Header(default=None),
    ):
        _authorise(settings, authorization)

        # `file.size` is already known from the multipart parser without
        # reading anything ourselves — the whole part has already been
        # spooled by the time this handler runs. Checking it first means a
        # too-large upload never gets its own second copy made by our
        # `await file.read()` below, and RAGFlow gets told plainly (413) not
        # to spend its three retries on a document we'll never accept.
        if file.size is not None and file.size > settings.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"upload exceeds the {settings.max_upload_bytes}-byte limit",
            )

        pdf_bytes = await file.read()
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="empty upload")

        options: dict[str, Any] = {}
        if image_output is not None:
            options["image_output"] = image_output
        if not _is_unset(sanitize):
            # Reuses config._bool's true/false spellings and its
            # warn-only-on-junk behaviour: RAGFlow's actual "True"/"False"
            # casing is silent, and only a genuine typo logs a warning. An
            # empty field is treated the same as an absent one (both skip
            # this branch), so it falls through to convert.py's own default
            # instead of a blank field silently forcing sanitize off.
            options["sanitize"] = _parse_bool({"sanitize": sanitize}, "sanitize", False)

        filename = file.filename or "input.pdf"

        # A decisively unreadable PDF must fail fast and loudly. Routing it to
        # the OCR tier instead would burn ~27 minutes of CPU (540 s timeout x
        # RAGFlow's 3 retries) on a document no tier can read.
        try:
            tier = choose_tier(pdf_bytes, hybrid, settings)
        except UnreadablePdf as exc:
            logger.error("[file_parse] %s is unreadable: %s", filename, exc)
            raise HTTPException(status_code=400, detail=str(exc))

        queue_budget = settings.timeout_seconds * _QUEUE_BUDGET_FRACTION
        try:
            await asyncio.wait_for(app.state.slots.acquire(), timeout=queue_budget)
        except (asyncio.TimeoutError, TimeoutError):
            # 503, not 504: this request's conversion never started, so
            # "gateway timeout" (the work itself took too long) would be
            # misleading. 503 says the service is at capacity — a distinct,
            # honest signal from "your document was slow."
            logger.warning(
                "[file_parse] %s rejected: no free slot within %.1fs", filename, queue_budget
            )
            raise HTTPException(status_code=503, detail="service is at capacity, try again shortly")

        app.state.in_flight += 1
        # run_in_threadpool(...) returns a coroutine, not a Future, so it has
        # no add_done_callback — ensure_future wraps it into a Task that does,
        # and schedules it immediately so it starts running independently of
        # whatever the code below does to `task`.
        task: asyncio.Task = asyncio.ensure_future(
            run_in_threadpool(app.state.run_convert, pdf_bytes, filename, tier, settings, options)
        )

        def _release_slot(t: asyncio.Task) -> None:
            # Fires when the THREAD ends, not when a caller stops waiting for
            # it — this is what actually bounds concurrency (see the comment
            # on app.state.slots above).
            app.state.in_flight -= 1
            app.state.slots.release()
            if t.cancelled():
                return
            # Retrieve the exception so asyncio doesn't log "Task exception
            # was never retrieved" for a task nobody else is still awaiting
            # (e.g. after this request already answered 504) — and log the
            # real outcome, since the client that got the 504 never will.
            exc = t.exception()
            if exc is not None:
                logger.warning(
                    "[file_parse] %s: abandoned conversion finished with %s: %s",
                    filename, type(exc).__name__, exc,
                )

        task.add_done_callback(_release_slot)

        try:
            # shield is required: without it, wait_for's timeout cancels
            # `task` itself, which still cannot stop the underlying thread
            # but now resolves early — reproducing the original bug (slot
            # released while the thread keeps running) with more code.
            result = await asyncio.wait_for(asyncio.shield(task), timeout=settings.timeout_seconds)
        except HTTPException:
            # Something inside this block already produced a deliberate
            # status code (e.g. a substituted run_convert in tests, or a
            # future check moved in here) — it must reach the caller as
            # itself, not get relabelled 502 by the broad `except Exception`
            # below.
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
