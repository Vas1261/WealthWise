from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MarketPrice(Base):
    """Global last-known price per ISIN from Upstox (spec 4.5 cache). Public data: no user link.

    Only the latest successful price is kept, with the exchange it came from (NSE preferred, else BSE);
    fetched_at is the "last updated" time shown to users.
    """

    __tablename__ = "market_prices"

    id: Mapped[int] = mapped_column(primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), unique=True)
    exchange: Mapped[str] = mapped_column(String(3))
    trading_symbol: Mapped[str | None] = mapped_column(String(64))
    price: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    currency: Mapped[str] = mapped_column(String(3))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
