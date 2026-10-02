"""Portfolio routes. The user always comes from the session cookie (never from request data).

Presentation rounding happens only here, after every total is computed: money 2 dp, P&L % 2 dp,
prices 4 dp (ROUND_HALF_EVEN), quantities as stored. Numbers are serialised as strings so clients
never see binary floating point.
"""

from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.domain.portfolio import CurrencyTotals, HoldingValuation, Money, PortfolioSummary
from app.models import User
from app.services import portfolio_service

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_session)]

MONEY_PLACES = 2
PERCENT_PLACES = 2
PRICE_PLACES = 4
_FORMAT_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN)


class StatusCountsOut(BaseModel):
    holdings: int
    priced: int
    stale: int
    unavailable: int
    not_supported: int
    currency_mismatch: int
    not_configured: int


class TotalsOut(BaseModel):
    currency: str
    invested_value: str
    priced_invested_value: str
    current_value: str | None
    unrealized_pnl: str | None
    unrealized_pnl_pct: str | None
    unpriced_invested_value: str
    cost_floor_value: str  # mutual funds at cost: a labelled floor, not part of current_value
    counts: StatusCountsOut
    contains_stale_prices: bool
    oldest_price_as_of: datetime | None


class HoldingOut(BaseModel):
    holding_id: int
    instrument_name: str
    isin: str | None
    symbol: str | None
    asset_class: str
    source_broker: str
    native_currency: str
    statement_date: date
    quantity: str
    average_buy_price: str
    invested_value: str
    price_status: str
    price_issue: str | None
    price_as_of: datetime | None
    currency_mismatch: bool
    current_price: str | None
    current_value: str | None
    unrealized_pnl: str | None
    unrealized_pnl_pct: str | None
    cost_floor_value: str | None


class PortfolioOut(BaseModel):
    reporting_currency: str
    reporting_totals: TotalsOut | None
    fx_required: bool
    contains_stale_prices: bool
    oldest_price_as_of: datetime | None
    totals_by_currency: dict[str, TotalsOut]
    by_asset_class: dict[str, dict[str, TotalsOut]]
    by_broker: dict[str, dict[str, TotalsOut]]
    holdings: list[HoldingOut]


def _fixed(value: Decimal | None, places: int) -> str | None:
    if value is None:
        return None
    with localcontext(_FORMAT_CONTEXT):
        rounded = value.quantize(Decimal(1).scaleb(-places))
    return str(rounded.copy_abs() if rounded.is_zero() else rounded)  # never "-0.00"


def _money(value: Money | Decimal | None) -> str | None:
    return _fixed(value.amount if isinstance(value, Money) else value, MONEY_PLACES)


def _totals(t: CurrencyTotals) -> TotalsOut:
    return TotalsOut(
        currency=t.currency,
        invested_value=_money(t.invested_value) or "",
        priced_invested_value=_money(t.priced_invested_value) or "",
        current_value=_money(t.current_value),
        unrealized_pnl=_money(t.unrealized_pnl),
        unrealized_pnl_pct=_fixed(t.unrealized_pnl_pct, PERCENT_PLACES),
        unpriced_invested_value=_money(t.unpriced_invested_value) or "",
        cost_floor_value=_money(t.cost_floor_value) or "",
        counts=StatusCountsOut(
            holdings=t.holdings, priced=t.priced, stale=t.stale, unavailable=t.unavailable,
            not_supported=t.not_supported, currency_mismatch=t.currency_mismatch, not_configured=t.not_configured,
        ),
        contains_stale_prices=t.contains_stale_prices,
        oldest_price_as_of=t.oldest_price_as_of,
    )


def _holding(v: HoldingValuation) -> HoldingOut:
    p = v.position
    return HoldingOut(
        holding_id=p.holding_id,
        instrument_name=p.instrument_name,
        isin=p.isin,
        symbol=p.symbol,
        asset_class=p.asset_class,
        source_broker=p.source_broker,
        native_currency=p.native_currency,
        statement_date=p.statement_date,
        quantity=str(p.quantity),
        average_buy_price=_fixed(p.average_buy_price, PRICE_PLACES) or "",
        invested_value=_money(v.invested_value) or "",
        price_status=v.price.status.value,
        price_issue=v.price.issue.value if v.price.issue else None,
        price_as_of=v.price.as_of if v.priced else None,
        currency_mismatch=v.currency_mismatch,
        current_price=_fixed(v.current_price, PRICE_PLACES),
        current_value=_money(v.current_value),
        unrealized_pnl=_money(v.unrealized_pnl),
        unrealized_pnl_pct=_fixed(v.unrealized_pnl_pct, PERCENT_PLACES),
        cost_floor_value=_money(v.cost_floor_value),
    )


def _portfolio(summary: PortfolioSummary) -> PortfolioOut:
    return PortfolioOut(
        reporting_currency=summary.reporting_currency,
        reporting_totals=_totals(summary.reporting_totals) if summary.reporting_totals else None,
        fx_required=summary.fx_required,
        contains_stale_prices=summary.contains_stale_prices,
        oldest_price_as_of=summary.oldest_price_as_of,
        totals_by_currency={c: _totals(t) for c, t in summary.totals_by_currency.items()},
        by_asset_class={k: {c: _totals(t) for c, t in g.items()} for k, g in summary.by_asset_class.items()},
        by_broker={k: {c: _totals(t) for c, t in g.items()} for k, g in summary.by_broker.items()},
        holdings=[_holding(v) for v in summary.holdings],
    )


def _get(db: Session, user: User, *, force_refresh: bool) -> PortfolioOut:
    try:
        return _portfolio(portfolio_service.get_portfolio(db, user, force_refresh=force_refresh))
    except portfolio_service.PortfolioDataError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{error}; re-import the latest {error.broker} {error.asset_class} statement",
        ) from None


@router.get("", response_model=PortfolioOut)
def get_portfolio(user: CurrentUser, db: DbSession) -> PortfolioOut:
    return _get(db, user, force_refresh=False)


@router.post("/refresh", response_model=PortfolioOut)
def refresh_portfolio(user: CurrentUser, db: DbSession) -> PortfolioOut:
    """Re-fetch prices past their cache window (Module 6's 60-second floor still applies). CSRF-protected."""
    return _get(db, user, force_refresh=True)
