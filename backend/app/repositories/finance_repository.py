"""User-scoped persistence for finance entries and budgets (SQLAlchemy only; no calculations).

Every query filters on user_id, including lookups by id, so another user's row is simply not found.
Totals are never computed in SQL (SQLite's SUM over Numeric returns a float): rows go to the domain.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.finance import EntryKind, EntryRow
from app.models import Budget, FinanceEntry

# ponytail: cash and budget totals load the rows they sum; fine at personal scale, add SQL-side
# exact aggregation if one user ever has hundreds of thousands of entries.


def create_entry(
    db: Session, user_id: int, *, kind: str, category: str, amount: Decimal, currency: str, entry_date: date,
    description: str | None,
) -> FinanceEntry:
    entry = FinanceEntry(user_id=user_id, kind=kind, category=category, amount=amount, currency=currency,
                         entry_date=entry_date, description=description)
    db.add(entry)
    db.flush()
    return entry


def get_entry(db: Session, user_id: int, entry_id: int) -> FinanceEntry | None:
    return db.execute(
        select(FinanceEntry).where(FinanceEntry.id == entry_id, FinanceEntry.user_id == user_id)
    ).scalar_one_or_none()


def list_entries(
    db: Session, user_id: int, *, start: date | None = None, end: date | None = None, kind: str | None = None,
    category: str | None = None,
) -> list[FinanceEntry]:
    """Newest first; start and end are inclusive; category matches exactly."""
    query = select(FinanceEntry).where(FinanceEntry.user_id == user_id)
    if start is not None:
        query = query.where(FinanceEntry.entry_date >= start)
    if end is not None:
        query = query.where(FinanceEntry.entry_date <= end)
    if kind is not None:
        query = query.where(FinanceEntry.kind == kind)
    if category is not None:
        query = query.where(FinanceEntry.category == category)
    return list(db.execute(query.order_by(FinanceEntry.entry_date.desc(), FinanceEntry.id.desc())).scalars())


def replace_entry(
    db: Session, user_id: int, entry_id: int, *, kind: str, category: str, amount: Decimal, entry_date: date,
    description: str | None,
) -> FinanceEntry | None:
    """Replace the user-editable fields. currency, user_id and created_at never change (decision P3)."""
    entry = get_entry(db, user_id, entry_id)
    if entry is None:
        return None
    entry.kind = kind
    entry.category = category
    entry.amount = amount
    entry.entry_date = entry_date
    entry.description = description
    db.flush()
    return entry


def delete_entry(db: Session, user_id: int, entry_id: int) -> bool:
    entry = get_entry(db, user_id, entry_id)
    if entry is None:
        return False
    db.delete(entry)
    db.flush()
    return True


def list_cash_rows(db: Session, user_id: int) -> list[EntryRow]:
    """Every entry of the user (no descriptions), for the cash position."""
    rows = db.execute(
        select(FinanceEntry.kind, FinanceEntry.category, FinanceEntry.amount, FinanceEntry.currency,
               FinanceEntry.entry_date)
        .where(FinanceEntry.user_id == user_id)
        .order_by(FinanceEntry.id)
    ).all()
    return [EntryRow(*row) for row in rows]


def list_expenses_between(db: Session, user_id: int, start: date, end_exclusive: date | None) -> list[EntryRow]:
    query = select(
        FinanceEntry.kind, FinanceEntry.category, FinanceEntry.amount, FinanceEntry.currency, FinanceEntry.entry_date
    ).where(
        FinanceEntry.user_id == user_id, FinanceEntry.kind == EntryKind.EXPENSE.value, FinanceEntry.entry_date >= start
    )
    if end_exclusive is not None:
        query = query.where(FinanceEntry.entry_date < end_exclusive)
    return [EntryRow(*row) for row in db.execute(query.order_by(FinanceEntry.id)).all()]


def create_budget(
    db: Session, user_id: int, *, category: str, year: int, month: int, amount: Decimal, currency: str
) -> Budget:
    budget = Budget(user_id=user_id, category=category, year=year, month=month, amount=amount, currency=currency)
    db.add(budget)
    db.flush()
    return budget


def get_budget(db: Session, user_id: int, budget_id: int) -> Budget | None:
    return db.execute(select(Budget).where(Budget.id == budget_id, Budget.user_id == user_id)).scalar_one_or_none()


def list_budgets(db: Session, user_id: int, year: int, month: int) -> list[Budget]:
    return list(
        db.execute(
            select(Budget)
            .where(Budget.user_id == user_id, Budget.year == year, Budget.month == month)
            .order_by(Budget.category, Budget.id)
        ).scalars()
    )


def set_budget_amount(db: Session, user_id: int, budget_id: int, amount: Decimal) -> Budget | None:
    """Only the amount changes; category, period and currency are fixed."""
    budget = get_budget(db, user_id, budget_id)
    if budget is None:
        return None
    budget.amount = amount
    db.flush()
    return budget


def delete_budget(db: Session, user_id: int, budget_id: int) -> bool:
    budget = get_budget(db, user_id, budget_id)
    if budget is None:
        return False
    db.delete(budget)
    db.flush()
    return True
