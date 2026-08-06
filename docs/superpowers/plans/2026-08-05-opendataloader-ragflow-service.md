# OpenDataLoader PDF Service for RAGFlow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the HTTP service that RAGFlow's existing OpenDataLoader PDF parser client expects but which nobody publishes, so PDFs with a text layer are parsed by a fast deterministic Java engine instead of CPU-bound DeepDOC.

**Architecture:** Two containers on RAGFlow's existing Docker network. `odl-api` is small and always on: FastAPI implementing `GET /health` and `POST /file_parse`, routing each document to the fast local tier or, when the PDF has no text layer, to an optional heavyweight `odl-hybrid` Docling/OCR backend. RAGFlow reaches it by service name; no RAGFlow source is modified.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, pypdf, `opendataloader-pdf` (Apache-2.0, bundles a Java engine, needs a JRE), Docker Compose, pytest.

**Design spec:** [`docs/superpowers/specs/2026-08-05-opendataloader-ragflow-service-design.md`](../specs/2026-08-05-opendataloader-ragflow-service-design.md)

---

## Background the implementer needs

You do **not** need to understand Autodesk Vault, which is what the rest of this repo is about. This service is self-contained infrastructure for a locally running RAGFlow instance.

**RAGFlow already contains the client.** In the running container `docker-ragflow-cpu-1`, the file `/ragflow/deepdoc/parser/opendataloader_parser.py` POSTs a PDF to `{OPENDATALOADER_APISERVER}/file_parse` and reads the response. Your job is only the server side. Read that file if you need ground truth:

```bash
docker exec docker-ragflow-cpu-1 sh -c 'cat /ragflow/deepdoc/parser/opendataloader_parser.py'
```

**The contract, which is fixed and must not be redesigned:**

```
GET  /health   → 200 {"status": "ok"}
POST /file_parse
     multipart/form-data:
       file          required   (filename, bytes, "application/pdf")
       hybrid        optional   str
       image_output  optional   str
       sanitize      optional   "true" | "false"
     → 200 {"json_doc": <object>|null, "md_text": <string>|null}
Both endpoints accept an optional `Authorization: Bearer <token>` header.
```

RAGFlow retries `/file_parse` three times and uses a 600 s timeout.

**Why `json_doc` matters more than `md_text`:** RAGFlow's `_sections_from_markdown` turns `md_text` into a **single section covering the entire document**. That destroys chunk boundaries and source citations. `json_doc` is the real output; `md_text` is a fallback that must be logged loudly when used.

**The `opendataloader_pdf.convert()` signature** (verified from upstream docs — these are the parameters this plan uses):

```python
convert(input_path, output_dir=None, format="json", sanitize=False,
        image_output="external", hybrid="off", hybrid_mode="auto",
        hybrid_url=None, hybrid_timeout="0", hybrid_fallback=False,
        threads="1", quiet=False, ...)
```

`convert()` writes files into `output_dir` and returns nothing useful. It spawns a JVM per call. Note `hybrid` defaults to the string `"off"`, not `None`, and `hybrid_timeout`/`threads` are **strings**.

**Repo test conventions:** `tests/conftest.py` puts the repo root on `sys.path`, so tests import modules by their top-level path. Tests are plain pytest functions, no classes. `pytest.ini` sets `asyncio_mode = auto`. Run tests with `python -m pytest` from the repo root.

---

## File structure

| File | Responsibility |
| --- | --- |
| `opendataloader/__init__.py` | Package marker (empty) |
| `opendataloader/service/__init__.py` | Package marker (empty) |
| `opendataloader/service/config.py` | Read settings from environment; one frozen dataclass |
| `opendataloader/service/router.py` | Measure a PDF's text layer, decide local vs hybrid |
| `opendataloader/service/convert.py` | Call `opendataloader_pdf.convert`, manage temp dirs, read results back |
| `opendataloader/service/app.py` | FastAPI app: HTTP contract, auth, concurrency, error mapping |
| `opendataloader/requirements.api.txt` | Runtime deps for the `odl-api` image |
| `opendataloader/requirements.hybrid.txt` | Runtime deps for the `odl-hybrid` image |
| `opendataloader/Dockerfile.api` | JRE 17 + Python + the API service |
| `opendataloader/Dockerfile.hybrid` | Python only; runs `opendataloader-pdf-hybrid` |
| `opendataloader/docker-compose.opendataloader.yml` | Standalone compose project on RAGFlow's external network |
| `opendataloader/README.md` | Setup, configuration, troubleshooting |
| `tests/test_opendataloader_config.py` | Config defaults and overrides |
| `tests/test_opendataloader_router.py` | Text-layer detection and tier choice |
| `tests/test_opendataloader_convert.py` | Convert wrapper: argument building, result reading, errors |
| `tests/test_opendataloader_service.py` | HTTP contract, auth, error mapping |
| `tests/test_opendataloader_ragflow_contract.py` | Our output survives RAGFlow's own parsing logic |
| `tests/fixtures/ragflow_element_walker.py` | Verbatim copy of RAGFlow's element-walking functions |
| `tests/fixtures/odl_sample_doc.json` | A **real** captured `json_doc`, not hand-written |

Each module has one job and is testable without Docker and without RAGFlow.

---

## Task 1: Package skeleton and configuration

**Files:**
- Create: `opendataloader/__init__.py`
- Create: `opendataloader/service/__init__.py`
- Create: `opendataloader/service/config.py`
- Test: `tests/test_opendataloader_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_opendataloader_config.py`:

