"""Settings for the OpenDataLoader service.

Everything is read from the environment: the service only ever runs as a
container, so compose is the single place these are written.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    api_key: str  # ODL_API_KEY — bearer token RAGFlow must send; "" disables auth
    hybrid_url: str  # ODL_HYBRID_URL — base URL of the odl-hybrid sidecar
    hybrid_backend: str  # ODL_HYBRID_BACKEND — docling backend name passed through to odl-hybrid
    enable_hybrid: bool  # ODL_ENABLE_HYBRID — whether scanned pages may fall back to the hybrid OCR tier
    min_chars_per_page: int  # ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE — below this a page counts as scanned; 0 = never route to hybrid
    sample_pages: int  # ODL_TEXT_LAYER_SAMPLE_PAGES — number of pages sampled to judge text-layer quality
    max_concurrency: int  # ODL_MAX_CONCURRENCY — size of the asyncio.Semaphore gating concurrent parses
    timeout_seconds: int  # ODL_TIMEOUT — asyncio.wait_for timeout per request, in seconds


def _is_unset(raw: str | None) -> bool:
    # Compose's `VAR: "${VAR}"` with the host variable unset, and a bare
    # `VAR=` line in .env, both produce an empty string — that has to mean
    # "not supplied," the same as the key being absent entirely, not operator
    # error. Every helper below routes through here so they agree on that.
    return raw is None or not str(raw).strip()


def _str(env: Mapping[str, str], key: str, default: str) -> str:
    raw = env.get(key)
    return default if _is_unset(raw) else str(raw).strip()


def _int(env: Mapping[str, str], key: str, default: int, minimum: int = 1) -> int:
    # A typo in compose should degrade to the default, not crash the service on
    # import — the container would crash-loop with a stack trace nobody reads.
    # Silence on a clean start depends on every default already satisfying its
    # own minimum (50 >= 0, 5/4/540 >= 1) — a future field whose default sits
    # below its own minimum would warn on every startup though nothing here
    # was misconfigured.
    raw = env.get(key)
    if _is_unset(raw):
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        logger.warning("[config] %s=%r is not a number — using %s", key, raw, default)
        return default
    if value < minimum:
        # 0 or negative is never meaningful here and fails silently downstream:
        # Semaphore(0) never admits a request, wait_for(timeout=0) times out
        # instantly, and Semaphore(-1) raises at construction.
        logger.warning(
            "[config] %s=%r is below the minimum of %s — using %s", key, raw, minimum, default
        )
        return default
    return value


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if _is_unset(raw):
        return default
    normalized = str(raw).strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    # Unrecognised, non-empty input defaults to True for ODL_ENABLE_HYBRID, so
    # a silent fallback here would silently turn on the expensive OCR tier.
    logger.warning("[config] %s=%r is not a recognised boolean — using %s", key, raw, default)
    return default


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    return Settings(
        api_key=_str(env, "ODL_API_KEY", ""),
        hybrid_url=_str(env, "ODL_HYBRID_URL", "http://odl-hybrid:5002"),
        hybrid_backend=_str(env, "ODL_HYBRID_BACKEND", "docling-fast"),
        enable_hybrid=_bool(env, "ODL_ENABLE_HYBRID", True),
        min_chars_per_page=_int(env, "ODL_TEXT_LAYER_MIN_CHARS_PER_PAGE", 50, minimum=0),
        sample_pages=_int(env, "ODL_TEXT_LAYER_SAMPLE_PAGES", 5),
        max_concurrency=_int(env, "ODL_MAX_CONCURRENCY", 4),
        timeout_seconds=_int(env, "ODL_TIMEOUT", 540),
    )
