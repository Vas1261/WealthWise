from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SYMBOL_STATUSES = ("resolved", "not_found", "ambiguous")


class InstrumentSymbol(Base):
    """Global ISIN -> Twelve Data listing cache (spec 4.5). Public data: no user link.

    Failed lookups are cached too (not_found / ambiguous) so they are not retried on every
    refresh; transient failures (network, rate limit, plan) are never stored.
    """

    __tablename__ = "instrument_symbols"
    __table_args__ = (
        CheckConstraint(
            "status != 'resolved' OR (symbol IS NOT NULL AND mic_code IS NOT NULL)", name="resolved_has_listing"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), unique=True)
    status: Mapped[str] = mapped_column(
        Enum(*SYMBOL_STATUSES, name="symbol_status", native_enum=False, create_constraint=True)
    )
    symbol: Mapped[str | None] = mapped_column(String(32))
    mic_code: Mapped[str | None] = mapped_column(String(10))
    exchange: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(3))
    instrument_name: Mapped[str | None] = mapped_column(String(255))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
