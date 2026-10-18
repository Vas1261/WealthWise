"""Persistence of pinned historical FX rates (SQLAlchemy only). Rows are inserted once and never updated."""

from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HistoricalFxRate


def get_pinned(db: Session, base: str, quote: str, dates: Iterable[date]) -> dict[date, HistoricalFxRate]:
    wanted = list(dict.fromkeys(dates))
    if not wanted:
        return {}
    rows = db.execute(
        select(HistoricalFxRate).where(
            HistoricalFxRate.base == base,
            HistoricalFxRate.quote == quote,
            HistoricalFxRate.requested_date.in_(wanted),
        )
    ).scalars()
    return {row.requested_date: row for row in rows}


def save_pinned(
    db: Session, base: str, quote: str, rates: dict[date, tuple[Decimal, date]], fetched_at: datetime
) -> None:
    """Insert rates for dates that have none yet (an existing pinned rate is never replaced)."""
    existing = get_pinned(db, base, quote, rates)
    for requested_date, (rate, rate_date) in rates.items():
        if requested_date not in existing:
            db.add(HistoricalFxRate(
                base=base, quote=quote, requested_date=requested_date, rate=rate, rate_date=rate_date,
                fetched_at=fetched_at.astimezone(UTC),
            ))
    db.flush()
