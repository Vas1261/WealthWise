"""Portfolio Service (spec 4.3): the authenticated user's effective holdings valued with live prices.

Orchestration only: the repository selects the effective holdings, Module 6's Market Service
supplies prices (one get_prices call per request, equities only; it resolves and prices each ISIN
once and applies its cache and request budget), and the domain does every calculation.
Mutual funds are never sent for pricing; they carry Module 6's own NOT_SUPPORTED result.
Top-level totals are converted into the user's reporting currency with Module 8 FX rates (cached;
a forced price refresh does not force FX). FX never fills in a missing or mismatched market price.
"""

from sqlalchemy.orm import Session

from app.domain.holdings import ASSET_CLASS_MUTUAL_FUND
from app.domain.market import PriceIssue, PriceResult, PriceStatus
from app.domain.portfolio import InvalidPositionError, PortfolioSummary, summarize, validate_position, value_holding
from app.models import User
from app.repositories import portfolio_repository
from app.services import fx_service, market_service
from app.services.market_service import InstrumentRef

MUTUAL_FUND_PRICE = PriceResult(PriceStatus.NOT_SUPPORTED, issue=PriceIssue.MUTUAL_FUND)


class PortfolioDataError(Exception):
    """Persisted holdings violate import invariants; the portfolio cannot be valued honestly."""

    def __init__(self, broker: str, asset_class: str) -> None:
        super().__init__(f"Stored {broker} {asset_class} holdings are invalid")
        self.broker = broker
        self.asset_class = asset_class


def get_portfolio(db: Session, user: User, *, force_refresh: bool = False) -> PortfolioSummary:
    positions = portfolio_repository.get_effective_positions(db, user.id)
    for position in positions:  # before any market request: never spend API credits on corrupt data
        try:
            validate_position(position)
        except InvalidPositionError:
            raise PortfolioDataError(position.source_broker, position.asset_class) from None

    equities = [p for p in positions if p.asset_class != ASSET_CLASS_MUTUAL_FUND]
    prices: dict[int, PriceResult] = {}
    if equities:
        results = market_service.get_prices(
            db,
            [InstrumentRef(p.isin, p.symbol, p.asset_class) for p in equities],
            force_refresh=force_refresh,
        )
        prices = {p.holding_id: price for p, price in zip(equities, results, strict=True)}

    valuations = [
        value_holding(p, MUTUAL_FUND_PRICE if p.asset_class == ASSET_CLASS_MUTUAL_FUND else prices[p.holding_id])
        for p in positions
    ]
    currencies = [v.invested_value.currency for v in valuations]
    fx_rates = fx_service.get_rates(currencies, user.reporting_currency) if currencies else {}
    return summarize(valuations, user.reporting_currency, fx_rates)
