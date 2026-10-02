from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MarketPrice(Base):
    """Global last-known Twelve Data price per listing (spec 4.5 cache). Public data: no user link.

    Only the latest successful price is kept; fetched_at is the "last updated" time shown to users.
    """

    __tablename__ = "market_prices"
    __table_args__ = (UniqueConstraint("symbol", "mic_code", name="uq_market_prices_listing"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    mic_code: Mapped[str] = mapped_column(String(10))
    price: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    currency: Mapped[str] = mapped_column(String(3))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
