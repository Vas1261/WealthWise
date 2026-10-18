"""Market data rules as pure functions (no database, HTTP, or framework imports).

Prices come from Upstox, which identifies an Indian listing by exchange segment and ISIN
(`NSE_EQ|<ISIN>`, `BSE_EQ|<ISIN>`), so no symbol search is needed. Covers which listing to use
(NSE preferred, else BSE), price validation and cache freshness (M5/M6).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

NSE = "NSE"
BSE = "BSE"
PREFERRED_EXCHANGES = (NSE, BSE)  # NSE first, then BSE (decision M3)
INR = "INR"  # Upstox quotes Indian listings in rupees

FORCED_REFRESH_MIN_INTERVAL = timedelta(seconds=60)  # M6


class PriceStatus(StrEnum):
    FRESH = "fresh"  # fetched within the cache window
    STALE = "stale"  # last known price, older than the cache window, because a refresh failed
    UNAVAILABLE = "unavailable"  # no price could be obtained and none is cached
    NOT_SUPPORTED = "not_supported"  # never priced through the price service (mutual funds, M11)


class PriceIssue(StrEnum):
    """Why a price is stale, unavailable or unsupported. Never carries provider messages."""

    NOT_CONFIGURED = "not_configured"
    SERVICE_UNAVAILABLE = "service_unavailable"
    RATE_LIMITED = "rate_limited"
    CREDENTIALS_REJECTED = "credentials_rejected"
    NOT_ENTITLED = "not_entitled"
    REQUEST_REJECTED = "request_rejected"
    NOT_LISTED = "not_listed"
    INVALID_RESPONSE = "invalid_response"
    MISSING_ISIN = "missing_isin"
    MUTUAL_FUND = "mutual_fund"


@dataclass(frozen=True)
class Quote:
    """One exchange's last traded price for an ISIN."""

    exchange: str  # NSE | BSE
    trading_symbol: str | None  # the exchange's ticker as Upstox reports it, for reference only
    price: Decimal


@dataclass(frozen=True)
class PriceResult:
    status: PriceStatus
    price: Decimal | None = None
    as_of: datetime | None = None  # when WealthWise fetched the price ("last updated")
    currency: str | None = None
    issue: PriceIssue | None = None


class InvalidPrice(ValueError):
    pass


def choose_quote(quotes: dict[str, Quote]) -> Quote | None:
    """The NSE quote if there is one, else the BSE quote, else None (not listed on either)."""
    return next((quotes[exchange] for exchange in PREFERRED_EXCHANGES if exchange in quotes), None)


def parse_price(value: object) -> Decimal:
    """A provider price must be a JSON number (parsed as Decimal) that is finite and greater than zero."""
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise InvalidPrice("price is not a number")
    price = Decimal(value)
    if not price.is_finite() or price <= 0:
        raise InvalidPrice("price must be a finite number greater than zero")
    return price


def is_fresh(fetched_at: datetime, now: datetime, ttl: timedelta) -> bool:
    return now - fetched_at < ttl


def forced_refresh_allowed(fetched_at: datetime, now: datetime) -> bool:
    return now - fetched_at >= FORCED_REFRESH_MIN_INTERVAL
