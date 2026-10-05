"""Personal finance rules as pure functions (Module 9, spec 4.4; no database, HTTP or framework imports).

Rules (decisions P1-P11):
- Amounts are strings of ASCII digits with at most two decimal places and greater than zero (up to the
  column's 18 whole digits); anything else is rejected, never rounded. The entry kind gives the sign.
- Categories are compared exactly after trimming and collapsing internal whitespace.
- Cash position = income minus expenses over every recorded entry (future-dated included), kept per
  currency and never summed across currencies before conversion.
- Budgets belong to one year-month and count expenses only, in the same category and currency.
- Net worth = priced portfolio current value (already in the reporting currency, Module 8) + cash
  converted at today's rate. Unpriced holdings are left out and reported; the mutual-fund cost floor
  is shown separately and never added. Any unusable rate means no total (never a partial one).
- No rounding: arithmetic runs in the portfolio calculation context; presentation rounds.
Validation errors carry only an issue code, never the submitted value.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from enum import StrEnum

from app.domain.fx import FxRate, FxStatus, identity_rate
from app.domain.portfolio import CALCULATION_CONTEXT, HUNDRED, ZERO, PortfolioSummary

# [0-9] rather than \d: \d also matches non-ASCII digits. 18 + 2 digits fit the Numeric(20, 2) money column.
AMOUNT = re.compile(r"[0-9]{1,18}(?:\.[0-9]{1,2})?")
MONTH = re.compile(r"([0-9]{4})-([0-9]{2})")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MAX_CATEGORY_LENGTH = 50
MAX_DESCRIPTION_LENGTH = 255
MIN_YEAR = 1900
MAX_YEAR = 9999


class EntryKind(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


class FinanceIssue(StrEnum):
    INVALID_AMOUNT = "invalid_amount"
    INVALID_CATEGORY = "invalid_category"
    INVALID_DESCRIPTION = "invalid_description"
    INVALID_DATE = "invalid_date"
    INVALID_MONTH = "invalid_month"
    INVALID_DATE_RANGE = "invalid_date_range"


class FinanceValidationError(ValueError):
    """Carries only an issue code; the message never contains the submitted value."""

    def __init__(self, issue: FinanceIssue) -> None:
        super().__init__(issue.value)
        self.issue = issue


@dataclass(frozen=True)
class EntryRow:
    kind: str
    category: str
    amount: Decimal
    currency: str
    entry_date: date


@dataclass(frozen=True)
class BudgetRow:
    budget_id: int
    category: str
    year: int
    month: int
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class BudgetLine:
    budget_id: int
    category: str
    currency: str
    budget: Decimal
    actual: Decimal
    remaining: Decimal
    utilization_pct: Decimal
    over_budget: bool


@dataclass(frozen=True)
class UnbudgetedLine:
    category: str
    currency: str
    actual: Decimal


@dataclass(frozen=True)
class BudgetSummary:
    year: int
    month: int
    budgets: list[BudgetLine]
    unbudgeted: list[UnbudgetedLine]


@dataclass(frozen=True)
class NetWorth:
    reporting_currency: str
    net_worth: Decimal | None  # None when any needed FX rate is unusable
    portfolio_value: Decimal | None  # priced current value only; None when the portfolio could not be converted
    cash_position: Decimal | None  # None when any cash currency cannot be converted
    cash_by_currency: dict[str, Decimal]  # native, unconverted
    complete: bool  # a total exists and no holding was left out for lack of a price
    unpriced_holdings: int
    cost_floor_value: Decimal | None  # mutual funds at cost: shown, never part of net_worth
    contains_stale_prices: bool
    oldest_price_as_of: datetime | None
    fx_rates: dict[str, FxRate]  # currency -> rate into the reporting currency
    fx_unavailable: list[str]

    @property
    def fx_stale(self) -> bool:
        return any(rate.status == FxStatus.STALE for rate in self.fx_rates.values())


# --- Input rules ---------------------------------------------------------------------------------------


def parse_amount(value: object) -> Decimal:
    if not isinstance(value, str) or AMOUNT.fullmatch(value) is None:
        raise FinanceValidationError(FinanceIssue.INVALID_AMOUNT)
    amount = Decimal(value)
    if amount <= 0:
        raise FinanceValidationError(FinanceIssue.INVALID_AMOUNT)
    return amount


def normalize_category(value: object) -> str:
    if not isinstance(value, str):
        raise FinanceValidationError(FinanceIssue.INVALID_CATEGORY)
    category = " ".join(value.split())
    if not 1 <= len(category) <= MAX_CATEGORY_LENGTH:
        raise FinanceValidationError(FinanceIssue.INVALID_CATEGORY)
    return category


def normalize_description(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FinanceValidationError(FinanceIssue.INVALID_DESCRIPTION)
    description = value.strip()
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise FinanceValidationError(FinanceIssue.INVALID_DESCRIPTION)
    return description or None


def parse_date(value: object) -> date:
    """'YYYY-MM-DD', a real calendar date from 1900 to 9999; future dates are allowed (decision P6)."""
    if not isinstance(value, str) or DATE.fullmatch(value) is None:
        raise FinanceValidationError(FinanceIssue.INVALID_DATE)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise FinanceValidationError(FinanceIssue.INVALID_DATE) from None
    if not MIN_YEAR <= parsed.year <= MAX_YEAR:
        raise FinanceValidationError(FinanceIssue.INVALID_DATE)
    return parsed


def parse_month(value: object) -> tuple[int, int]:
    """'YYYY-MM' -> (year, month)."""
    match = MONTH.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise FinanceValidationError(FinanceIssue.INVALID_MONTH)
    year, month = int(match[1]), int(match[2])
    if not (MIN_YEAR <= year <= MAX_YEAR and 1 <= month <= 12):
        raise FinanceValidationError(FinanceIssue.INVALID_MONTH)
    return year, month


def month_bounds(year: int, month: int) -> tuple[date, date | None]:
    """First day of the month and first day of the next month (None after December 9999): [start, end)."""
    start = date(year, month, 1)
    if month < 12:
        return start, date(year, month + 1, 1)
    return start, date(year + 1, 1, 1) if year < MAX_YEAR else None


def check_date_range(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and start > end:
        raise FinanceValidationError(FinanceIssue.INVALID_DATE_RANGE)


# --- Calculations --------------------------------------------------------------------------------------


def cash_by_currency(entries: Iterable[EntryRow]) -> dict[str, Decimal]:
    """Income minus expenses per currency over every entry; may be zero or negative, never clamped."""
    cash: dict[str, Decimal] = {}
    with localcontext(CALCULATION_CONTEXT):
        for entry in entries:
            signed = entry.amount if entry.kind == EntryKind.INCOME else -entry.amount
            cash[entry.currency] = cash.get(entry.currency, ZERO) + signed
    return cash


def in_month(day: date, year: int, month: int) -> bool:
    start, end = month_bounds(year, month)
    return start <= day and (end is None or day < end)


def budget_vs_actual(
    year: int, month: int, budgets: Sequence[BudgetRow], entries: Iterable[EntryRow]
) -> BudgetSummary:
    """Each budget against its month's expenses in the same category and currency (income never counts).

    Expenses without a matching budget are listed per (category, currency) in `unbudgeted`.
    """
    actuals: dict[tuple[str, str], Decimal] = {}
    with localcontext(CALCULATION_CONTEXT):
        for entry in entries:
            if entry.kind == EntryKind.EXPENSE and in_month(entry.entry_date, year, month):
                key = (entry.category, entry.currency)
                actuals[key] = actuals.get(key, ZERO) + entry.amount
        lines = []
        for budget in budgets:
            if (budget.year, budget.month) != (year, month):
                raise ValueError("budget_vs_actual received a budget for another month")
            actual = actuals.get((budget.category, budget.currency), ZERO)
            lines.append(
                BudgetLine(
                    budget_id=budget.budget_id,
                    category=budget.category,
                    currency=budget.currency,
                    budget=budget.amount,
                    actual=actual,
                    remaining=budget.amount - actual,
                    utilization_pct=actual / budget.amount * HUNDRED,
                    over_budget=actual > budget.amount,
                )
            )
    budgeted = {(b.category, b.currency) for b in budgets}
    unbudgeted = [
        UnbudgetedLine(category, currency, actual)
        for (category, currency), actual in sorted(actuals.items())
        if (category, currency) not in budgeted
    ]
    return BudgetSummary(year, month, lines, unbudgeted)


def convert_amounts(
    amounts: Mapping[str, Decimal], fx_rates: Mapping[str, FxRate], reporting_currency: str
) -> tuple[Decimal | None, list[str], dict[str, FxRate]]:
    """Sum amounts after converting each currency into the reporting currency.

    Returns (total, currencies without a usable rate, the rates for these currencies). The reporting
    currency converts at exactly 1. Any missing, unusable or wrong-pair rate gives no total at all.
    """
    rates = {reporting_currency: identity_rate(reporting_currency), **fx_rates}
    shown: dict[str, FxRate] = {}
    missing: list[str] = []
    for currency in amounts:
        rate = rates.get(currency)
        if rate is None or (rate.base, rate.quote) != (currency, reporting_currency):
            missing.append(currency)
            continue
        shown[currency] = rate
        if not rate.usable:
            missing.append(currency)
    if missing:
        return None, sorted(missing), shown
    with localcontext(CALCULATION_CONTEXT):
        total = sum((amount * _rate(shown[currency]) for currency, amount in amounts.items()), ZERO)
    return total, [], shown


def net_worth(summary: PortfolioSummary, cash: Mapping[str, Decimal], fx_rates: Mapping[str, FxRate]) -> NetWorth:
    """Priced portfolio current value (reporting currency) + converted cash (decision P4).

    fx_rates: cash currency -> rate into the summary's reporting currency.
    """
    reporting_currency = summary.reporting_currency
    totals = summary.reporting_totals
    portfolio_value: Decimal | None
    cost_floor: Decimal | None
    if not summary.totals_by_currency:  # no holdings: a genuine zero
        portfolio_value, cost_floor = ZERO, ZERO
    elif totals is None:  # holdings that could not be converted
        portfolio_value, cost_floor = None, None
    else:
        portfolio_value = totals.current_value if totals.current_value is not None else ZERO
        cost_floor = totals.cost_floor_value
    unpriced = sum(t.holdings - t.priced for t in summary.totals_by_currency.values())

    cash_position, cash_missing, cash_rates = convert_amounts(cash, fx_rates, reporting_currency)
    total: Decimal | None = None
    if portfolio_value is not None and cash_position is not None:
        with localcontext(CALCULATION_CONTEXT):
            total = portfolio_value + cash_position
    return NetWorth(
        reporting_currency=reporting_currency,
        net_worth=total,
        portfolio_value=portfolio_value,
        cash_position=cash_position,
        cash_by_currency=dict(cash),
        complete=total is not None and unpriced == 0,
        unpriced_holdings=unpriced,
        cost_floor_value=cost_floor,
        contains_stale_prices=summary.contains_stale_prices,
        oldest_price_as_of=summary.oldest_price_as_of,
        fx_rates={**(totals.fx_rates if totals is not None else {}), **cash_rates},
        fx_unavailable=sorted(set(summary.fx_unavailable) | set(cash_missing)),
    )


def _rate(rate: FxRate) -> Decimal:
    if rate.rate is None:  # unreachable: only usable rates get here
        raise ValueError("unusable FX rate")
    return rate.rate
