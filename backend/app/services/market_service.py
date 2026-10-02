"""Market Service (spec 4.5): ISIN -> Twelve Data symbol resolution and cached live prices.

Market data is public, so caches are global and these functions take no user id; user scoping
belongs to the callers (Portfolio Engine). The service never invents a price and never falls back
to a broker file's closing price: every result carries an explicit PriceStatus and, when not
fresh, a PriceIssue.

Cache rules (decisions M5-M8):
- a price younger than the TTL (5-15 min, default 10) is served without a request;
- forced refresh bypasses the TTL but not a 60-second floor;
- a failed refresh serves the last known price as STALE with its timestamp, or UNAVAILABLE;
- resolved symbols are permanent; not_found/ambiguous lookups are retried after 7 days;
  transient failures (network, rate limit, plan, budget) are never cached.
"""

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.holdings import ASSET_CLASS_MUTUAL_FUND
from app.domain.market import (
    INR,
    PriceIssue,
    PriceResult,
    PriceStatus,
    RequestBudget,
    SymbolChoice,
    SymbolStatus,
    choose_candidate,
    forced_refresh_allowed,
    is_fresh,
    symbol_lookup_due,
)
from app.integrations.twelve_data import TwelveDataClient, TwelveDataError
from app.repositories import market_repository
from app.repositories.market_repository import CachedPrice, SymbolMapping

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class InstrumentRef:
    """What the service needs to price one holding (from NormalizedHolding / Holding)."""

    isin: str | None
    broker_symbol: str | None  # the broker's own ticker (Zerodha); None for Groww
    asset_class: str


class _MarketDataIssue(Exception):
    def __init__(self, issue: PriceIssue) -> None:
        super().__init__(issue.value)
        self.issue = issue


# One rolling-minute request budget per process, shared by all users (the API key's limit is global).
_shared_budget = RequestBudget(settings.twelve_data_credits_per_minute)
_budget_lock = threading.Lock()


def default_client() -> TwelveDataClient | None:
    """A client built from configuration, or None when no API key is configured."""
    try:
        return TwelveDataClient(settings.twelve_data_api_key)
    except TwelveDataError:
        return None


def resolve_isins(
    db: Session,
    instruments: Sequence[InstrumentRef],
    *,
    client: TwelveDataClient | None = None,
    budget: RequestBudget | None = None,
    now: datetime | None = None,
) -> dict[str, SymbolMapping | PriceIssue]:
    """Resolve and cache ISIN -> Twelve Data listings (for the future import service, decision M10)."""
    return _run(client, lambda c: {
        isin: _resolve(db, isin, ticker, c, budget or _shared_budget, now or _now())
        for isin, ticker in _tickers_by_isin(instruments).items()
    })


def get_prices(
    db: Session,
    instruments: Sequence[InstrumentRef],
    *,
    force_refresh: bool = False,
    client: TwelveDataClient | None = None,
    budget: RequestBudget | None = None,
    now: datetime | None = None,
) -> list[PriceResult]:
    """One PriceResult per instrument, in order. Each distinct ISIN is resolved and priced once."""
    now = now or _now()
    budget = budget or _shared_budget
    ttl = timedelta(minutes=settings.market_price_ttl_minutes)
    tickers = _tickers_by_isin(instruments)

    def price_all(c: TwelveDataClient | None) -> list[PriceResult]:
        by_isin: dict[str, PriceResult] = {}
        results = []
        for ref in instruments:
            if ref.asset_class == ASSET_CLASS_MUTUAL_FUND:
                results.append(PriceResult(PriceStatus.NOT_SUPPORTED, issue=PriceIssue.MUTUAL_FUND))
            elif not ref.isin:
                results.append(PriceResult(PriceStatus.UNAVAILABLE, issue=PriceIssue.MISSING_ISIN))
            else:
                if ref.isin not in by_isin:
                    by_isin[ref.isin] = _price(db, ref.isin, tickers[ref.isin], c, budget, now, ttl, force_refresh)
                results.append(by_isin[ref.isin])
        return results

    return _run(client, price_all)


