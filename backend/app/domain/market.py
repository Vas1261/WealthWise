"""Market data rules as pure functions (no database, HTTP, or framework imports).

Covers Twelve Data symbol selection (decisions M2/M3), price validation, cache freshness
(M5/M6), symbol-lookup retry (M8), and a rolling-minute request budget (rate guard).
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.domain.holdings import parse_positive_decimal

# Exchange preference: NSE first, then BSE (decision M3).
NSE_MIC = "XNSE"
BSE_MIC = "XBOM"
PREFERRED_MICS = (NSE_MIC, BSE_MIC)
INDIA = "India"
INR = "INR"

FORCED_REFRESH_MIN_INTERVAL = timedelta(seconds=60)  # M6
SYMBOL_RETRY_AFTER = timedelta(days=7)  # M8: not_found / ambiguous lookups are retried after this


class PriceStatus(StrEnum):
    FRESH = "fresh"  # fetched within the cache window
    STALE = "stale"  # last known price, older than the cache window, because a refresh failed
    UNAVAILABLE = "unavailable"  # no price could be obtained and none is cached
    NOT_SUPPORTED = "not_supported"  # never priced through Twelve Data (mutual funds, M11)


class PriceIssue(StrEnum):
    """Why a price is stale, unavailable or unsupported. Never carries provider messages."""

    NOT_CONFIGURED = "not_configured"
    SERVICE_UNAVAILABLE = "service_unavailable"
    RATE_LIMITED = "rate_limited"
    REQUEST_BUDGET_EXHAUSTED = "request_budget_exhausted"
    CREDENTIALS_REJECTED = "credentials_rejected"
    NOT_ENTITLED = "not_entitled"
    SYMBOL_NOT_FOUND = "symbol_not_found"
    SYMBOL_AMBIGUOUS = "symbol_ambiguous"
    INVALID_RESPONSE = "invalid_response"
    MISSING_ISIN = "missing_isin"
    MUTUAL_FUND = "mutual_fund"


class SymbolStatus(StrEnum):
    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class SymbolCandidate:
    symbol: str
    instrument_name: str
    exchange: str
    mic_code: str
    country: str
    currency: str
    instrument_type: str


@dataclass(frozen=True)
class SymbolChoice:
    status: SymbolStatus
    candidate: SymbolCandidate | None = None


@dataclass(frozen=True)
class PriceResult:
    status: PriceStatus
    price: Decimal | None = None
    as_of: datetime | None = None  # when WealthWise fetched the price ("last updated")
    currency: str | None = None
    issue: PriceIssue | None = None


def choose_candidate(candidates: list[SymbolCandidate], *, ticker: str | None = None) -> SymbolChoice:
    """Pick the Twelve Data listing for one security, never by name similarity.

    Eligible candidates are Indian (country India, currency INR) listings on NSE or BSE; when
    searching by ticker the symbol must also match exactly. NSE is preferred over BSE. Two or more
    distinct symbols on the deciding exchange are ambiguous rather than guessed.
    """
    eligible = [
        c
        for c in candidates
        if c.country == INDIA and c.currency == INR and c.mic_code in PREFERRED_MICS
        and (ticker is None or c.symbol == ticker)
    ]
    for mic in PREFERRED_MICS:
        on_exchange = {c.symbol: c for c in eligible if c.mic_code == mic}
        if len(on_exchange) > 1:
            return SymbolChoice(SymbolStatus.AMBIGUOUS)
        if on_exchange:
            return SymbolChoice(SymbolStatus.RESOLVED, next(iter(on_exchange.values())))
    return SymbolChoice(SymbolStatus.NOT_FOUND)


def parse_price(value: object) -> Decimal:
    """A provider price must be a finite number greater than zero (raises InvalidValue otherwise)."""
    return parse_positive_decimal(value)


def is_fresh(fetched_at: datetime, now: datetime, ttl: timedelta) -> bool:
    return now - fetched_at < ttl


def forced_refresh_allowed(fetched_at: datetime, now: datetime) -> bool:
    return now - fetched_at >= FORCED_REFRESH_MIN_INTERVAL


def symbol_lookup_due(status: SymbolStatus, checked_at: datetime, now: datetime) -> bool:
    """Resolved mappings are permanent; failed lookups are retried after SYMBOL_RETRY_AFTER."""
    return status != SymbolStatus.RESOLVED and now - checked_at >= SYMBOL_RETRY_AFTER


class RequestBudget:
    """At most `limit` provider requests in any rolling `window` (default one minute)."""

    def __init__(self, limit: int, window: timedelta = timedelta(minutes=1)) -> None:
        self.limit = limit
        self.window = window
        self._spent: deque[datetime] = deque()

    def try_spend(self, now: datetime) -> bool:
        while self._spent and now - self._spent[0] >= self.window:
            self._spent.popleft()
        if len(self._spent) >= self.limit:
            return False
        self._spent.append(now)
        return True
