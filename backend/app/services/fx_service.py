"""FX Service (Module 8): Frankfurter rates with an in-process cache (decisions F3, F5, F8).

- Same-currency conversion is exactly Decimal("1") and never makes a request.
- Each (base, quote) pair is cached independently for FX_CACHE_TTL_MINUTES (default 60) and always
  requested in the needed direction (inverses are never derived locally).
- A failed refresh serves the last good rate as STALE with its timestamp (no age limit within the
  process); with nothing cached the result is UNAVAILABLE with an FxIssue. Rates are never invented.
- With FX_ENABLED=false no request is made (DISABLED).
The cache lives in this process only: it is lost on restart and not shared between processes.
"""

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TypeVar

from app.core.config import settings
from app.domain.fx import FxIssue, FxRate, FxStatus, identity_rate, is_currency_code, is_fresh
from app.integrations.frankfurter import FrankfurterClient, FrankfurterError

logger = logging.getLogger(__name__)

T = TypeVar("T")
CURRENCY_LIST_TTL = timedelta(hours=24)


class FxUnavailableError(Exception):
    """Frankfurter could not be reached and nothing usable is cached."""

    def __init__(self, issue: FxIssue) -> None:
        super().__init__(issue.value)
        self.issue = issue


@dataclass(frozen=True)
class _CachedRate:
    rate: Decimal
    rate_date: date
    fetched_at: datetime


_rates: dict[tuple[str, str], _CachedRate] = {}
_currencies: tuple[datetime, frozenset[str]] | None = None
_lock = threading.Lock()


def clear_cache() -> None:
    global _currencies
    with _lock:
        _rates.clear()
        _currencies = None


def default_client() -> FrankfurterClient | None:
    """A client from configuration, or None when FX is disabled."""
    return FrankfurterClient(settings.frankfurter_base_url) if settings.fx_enabled else None


def get_rates(
    bases: Iterable[str], quote: str, *, client: FrankfurterClient | None = None, now: datetime | None = None
) -> dict[str, FxRate]:
    """Rate from each base currency into `quote`, keyed by base. One request per uncached pair."""
    now = now or datetime.now(UTC)
    return _run(client, lambda c: {base: _rate(base, quote, c, now) for base in dict.fromkeys(bases)})


def get_rate(base: str, quote: str, *, client: FrankfurterClient | None = None, now: datetime | None = None) -> FxRate:
    return get_rates([base], quote, client=client, now=now)[base]


def supported_currencies(*, client: FrankfurterClient | None = None, now: datetime | None = None) -> frozenset[str]:
    """Codes Frankfurter supports (cached 24 h; a failed refresh keeps the last list).

    Raises FxUnavailableError when no list can be obtained.
    """
    global _currencies
    now = now or datetime.now(UTC)
    with _lock:
        cached = _currencies
    if cached is not None and now - cached[0] < CURRENCY_LIST_TTL:
        return cached[1]

    def fetch(c: FrankfurterClient | None) -> frozenset[str]:
        global _currencies
        if c is None:
            raise FxUnavailableError(FxIssue.DISABLED)
        try:
            codes = frozenset(c.currencies())
        except FrankfurterError as error:
            logger.warning("Frankfurter currency list refresh failed: %s", error.issue.value)
            if cached is not None:
                return cached[1]
            raise FxUnavailableError(error.issue) from None
        with _lock:
            _currencies = (now, codes)
        return codes

    return _run(client, fetch)


def _rate(base: str, quote: str, client: FrankfurterClient | None, now: datetime) -> FxRate:
    if not (is_currency_code(base) and is_currency_code(quote)):
        return FxRate(base, quote, FxStatus.UNAVAILABLE, issue=FxIssue.INVALID_CURRENCY)
    if base == quote:
        return identity_rate(base)

    with _lock:
        cached = _rates.get((base, quote))
    ttl = timedelta(minutes=settings.fx_cache_ttl_minutes)
    if cached is not None and is_fresh(cached.fetched_at, now, ttl):
        return _from_cache(base, quote, cached, FxStatus.FRESH)

    if client is None:
        issue = FxIssue.DISABLED
    else:
        try:
            fetched = client.rate(base, quote)
        except FrankfurterError as error:
            issue = error.issue
        else:
            entry = _CachedRate(fetched.rate, fetched.rate_date, now)
            with _lock:
                _rates[(base, quote)] = entry
            return _from_cache(base, quote, entry, FxStatus.FRESH)

    logger.warning("FX rate refresh failed for %s/%s: %s", base, quote, issue.value)
    if cached is not None:
        return _from_cache(base, quote, cached, FxStatus.STALE, issue)
    return FxRate(base, quote, FxStatus.UNAVAILABLE, issue=issue)


def _from_cache(
    base: str, quote: str, cached: _CachedRate, status: FxStatus, issue: FxIssue | None = None
) -> FxRate:
    return FxRate(base, quote, status, rate=cached.rate, rate_date=cached.rate_date, fetched_at=cached.fetched_at,
                  issue=issue)


def _run(client: FrankfurterClient | None, work: Callable[[FrankfurterClient | None], T]) -> T:
    """Run with the given client, or with one built from configuration (closed afterwards)."""
    if client is not None:
        return work(client)
    owned = default_client()
    try:
        return work(owned)
    finally:
        if owned is not None:
            owned.close()