def _price(
    db: Session,
    isin: str,
    ticker: str | None,
    client: TwelveDataClient | None,
    budget: RequestBudget,
    now: datetime,
    ttl: timedelta,
    force_refresh: bool,
) -> PriceResult:
    mapping = _resolve(db, isin, ticker, client, budget, now)
    if isinstance(mapping, PriceIssue):
        return PriceResult(PriceStatus.UNAVAILABLE, issue=mapping)
    if mapping.status != SymbolStatus.RESOLVED or mapping.symbol is None or mapping.mic_code is None:
        issue = PriceIssue.SYMBOL_AMBIGUOUS if mapping.status == SymbolStatus.AMBIGUOUS else PriceIssue.SYMBOL_NOT_FOUND
        return PriceResult(PriceStatus.UNAVAILABLE, issue=issue)

    symbol, mic_code = mapping.symbol, mapping.mic_code
    cached = market_repository.get_cached_price(db, symbol, mic_code)
    if cached is not None and is_fresh(cached.fetched_at, now, ttl):
        if not force_refresh or not forced_refresh_allowed(cached.fetched_at, now):
            return _from_cache(cached, PriceStatus.FRESH)

    if client is None:
        issue = PriceIssue.NOT_CONFIGURED
    else:
        try:
            price = _call(budget, now, lambda: client.price(symbol, mic_code))
        except _MarketDataIssue as error:
            issue = error.issue
        else:
            saved = market_repository.save_price(db, symbol, mic_code, price, mapping.currency or INR, now)
            db.commit()
            return _from_cache(saved, PriceStatus.FRESH)

    logger.warning("Twelve Data price refresh failed for %s on %s: %s", symbol, mic_code, issue.value)
    if cached is not None:
        return _from_cache(cached, PriceStatus.STALE, issue)
    return PriceResult(PriceStatus.UNAVAILABLE, issue=issue)


def _resolve(
    db: Session,
    isin: str,
    ticker: str | None,
    client: TwelveDataClient | None,
    budget: RequestBudget,
    now: datetime,
) -> SymbolMapping | PriceIssue:
    """Cached mapping, or a fresh lookup: by ISIN first, then by the broker's ticker (decision M2)."""
    mapping = market_repository.get_symbol_mapping(db, isin)
    if mapping is not None and not symbol_lookup_due(mapping.status, mapping.checked_at, now):
        return mapping
    if client is None:
        return mapping if mapping is not None else PriceIssue.NOT_CONFIGURED

    by_isin = _search(client, budget, now, isin, ticker=None)  # None: ISIN search not in the plan
    if isinstance(by_isin, PriceIssue):
        return by_isin
    by_ticker: SymbolChoice | PriceIssue | None = None
    if ticker and (by_isin is None or by_isin.status != SymbolStatus.RESOLVED):
        by_ticker = _search(client, budget, now, ticker, ticker=ticker)
        if isinstance(by_ticker, PriceIssue):
            return by_ticker

    choices = [c for c in (by_isin, by_ticker) if isinstance(c, SymbolChoice)]
    if not choices:
        return PriceIssue.NOT_ENTITLED  # ISIN search unavailable and no ticker to fall back on
    resolved = next((c for c in choices if c.status == SymbolStatus.RESOLVED), None)
    ambiguous = next((c for c in choices if c.status == SymbolStatus.AMBIGUOUS), None)
    choice = resolved or ambiguous or SymbolChoice(SymbolStatus.NOT_FOUND)
    saved = market_repository.save_symbol_mapping(db, isin, choice, now)
    db.commit()
    return saved


def _search(
    client: TwelveDataClient, budget: RequestBudget, now: datetime, query: str, *, ticker: str | None
) -> SymbolChoice | PriceIssue | None:
    """A SymbolChoice, None if the plan lacks this search (ISIN add-on), or a transient PriceIssue."""
    try:
        candidates = _call(budget, now, lambda: client.symbol_search(query))
    except _MarketDataIssue as error:
        if error.issue == PriceIssue.SYMBOL_NOT_FOUND:
            return SymbolChoice(SymbolStatus.NOT_FOUND)
        if error.issue == PriceIssue.NOT_ENTITLED and ticker is None:
            return None
        logger.warning("Twelve Data symbol search failed: %s", error.issue.value)
        return error.issue
    return choose_candidate(candidates, ticker=ticker)


def _call(budget: RequestBudget, now: datetime, request: Callable[[], T]) -> T:
    with _budget_lock:
        allowed = budget.try_spend(now)
    if not allowed:
        raise _MarketDataIssue(PriceIssue.REQUEST_BUDGET_EXHAUSTED)
    try:
        return request()
    except TwelveDataError as error:
        raise _MarketDataIssue(error.issue) from None


def _run(client: TwelveDataClient | None, work: Callable[[TwelveDataClient | None], T]) -> T:
    """Run with the given client, or with one built from configuration (closed afterwards)."""
    if client is not None:
        return work(client)
    owned = default_client()
    try:
        return work(owned)
    finally:
        if owned is not None:
            owned.close()


def _tickers_by_isin(instruments: Sequence[InstrumentRef]) -> dict[str, str | None]:
    """Each equity ISIN once, with the first broker ticker seen for it (Zerodha) if any."""
    tickers: dict[str, str | None] = {}
    for ref in instruments:
        if ref.isin and ref.asset_class != ASSET_CLASS_MUTUAL_FUND:
            tickers[ref.isin] = tickers.get(ref.isin) or ref.broker_symbol
    return tickers


def _from_cache(cached: CachedPrice, status: PriceStatus, issue: PriceIssue | None = None) -> PriceResult:
    return PriceResult(status, price=cached.price, as_of=cached.fetched_at, currency=cached.currency, issue=issue)


def _now() -> datetime:
    return datetime.now(UTC)
