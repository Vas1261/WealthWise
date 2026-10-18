"""Report Service (FR-23, FR-24): gathers the data of a report; the API renders it as CSV or PDF.

Orchestration only: the monthly report reuses Module 9's budget summary and the month's entries, the
portfolio report is the Portfolio Service's valuation (live prices, FX and pinned FX included).
"""

from sqlalchemy.orm import Session

from app.domain import finance
from app.domain.portfolio import PortfolioSummary
from app.domain.reports import MonthlyReport, MonthSeriesPoint, month_series, monthly_report
from app.models import User
from app.repositories import finance_repository
from app.services import finance_service, portfolio_service


def monthly(db: Session, user: User, month: object) -> MonthlyReport:
    """Raises FinanceValidationError for an invalid month."""
    year, month_number = finance.parse_month(month)
    start, end = finance.month_bounds(year, month_number)
    entries = finance_repository.list_entries_between(db, user.id, start, end)
    return monthly_report(year, month_number, entries, finance_service.budget_summary(db, user, month))


def monthly_totals(db: Session, user: User, end: object, months: int) -> list[MonthSeriesPoint]:
    """Raises FinanceValidationError for an invalid end month."""
    year, month = finance.parse_month(end)
    first = month_series(year, month, months, [])[0]
    start, _ = finance.month_bounds(first.year, first.month)
    _, end_exclusive = finance.month_bounds(year, month)
    return month_series(year, month, months, finance_repository.list_entries_between(db, user.id, start, end_exclusive))


def portfolio(db: Session, user: User) -> PortfolioSummary:
    """Raises portfolio_service.PortfolioDataError when stored holdings are invalid."""
    return portfolio_service.get_portfolio(db, user)