```python
"""The service is configured entirely from the environment, because it runs as
a container and compose is the only place its settings are written."""
from opendataloader.service.config import Settings, load_settings


def test_defaults_when_environment_is_empty():
    s = load_settings({})
    assert s.api_key == ""
    assert s.hybrid_url == "http://odl-hybrid:5002"
    assert s.hybrid_backend == "docling-fast"
    assert s.enable_hybrid is True
    assert s.min_chars_per_page == 50
    assert s.sample_pages == 5
    assert s.max_concurrency == 4
    assert s.timeout_seconds == 540


def test_values_are_read_from_the_environment():
    s = load_settings({
        "ODL_API_KEY": "secret",
        "ODL_HYBRID_URL": "http://elsewhere:5002",
        "ODL_HYBRID_BACKEND": "docling-full",
        "ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE": "120",
        "ODL_TEXT_LAYER_SAMPLE_PAGES": "3",
        "ODL_MAX_CONCURRENCY": "8",
        "ODL_TIMEOUT": "300",
    })
    assert s.api_key == "secret"
    assert s.hybrid_url == "http://elsewhere:5002"
    assert s.hybrid_backend == "docling-full"
    assert s.min_chars_per_page == 120
    assert s.sample_pages == 3
    assert s.max_concurrency == 8
    assert s.timeout_seconds == 300


def test_enable_hybrid_accepts_the_usual_spellings_of_false():
    for value in ("false", "False", "0", "no", "off"):
        assert load_settings({"ODL_ENABLE_HYBRID": value}).enable_hybrid is False
    for value in ("true", "True", "1", "yes", "on"):
        assert load_settings({"ODL_ENABLE_HYBRID": value}).enable_hybrid is True


def test_a_malformed_number_falls_back_to_the_default():
    # A typo in compose must not take the whole service down at import time.
    s = load_settings({"ODL_MAX_CONCURRENCY": "lots"})
    assert s.max_concurrency == 4


def test_settings_are_frozen():
    import dataclasses
    import pytest

    s = load_settings({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.api_key = "mutated"


def test_api_key_is_stripped():
    # Compose files pick up trailing whitespace surprisingly often.
    assert load_settings({"ODL_API_KEY": "  secret  "}).api_key == "secret"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_opendataloader_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opendataloader'`

- [ ] **Step 3: Write minimal implementation**

Create `opendataloader/__init__.py` (empty file) and `opendataloader/service/__init__.py` (empty file).

Create `opendataloader/service/config.py`:

```python
"""Settings for the OpenDataLoader service.

Everything is read from the environment: the service only ever runs as a
container, so compose is the single place these are written.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    api_key: str
    hybrid_url: str
    hybrid_backend: str
    enable_hybrid: bool
    min_chars_per_page: int
    sample_pages: int
    max_concurrency: int
    timeout_seconds: int


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    # A typo in compose should degrade to the default, not crash the service on
    # import — the container would crash-loop with a stack trace nobody reads.
    try:
        return int(str(env.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = str(env.get(key, "")).strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return default


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    return Settings(
        api_key=str(env.get("ODL_API_KEY", "")).strip(),
        hybrid_url=str(env.get("ODL_HYBRID_URL", "http://odl-hybrid:5002")).strip(),
        hybrid_backend=str(env.get("ODL_HYBRID_BACKEND", "docling-fast")).strip(),
        enable_hybrid=_bool(env, "ODL_ENABLE_HYBRID", True),
        min_chars_per_page=_int(env, "ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE", 50),
        sample_pages=_int(env, "ODL_TEXT_LAYER_SAMPLE_PAGES", 5),
        max_concurrency=_int(env, "ODL_MAX_CONCURRENCY", 4),
        timeout_seconds=_int(env, "ODL_TIMEOUT", 540),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_opendataloader_config.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add opendataloader/__init__.py opendataloader/service/__init__.py opendataloader/service/config.py tests/test_opendataloader_config.py
git commit -m "feat(opendataloader): read service settings from the environment"
```

---

## Task 2: Text-layer detection and tier routing

This is the heart of the design: a PDF that already carries text must never reach an ML model.

**Files:**
- Create: `opendataloader/service/router.py`
- Test: `tests/test_opendataloader_router.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_opendataloader_router.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_opendataloader_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opendataloader.service.router'`

- [ ] **Step 3: Write minimal implementation**

Create `opendataloader/service/router.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_opendataloader_router.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add opendataloader/service/router.py tests/test_opendataloader_router.py
git commit -m "feat(opendataloader): route by text layer so digital PDFs skip OCR"
```

---

## Task 3: The convert wrapper

**Files:**
- Create: `opendataloader/service/convert.py`
- Test: `tests/test_opendataloader_convert.py`

`opendataloader_pdf` is **not installed on the development host** — only inside the container. So the import must happen lazily, inside the function that calls it, or every test in this file would fail at import time.

- [ ] **Step 1: Write the failing test**

Create `tests/test_opendataloader_convert.py`:

