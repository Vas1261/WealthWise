from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class HistoricalFxRate(Base):
    """A pinned historical rate (FR-20): units of `quote` per one `base` for a requested date. Public data.

    rate_date is the provider's date for the rate (the requested date, or the latest earlier one when
    no rate exists for that day). Rows are written once and never changed, so a pinned value never moves.
    """

    __tablename__ = "historical_fx_rates"
    __table_args__ = (
        UniqueConstraint("base", "quote", "requested_date", name="uq_historical_fx_rates_pair_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    base: Mapped[str] = mapped_column(String(3))
    quote: Mapped[str] = mapped_column(String(3))
    requested_date: Mapped[date] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(Numeric(24, 12))
    rate_date: Mapped[date] = mapped_column(Date)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
