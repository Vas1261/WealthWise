"""Foreign-exchange rules as pure functions (no database, HTTP, or framework imports).

A rate converts an amount in `base` into `quote`: amount_in_quote = amount_in_base x rate.
Rates are exact Decimals; same-currency conversion is exactly Decimal("1") and never needs a request.
"""

import re
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

ONE = Decimal(1)
CURRENCY_CODE = re.compile(r"[A-Z]{3}")


class FxStatus(StrEnum):
    IDENTITY = "identity"  # same currency: exactly 1, no request
    FRESH = "fresh"  # fetched within the cache window
    STALE = "stale"  # last good rate, older than the cache window, because a refresh failed
    HISTORICAL = "historical"  # the rate of a past date, pinned once and never changed (FR-20)
    UNAVAILABLE = "unavailable"  # no rate could be obtained and none is cached


class FxIssue(StrEnum):
    """Why a rate is stale or unavailable. Never carries upstream messages."""

    DISABLED = "disabled"
    INVALID_CURRENCY = "invalid_currency"
    UNSUPPORTED_CURRENCY = "unsupported_currency"
    SERVICE_UNAVAILABLE = "service_unavailable"
    RATE_LIMITED = "rate_limited"
    INVALID_RESPONSE = "invalid_response"
    NO_RATE_FOR_DATE = "no_rate_for_date"  # the provider has no rate on or shortly before a pinned date


USABLE_FX_STATUSES = (FxStatus.IDENTITY, FxStatus.FRESH, FxStatus.STALE, FxStatus.HISTORICAL)
# Days fetched before the earliest wanted date, so a date after a weekend or holiday finds a rate.
HISTORY_LOOKBACK = timedelta(days=10)


class InvalidRate(ValueError):
    pass


@dataclass(frozen=True)
class FxRate:
    base: str
    quote: str
    status: FxStatus
    rate: Decimal | None = None
    rate_date: date | None = None  # the provider's date for the rate
    fetched_at: datetime | None = None  # when WealthWise fetched it
    issue: FxIssue | None = None

    @property
    def usable(self) -> bool:
        return self.status in USABLE_FX_STATUSES and self.rate is not None


def is_currency_code(value: object) -> bool:
    """Strict ISO 4217 shape: exactly three upper-case ASCII letters."""
    return isinstance(value, str) and CURRENCY_CODE.fullmatch(value) is not None


def identity_rate(currency: str) -> FxRate:
    return FxRate(currency, currency, FxStatus.IDENTITY, rate=ONE)


def parse_rate(value: object) -> Decimal:
    """A provider rate must be a JSON number (parsed as Decimal) that is finite and greater than zero."""
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise InvalidRate("rate is not a number")
    rate = Decimal(value)
    if not rate.is_finite() or rate <= 0:
        raise InvalidRate("rate must be a finite number greater than zero")
    return rate


def is_fresh(fetched_at: datetime, now: datetime, ttl: timedelta) -> bool:
    return now - fetched_at < ttl


def rates_on_or_before(
    series: Sequence[tuple[date, Decimal]], wanted: Iterable[date]
) -> dict[date, tuple[Decimal, date]]:
    """For each wanted date, the rate of that date or else of the latest earlier date in the series:
    (rate, rate_date). Dates with no earlier rate in the series are left out."""
    ordered = sorted(series)
    days = [day for day, _ in ordered]
    found: dict[date, tuple[Decimal, date]] = {}
    for day in wanted:
        index = bisect_right(days, day)
        if index:
            rate_date, rate = ordered[index - 1]
            found[day] = (rate, rate_date)
    return found