```python
"""The convert wrapper turns a tier decision into arguments, and an output
directory into a response body.

`opendataloader_pdf` is only installed in the container, so these tests
monkeypatch the single function that touches it.
"""
from pathlib import Path

import pytest

from opendataloader.service import convert as convert_mod
from opendataloader.service.config import load_settings
from opendataloader.service.convert import ConvertError, run_convert
from opendataloader.service.router import Tier

PDF = b"%PDF-1.4 pretend"


def _capture(monkeypatch, writes=None):
    """Replace the JVM call; record kwargs and optionally write fake output."""
    seen = {}

    def fake_call(input_path, output_dir, **kwargs):
        seen["input_path"] = input_path
        seen["output_dir"] = output_dir
        seen.update(kwargs)
        for name, content in (writes or {}).items():
            Path(output_dir, name).write_text(content, encoding="utf-8")

    monkeypatch.setattr(convert_mod, "_call_convert", fake_call)
    return seen


def test_the_local_tier_disables_hybrid(monkeypatch):
    seen = _capture(monkeypatch, {"doc.json": '{"type": "document"}'})
    run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})
    assert seen["hybrid"] == "off"
    assert seen["format"] == "markdown,json"


def test_the_hybrid_tier_passes_the_backend_url_and_fallback(monkeypatch):
    seen = _capture(monkeypatch, {"doc.json": '{"type": "document"}'})
    run_convert(PDF, "doc.pdf", Tier.HYBRID, load_settings({}), {})
    assert seen["hybrid"] == "docling-fast"
    assert seen["hybrid_url"] == "http://odl-hybrid:5002"
    # Without fallback, a stopped hybrid container turns every scan into an
    # error instead of a degraded-but-useful local parse.
    assert seen["hybrid_fallback"] is True


def test_json_output_is_returned_as_json_doc(monkeypatch):
    _capture(monkeypatch, {"doc.json": '{"type": "document", "content": "hi"}'})
    result = run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})
    assert result.json_doc == {"type": "document", "content": "hi"}


def test_markdown_output_is_returned_as_md_text(monkeypatch):
    _capture(monkeypatch, {"doc.json": "{}", "doc.md": "# Title"})
    result = run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})
    assert result.md_text == "# Title"


def test_markdown_alone_still_produces_a_result(monkeypatch):
    # Degraded, but better than failing: RAGFlow will make one big section.
    _capture(monkeypatch, {"doc.md": "# Only markdown"})
    result = run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})
    assert result.json_doc is None
    assert result.md_text == "# Only markdown"


def test_no_output_at_all_raises(monkeypatch):
    # Never return an empty success — a document that silently yields zero
    # chunks is only discovered later as a bad retrieval.
    _capture(monkeypatch, {})
    with pytest.raises(ConvertError):
        run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})


def test_unparseable_json_falls_back_to_markdown(monkeypatch):
    _capture(monkeypatch, {"doc.json": "{not json", "doc.md": "# Fallback"})
    result = run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})
    assert result.json_doc is None
    assert result.md_text == "# Fallback"


def test_extra_form_options_are_forwarded(monkeypatch):
    seen = _capture(monkeypatch, {"doc.json": "{}"})
    run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}),
                {"sanitize": True, "image_output": "none"})
    assert seen["sanitize"] is True
    assert seen["image_output"] == "none"


def test_a_failing_conversion_becomes_a_converterror(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("jvm exploded")

    monkeypatch.setattr(convert_mod, "_call_convert", boom)
    with pytest.raises(ConvertError):
        run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})


def test_the_temporary_directory_is_removed(monkeypatch):
    seen = _capture(monkeypatch, {"doc.json": "{}"})
    run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})
    assert not Path(seen["output_dir"]).exists()


def test_the_temporary_directory_is_removed_even_on_failure(monkeypatch):
    seen = _capture(monkeypatch, {})
    with pytest.raises(ConvertError):
        run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})
    assert not Path(seen["output_dir"]).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_opendataloader_convert.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opendataloader.service.convert'`

- [ ] **Step 3: Write minimal implementation**

Create `opendataloader/service/convert.py`:

```python
"""Run one PDF through OpenDataLoader and read the result back.

`convert()` writes files into a directory and spawns a JVM per call, so each
request gets its own temporary directory which is always removed afterwards.
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .router import Tier

logger = logging.getLogger(__name__)


class ConvertError(RuntimeError):
    """Conversion failed, or produced nothing usable."""


@dataclass(frozen=True)
class ConvertResult:
    json_doc: Any | None
    md_text: str | None


def _call_convert(input_path: str, output_dir: str, **kwargs) -> None:
    """The only place that touches opendataloader_pdf.

    Imported lazily: the package ships a Java engine and is installed in the
    container only, so importing at module scope would break host-side tests.
    Tests monkeypatch this function.
    """
    import opendataloader_pdf

    opendataloader_pdf.convert(input_path=[input_path], output_dir=output_dir, **kwargs)


def _read_first(output_dir: Path, suffix: str) -> str | None:
    # Glob rather than assume a filename: the engine derives the stem from the
    # input and we would rather not depend on that rule.
    for path in sorted(output_dir.rglob(f"*{suffix}")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            return text
    return None


def run_convert(
    pdf_bytes: bytes,
    filename: str,
    tier: Tier,
    settings: Settings,
    options: dict,
) -> ConvertResult:
    workdir = Path(tempfile.mkdtemp(prefix="odl-"))
    try:
        source = workdir / "input"
        source.mkdir()
        pdf_path = source / (Path(filename).name or "input.pdf")
        pdf_path.write_bytes(pdf_bytes)

        out = workdir / "out"
        out.mkdir()

        kwargs: dict[str, Any] = {
            "format": "markdown,json",
            # One JVM thread per conversion; the service bounds concurrency
            # itself, and letting each call fan out would defeat that.
            "threads": "1",
            "quiet": True,
        }
        if tier is Tier.HYBRID:
            kwargs["hybrid"] = settings.hybrid_backend
            kwargs["hybrid_url"] = settings.hybrid_url
            kwargs["hybrid_timeout"] = str(settings.timeout_seconds * 1000)
            # Degrade to a local parse when the hybrid container is stopped.
            kwargs["hybrid_fallback"] = True
        else:
            kwargs["hybrid"] = "off"
        kwargs.update(options)

        # The wall-clock timeout is enforced by the caller (app.py wraps this in
        # asyncio.wait_for); the JVM offers no cancellation hook of its own.
        try:
            _call_convert(str(pdf_path), str(out), **kwargs)
        except Exception as exc:
            raise ConvertError(f"conversion failed: {exc}") from exc

        raw_json = _read_first(out, ".json")
        md_text = _read_first(out, ".md")

        json_doc = None
        if raw_json:
            try:
                json_doc = json.loads(raw_json)
            except ValueError as exc:
                logger.warning("[convert] output JSON was unreadable: %s", exc)

        if json_doc is None and not md_text:
            raise ConvertError("conversion produced no usable output")
        if json_doc is None:
            logger.warning(
                "[convert] no JSON for %s — RAGFlow will treat the markdown as a "
                "single section, losing chunk boundaries and citations",
                filename,
            )

        return ConvertResult(json_doc=json_doc, md_text=md_text)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_opendataloader_convert.py -v`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add opendataloader/service/convert.py tests/test_opendataloader_convert.py
