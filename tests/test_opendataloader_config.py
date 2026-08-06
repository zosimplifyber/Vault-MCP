"""The service is configured entirely from the environment, because it runs as
a container and compose is the only place its settings are written."""
import dataclasses

import pytest

from opendataloader.service.config import load_settings


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


def test_zero_or_negative_falls_back_to_the_default_for_fields_with_a_floor():
    # Semaphore(0) never admits a request, Semaphore(-1) raises at construction,
    # and wait_for(timeout=0) times out instantly — none of these are usable,
    # so they must not survive config loading. Expected values are hardcoded
    # rather than read back from load_settings({}) so a wrong default can't
    # hide behind comparing the same code path to itself.
    fields = {
        "ODL_TEXT_LAYER_SAMPLE_PAGES": ("sample_pages", 5),
        "ODL_MAX_CONCURRENCY": ("max_concurrency", 4),
        "ODL_TIMEOUT": ("timeout_seconds", 540),
    }
    for env_key, (attr, expected_default) in fields.items():
        for value in ("0", "-1"):
            s = load_settings({env_key: value})
            assert getattr(s, attr) == expected_default


def test_min_chars_per_page_allows_zero_but_not_negative():
    # Zero legitimately means "never treat a page as scanned" — unlike the
    # other integer fields, it must be preserved rather than treated as unset.
    assert load_settings({"ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE": "0"}).min_chars_per_page == 0
    assert load_settings({"ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE": "-1"}).min_chars_per_page == 50


def test_empty_string_is_treated_as_unset_for_every_field_type():
    # Compose's `VAR: "${VAR}"` with the host variable unset, and a bare
    # `VAR=` line in .env, both produce an empty string — that has to mean
    # "not supplied" for every field type, not just the string ones.
    s = load_settings({
        "ODL_HYBRID_BACKEND": "",
        "ODL_ENABLE_HYBRID": "",
        "ODL_MAX_CONCURRENCY": "",
    })
    assert s.hybrid_backend == "docling-fast"
    assert s.enable_hybrid is True
    assert s.max_concurrency == 4


def test_none_value_is_treated_as_unset_for_every_field_type():
    # An explicit None in the env mapping (as opposed to an absent key) must
    # resolve to the default too, the same as it does for the string fields.
    s = load_settings({
        "ODL_HYBRID_BACKEND": None,
        "ODL_ENABLE_HYBRID": None,
        "ODL_MAX_CONCURRENCY": None,
    })
    assert s.hybrid_backend == "docling-fast"
    assert s.enable_hybrid is True
    assert s.max_concurrency == 4


def test_hybrid_url_empty_string_uses_the_default_not_a_blank_url():
    # Pinning a deliberate behaviour change: the first hardening commit
    # preserved "" here as a literal blank URL. This commit makes "" mean
    # "not supplied," consistent with every other field.
    assert load_settings({"ODL_HYBRID_URL": ""}).hybrid_url == "http://odl-hybrid:5002"


def test_a_clean_load_emits_no_warnings(caplog):
    with caplog.at_level("WARNING", logger="opendataloader.service.config"):
        load_settings({})
    assert caplog.records == []


def test_a_malformed_number_logs_a_warning(caplog):
    with caplog.at_level("WARNING", logger="opendataloader.service.config"):
        load_settings({"ODL_MAX_CONCURRENCY": "lots"})
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_empty_string_does_not_log_a_warning(caplog):
    # Same defect class as the malformed-input warning, just moved: a
    # correctly configured container must not warn about settings nobody
    # misconfigured.
    with caplog.at_level("WARNING", logger="opendataloader.service.config"):
        load_settings({
            "ODL_HYBRID_BACKEND": "",
            "ODL_ENABLE_HYBRID": "",
            "ODL_MAX_CONCURRENCY": "",
            "ODL_TIMEOUT": "",
        })
    assert caplog.records == []


def test_settings_are_frozen():
    s = load_settings({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.api_key = "mutated"


def test_api_key_is_stripped():
    # Compose files pick up trailing whitespace surprisingly often.
    assert load_settings({"ODL_API_KEY": "  secret  "}).api_key == "secret"


def test_hybrid_url_and_backend_are_stripped():
    s = load_settings({
        "ODL_HYBRID_URL": "  http://elsewhere:5002  ",
        "ODL_HYBRID_BACKEND": "  docling-full  ",
    })
    assert s.hybrid_url == "http://elsewhere:5002"
    assert s.hybrid_backend == "docling-full"


def test_integer_values_are_stripped():
    assert load_settings({"ODL_MAX_CONCURRENCY": " 8 "}).max_concurrency == 8


def test_load_settings_reads_the_real_environment_when_no_mapping_is_given(monkeypatch):
    # This is the branch create_app() uses at module scope (Task 4) — every
    # other test in this file passes an explicit dict and never exercises it.
    monkeypatch.setenv("ODL_API_KEY", "from-os-environ")
    assert load_settings().api_key == "from-os-environ"
