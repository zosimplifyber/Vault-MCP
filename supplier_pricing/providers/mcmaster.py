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
            import requests
            session = requests.Session()
            cert = self._cert
            if cert and str(cert).lower().endswith((".pfx", ".p12")):
                # McMaster issues a PKCS#12 client certificate. requests_pkcs12
                # attaches it (in memory) to every HTTPS request.
                from requests_pkcs12 import Pkcs12Adapter
                session.mount("https://", Pkcs12Adapter(
                    pkcs12_filename=cert, pkcs12_password=self._cert_password))
            elif cert:
                session.cert = cert     # already-PEM cert path or (cert, key) tuple
            self._client = session
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


# McMaster renders the price into a CSS-module <div> whose class starts with
# "_price_" (the hash suffix changes per deploy, so match the stable prefix).
# Confirmed live 2026-07-24: /1078A331/ -> "$7.08 each", no login required.
PRICE_SELECTOR = '[class*="_price_"]'


class McMasterBrowserProvider(PriceProvider):
    """Playwright fallback: read the public list price from the product page.

    McMaster shows list prices without a login, so no sign-in is required. The
    browser is launched once and reused across all get_price() calls (important
    for a bulk run and to keep Akamai cookies warm); call close() when done, or
    use it as a context manager.
    """
    vendor_key = "mcmaster"

    def __init__(self, *, user_data_dir: str = "", lead_time_days: int = 1,
                 headless: bool = True):
        self.user_data_dir = user_data_dir
        self.lead_time_days = lead_time_days
        self.headless = headless
        self._pw = None
        self._ctx = None
        self._browser = None
        self._page = None

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        if self.user_data_dir:
            self._ctx = self._pw.chromium.launch_persistent_context(
                self.user_data_dir, headless=self.headless)
            self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        else:
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._ctx = self._browser.new_context()
            self._page = self._ctx.new_page()
        return self._page

    def get_price(self, part_number: str, qty: int = 1) -> PriceResult:
        source = "mcmaster:web"
        url = f"https://www.mcmaster.com/{part_number}/"
        try:
            page = self._ensure_page()
        except Exception as exc:
            return _err(part_number, f"playwright unavailable: {exc}", source)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            price = None
            try:
                page.wait_for_selector(PRICE_SELECTOR, timeout=15000)
                loc = page.locator(PRICE_SELECTOR).first
                price = parse_price_from_text(loc.inner_text())
            except Exception:
                pass
            if price is None:                       # fallback: whole-page text
                price = parse_price_from_text(page.inner_text("body"))
            if price is None:
                return _err(part_number, "price not found on page", source)
            return PriceResult(
                part_number=part_number, vendor=VENDOR, unit_price=price,
                currency="USD", lead_time_days=self.lead_time_days,
                source=source, source_url=url, fetched_at=_utcnow_iso(),
            )
        except Exception as exc:
            return _err(part_number, str(exc), source)

    def close(self) -> None:
        for closer in (getattr(self._ctx, "close", None),
                       getattr(self._browser, "close", None),
                       getattr(self._pw, "stop", None)):
            try:
                if closer:
                    closer()
            except Exception:
                pass
        self._pw = self._ctx = self._browser = self._page = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _DisabledProvider(PriceProvider):
    """Returned when neither the API is configured nor scraping is allowed.

    Every lookup fails safely with an explanatory message — the tool never scrapes
    McMaster (ban risk) unless you opt in.
    """
    vendor_key = "mcmaster"

    def __init__(self, reason: str):
        self.reason = reason

    def get_price(self, part_number: str, qty: int = 1) -> PriceResult:
        return _err(part_number, self.reason, "mcmaster:disabled")


_NO_API_REASON = (
    "No McMaster API certificate configured. Browser scraping is DISABLED by "
    "default to protect your account from bans. Configure the official API "
    "(supplier_pricing.mcmaster.api_cert + api_cert_password + api_user + "
    "api_password), or pass --allow-scrape / set mcmaster.allow_scrape=true to "
    "use the browser fallback deliberately."
)


def make_mcmaster_provider(config: dict | None = None, *,
                           allow_scrape: bool = False) -> PriceProvider:
    """Choose a McMaster provider, safe by default.

    - API cert configured (mode auto/api) -> official API (no scraping).
    - mode "api" but no cert -> disabled (refuses to scrape).
    - no cert, scraping not allowed -> disabled (default; protects the account).
    - no cert, scraping explicitly allowed (flag/config/mode "browser") -> browser.
    """
    cfg = (config or {}).get("mcmaster", {}) if config else {}
    cert = cfg.get("api_cert")
    mode = (cfg.get("mode") or "auto").lower()

    if cert and mode in ("auto", "api"):
        return McMasterApiProvider(
            cert=cert, cert_password=cfg.get("api_cert_password", ""),
            user=cfg.get("api_user", ""), password=cfg.get("api_password", ""),
        )
    if mode == "api":
        return _DisabledProvider(
            "McMaster mode is 'api' but no api_cert is configured; refusing to scrape.")

    allow = allow_scrape or bool(cfg.get("allow_scrape")) or mode == "browser"
    if allow:
        return McMasterBrowserProvider(user_data_dir=cfg.get("user_data_dir", ""))
    return _DisabledProvider(_NO_API_REASON)