git commit -m "feat(opendataloader): wrap convert with per-request temp dirs"
```

---

## Task 4: The FastAPI app and the HTTP contract

**Files:**
- Create: `opendataloader/service/app.py`
- Modify: `requirements-dev.txt`
- Test: `tests/test_opendataloader_service.py`

- [ ] **Step 1: Add the test-time dependencies**

Append to `requirements-dev.txt`:

```
# OpenDataLoader service tests — the service itself runs in a container, but
# its unit tests exercise the FastAPI app in-process.
fastapi>=0.110.0
python-multipart>=0.0.9
```

(`reportlab` was already added to this file in Task 2, where the router tests
first needed it. Do not add it twice.)

Run: `python -m pip install -r requirements-dev.txt`
Expected: installs without error.

- [ ] **Step 2: Write the failing test**

Create `tests/test_opendataloader_service.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_opendataloader_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opendataloader.service.app'`

- [ ] **Step 4: Write minimal implementation**

Create `opendataloader/service/app.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_opendataloader_service.py -v`
Expected: PASS — 12 passed

- [ ] **Step 6: Run the whole suite to confirm nothing else broke**

Run: `python -m pytest -q`
Expected: PASS — all pre-existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add opendataloader/service/app.py tests/test_opendataloader_service.py requirements-dev.txt
git commit -m "feat(opendataloader): serve the /file_parse contract RAGFlow expects"
```

---

## Task 5: The API container image

**Files:**
- Create: `opendataloader/requirements.api.txt`
- Create: `opendataloader/Dockerfile.api`

- [ ] **Step 1: Write the runtime requirements**

Create `opendataloader/requirements.api.txt`:

```
# Runtime dependencies for the odl-api image.
fastapi>=0.110.0
uvicorn[standard]>=0.30.0
python-multipart>=0.0.9
# The [crypto] extra is NOT optional here. Plain pypdf ships AES support only in
# that extra, and nothing else in this file pulls `cryptography` transitively
# (verified: resolving without it yields 22 packages, none of them cryptography).
# Without it, every AES-encrypted PDF — the default since Acrobat 7 — raises
# DependencyError inside the router, is read as "no text layer", and is routed to
# the OCR tier that cannot decrypt it either. That costs ~27 minutes of CPU per
# document (540 s timeout x 3 RAGFlow retries) and returns nothing.
pypdf[crypto]>=4.0.0
opendataloader-pdf>=2.0.0
```

- [ ] **Step 2: Write the Dockerfile**

Create `opendataloader/Dockerfile.api`:

```dockerfile
# The API tier: a JRE plus the OpenDataLoader engine and a thin HTTP shim.
# Deliberately free of ML models — this is the fast path, and every megabyte of
# model here would be loaded for documents that never need one.
FROM eclipse-temurin:17-jre-jammy

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv curl \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY opendataloader/requirements.api.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY opendataloader/__init__.py /app/opendataloader/__init__.py
COPY opendataloader/service /app/opendataloader/service

EXPOSE 5060

# The healthcheck mirrors what RAGFlow's client calls, so a container reporting
# healthy means RAGFlow's check would also succeed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5060/health || exit 1

