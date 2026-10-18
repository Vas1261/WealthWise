"""Market Service (spec 4.5): cached live prices for Indian equities from Upstox.

Market data is public, so the cache is global and these functions take no user id; user scoping
belongs to the callers (Portfolio Engine). The service never invents a price and never falls back
to a broker file's closing price: every result carries an explicit PriceStatus and, when not
fresh, a PriceIssue.

Upstox addresses a listing by exchange segment + ISIN, so there is no symbol lookup: every ISIN that
needs a price is asked for on NSE and BSE in one request (up to 250 ISINs per request), and the NSE
price is used when there is one, else the BSE price (decision M3). An ISIN on neither is NOT_LISTED.

Cache rules (decisions M5-M7):
- a price younger than the TTL (5-15 min, default 10) is served without a request;
- forced refresh bypasses the TTL but not a 60-second floor;
- a failed refresh serves the last known price as STALE with its timestamp, or UNAVAILABLE.
"""

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.holdings import ASSET_CLASS_MUTUAL_FUND
from app.domain.market import INR, PriceIssue, PriceResult, PriceStatus, choose_quote, forced_refresh_allowed, is_fresh
from app.integrations.upstox import UpstoxClient, UpstoxError
from app.repositories import market_repository
from app.repositories.market_repository import CachedPrice

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class InstrumentRef:
    """What the service needs to price one holding (from NormalizedHolding / Holding)."""

    isin: str | None
    asset_class: str


# Requests in this process take turns to check the cache and fetch what is missing. The dashboard loads
# net worth and the portfolio at the same moment; without this both would fetch the same prices. The one
# that waits finds the first one's prices in the cache and makes no request. Other worker processes are
# covered by _save_and_commit.
_fetch_lock = threading.Lock()


def default_client() -> UpstoxClient | None:
    """A client built from configuration, or None when no access token is configured."""
    try:
        return UpstoxClient(settings.upstox_analytics_token)
    except UpstoxError:
        return None


def get_prices(
    db: Session,
    instruments: Sequence[InstrumentRef],
    *,
    force_refresh: bool = False,
    client: UpstoxClient | None = None,
    now: datetime | None = None,
) -> list[PriceResult]:
    """One PriceResult per instrument, in order. Each distinct ISIN is priced once."""
    now = now or _now()
    isins = list(dict.fromkeys(
        ref.isin for ref in instruments if ref.isin and ref.asset_class != ASSET_CLASS_MUTUAL_FUND
    ))
    by_isin = _run(client, lambda c: _price_isins(db, isins, c, now, force_refresh)) if isins else {}

    results = []
    for ref in instruments:
        if ref.asset_class == ASSET_CLASS_MUTUAL_FUND:
            results.append(PriceResult(PriceStatus.NOT_SUPPORTED, issue=PriceIssue.MUTUAL_FUND))
        elif not ref.isin:
            results.append(PriceResult(PriceStatus.UNAVAILABLE, issue=PriceIssue.MISSING_ISIN))
        else:
            results.append(by_isin[ref.isin])
    return results


def _price_isins(
    db: Session, isins: list[str], client: UpstoxClient | None, now: datetime, force_refresh: bool
) -> dict[str, PriceResult]:
    ttl = timedelta(minutes=settings.market_price_ttl_minutes)
    with _fetch_lock:
        cached = market_repository.get_cached_prices(db, isins)
        results: dict[str, PriceResult] = {}
        due = []
        for isin in isins:
            price = cached.get(isin)
            if price is not None and is_fresh(price.fetched_at, now, ttl) and not (
                force_refresh and forced_refresh_allowed(price.fetched_at, now)
            ):
                results[isin] = _from_cache(price, PriceStatus.FRESH)
            else:
                due.append(isin)
        if not due:
            return results

        if client is None:
            failure: PriceIssue | None = PriceIssue.NOT_CONFIGURED
        else:
            try:
                quotes = client.last_prices(due)
            except UpstoxError as error:
                failure = error.issue
            else:
                failure = None
                listed = {isin: quote for isin in due if (quote := choose_quote(quotes.get(isin, {})))}
                saved = _save_and_commit(db, lambda: {
                    isin: market_repository.save_price(db, isin, q.exchange, q.trading_symbol, q.price, INR, now)
                    for isin, q in listed.items()
                })
                for isin in due:
                    results[isin] = (
                        _from_cache(saved[isin], PriceStatus.FRESH) if isin in saved
                        else _fallback(cached.get(isin), PriceIssue.NOT_LISTED)
                    )
                unlisted = len(due) - len(saved)
                if unlisted:
                    logger.warning("Upstox has no NSE or BSE price for %d of %d instruments", unlisted, len(due))
                return results

        logger.warning("Upstox price refresh failed for %d instruments: %s", len(due), failure.value)
        for isin in due:
            results[isin] = _fallback(cached.get(isin), failure)
        return results


def _fallback(cached: CachedPrice | None, issue: PriceIssue) -> PriceResult:
    """The last known price as STALE, or UNAVAILABLE when there is none."""
    if cached is not None:
        return _from_cache(cached, PriceStatus.STALE, issue)
    return PriceResult(PriceStatus.UNAVAILABLE, issue=issue)


def _save_and_commit(db: Session, save: Callable[[], T]) -> T:
    """Write cache rows and commit. Requests in other processes (several workers) are not held back by
    _fetch_lock, so two of them can both find no row and both insert it; the loser hits the unique
    constraint. Then the rows exist, so roll back and save once more, which updates them with this
    request's equally fresh values."""
    try:
        saved = save()
        db.commit()
    except IntegrityError:
        db.rollback()
        saved = save()
        db.commit()
    return saved


def _run(client: UpstoxClient | None, work: Callable[[UpstoxClient | None], T]) -> T:
    """Run with the given client, or with one built from configuration (closed afterwards)."""
    if client is not None:
        return work(client)
    owned = default_client()
    try:
        return work(owned)
    finally:
        if owned is not None:
            owned.close()


def _from_cache(cached: CachedPrice, status: PriceStatus, issue: PriceIssue | None = None) -> PriceResult:
    return PriceResult(status, price=cached.price, as_of=cached.fetched_at, currency=cached.currency, issue=issue)


def _now() -> datetime:
    return datetime.now(UTC)
