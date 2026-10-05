"""Thin HTTP client for the Frankfurter FX API v2 (real integration; tests inject a mock transport).

No API key exists. Endpoints used:
- GET /v2/rate/{base}/{quote} -> {"date": "YYYY-MM-DD", "base": "USD", "quote": "INR", "rate": 96.14}
- GET /v2/currencies          -> [{"iso_code": "AED", ...}, ...]
Rates are parsed straight from JSON into Decimal (never float). Every failure becomes a
FrankfurterError carrying an FxIssue and a fixed message; upstream messages are never propagated.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx2

from app.domain.fx import FxIssue, InvalidRate, is_currency_code, parse_rate

DEFAULT_BASE_URL = "https://api.frankfurter.dev"
TIMEOUT_SECONDS = 10.0

_MESSAGES = {
    FxIssue.INVALID_CURRENCY: "Currency codes must be three upper-case letters",
    FxIssue.UNSUPPORTED_CURRENCY: "Frankfurter does not support this currency",
    FxIssue.SERVICE_UNAVAILABLE: "Frankfurter is unreachable or returned a server error",
    FxIssue.RATE_LIMITED: "Frankfurter rate limit reached",
    FxIssue.INVALID_RESPONSE: "Frankfurter returned an unexpected response",
}


class FrankfurterError(Exception):
    def __init__(self, issue: FxIssue) -> None:
        super().__init__(_MESSAGES[issue])
        self.issue = issue


@dataclass(frozen=True)
class Quote:
    base: str
    quote: str
    rate: Decimal
    rate_date: date


class FrankfurterClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        transport: httpx2.BaseTransport | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._http = httpx2.Client(base_url=base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def rate(self, base: str, quote: str) -> Quote:
        """GET /v2/rate/{base}/{quote}: units of `quote` per one `base`."""
        if not (is_currency_code(base) and is_currency_code(quote)):
            raise FrankfurterError(FxIssue.INVALID_CURRENCY)
        body = self._get(f"/v2/rate/{base}/{quote}")
        if not isinstance(body, dict) or body.get("base") != base or body.get("quote") != quote:
            raise FrankfurterError(FxIssue.INVALID_RESPONSE)
        try:
            rate = parse_rate(body.get("rate"))
            rate_date = date.fromisoformat(body["date"]) if isinstance(body.get("date"), str) else None
        except (InvalidRate, ValueError):
            raise FrankfurterError(FxIssue.INVALID_RESPONSE) from None
        if rate_date is None:
            raise FrankfurterError(FxIssue.INVALID_RESPONSE)
        return Quote(base, quote, rate, rate_date)

    def currencies(self) -> set[str]:
        """GET /v2/currencies: the ISO codes Frankfurter currently supports."""
        body = self._get("/v2/currencies")
        if not isinstance(body, list) or not body:
            raise FrankfurterError(FxIssue.INVALID_RESPONSE)
        codes = {item.get("iso_code") for item in body if isinstance(item, dict)}
        if len(codes) != len(body) or not all(is_currency_code(code) for code in codes):
            raise FrankfurterError(FxIssue.INVALID_RESPONSE)
        return {code for code in codes if isinstance(code, str)}

    def _get(self, path: str) -> object:
        try:
            response = self._http.get(path)
        except httpx2.TransportError:  # timeouts, connection and protocol errors
            raise FrankfurterError(FxIssue.SERVICE_UNAVAILABLE) from None
        if response.status_code == 422:
            raise FrankfurterError(FxIssue.UNSUPPORTED_CURRENCY)
        if response.status_code == 429:
            raise FrankfurterError(FxIssue.RATE_LIMITED)
        if 500 <= response.status_code < 600:
            raise FrankfurterError(FxIssue.SERVICE_UNAVAILABLE)
        if response.status_code != 200:
            raise FrankfurterError(FxIssue.INVALID_RESPONSE)
        try:
            return response.json(parse_float=Decimal)
        except ValueError:
            raise FrankfurterError(FxIssue.INVALID_RESPONSE) from None