CMD ["uvicorn", "opendataloader.service.app:app", "--host", "0.0.0.0", "--port", "5060"]
```

- [ ] **Step 3: Build the image**

Run from the repo root:

```bash
docker build -f opendataloader/Dockerfile.api -t odl-api:local .
```

Expected: build completes, final message `naming to docker.io/library/odl-api:local`.

- [ ] **Step 4: Verify Java and the package are present**

```bash
docker run --rm odl-api:local sh -c 'java -version && python3 -c "import opendataloader_pdf; print(\"odl ok\")"'
```

Expected: a Java 17 version banner, then `odl ok`.

- [ ] **Step 5: Verify the service answers**

```bash
docker run --rm -d --name odl-smoke -p 127.0.0.1:5060:5060 odl-api:local
curl -fsS http://127.0.0.1:5060/health
docker rm -f odl-smoke
```

Expected: `{"status":"ok"}`

- [ ] **Step 6: Commit**

```bash
git add opendataloader/requirements.api.txt opendataloader/Dockerfile.api
git commit -m "build(opendataloader): image for the fast local tier"
```

---

## Task 6: Capture a real parse tree as a fixture

The next task asserts our output survives RAGFlow's parsing. That assertion is only worth anything against a **real** OpenDataLoader parse tree. Do not hand-write this fixture.

**Files:**
- Create: `tests/fixtures/odl_sample_doc.json`

- [ ] **Step 1: Pick a real PDF with text and at least one table**

Any born-digital PDF will do. Two already sitting in `~/Documents` are suitable: `CD-001567.pdf` or `MC-ISO 4014.pdf`.

- [ ] **Step 2: Parse it through the built image**

```bash
docker run --rm -d --name odl-fixture -p 127.0.0.1:5060:5060 odl-api:local
curl -fsS -X POST http://127.0.0.1:5060/file_parse \
  -F "file=@$HOME/Documents/MC-ISO 4014.pdf;type=application/pdf" \
  -o /tmp/odl_response.json
docker rm -f odl-fixture
```

Expected: `/tmp/odl_response.json` is written and is not empty.

- [ ] **Step 3: Extract just the parse tree and save it**

```bash
python -c "import json,pathlib; d=json.load(open('/tmp/odl_response.json')); assert d['json_doc'], 'no json_doc — do not continue, the service is not returning a parse tree'; pathlib.Path('tests/fixtures/odl_sample_doc.json').write_text(json.dumps(d['json_doc'], indent=2), encoding='utf-8')"
```

Expected: no output, and `tests/fixtures/odl_sample_doc.json` exists.

If the assertion fires, stop and investigate: a service that returns only `md_text` is the degraded path, and shipping it as normal would hide the problem the whole design is meant to avoid.

- [ ] **Step 4: Confirm the fixture has the fields RAGFlow reads**

```bash
python -c "import json; s=open('tests/fixtures/odl_sample_doc.json').read(); print('bounding box:', 'bounding box' in s); print('page number:', 'page number' in s); print('bytes:', len(s))"
```

Expected: both `True`. If either is `False`, RAGFlow will produce no bounding boxes and citations will not link — record that in the task notes before moving on.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/odl_sample_doc.json
git commit -m "test(opendataloader): capture a real parse tree as a fixture"
```

---

## Task 7: Prove the output survives RAGFlow's parser

This is the highest-value test in the plan. Everything else verifies our own code; this verifies the assumption the whole integration rests on.

**Files:**
- Create: `tests/fixtures/ragflow_element_walker.py`
- Test: `tests/test_opendataloader_ragflow_contract.py`

- [ ] **Step 1: Copy RAGFlow's element-walking functions verbatim**

Extract them from the running container rather than retyping:

```bash
docker exec docker-ragflow-cpu-1 sh -c 'cat /ragflow/deepdoc/parser/opendataloader_parser.py' > /tmp/ragflow_parser.py
```

Create `tests/fixtures/ragflow_element_walker.py` containing **only** the pure, dependency-free helpers copied from that file — `_TEXT_TYPES`, `_TABLE_TYPES`, `_IMAGE_TYPES`, `_FORMULA_TYPES`, `_as_float`, `_bbox_from_element`, `_iter_elements`, `_element_text`, `_element_html`, and the `_BBox` dataclass. Start the file with this header:

```python
"""Copied verbatim from RAGFlow v0.26.4,
/ragflow/deepdoc/parser/opendataloader_parser.py.

These are the functions RAGFlow uses to walk our /file_parse response. They are
duplicated here on purpose: testing against our own idea of the schema would
prove nothing, and RAGFlow is not importable from this repo. If RAGFlow is
upgraded and the contract test starts failing, re-copy this file first — the
failure may be an upstream change rather than a bug in our service.

Do not edit these functions to make a test pass.
"""
```

Copy the bodies exactly as they appear in `/tmp/ragflow_parser.py`. Do not adapt them.

- [ ] **Step 2: Write the failing test**

Create `tests/test_opendataloader_ragflow_contract.py`:

```python
"""Does RAGFlow actually understand what we send it?

Every other test checks our code against our own expectations. This one checks
our output against RAGFlow's real parsing logic, using a parse tree captured
from the real engine. If this passes, an ingest will produce chunks; if it
fails, ingests would silently yield nothing useful.
"""
import json
from pathlib import Path

import pytest

from tests.fixtures.ragflow_element_walker import (
    _bbox_from_element,
    _element_text,
    _iter_elements,
)

FIXTURE = Path(__file__).parent / "fixtures" / "odl_sample_doc.json"


@pytest.fixture(scope="module")
def parse_tree():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_ragflow_finds_elements_in_our_output(parse_tree):
    assert list(_iter_elements(parse_tree)), (
        "RAGFlow's walker found no elements — it looks for dicts carrying 'type' "
        "plus one of 'content'/'text'/'cells'"
    )


def test_ragflow_extracts_real_text(parse_tree):
    texts = [_element_text(el).strip() for el in _iter_elements(parse_tree)]
    assert any(texts), "no element yielded text; every chunk would be empty"


def test_ragflow_reads_bounding_boxes(parse_tree):
    boxes = [_bbox_from_element(el) for el in _iter_elements(parse_tree)]
    found = [b for b in boxes if b is not None]
    assert found, (
        "no bounding boxes parsed — source citations would not link to the page. "
        "RAGFlow wants 'bounding box' as [left, bottom, right, top] and 'page number'"
    )


def test_bounding_boxes_are_sane(parse_tree):
    for box in filter(None, (_bbox_from_element(el) for el in _iter_elements(parse_tree))):
        assert box.page_no >= 1
        assert box.x1 > box.x0
        assert box.y1 > box.y0


def test_elements_carry_types_ragflow_recognises(parse_tree):
    known = {
        "heading", "title", "paragraph", "text", "list", "list_item", "caption",
        "table", "image", "picture", "figure", "formula", "equation",
    }
    seen = {str(el.get("type", "")).lower() for el in _iter_elements(parse_tree)}
    assert seen & known, f"no recognised element types; saw {sorted(seen)}"
```

