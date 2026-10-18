"""Report contents as pure functions (FR-23 monthly report, FR-24 portfolio report). No I/O, no formatting.

The monthly report has three summary components for one month: income and expense totals, totals per
category, and budget against actual (Module 9's budget_vs_actual), plus the month's entries. Amounts are
kept per currency and never summed across currencies. The portfolio report is the PortfolioSummary itself.
month_series gives the same per-currency totals for a run of months (the dashboard's income/expenses chart).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.domain.finance import BudgetSummary, EntryKind, EntryRow, in_month
from app.domain.portfolio import CALCULATION_CONTEXT, ZERO


@dataclass(frozen=True)
class MonthTotals:
    currency: str
    income: Decimal
    expenses: Decimal
    net: Decimal  # income - expenses


@dataclass(frozen=True)
class CategoryTotal:
    kind: str
    category: str
    currency: str
    amount: Decimal
    entries: int


@dataclass(frozen=True)
class MonthSeriesPoint:
    year: int
    month: int
    totals: list[MonthTotals]  # one per currency with entries that month; empty when there are none


@dataclass(frozen=True)
class MonthlyReport:
    year: int
    month: int
    totals: list[MonthTotals]  # one per currency, by currency code
    categories: list[CategoryTotal]  # income first, then expenses; largest first
    budget: BudgetSummary
    entries: list[EntryRow]  # the month's entries, oldest first


def monthly_report(year: int, month: int, entries: Sequence[EntryRow], budget: BudgetSummary) -> MonthlyReport:
    if (budget.year, budget.month) != (year, month):
        raise ValueError("monthly_report received a budget summary for another month")
    rows = [e for e in entries if in_month(e.entry_date, year, month)]
    groups: dict[tuple[str, str, str], tuple[Decimal, int]] = {}
    with localcontext(CALCULATION_CONTEXT):
        for e in rows:
            amount, count = groups.get((e.kind, e.category, e.currency), (ZERO, 0))
            groups[(e.kind, e.category, e.currency)] = (amount + e.amount, count + 1)
    categories = sorted(
        (CategoryTotal(kind, category, currency, amount, count)
         for (kind, category, currency), (amount, count) in groups.items()),
        key=lambda c: (c.kind != EntryKind.INCOME.value, c.currency, -c.amount, c.category),
    )
    return MonthlyReport(year, month, _totals(rows), categories, budget, sorted(rows, key=lambda e: e.entry_date))


def month_series(end_year: int, end_month: int, months: int, entries: Sequence[EntryRow]) -> list[MonthSeriesPoint]:
    """Income, expenses and net per currency for each of `months` months ending with end_year-end_month,
    oldest first. Months without entries are included with no totals."""
    points = []
    year, month = end_year, end_month
    for _ in range(months):
        points.append(MonthSeriesPoint(year, month, _totals([e for e in entries if in_month(e.entry_date, year, month)])))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return points[::-1]


def _totals(rows: Sequence[EntryRow]) -> list[MonthTotals]:
    sums: dict[tuple[str, str], Decimal] = {}
    with localcontext(CALCULATION_CONTEXT):
        for e in rows:
            sums[(e.currency, e.kind)] = sums.get((e.currency, e.kind), ZERO) + e.amount
        totals = []
        for currency in sorted({c for c, _ in sums}):
            income = sums.get((currency, EntryKind.INCOME.value), ZERO)
            expenses = sums.get((currency, EntryKind.EXPENSE.value), ZERO)
            totals.append(MonthTotals(currency, income, expenses, income - expenses))
    return totals
