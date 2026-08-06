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
    # One JVM thread per conversion — the service's own asyncio.Semaphore is
    # what bounds concurrency, not the engine; quiet suppresses engine output
    # that would otherwise interleave across concurrent requests.
    assert seen["threads"] == "1"
    assert seen["quiet"] is True


def test_the_hybrid_tier_passes_the_backend_url_and_fallback(monkeypatch):
    seen = _capture(monkeypatch, {"doc.json": '{"type": "document"}'})
    settings = load_settings({})
    run_convert(PDF, "doc.pdf", Tier.HYBRID, settings, {})
    assert seen["hybrid"] == "docling-fast"
    assert seen["hybrid_url"] == "http://odl-hybrid:5002"
    # 70% of the outer asyncio.wait_for budget, not the full budget: the
    # remaining 30% is what hybrid_fallback needs to actually run a local
    # parse. At the full budget the caller's own timeout would already have
    # abandoned the call by the moment hybrid times out, so fallback would
    # only ever fire when hybrid is down, never when it's merely slow.
    assert seen["hybrid_timeout"] == str(int(settings.timeout_seconds * 1000 * 0.7))
    # Without fallback, a stopped hybrid container turns every scan into an
    # error instead of a degraded-but-useful local parse.
    assert seen["hybrid_fallback"] is True


def test_reserved_options_cannot_override_the_tier_or_output_contract(monkeypatch):
    # `options` comes straight from HTTP form fields on an eventual
    # /file_parse request. Without this, a caller could redirect hybrid PDF
    # bytes to a host of their choosing (hybrid_url), defeat routing on a
    # HYBRID-tier request (hybrid), or silently drop the JSON output while
    # still getting a 200 back (format) — the exact silent degradation this
    # service exists to prevent.
    seen = _capture(monkeypatch, {"doc.json": "{}"})
    run_convert(
        PDF,
        "doc.pdf",
        Tier.HYBRID,
        load_settings({}),
        {"format": "markdown", "hybrid_url": "http://evil", "hybrid": "off"},
    )
    assert seen["format"] == "markdown,json"
    assert seen["hybrid_url"] == "http://odl-hybrid:5002"
    assert seen["hybrid"] == "docling-fast"


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
    with pytest.raises(ConvertError) as excinfo:
        run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})
    # Chained, not swallowed: quiet=True suppresses the engine's own
    # diagnostics, so the original exception is nearly all an operator gets.
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_the_temporary_directory_is_removed(monkeypatch):
    seen = _capture(monkeypatch, {"doc.json": "{}"})
    run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})
    # The whole per-request temp root, not just its "out" subdirectory — a
    # partial cleanup that only removed "out" would still pass a narrower
    # assertion here.
    assert not Path(seen["output_dir"]).parent.exists()


def test_the_temporary_directory_is_removed_even_on_failure(monkeypatch):
    seen = _capture(monkeypatch, {})
    with pytest.raises(ConvertError):
        run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})
    assert not Path(seen["output_dir"]).parent.exists()


def test_nested_output_files_are_still_found(monkeypatch):
    # image_output can land engine output in a subdirectory of output_dir;
    # this pins rglob (recursive) over glob (top-level only) as the
    # deliberate choice — nothing else in this suite would catch a
    # regression to a non-recursive glob.
    def fake_call(input_path, output_dir, **kwargs):
        nested = Path(output_dir, "doc")
        nested.mkdir()
        (nested / "doc.json").write_text('{"type": "document"}', encoding="utf-8")

    monkeypatch.setattr(convert_mod, "_call_convert", fake_call)
    result = run_convert(PDF, "doc.pdf", Tier.LOCAL, load_settings({}), {})
    assert result.json_doc == {"type": "document"}


# `Path(filename).name` alone does not defang every hostile filename: a name
# that *reduces* to a bare ".." (e.g. "..", "foo/..") comes back unchanged
# from pathlib rather than "", because pathlib treats ".." as a literal final
# component, not something to resolve. Joined below as `source / name`, that
# walks the write straight back out of the per-request `source` directory —
# so existence has to be checked from inside the fake call, before
# run_convert's `finally` deletes the whole temp tree.
@pytest.mark.parametrize(
    "hostile_name",
    ["..", "../..", "foo/..", "../../etc/passwd", "../../../evil.pdf"],
)
def test_a_traversal_filename_is_written_inside_the_source_directory(monkeypatch, hostile_name):
    written = {}

    def fake_call(input_path, output_dir, **kwargs):
        path = Path(input_path)
        written["parent_name"] = path.parent.name
        written["existed_at_call_time"] = path.exists()
        Path(output_dir, "doc.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(convert_mod, "_call_convert", fake_call)
    run_convert(PDF, hostile_name, Tier.LOCAL, load_settings({}), {})
    assert written["parent_name"] == "input"
    assert written["existed_at_call_time"]


def test_an_empty_or_dot_only_filename_still_produces_a_file(monkeypatch):
    existed = {}

    def fake_call(input_path, output_dir, **kwargs):
        existed["value"] = Path(input_path).exists()
        Path(output_dir, "doc.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(convert_mod, "_call_convert", fake_call)
    run_convert(PDF, "", Tier.LOCAL, load_settings({}), {})
    assert existed["value"]