- [ ] **Step 3: Run test to verify it fails**

Before creating `tests/fixtures/ragflow_element_walker.py`, run:

Run: `python -m pytest tests/test_opendataloader_ragflow_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.fixtures.ragflow_element_walker'`

- [ ] **Step 4: Add the fixture package marker and re-run**

```bash
python -c "import pathlib; p=pathlib.Path('tests/fixtures/__init__.py'); p.exists() or p.write_text('')"
python -m pytest tests/test_opendataloader_ragflow_contract.py -v
```

Expected: PASS — 5 passed.

If `test_ragflow_reads_bounding_boxes` fails, do not weaken the test. It means citations will not work, which is a real finding to report.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/ragflow_element_walker.py tests/fixtures/__init__.py tests/test_opendataloader_ragflow_contract.py
git commit -m "test(opendataloader): verify our output against RAGFlow's own walker"
```

---

## Task 8: The hybrid container image

**Files:**
- Create: `opendataloader/requirements.hybrid.txt`
- Create: `opendataloader/Dockerfile.hybrid`

The hybrid backend is a Docling/Python server. It needs **no JRE** — only the client side invokes Java.

- [ ] **Step 1: Write the runtime requirements**

Create `opendataloader/requirements.hybrid.txt`:

```
# Runtime dependencies for the odl-hybrid image. The hybrid backend is the
# Docling/OCR server; it is pure Python and never invokes Java.
opendataloader-pdf[hybrid]>=2.0.0
```

- [ ] **Step 2: Write the Dockerfile**

Create `opendataloader/Dockerfile.hybrid`:

```dockerfile
# The hybrid tier: Docling plus OCR, for PDFs with no text layer.
# Large and slow on CPU by design — it exists so that scanned documents are
# possible, not cheap. Kept in a separate image so the fast path never loads it.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    TORCH_HOME=/models

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY opendataloader/requirements.hybrid.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# Models land here and the compose file mounts a named volume over it, so the
# first-run download survives restarts.
RUN mkdir -p /models

EXPOSE 5002

CMD ["opendataloader-pdf-hybrid", "--host", "0.0.0.0", "--port", "5002", \
     "--force-ocr", "--ocr-lang", "en", "--device", "cpu"]
```

- [ ] **Step 3: Build the image**

```bash
docker build -f opendataloader/Dockerfile.hybrid -t odl-hybrid:local .
```

Expected: build completes. This image is large (multiple GB) and the build is slow.

- [ ] **Step 4: Verify the entrypoint exists**

```bash
docker run --rm odl-hybrid:local opendataloader-pdf-hybrid --help
```

Expected: usage text listing `--port`, `--host`, `--force-ocr`, `--ocr-lang`, `--device`.

If a flag used in the `CMD` is absent from this help output, correct the `CMD` to match the real flags and note the difference — the flag names came from upstream documentation, and the installed version is the authority.

- [ ] **Step 5: Commit**

```bash
git add opendataloader/requirements.hybrid.txt opendataloader/Dockerfile.hybrid
git commit -m "build(opendataloader): image for the optional Docling/OCR tier"
```

---

## Task 9: Compose project and documentation

**Files:**
- Create: `opendataloader/docker-compose.opendataloader.yml`
- Create: `opendataloader/README.md`

- [ ] **Step 1: Write the compose file**

Create `opendataloader/docker-compose.opendataloader.yml`:

```yaml
# A standalone compose project that attaches to RAGFlow's existing network.
#
# Deliberately NOT an edit to the ragflow checkout: that repo is upstream code,
# and keeping our services here means `git pull` there can never conflict with
# them. RAGFlow reaches these containers by service name on the shared network.
#
# Start the fast tier only:
#   docker compose -f opendataloader/docker-compose.opendataloader.yml up -d
# Start the OCR tier as well:
#   docker compose -f opendataloader/docker-compose.opendataloader.yml --profile hybrid up -d

name: opendataloader

