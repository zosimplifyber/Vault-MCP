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
