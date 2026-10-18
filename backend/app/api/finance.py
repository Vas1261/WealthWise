"""Personal finance routes (Module 9). The user always comes from the session cookie, never from request data.

Writes (POST/PUT/DELETE) are CSRF-protected by the global middleware. Another user's entry or budget id
is answered exactly like a missing one (404). Money is serialised as strings with 2 dp, percentages 2 dp
(shared formatter). Amounts, dates and months are sent as strings and validated by the domain; error
bodies carry only a fixed message and a code, never a submitted value. Request bodies have no currency
or user field: currency is assigned from the reporting currency when a row is created and never changes.

Status codes: 201 created; 204 deleted; 404 entry_not_found | budget_not_found; 409 budget_exists, or
the portfolio's invalid-holdings conflict for net worth; 422 {detail, code} for invalid_amount |
invalid_category | invalid_description | invalid_date | invalid_month | invalid_date_range.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal, TypeVar

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictStr
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.formatting import MONEY_PLACES, PERCENT_PLACES, fixed
from app.api.portfolio import FxRateOut, _fx_rate
from app.core.database import get_session
from app.domain.finance import BudgetSummary, FinanceIssue, FinanceValidationError, NetWorth
from app.models import Budget, FinanceEntry, User
from app.services import finance_service, portfolio_service, report_service

PREFIX = "/api/finance"
router = APIRouter(prefix=PREFIX, tags=["finance"])

CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_session)]
Kind = Literal["income", "expense"]
T = TypeVar("T")
# Row ids are 64-bit integers in the database. A larger number in the URL cannot even be bound to a query
# (SQLite raises OverflowError, which surfaced as a 500), so it is refused like any other malformed id.
MAX_ROW_ID = 2**63 - 1
RowId = Annotated[int, Path(le=MAX_ROW_ID)]

ISSUE_MESSAGES = {
    FinanceIssue.INVALID_AMOUNT: (
        "Amount must be a string with a positive number of at most 18 whole digits and 2 decimal places"
    ),
    FinanceIssue.INVALID_CATEGORY: "Category must be 1 to 50 characters",
    FinanceIssue.INVALID_DESCRIPTION: "Description must be at most 255 characters",
    FinanceIssue.INVALID_DATE: "Dates must be real calendar dates in YYYY-MM-DD form between 1900 and 9999",
    FinanceIssue.INVALID_MONTH: "Month must be in YYYY-MM form between 1900 and 9999",
    FinanceIssue.INVALID_DATE_RANGE: "start must not be after end",
}


class EntryIn(BaseModel):
    """A complete entry (POST and PUT). Unknown fields such as currency or user_id are ignored."""

    kind: Kind
    category: StrictStr
    amount: StrictStr
    date: StrictStr
    description: StrictStr | None = None


class BudgetIn(BaseModel):
    month: StrictStr
    category: StrictStr
    amount: StrictStr


class BudgetAmountIn(BaseModel):
    amount: StrictStr


class EntryOut(BaseModel):
    id: int
    kind: str
    category: str
    amount: str
    currency: str
    date: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class BudgetOut(BaseModel):
    id: int
    month: str
    category: str
    amount: str
    currency: str
    created_at: datetime
    updated_at: datetime


class BudgetLineOut(BaseModel):
    budget_id: int
    category: str
    currency: str
    budget: str
    actual: str
    remaining: str
    utilization_pct: str
    over_budget: bool


class UnbudgetedOut(BaseModel):
    category: str
    currency: str
    actual: str


class MonthTotalsOut(BaseModel):
    currency: str
    income: str
    expenses: str
    net: str


class MonthPointOut(BaseModel):
    month: str  # YYYY-MM
    totals: list[MonthTotalsOut]  # one per currency with entries that month; empty when none


class BudgetSummaryOut(BaseModel):
    month: str
    budgets: list[BudgetLineOut]
    unbudgeted: list[UnbudgetedOut]


class NetWorthOut(BaseModel):
    reporting_currency: str
    net_worth: str | None
    portfolio_value: str | None  # priced holdings only, in the reporting currency
    cash_position: str | None  # income minus expenses, converted into the reporting currency
    cash_by_currency: dict[str, str]  # native, unconverted
    complete: bool
    unpriced_holdings: int
    cost_floor_value: str | None  # mutual funds at cost: shown separately, never part of net_worth
    contains_stale_prices: bool
    oldest_price_as_of: datetime | None
    fx_stale: bool
    fx_rates: list[FxRateOut]
    fx_unavailable: list[str]


def _money(value: Decimal) -> str:
    return fixed(value, MONEY_PLACES) or ""


def _utc(value: datetime) -> datetime:
    # SQLite drops tzinfo on read; stored timestamps are UTC (same rule as market_repository).
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _month(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _entry(e: FinanceEntry) -> EntryOut:
    return EntryOut(
        id=e.id, kind=e.kind, category=e.category, amount=_money(e.amount), currency=e.currency,
        date=e.entry_date.isoformat(), description=e.description, created_at=_utc(e.created_at), updated_at=_utc(e.updated_at),
    )


def _budget(b: Budget) -> BudgetOut:
    return BudgetOut(
        id=b.id, month=_month(b.year, b.month), category=b.category, amount=_money(b.amount), currency=b.currency,
        created_at=_utc(b.created_at), updated_at=_utc(b.updated_at),
    )


def _summary(s: BudgetSummary) -> BudgetSummaryOut:
    return BudgetSummaryOut(
        month=_month(s.year, s.month),
        budgets=[
            BudgetLineOut(
                budget_id=line.budget_id, category=line.category, currency=line.currency, budget=_money(line.budget),
                actual=_money(line.actual), remaining=_money(line.remaining),
                utilization_pct=fixed(line.utilization_pct, PERCENT_PLACES) or "", over_budget=line.over_budget,
            )
            for line in s.budgets
        ],
        unbudgeted=[UnbudgetedOut(category=u.category, currency=u.currency, actual=_money(u.actual))
                    for u in s.unbudgeted],
    )


def _net_worth(n: NetWorth) -> NetWorthOut:
    return NetWorthOut(
        reporting_currency=n.reporting_currency,
        net_worth=fixed(n.net_worth, MONEY_PLACES),
        portfolio_value=fixed(n.portfolio_value, MONEY_PLACES),
        cash_position=fixed(n.cash_position, MONEY_PLACES),
        cash_by_currency={currency: _money(amount) for currency, amount in n.cash_by_currency.items()},
        complete=n.complete,
        unpriced_holdings=n.unpriced_holdings,
        cost_floor_value=fixed(n.cost_floor_value, MONEY_PLACES),
        contains_stale_prices=n.contains_stale_prices,
        oldest_price_as_of=n.oldest_price_as_of,
        fx_stale=n.fx_stale,
        fx_rates=[_fx_rate(currency, rate) for currency, rate in n.fx_rates.items()],
        fx_unavailable=n.fx_unavailable,
    )


def _problem(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": code}, status_code=status_code)


def _run(work: Callable[[], T]) -> T | JSONResponse:
    """Map service errors to fixed responses; nothing submitted is ever echoed."""
    try:
        return work()
    except FinanceValidationError as error:
        return _problem(status.HTTP_422_UNPROCESSABLE_CONTENT, error.issue.value, ISSUE_MESSAGES[error.issue])
    except finance_service.EntryNotFound:
        return _problem(status.HTTP_404_NOT_FOUND, "entry_not_found", "Finance entry not found")
    except finance_service.BudgetNotFound:
        return _problem(status.HTTP_404_NOT_FOUND, "budget_not_found", "Budget not found")
    except finance_service.BudgetExists:
        return _problem(status.HTTP_409_CONFLICT, "budget_exists", "A budget for this category and month already exists")


async def finance_validation_handler(request: Request, exc: Exception) -> Response:
    """Schema errors on finance routes without FastAPI's echoed `input`/`ctx` (they could hold personal
    values). Every other route keeps FastAPI's default response unchanged."""
    if not isinstance(exc, RequestValidationError):  # registered for RequestValidationError only
        raise exc
    if request.url.path != PREFIX and not request.url.path.startswith(PREFIX + "/"):
        return await request_validation_exception_handler(request, exc)
    errors = [{"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()]
    return JSONResponse({"detail": errors, "code": "invalid_request"},
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)


# --- Entries -------------------------------------------------------------------------------------------


@router.post("/entries", status_code=status.HTTP_201_CREATED, response_model=EntryOut)
def create_entry(body: EntryIn, user: CurrentUser, db: DbSession) -> EntryOut | JSONResponse:
    return _run(lambda: _entry(finance_service.create_entry(
        db, user, kind=body.kind, category=body.category, amount=body.amount, entry_date=body.date,
        description=body.description,
    )))


@router.get("/entries", response_model=list[EntryOut])
def list_entries(
    user: CurrentUser, db: DbSession, start: str | None = None, end: str | None = None, kind: Kind | None = None,
    category: str | None = None,
) -> list[EntryOut] | JSONResponse:
    return _run(lambda: [
        _entry(e) for e in finance_service.list_entries(db, user, start=start, end=end, kind=kind, category=category)
    ])


@router.get("/entries/{entry_id}", response_model=EntryOut)
def get_entry(entry_id: RowId, user: CurrentUser, db: DbSession) -> EntryOut | JSONResponse:
    return _run(lambda: _entry(finance_service.get_entry(db, user, entry_id)))


@router.put("/entries/{entry_id}", response_model=EntryOut)
def replace_entry(entry_id: RowId, body: EntryIn, user: CurrentUser, db: DbSession) -> EntryOut | JSONResponse:
    return _run(lambda: _entry(finance_service.replace_entry(
        db, user, entry_id, kind=body.kind, category=body.category, amount=body.amount, entry_date=body.date,
        description=body.description,
    )))


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(entry_id: RowId, user: CurrentUser, db: DbSession) -> Response:
    result = _run(lambda: finance_service.delete_entry(db, user, entry_id))
    return result if isinstance(result, JSONResponse) else Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Budgets (summary is registered before /budgets/{budget_id}) ---------------------------------------


@router.get("/budgets/summary", response_model=BudgetSummaryOut)
def budget_summary(user: CurrentUser, db: DbSession, month: Annotated[str, Query()]) -> BudgetSummaryOut | JSONResponse:
    return _run(lambda: _summary(finance_service.budget_summary(db, user, month)))


@router.post("/budgets", status_code=status.HTTP_201_CREATED, response_model=BudgetOut)
def create_budget(body: BudgetIn, user: CurrentUser, db: DbSession) -> BudgetOut | JSONResponse:
    return _run(lambda: _budget(finance_service.create_budget(
        db, user, month=body.month, category=body.category, amount=body.amount,
    )))


@router.get("/budgets", response_model=list[BudgetOut])
def list_budgets(user: CurrentUser, db: DbSession, month: Annotated[str, Query()]) -> list[BudgetOut] | JSONResponse:
    return _run(lambda: [_budget(b) for b in finance_service.list_budgets(db, user, month)])


@router.put("/budgets/{budget_id}", response_model=BudgetOut)
def set_budget_amount(budget_id: RowId, body: BudgetAmountIn, user: CurrentUser, db: DbSession) -> BudgetOut | JSONResponse:
    return _run(lambda: _budget(finance_service.set_budget_amount(db, user, budget_id, body.amount)))


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_budget(budget_id: RowId, user: CurrentUser, db: DbSession) -> Response:
    result = _run(lambda: finance_service.delete_budget(db, user, budget_id))
    return result if isinstance(result, JSONResponse) else Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Net worth -----------------------------------------------------------------------------------------


@router.get("/monthly-totals", response_model=list[MonthPointOut])
def monthly_totals(
    user: CurrentUser, db: DbSession, end: Annotated[str, Query()], months: Annotated[int, Query(ge=1, le=36)] = 12,
) -> list[MonthPointOut] | JSONResponse:
    """Income, expenses and net per currency for `months` months ending with `end` (YYYY-MM), oldest first."""
    return _run(lambda: [
        MonthPointOut(month=_month(p.year, p.month), totals=[
            MonthTotalsOut(currency=t.currency, income=_money(t.income), expenses=_money(t.expenses), net=_money(t.net))
            for t in p.totals
        ])
        for p in report_service.monthly_totals(db, user, end, months)
    ])


@router.get("/net-worth", response_model=NetWorthOut)
def get_net_worth(user: CurrentUser, db: DbSession) -> NetWorthOut | JSONResponse:
    try:
        return _net_worth(finance_service.get_net_worth(db, user))
    except portfolio_service.PortfolioDataError as error:  # same conflict as GET /api/portfolio
        return JSONResponse(
            {"detail": f"{error}; re-import the latest {error.broker} {error.asset_class} statement"},
            status_code=status.HTTP_409_CONFLICT,
        )
