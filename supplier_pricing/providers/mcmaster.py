"""McMaster-Carr price providers.

Two implementations, same interface:

* ``McMasterApiProvider`` — the official Product Information API. Auth is a client
  TLS certificate (.pfx) + a login that returns a 24h bearer token. A part must be
  *subscribed* (``PUT /v1/products``) before ``GET /v1/products/{pn}/price`` works.
  Preferred when a cert is configured.
* ``McMasterBrowserProvider`` — Playwright fallback that reads the list price from
  the public product page using the owner's logged-in session. Used when no API
  cert is available. (Selectors need one live confirmation — see README.)

McMaster does not expose lead time; per owner decision every result uses
``lead_time_days = 1``.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..models import PriceBreak, PriceResult
from .base import PriceProvider

API_BASE = "https://api.mcmaster.com/v1"
VENDOR = "McMaster-Carr"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _err(part_number: str, message: str, source: str) -> PriceResult:
    return PriceResult(part_number=part_number, vendor=VENDOR, unit_price=None,
                       lead_time_days=1, source=source, error=message,
                       fetched_at=_utcnow_iso())


def parse_price_from_text(text: str) -> float | None:
    """Pull the first dollar amount out of free text ("Each $3.46" -> 3.46)."""
    if not text:
        return None
    m = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _select_unit_price(breaks: list[PriceBreak], qty: int) -> float:
    """Pick the tier whose min_qty is the largest <= qty (else the smallest)."""
    eligible = [b for b in breaks if b.min_qty <= qty]
    if eligible:
        return max(eligible, key=lambda b: b.min_qty).unit_price
    return min(breaks, key=lambda b: b.min_qty).unit_price


class McMasterApiProvider(PriceProvider):
    vendor_key = "mcmaster"

    def __init__(self, *, cert=None, cert_password: str = "", user: str = "",
                 password: str = "", client=None, lead_time_days: int = 1):
        self._cert = cert
        self._cert_password = cert_password
        self._user = user
        self._password = password
        self._client = client            # injected httpx.Client-shaped object (tests)
        self._token: str | None = None
        self.lead_time_days = lead_time_days

    # -- http -------------------------------------------------------------
    def _http(self):
        if self._client is None:
            import httpx
            # PKCS#12 client cert support: prefer requests-pkcs12-style loading via
            # httpx's SSLContext. Kept lazy so importing this module is cheap.
            self._client = httpx.Client(timeout=30, cert=self._cert)
        return self._client

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}",
                "Accept": "application/json"}

    def _ensure_login(self) -> None:
        if self._token:
            return
        resp = self._http().post(f"{API_BASE}/login",
                                 json={"UserName": self._user,
                                       "Password": self._password},
                                 headers={"Accept": "application/json"})
        if resp.status_code >= 400:
            raise RuntimeError(f"login HTTP {resp.status_code}: {resp.text[:120]}")
        token = (resp.json() or {}).get("AuthToken")
        if not token:
            raise RuntimeError("login response had no AuthToken")
        self._token = token

    def _subscribe(self, part_number: str) -> None:
        # Best-effort: an already-subscribed part (or a cap) is not fatal — the
        # price GET will still work / will report the real problem.
        try:
            self._http().put(f"{API_BASE}/products",
                             json={"URL": f"https://mcmaster.com/_{part_number}_"},
                             headers=self._auth_headers())
        except Exception:
            pass

    # -- api --------------------------------------------------------------
    def get_price(self, part_number: str, qty: int = 1) -> PriceResult:
        source = "mcmaster:api"
        try:
            self._ensure_login()
            self._subscribe(part_number)
            resp = self._http().get(f"{API_BASE}/products/{part_number}/price",
                                    headers=self._auth_headers())
            if resp.status_code >= 400:
                return _err(part_number, f"price HTTP {resp.status_code}", source)
            data = resp.json() or []
            breaks = sorted(
                (PriceBreak(min_qty=int(d["MinimumQuantity"]),
                            unit_price=float(d["Amount"])) for d in data),
                key=lambda b: b.min_qty,
            )
            if not breaks:
                return _err(part_number, "no price returned", source)
            return PriceResult(
                part_number=part_number, vendor=VENDOR,
                unit_price=_select_unit_price(breaks, qty),
                currency="USD", price_breaks=breaks,
                lead_time_days=self.lead_time_days, source=source,
                source_url=f"https://www.mcmaster.com/{part_number}/",
                fetched_at=_utcnow_iso(), raw={"price": data},
            )
        except Exception as exc:  # never raise to the orchestrator
            return _err(part_number, str(exc), source)


class McMasterBrowserProvider(PriceProvider):
    """Playwright fallback: read the public list price with the owner's session.

    NOTE: the price selector is best-effort and needs one live confirmation; until
    then get_price returns an error result rather than a fabricated price.
    """
    vendor_key = "mcmaster"

    def __init__(self, *, user_data_dir: str = "", lead_time_days: int = 1,
                 headless: bool = True):
        self.user_data_dir = user_data_dir
        self.lead_time_days = lead_time_days
        self.headless = headless

    def get_price(self, part_number: str, qty: int = 1) -> PriceResult:
        source = "mcmaster:web"
        url = f"https://www.mcmaster.com/{part_number}/"
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            return _err(part_number, f"playwright unavailable: {exc}", source)
        try:
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(
                    self.user_data_dir or ".mcmaster-profile",
                    headless=self.headless,
                )
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                text = page.inner_text("body")
                ctx.close()
            price = parse_price_from_text(text)
            if price is None:
                return _err(part_number, "price not found on page (confirm selector)",
                            source)
            return PriceResult(
                part_number=part_number, vendor=VENDOR, unit_price=price,
                currency="USD", lead_time_days=self.lead_time_days,
                source=source, source_url=url, fetched_at=_utcnow_iso(),
            )
        except Exception as exc:
            return _err(part_number, str(exc), source)


def make_mcmaster_provider(config: dict | None = None) -> PriceProvider:
    """Pick the API provider when a cert is configured, else the browser fallback."""
    cfg = (config or {}).get("mcmaster", {}) if config else {}
    cert = cfg.get("api_cert")
    if cert:
        cert_tuple = cert
        if cfg.get("api_cert_password"):
            # httpx accepts (certfile, keyfile, password) — pass through as given.
            cert_tuple = cert
        return McMasterApiProvider(
            cert=cert_tuple, cert_password=cfg.get("api_cert_password", ""),
            user=cfg.get("api_user", ""), password=cfg.get("api_password", ""),
        )
    return McMasterBrowserProvider(user_data_dir=cfg.get("user_data_dir", ""))
