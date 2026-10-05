from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow
from app.models.finance_entry import AMOUNT_POSITIVE, MONEY


class Budget(Base):
    """A monthly expense budget for one category in one specific year-month (decisions P2, P7).

    currency is fixed when the budget is created (decision P3), like FinanceEntry.currency.
    """

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "year", "month", "category", name="uq_budgets_user_period_category"),
        CheckConstraint(AMOUNT_POSITIVE, name="amount_positive"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month_range"),
        CheckConstraint("year BETWEEN 1900 AND 9999", name="year_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(50))
    year: Mapped[int]
    month: Mapped[int]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
