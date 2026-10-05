"""Finance Service (Module 9, spec 4.4): income/expense entries, monthly budgets and net worth.

Orchestration only: the domain validates and calculates, the repository persists (always for the
authenticated user), Module 7's Portfolio Service values the holdings and Module 8's FX Service supplies
rates for cash held in other currencies. No new external integration.

Currency (decision P3): entries and budgets take the user's reporting currency when they are created
and keep it for good; updates never re-stamp it and the client never supplies it.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain import finance
from app.domain.finance import BudgetRow, BudgetSummary, EntryKind, NetWorth
from app.models import Budget, FinanceEntry, User
from app.repositories import finance_repository
from app.services import fx_service, portfolio_service


class EntryNotFound(Exception):
    """No such entry for this user (another user's entry is indistinguishable from a missing one)."""


class BudgetNotFound(Exception):
    """No such budget for this user."""


class BudgetExists(Exception):
    """A budget for this category and month already exists."""


def _entry_fields(kind: str, category: object, amount: object, entry_date: object, description: object) -> dict:
    return {
        "kind": EntryKind(kind).value,
        "category": finance.normalize_category(category),
        "amount": finance.parse_amount(amount),
        "entry_date": finance.parse_date(entry_date),
        "description": finance.normalize_description(description),
    }


def create_entry(
    db: Session, user: User, *, kind: str, category: object, amount: object, entry_date: object, description: object
) -> FinanceEntry:
    fields = _entry_fields(kind, category, amount, entry_date, description)
    entry = finance_repository.create_entry(db, user.id, currency=user.reporting_currency, **fields)
    db.commit()
    return entry


def get_entry(db: Session, user: User, entry_id: int) -> FinanceEntry:
    entry = finance_repository.get_entry(db, user.id, entry_id)
    if entry is None:
        raise EntryNotFound
    return entry


def list_entries(
    db: Session, user: User, *, start: object = None, end: object = None, kind: str | None = None,
    category: object = None,
) -> list[FinanceEntry]:
    first = finance.parse_date(start) if start is not None else None
    last = finance.parse_date(end) if end is not None else None
    finance.check_date_range(first, last)
    return finance_repository.list_entries(
        db, user.id, start=first, end=last, kind=EntryKind(kind).value if kind is not None else None,
        category=finance.normalize_category(category) if category is not None else None,
    )


def replace_entry(
    db: Session, user: User, entry_id: int, *, kind: str, category: object, amount: object, entry_date: object,
    description: object,
) -> FinanceEntry:
    """Complete replacement (decision P9); the stored currency is kept."""
    fields = _entry_fields(kind, category, amount, entry_date, description)
    entry = finance_repository.replace_entry(db, user.id, entry_id, **fields)
    if entry is None:
        raise EntryNotFound
    db.commit()
    return entry


def delete_entry(db: Session, user: User, entry_id: int) -> None:
    if not finance_repository.delete_entry(db, user.id, entry_id):
        raise EntryNotFound
    db.commit()


def create_budget(db: Session, user: User, *, month: object, category: object, amount: object) -> Budget:
    year, month_number = finance.parse_month(month)
    name = finance.normalize_category(category)
    value = finance.parse_amount(amount)
    try:
        budget = finance_repository.create_budget(
            db, user.id, category=name, year=year, month=month_number, amount=value,
            currency=user.reporting_currency,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise BudgetExists from None
    return budget


def list_budgets(db: Session, user: User, month: object) -> list[Budget]:
    year, month_number = finance.parse_month(month)
    return finance_repository.list_budgets(db, user.id, year, month_number)


def set_budget_amount(db: Session, user: User, budget_id: int, amount: object) -> Budget:
    value = finance.parse_amount(amount)
    budget = finance_repository.set_budget_amount(db, user.id, budget_id, value)
    if budget is None:
        raise BudgetNotFound
    db.commit()
    return budget


def delete_budget(db: Session, user: User, budget_id: int) -> None:
    if not finance_repository.delete_budget(db, user.id, budget_id):
        raise BudgetNotFound
    db.commit()


def budget_summary(db: Session, user: User, month: object) -> BudgetSummary:
    year, month_number = finance.parse_month(month)
    budgets = [
        BudgetRow(b.id, b.category, b.year, b.month, b.amount, b.currency)
        for b in finance_repository.list_budgets(db, user.id, year, month_number)
    ]
    start, end = finance.month_bounds(year, month_number)
    expenses = finance_repository.list_expenses_between(db, user.id, start, end)
    return finance.budget_vs_actual(year, month_number, budgets, expenses)


def get_net_worth(db: Session, user: User) -> NetWorth:
    """Raises portfolio_service.PortfolioDataError when stored holdings are invalid."""
    summary = portfolio_service.get_portfolio(db, user)
    cash = finance.cash_by_currency(finance_repository.list_cash_rows(db, user.id))
    rates = fx_service.get_rates(cash, user.reporting_currency) if cash else {}
    return finance.net_worth(summary, cash, rates)
