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
from pathlib import Path, PurePosixPath
from typing import Any

from .config import Settings
from .normalize import normalize_tables
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


def _safe_filename(filename: str) -> str:
    """Reduce an untrusted upload filename to a bare, harmless basename.

    `Path(filename).name` alone is not enough. It correctly strips a leading
    "../../" (the traversal segments become the path's parent, not its name),
    but a filename that *reduces* to a bare ".." — e.g. "..", "foo/..", or
    "../.." — comes back as ".." unchanged, since pathlib treats it as the
    final literal component rather than resolving it. That string then walks
    right back out of `source` when joined below (`source / ".."` is
    `source`'s parent). A single "." collapses to "" via the same mechanism
    and is already handled by the empty-name fallback.

    We also normalize backslashes before splitting: the service runs on Linux
    in the container, where `pathlib.Path` is `PosixPath` and a literal
    backslash is not a separator, so `Path("..\\..\\evil.pdf").name` would
    return the whole string as one (harmless but confusing) filename there —
    while on this Windows dev host, `WindowsPath` treats it as a separator
    and strips it, masking the platform difference in tests. Normalizing
    first makes the result identical on both.
    """
    normalized = filename.replace("\\", "/")
    name = PurePosixPath(normalized).name
    if name in ("", ".", ".."):
        return "input.pdf"
    return name


def _read_first(output_dir: Path, suffix: str) -> str | None:
    # Glob rather than assume a filename: the engine derives the stem from the
    # input and we would rather not depend on that rule. Sorting makes the
    # choice deterministic (rglob order is otherwise filesystem-dependent),
    # but "alphabetically first" is still a guess at "the main document" if
    # the engine ever emits more than one file per suffix — log it so that
    # guess is visible instead of silently returning page-001 every time.
    matches = sorted(output_dir.rglob(f"*{suffix}"))
    if len(matches) > 1:
        logger.warning(
            "[convert] %d %s candidates, using %s", len(matches), suffix, matches[0]
        )
    for path in matches:
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            # The only fully silent path in this module otherwise sits upstream
            # of the least useful error: without this, a file the engine wrote
            # but we can't read surfaces as "conversion produced no usable
            # output", which blames the engine for what is actually a
            # filesystem fault on our side.
            logger.warning("[convert] could not read %s: %s", path, exc)
            continue
        if text:
            return text
    return None


# The service's own contract, not something a caller-supplied form field may
# redirect: `format` is what makes JSON (the real output) come back at all,
# the hybrid_* keys and `threads` encode the tier decision this module just
# made. `options` may tune the engine (sanitize, image_output) — not steer it
# to a different endpoint or silently drop the structured output while still
# returning 200. There is no consumer yet (app.py, Task 4) that would
# whitelist request fields before they reach here, so this module has to
# defend its own invariants rather than assume one will exist.
_RESERVED_KWARGS = frozenset(
    {"format", "hybrid", "hybrid_url", "hybrid_timeout", "hybrid_fallback", "threads"}
)


def run_convert(
    pdf_bytes: bytes,
    filename: str,
    tier: Tier,
    settings: Settings,
    options: dict[str, Any],
) -> ConvertResult:
    workdir = Path(tempfile.mkdtemp(prefix="odl-"))
    try:
        source = workdir / "input"
        source.mkdir()
        pdf_path = source / _safe_filename(filename)
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
            # Deliberately less than the caller's full budget: the remaining
            # time is what hybrid_fallback needs to produce a local parse. At
            # the full budget, the outer asyncio.wait_for(timeout_seconds)
            # around this whole call has already given up by the moment
            # hybrid times out, so fallback only ever fires when hybrid is
            # down (fails fast) and never when it is merely slow — the more
            # common failure on a CPU-only host.
            kwargs["hybrid_timeout"] = str(int(settings.timeout_seconds * 1000 * 0.7))
            # Degrade to a local parse when the hybrid container is stopped
            # or too slow to answer within its share of the budget above.
            kwargs["hybrid_fallback"] = True
        else:
            kwargs["hybrid"] = "off"

        overridden = _RESERVED_KWARGS & options.keys()
        for key in sorted(overridden):
            logger.warning("[convert] ignoring caller override of %r", key)
        kwargs.update({k: v for k, v in options.items() if k not in _RESERVED_KWARGS})

        # The wall-clock timeout is enforced by the caller (app.py wraps this in
        # asyncio.wait_for); the JVM offers no cancellation hook of its own.
        try:
            _call_convert(str(pdf_path), str(out), **kwargs)
        except Exception as exc:
            # quiet=True above suppresses the engine's own diagnostics, so this
            # string is nearly all an operator gets — several exception types
            # (e.g. a bare RuntimeError with no args) stringify to "", which
            # would otherwise leave "conversion failed: " and nothing else.
            raise ConvertError(f"conversion failed: {type(exc).__name__}: {exc}") from exc

        raw_json = _read_first(out, ".json")
        md_text = _read_first(out, ".md")

        json_doc = None
        if raw_json:
            try:
                json_doc = json.loads(raw_json)
            except ValueError as exc:
                logger.warning("[convert] output JSON for %s was unreadable: %s", filename, exc)

        if json_doc is not None:
            # Text passes through untouched, but tables do not survive the trip
            # into RAGFlow unaided — measured, its converter found zero tables
            # in a real 4x4 table and scattered the values across sixteen
            # one-word sections. See normalize.py for the two schema
            # mismatches behind that.
            json_doc = normalize_tables(json_doc)

        if json_doc is None and not md_text:
            raise ConvertError("conversion produced no usable output")
        if json_doc is None:
            logger.warning(
                "[convert] no JSON for %s — RAGFlow will treat the markdown as a "
                "single section, losing chunk boundaries and citations",
                filename,
            )

        # workdir (and any image sidecars the engine wrote alongside the JSON,
        # when image_output != "none") is removed in `finally` below — file
        # paths the engine embedded inside json_doc/md_text will dangle. That
        # is by design: RAGFlow asked for text and structure back, not files.
        return ConvertResult(json_doc=json_doc, md_text=md_text)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if workdir.exists():
            # rmtree(ignore_errors=True) fails silently — in a long-running
            # container that can leak temp directories until the disk fills,
            # with nothing in the logs to explain why.
            logger.warning("[convert] temp dir survived removal: %s", workdir)
