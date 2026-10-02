"""Persistence for the global market-data caches (SQLAlchemy only).

Returns plain snapshots with timezone-aware UTC datetimes: SQLite drops tzinfo on read, and
callers compare these timestamps with an aware `now`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.market import SymbolCandidate, SymbolChoice, SymbolStatus
from app.models import InstrumentSymbol, MarketPrice


@dataclass(frozen=True)
class SymbolMapping:
    isin: str
    status: SymbolStatus
    symbol: str | None
    mic_code: str | None
    currency: str | None
    checked_at: datetime


@dataclass(frozen=True)
class CachedPrice:
    symbol: str
    mic_code: str
    price: Decimal
    currency: str
    fetched_at: datetime


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def get_symbol_mapping(db: Session, isin: str) -> SymbolMapping | None:
    row = db.execute(select(InstrumentSymbol).where(InstrumentSymbol.isin == isin)).scalar_one_or_none()
    if row is None:
        return None
    return SymbolMapping(
        row.isin, SymbolStatus(row.status), row.symbol, row.mic_code, row.currency, _utc(row.checked_at)
    )


def save_symbol_mapping(db: Session, isin: str, choice: SymbolChoice, checked_at: datetime) -> SymbolMapping:
    row = db.execute(select(InstrumentSymbol).where(InstrumentSymbol.isin == isin)).scalar_one_or_none()
    if row is None:
        row = InstrumentSymbol(isin=isin)
        db.add(row)
    candidate: SymbolCandidate | None = choice.candidate
    row.status = choice.status.value
    row.symbol = candidate.symbol if candidate else None
    row.mic_code = candidate.mic_code if candidate else None
    row.exchange = candidate.exchange if candidate else None
    row.currency = candidate.currency if candidate else None
    row.instrument_name = candidate.instrument_name if candidate else None
    row.checked_at = checked_at
    db.flush()
    return SymbolMapping(isin, choice.status, row.symbol, row.mic_code, row.currency, _utc(checked_at))


def get_cached_price(db: Session, symbol: str, mic_code: str) -> CachedPrice | None:
    row = db.execute(
        select(MarketPrice).where(MarketPrice.symbol == symbol, MarketPrice.mic_code == mic_code)
    ).scalar_one_or_none()
    if row is None:
        return None
    return CachedPrice(row.symbol, row.mic_code, row.price, row.currency, _utc(row.fetched_at))


def save_price(
    db: Session, symbol: str, mic_code: str, price: Decimal, currency: str, fetched_at: datetime
) -> CachedPrice:
    row = db.execute(
        select(MarketPrice).where(MarketPrice.symbol == symbol, MarketPrice.mic_code == mic_code)
    ).scalar_one_or_none()
    if row is None:
        row = MarketPrice(symbol=symbol, mic_code=mic_code)
        db.add(row)
    row.price = price
    row.currency = currency
    row.fetched_at = fetched_at
    db.flush()
    return CachedPrice(symbol, mic_code, price, currency, _utc(fetched_at))
