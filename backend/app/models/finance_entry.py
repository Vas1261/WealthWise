from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import CheckConstraint, Date, DateTime, Dialect, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, TypeEngine

from app.models.base import Base, utcnow

ENTRY_KINDS = ("income", "expense")
CENT = Decimal("0.01")
# SQLite text storage: 18 whole digits + "." + 2 decimals, plus room for a sign the CHECK then refuses.
SQLITE_MONEY_TEXT = String(22)
# Positivity as a number on every engine (a plain comparison on SQLite's text column would accept "0.00").
AMOUNT_POSITIVE = "CAST(amount AS NUMERIC) > 0"


class ExactMoney(TypeDecorator):
    """Exact Decimal money with two decimal places, stored without any rounding.

    Numeric(20, 2) on engines with a real decimal type (PostgreSQL). SQLite has none: its NUMERIC
    columns keep 64-bit floats, exact to about 15 significant digits only, so there the canonical
    decimal text (e.g. "1499.50") is stored and read back as Decimal. A value with more than two
    decimal places, or a non-finite one, is refused, never rounded.
    """

    impl = Numeric(20, 2)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(SQLITE_MONEY_TEXT)
        return dialect.type_descriptor(Numeric(20, 2))

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> Decimal | str | None:
        if value is None:
            return None
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("money must be a finite Decimal")
        try:
            cents = value.quantize(CENT)
        except InvalidOperation:
            raise ValueError("money has too many digits") from None
        if cents != value:
            raise ValueError("money must have at most two decimal places")
        return format(cents, "f") if dialect.name == "sqlite" else cents

    def process_result_value(self, value: object, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(str(value))


MONEY = ExactMoney()


class FinanceEntry(Base):
    """A manual income or expense entry (spec 4.4). Owned by one user.

    amount is always positive; kind gives the sign. currency is the user's reporting currency when the
    entry was created and never changes afterwards, so a later reporting-currency change never
    reinterprets it (decision P3).
    """

    __tablename__ = "finance_entries"
    __table_args__ = (
        CheckConstraint(AMOUNT_POSITIVE, name="amount_positive"),
        Index("ix_finance_entries_user_id_entry_date", "user_id", "entry_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(Enum(*ENTRY_KINDS, name="finance_kind", native_enum=False, create_constraint=True))
    category: Mapped[str] = mapped_column(String(50))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    entry_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
