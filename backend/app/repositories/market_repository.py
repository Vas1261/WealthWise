"""Persistence for the global market price cache (SQLAlchemy only).

Returns plain snapshots with timezone-aware UTC datetimes: SQLite drops tzinfo on read, and
callers compare these timestamps with an aware `now`.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MarketPrice


@dataclass(frozen=True)
class CachedPrice:
    isin: str
    exchange: str
    trading_symbol: str | None
    price: Decimal
    currency: str
    fetched_at: datetime


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _snapshot(row: MarketPrice) -> CachedPrice:
    return CachedPrice(row.isin, row.exchange, row.trading_symbol, row.price, row.currency, _utc(row.fetched_at))


def get_cached_prices(db: Session, isins: Iterable[str]) -> dict[str, CachedPrice]:
    """The cached price of each ISIN that has one."""
    wanted = list(dict.fromkeys(isins))
    if not wanted:
        return {}
    rows = db.execute(select(MarketPrice).where(MarketPrice.isin.in_(wanted))).scalars()
    return {row.isin: _snapshot(row) for row in rows}


def save_price(
    db: Session, isin: str, exchange: str, trading_symbol: str | None, price: Decimal, currency: str,
    fetched_at: datetime,
) -> CachedPrice:
    """Insert or replace the cached price of one ISIN."""
    row = db.execute(select(MarketPrice).where(MarketPrice.isin == isin)).scalar_one_or_none()
    if row is None:
        row = MarketPrice(isin=isin)
        db.add(row)
    row.exchange = exchange
    row.trading_symbol = trading_symbol
    row.price = price
    row.currency = currency
    row.fetched_at = fetched_at
    db.flush()
    return CachedPrice(isin, exchange, trading_symbol, price, currency, _utc(fetched_at))