services:
  odl-api:
    build:
      context: ..
      dockerfile: opendataloader/Dockerfile.api
    image: odl-api:local
    container_name: odl-api
    restart: unless-stopped
    environment:
      ODL_HYBRID_URL: http://odl-hybrid:5002
      ODL_HYBRID_BACKEND: docling-fast
      ODL_ENABLE_HYBRID: "true"
      ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE: "50"
      ODL_TEXT_LAYER_SAMPLE_PAGES: "5"
      ODL_MAX_CONCURRENCY: "4"
      ODL_TIMEOUT: "540"
      # ODL_API_KEY: set this and OPENDATALOADER_API_KEY in ragflow/docker/.env
      # together, or neither.
    ports:
      # Loopback only. RAGFlow reaches this over the shared docker network by
      # service name, so the published port exists purely for local curl and
      # troubleshooting — there is no reason to offer it to the LAN.
      - "127.0.0.1:5060:5060"
    networks:
      - ragflow

  odl-hybrid:
    build:
      context: ..
      dockerfile: opendataloader/Dockerfile.hybrid
    image: odl-hybrid:local
    container_name: odl-hybrid
    restart: unless-stopped
    # Opt-in: this container is multi-gigabyte and loads ML models at startup.
    # It stays stopped unless scanned documents are actually being ingested.
    profiles: ["hybrid"]
    volumes:
      # Without this, every restart re-downloads the Docling and OCR models.
      - odl_hybrid_models:/models
    # No published port: only odl-api talks to it, over the shared network.
    networks:
      - ragflow
    # GPU is not enabled. This machine has 4 GB of VRAM shared with the display
    # and Docker Desktop needs WSL2 passthrough set up first. To try it, install
    # the NVIDIA container toolkit, change --device in Dockerfile.hybrid, and:
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]

volumes:
  odl_hybrid_models:

networks:
  ragflow:
    # Created by the RAGFlow stack (compose project "docker", network "ragflow").
    # Verify with: docker network ls | grep ragflow
    name: docker_ragflow
    external: true
```

- [ ] **Step 2: Validate the compose file**

```bash
docker compose -f opendataloader/docker-compose.opendataloader.yml config >/dev/null && echo "compose ok"
```

Expected: `compose ok`

- [ ] **Step 3: Write the README**

Create `opendataloader/README.md`:

````markdown
# OpenDataLoader PDF service for RAGFlow

RAGFlow v0.26.4 ships a client for the OpenDataLoader PDF parser
(`/ragflow/deepdoc/parser/opendataloader_parser.py`) but no server to talk to,
and upstream publishes no image. This directory is that server.

Why bother: RAGFlow's default parser, DeepDOC, runs vision models on every page.
On a CPU-only host that is the ingestion bottleneck. OpenDataLoader's local tier
is a deterministic Java parser at roughly 0.015 s/page with no ML model, and
almost every document worth ingesting here already has a text layer.

## What runs

| Service | Port | Started by default | Purpose |
| --- | --- | --- | --- |
| `odl-api` | `127.0.0.1:5060` | yes | HTTP contract, routing, fast local tier |
| `odl-hybrid` | internal `5002` | no (`--profile hybrid`) | Docling + OCR for scanned PDFs |

`odl-api` measures each PDF's text layer and sends only documents that lack one
to `odl-hybrid`. If `odl-hybrid` is stopped, `hybrid_fallback` degrades those to
a local parse rather than failing.

## Start it

```bash
# fast tier only
docker compose -f opendataloader/docker-compose.opendataloader.yml up -d --build

# with OCR for scanned documents
docker compose -f opendataloader/docker-compose.opendataloader.yml --profile hybrid up -d --build
```

Check it:

```bash
curl -fsS http://127.0.0.1:5060/health
```

## Point RAGFlow at it

Add to `ragflow/docker/.env` (the ragflow service loads it via `env_file: .env`):

```
OPENDATALOADER_APISERVER=http://odl-api:5060
```

Restart RAGFlow, then in the dataset's **Configuration → Ingestion pipeline**
choose **OpenDataLoader** in the PDF parser dropdown.

The UI's **Model providers** page can configure the same value without editing
a file; the `.env` line is preferred here only because it is reproducible.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ODL_API_KEY` | *(unset)* | When set, both endpoints require `Authorization: Bearer`. Must match `OPENDATALOADER_API_KEY` on the RAGFlow side. |
| `ODL_HYBRID_URL` | `http://odl-hybrid:5002` | Hybrid backend address |
| `ODL_HYBRID_BACKEND` | `docling-fast` | Value passed as `hybrid` |
| `ODL_ENABLE_HYBRID` | `true` | `false` disables hybrid routing entirely |
| `ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE` | `50` | Below this, a document counts as scanned |
| `ODL_TEXT_LAYER_SAMPLE_PAGES` | `5` | Pages sampled when detecting |
| `ODL_MAX_CONCURRENCY` | `4` | Simultaneous conversions; each spawns a JVM |
| `ODL_TIMEOUT` | `540` | Per-conversion timeout, below RAGFlow's 600 s |

## Troubleshooting

**RAGFlow reports "OpenDataLoader not found" or the parser is unavailable.**
`OPENDATALOADER_APISERVER` is unset or unreachable. Confirm from inside the
RAGFlow container, which is the network path that actually matters:

```bash
docker exec docker-ragflow-cpu-1 sh -c 'curl -fsS http://odl-api:5060/health'
```

**Chunks are one enormous block per document.** The service returned `md_text`
without `json_doc`; RAGFlow turns markdown into a single section. Check
`docker logs odl-api` for the warning naming the file.

**Scanned PDFs produce nothing.** Either `odl-hybrid` is not running (start it
with `--profile hybrid`) or the document was misdetected. Detection is per
document, so a mostly-digital PDF with a few scanned inserts takes the local
path — force the hybrid tier for that dataset in the RAGFlow UI.

**Ingestion is slower than expected.** Each document costs about a second of JVM
startup, because `convert()` spawns one per call and RAGFlow sends one document
per request. That is inherent to this design.

## Tests

```bash
python -m pytest tests/test_opendataloader_*.py -v
```

