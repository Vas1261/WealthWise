"""Thin HTTP client for the Twelve Data REST API (real integration; tests inject a mock transport).

The API key travels only in the `Authorization: apikey ...` header, never in a URL, so it cannot
appear in request logs or exception text. Every failure becomes a TwelveDataError carrying a
PriceIssue and a fixed message; provider messages, URLs and headers are never propagated.
"""

from decimal import Decimal

import httpx2
from pydantic import SecretStr

from app.domain.holdings import InvalidValue
from app.domain.market import PriceIssue, SymbolCandidate, parse_price

BASE_URL = "https://api.twelvedata.com"
TIMEOUT_SECONDS = 10.0
CANDIDATE_FIELDS = ("symbol", "instrument_name", "exchange", "mic_code", "country", "currency", "instrument_type")

_MESSAGES = {
    PriceIssue.NOT_CONFIGURED: "Twelve Data API key is not configured",
    PriceIssue.SERVICE_UNAVAILABLE: "Twelve Data is unreachable or returned a server error",
    PriceIssue.RATE_LIMITED: "Twelve Data rate limit reached",
    PriceIssue.CREDENTIALS_REJECTED: "Twelve Data rejected the configured API key",
    PriceIssue.NOT_ENTITLED: "The Twelve Data plan does not include this data",
    PriceIssue.SYMBOL_NOT_FOUND: "Twelve Data does not know this symbol",
    PriceIssue.INVALID_RESPONSE: "Twelve Data returned an unexpected response",
}


class TwelveDataError(Exception):
    def __init__(self, issue: PriceIssue) -> None:
        super().__init__(_MESSAGES[issue])
        self.issue = issue


def _issue_for(code: object, message: object) -> PriceIssue:
    """Map an HTTP status or a body-level error code (Twelve Data also reports errors with HTTP 200)."""
    if code == 429:
        return PriceIssue.RATE_LIMITED
    if code == 401:
        return PriceIssue.CREDENTIALS_REJECTED
    if code == 403:
        return PriceIssue.NOT_ENTITLED
    if code in (400, 404):
        # Symbols outside the plan come back as 404 "... available starting with the Grow plan".
        if isinstance(message, str) and "plan" in message.lower():
            return PriceIssue.NOT_ENTITLED
        return PriceIssue.SYMBOL_NOT_FOUND
    if isinstance(code, int) and 500 <= code < 600:
        return PriceIssue.SERVICE_UNAVAILABLE
    return PriceIssue.INVALID_RESPONSE


class TwelveDataClient:
    def __init__(
        self,
        api_key: SecretStr,
        *,
        transport: httpx2.BaseTransport | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        key = api_key.get_secret_value()
        if not key:
            raise TwelveDataError(PriceIssue.NOT_CONFIGURED)
        self._http = httpx2.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"apikey {key}"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def symbol_search(self, query: str) -> list[SymbolCandidate]:
        """GET /symbol_search: candidate listings for a ticker or ISIN (ISIN needs the Data add-on)."""
        body = self._get("/symbol_search", {"symbol": query, "show_plan": "true"})
        data = body.get("data")
        if not isinstance(data, list):
            raise TwelveDataError(PriceIssue.INVALID_RESPONSE)
        candidates = []
        for item in data:
            if not isinstance(item, dict) or not all(isinstance(item.get(f), str) for f in CANDIDATE_FIELDS):
                raise TwelveDataError(PriceIssue.INVALID_RESPONSE)
            candidates.append(SymbolCandidate(**{f: item[f] for f in CANDIDATE_FIELDS}))
        return candidates

    def price(self, symbol: str, mic_code: str) -> Decimal:
        """GET /price: the real-time or latest available price for one listing."""
        body = self._get("/price", {"symbol": symbol, "mic_code": mic_code})
        try:
            return parse_price(body.get("price"))
        except InvalidValue:
            raise TwelveDataError(PriceIssue.INVALID_RESPONSE) from None

    def _get(self, path: str, params: dict[str, str]) -> dict:
        try:
            response = self._http.get(path, params=params)
        except httpx2.TransportError:  # timeouts, connection and protocol errors
            raise TwelveDataError(PriceIssue.SERVICE_UNAVAILABLE) from None
        try:
            body = response.json()
        except ValueError:
            body = None
        message = body.get("message") if isinstance(body, dict) else None
        if response.status_code != 200:
            raise TwelveDataError(_issue_for(response.status_code, message))
        if not isinstance(body, dict):
            raise TwelveDataError(PriceIssue.INVALID_RESPONSE)
        if body.get("status") == "error":
            raise TwelveDataError(_issue_for(body.get("code"), message))
        return body
