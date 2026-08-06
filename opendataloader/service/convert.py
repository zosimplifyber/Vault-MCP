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
    # the engine ever emits more than one file per suffix — see the report.
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