`tests/test_opendataloader_ragflow_contract.py` is the important one: it runs a
real captured parse tree through RAGFlow's own element-walking functions. If it
fails after a RAGFlow upgrade, re-copy `tests/fixtures/ragflow_element_walker.py`
from the container before assuming our service is at fault.
````

- [ ] **Step 4: Commit**

```bash
git add opendataloader/docker-compose.opendataloader.yml opendataloader/README.md
git commit -m "build(opendataloader): compose project and setup documentation"
```

---

## Task 10: Bring it up and verify end to end

Nothing before this proves the integration works. This task does.

**Files:**
- Modify: `C:\Users\ZakOlech\Documents\Custom Programs\ragflow\docker\.env` (one line)

- [ ] **Step 1: Confirm RAGFlow's network exists under the expected name**

```bash
docker network ls --format '{{.Name}}' | grep ragflow
```

Expected: `docker_ragflow`. If the name differs, update `networks.ragflow.name` in the compose file before continuing.

- [ ] **Step 2: Start the fast tier**

```bash
docker compose -f opendataloader/docker-compose.opendataloader.yml up -d --build
docker compose -f opendataloader/docker-compose.opendataloader.yml ps
```

Expected: `odl-api` running and eventually `healthy`.

- [ ] **Step 3: Verify RAGFlow can reach it over the shared network**

This is the path that matters — a working `curl` from the host proves less.

```bash
docker exec docker-ragflow-cpu-1 sh -c 'curl -fsS http://odl-api:5060/health'
```

Expected: `{"status":"ok"}`

- [ ] **Step 4: Parse a real document through the service**

```bash
curl -fsS -X POST http://127.0.0.1:5060/file_parse \
  -F "file=@$HOME/Documents/CD-001567.pdf;type=application/pdf" \
  | python -c "import json,sys; d=json.load(sys.stdin); print('json_doc:', d['json_doc'] is not None); print('md chars:', len(d['md_text'] or ''))"
```

Expected: `json_doc: True` and a non-zero markdown length.

- [ ] **Step 5: Confirm the fast path was taken**

```bash
docker logs odl-api 2>&1 | grep '\[router\]' | tail -5
```

Expected: a `local tier` line. A born-digital drawing reaching the hybrid tier means the threshold needs tuning.

- [ ] **Step 6: Point RAGFlow at the service**

Add one line to `C:\Users\ZakOlech\Documents\Custom Programs\ragflow\docker\.env`:

```
OPENDATALOADER_APISERVER=http://odl-api:5060
```

Then restart RAGFlow:

```bash
docker restart docker-ragflow-cpu-1
```

- [ ] **Step 7: Confirm RAGFlow picked the variable up**

```bash
docker exec docker-ragflow-cpu-1 sh -c 'echo $OPENDATALOADER_APISERVER'
```

Expected: `http://odl-api:5060`

- [ ] **Step 8: Verify through RAGFlow's own client code**

```bash
docker exec docker-ragflow-cpu-1 sh -c 'cd /ragflow && python -m deepdoc.parser.opendataloader_parser'
```

Expected: `OpenDataLoader service reachable: True`

- [ ] **Step 9: Ingest a document in the RAGFlow UI**

Create a dataset, set **PDF parser** to **OpenDataLoader**, upload a Vault drawing and a standards PDF, and parse them. Confirm the documents reach `SUCCESS`, and that chunks are multiple sensible blocks rather than one giant one.

- [ ] **Step 10: Confirm retrieval returns those chunks**

Ask a question whose answer only appears in the uploaded documents and confirm the retrieved chunks cite them.

- [ ] **Step 11: Record the outcome**

Append a short "Verification" section to
`docs/superpowers/specs/2026-08-05-opendataloader-ragflow-service-design.md`
recording the date, the documents parsed, observed parse time per document
versus DeepDOC, whether bounding boxes and citations worked, and any threshold
tuning applied. This is the evidence that the service does what it was built to
do, and the baseline for spotting regressions after a RAGFlow upgrade.

- [ ] **Step 12: Commit**

```bash
git add docs/superpowers/specs/2026-08-05-opendataloader-ragflow-service-design.md
git commit -m "docs(opendataloader): record the live verification run"
```

---

## Optional follow-up: the OCR tier

Only worth doing when scanned documents are actually queued. The image is
multi-gigabyte and downloads models on first run.

- [ ] **Step 1: Start it**

```bash
docker compose -f opendataloader/docker-compose.opendataloader.yml --profile hybrid up -d --build
docker logs -f odl-hybrid
```

Expected: model downloads, then a listening message on port 5002. Be patient on
first start.

- [ ] **Step 2: Parse a scanned PDF**

```bash
curl -fsS -X POST http://127.0.0.1:5060/file_parse \
  -F "file=@/path/to/a/scanned.pdf;type=application/pdf" \
  | python -c "import json,sys; d=json.load(sys.stdin); print('md chars:', len(d['md_text'] or ''))"
```

Expected: a non-zero character count, and a `hybrid tier` line in
`docker logs odl-api`.

- [ ] **Step 3: Confirm the fallback works**

```bash
docker stop odl-hybrid
curl -fsS -X POST http://127.0.0.1:5060/file_parse \
  -F "file=@/path/to/a/scanned.pdf;type=application/pdf" -o /dev/null -w '%{http_code}\n'
```

Expected: `200`, not a 502 — `hybrid_fallback` degrades to a local parse when
the backend is down. Restart it with `docker start odl-hybrid`.
