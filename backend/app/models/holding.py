from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import ASSET_CLASSES, BROKERS, Base

# Enough scale for MF units (3 dp) and NAV / average prices (4 dp) without rounding.
QUANTITY = Numeric(20, 6)
PRICE = Numeric(20, 6)


class Holding(Base):
    """Persisted NormalizedHolding (spec 2.4). Owned by the user via its import batch.

    Deliberately has no closing-price or file P&L columns: those file values are for
    parse validation only and live prices come from the Market Service (spec 2.3).
    """

    __tablename__ = "holdings"
    __table_args__ = (
        # The adapters already reject these; the database refuses them too (Module 7 data integrity).
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("average_buy_price >= 0", name="average_buy_price_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[str] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), index=True)
    instrument_name: Mapped[str] = mapped_column(String(255))
    isin: Mapped[str | None] = mapped_column(String(12))
    symbol: Mapped[str | None] = mapped_column(String(64))
    asset_class: Mapped[str] = mapped_column(
        Enum(*ASSET_CLASSES, name="asset_class", native_enum=False, create_constraint=True)
    )
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    average_buy_price: Mapped[Decimal] = mapped_column(PRICE)
    native_currency: Mapped[str] = mapped_column(String(3))
    source_broker: Mapped[str] = mapped_column(Enum(*BROKERS, name="broker", native_enum=False, create_constraint=True))

    import_batch: Mapped["ImportBatch"] = relationship(back_populates="holdings")
