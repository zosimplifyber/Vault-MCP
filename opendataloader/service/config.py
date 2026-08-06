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
