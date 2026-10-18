"""Thin HTTP client for the Upstox market quote API (real integration; tests inject a mock transport).

Endpoint: GET /v3/market-quote/ltp?instrument_key=NSE_EQ|<ISIN>,BSE_EQ|<ISIN>,...
-> {"status": "success", "data": {"NSE_EQ:<SYMBOL>": {"last_price": 1391.0,
                                                       "instrument_token": "NSE_EQ|<ISIN>", ...}}}
An instrument key is the exchange segment plus the ISIN, so every holding with an ISIN can be asked for
directly, on both exchanges, many per request. A listing that does not exist is simply absent from `data`.

The token (an Upstox Analytics Token: read-only, long-lived) travels only in the `Authorization: Bearer`
header, never in a URL, so it cannot appear in request logs or exception text. Prices are parsed straight
from JSON into Decimal. Every failure becomes an UpstoxError carrying a PriceIssue and a fixed message;
provider messages, URLs and headers are never propagated.
"""

from collections.abc import Iterable
from decimal import Decimal

import httpx2
from pydantic import SecretStr

from app.domain.market import BSE, NSE, InvalidPrice, PriceIssue, Quote, parse_price

BASE_URL = "https://api.upstox.com"
LTP_PATH = "/v3/market-quote/ltp"
TIMEOUT_SECONDS = 10.0
MAX_KEYS_PER_REQUEST = 500  # Upstox's documented limit for one market quote request
SEGMENTS = {NSE: "NSE_EQ", BSE: "BSE_EQ"}
EXCHANGE_OF_SEGMENT = {segment: exchange for exchange, segment in SEGMENTS.items()}

_MESSAGES = {
    PriceIssue.NOT_CONFIGURED: "Upstox access token is not configured",
    PriceIssue.SERVICE_UNAVAILABLE: "Upstox is unreachable or returned a server error",
    PriceIssue.RATE_LIMITED: "Upstox rate limit reached",
    PriceIssue.CREDENTIALS_REJECTED: "Upstox rejected the configured access token",
    PriceIssue.NOT_ENTITLED: "Upstox refused access to market quotes for this token",
    PriceIssue.REQUEST_REJECTED: "Upstox rejected the request",
    PriceIssue.INVALID_RESPONSE: "Upstox returned an unexpected response",
}


class UpstoxError(Exception):
    def __init__(self, issue: PriceIssue) -> None:
        super().__init__(_MESSAGES[issue])
        self.issue = issue


def _issue_for(status_code: int) -> PriceIssue:
    if status_code == 401:
        return PriceIssue.CREDENTIALS_REJECTED
    if status_code == 403:  # e.g. the token's app has no access to this API, or an IP is not whitelisted
        return PriceIssue.NOT_ENTITLED
    if status_code == 429:
        return PriceIssue.RATE_LIMITED
    if 500 <= status_code < 600:
        return PriceIssue.SERVICE_UNAVAILABLE
    if 400 <= status_code < 500:
        return PriceIssue.REQUEST_REJECTED
    return PriceIssue.INVALID_RESPONSE


def instrument_keys(isin: str) -> list[str]:
    return [f"{SEGMENTS[exchange]}|{isin}" for exchange in (NSE, BSE)]


class UpstoxClient:
    def __init__(
        self,
        access_token: SecretStr,
        *,
        transport: httpx2.BaseTransport | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        token = access_token.get_secret_value()
        if not token:
            raise UpstoxError(PriceIssue.NOT_CONFIGURED)
        self._http = httpx2.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def last_prices(self, isins: Iterable[str]) -> dict[str, dict[str, Quote]]:
        """Last traded price of each ISIN on NSE and BSE: {isin: {exchange: Quote}}.

        An ISIN listed on neither exchange is absent from the result. Raises UpstoxError if any request fails.
        """
        keys = [key for isin in dict.fromkeys(isins) for key in instrument_keys(isin)]
        quotes: dict[str, dict[str, Quote]] = {}
        for start in range(0, len(keys), MAX_KEYS_PER_REQUEST):
            chunk = keys[start:start + MAX_KEYS_PER_REQUEST]
            for isin, quote in self._ltp(chunk, set(chunk)):
                quotes.setdefault(isin, {})[quote.exchange] = quote
        return quotes

    def _ltp(self, keys: list[str], requested: set[str]) -> list[tuple[str, Quote]]:
        body = self._get(LTP_PATH, {"instrument_key": ",".join(keys)})
        data = body.get("data")
        if not isinstance(data, dict):
            raise UpstoxError(PriceIssue.INVALID_RESPONSE)
        found = []
        for name, item in data.items():
            token = item.get("instrument_token") if isinstance(item, dict) else None
            if not isinstance(token, str) or token not in requested:
                raise UpstoxError(PriceIssue.INVALID_RESPONSE)
            segment, _, isin = token.partition("|")
            try:
                price = parse_price(item.get("last_price"))
            except InvalidPrice:
                raise UpstoxError(PriceIssue.INVALID_RESPONSE) from None
            symbol = name.partition(":")[2] if isinstance(name, str) else ""
            found.append((isin, Quote(EXCHANGE_OF_SEGMENT[segment], symbol or None, price)))
        return found

    def _get(self, path: str, params: dict[str, str]) -> dict:
        try:
            response = self._http.get(path, params=params)
        except httpx2.TransportError:  # timeouts, connection and protocol errors
            raise UpstoxError(PriceIssue.SERVICE_UNAVAILABLE) from None
        if response.status_code != 200:
            raise UpstoxError(_issue_for(response.status_code))
        try:
            body = response.json(parse_float=Decimal)
        except ValueError:
            raise UpstoxError(PriceIssue.INVALID_RESPONSE) from None
        if not isinstance(body, dict) or body.get("status") != "success":
            raise UpstoxError(PriceIssue.INVALID_RESPONSE)
        return body
