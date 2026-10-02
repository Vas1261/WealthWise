"""Seed one account with all three real (private, git-ignored) statements and value it (spec 5, step 7).

Offline: the Twelve Data key is blank in tests, so equities are valued as UNAVAILABLE / NOT_CONFIGURED
and funds as NOT_SUPPORTED; what is verified is persistence, snapshot selection and cost totals.
Assertions report only counts or booleans, never names, ISINs or amounts.
"""

from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest

from app.adapters.groww_equity import GrowwEquityAdapter
from app.adapters.groww_mutual_fund import GrowwMutualFundAdapter
from app.adapters.zerodha_equity import ZerodhaEquityAdapter
from app.core.database import SessionLocal
from app.domain.market import PriceIssue, PriceStatus
from app.services import import_service, portfolio_service
from tests.portfolio_support import make_user

SAMPLES = Path(__file__).resolve().parents[2] / "sample_data"
FILES = {
    "groww_equity": sorted((SAMPLES / "groww" / "equity").glob("Stocks_Holdings_Statement_*.xlsx")),
    "zerodha_equity": sorted((SAMPLES / "zerodha" / "equity").glob("holdings-*.xlsx")),
    "groww_mf": sorted((SAMPLES / "groww" / "mutual_funds").glob("Mutual_Funds_Order_History_*.xlsx")),
}
ADAPTERS = {"groww_equity": GrowwEquityAdapter(), "zerodha_equity": ZerodhaEquityAdapter(),
            "groww_mf": GrowwMutualFundAdapter()}
STORED = Decimal("0.000001")  # holdings columns are Numeric(20,6)

pytestmark = [
    pytest.mark.usefixtures("migrated_db"),
    pytest.mark.skipif(
        not all(FILES.values()), reason="private sample files not present (sample_data/ is git-ignored)"
    ),
]


@pytest.fixture
def seeded_user():
    with SessionLocal() as db:
        user = make_user(db, "sample-owner@example.com")
        outcomes = [import_service.import_statement(db, user.id, str(paths[0]), paths[0].name)
                    for paths in FILES.values()]
    return user, outcomes


def test_all_three_statements_import_and_become_current(seeded_user):
    _, outcomes = seeded_user
    assert [(o.stored, o.current, o.rows_failed) for o in outcomes] == [(True, True, 0)] * 3
    assert {(o.broker, o.asset_class) for o in outcomes} == {("groww", "equity"), ("zerodha", "equity"),
                                                             ("groww", "mutual_fund")}


def test_portfolio_matches_independently_parsed_statements(seeded_user):
    user, _ = seeded_user
    expected_holdings = 0
    expected_invested = Decimal(0)
    for key, paths in FILES.items():
        result = ADAPTERS[key].parse(str(paths[0]), "independent")
        expected_holdings += len(result.holdings)
        for h in result.holdings:  # values as persisted (6 dp), multiplied exactly
            expected_invested += (h.quantity.quantize(STORED, ROUND_HALF_EVEN)
                                  * h.average_buy_price.quantize(STORED, ROUND_HALF_EVEN))
    with SessionLocal() as db:
        summary = portfolio_service.get_portfolio(db, user)
    totals = summary.totals_by_currency["INR"]
    holding_count_matches = len(summary.holdings) == totals.holdings == expected_holdings
    invested_matches = totals.invested_value == expected_invested
    assert holding_count_matches
    assert invested_matches
    assert set(summary.totals_by_currency) == {"INR"} and summary.fx_required is False


def test_offline_valuation_is_explicitly_unpriced(seeded_user):
    user, _ = seeded_user
    with SessionLocal() as db:
        summary = portfolio_service.get_portfolio(db, user)
    equities = [v for v in summary.holdings if v.position.asset_class == "equity"]
    funds = [v for v in summary.holdings if v.position.asset_class == "mutual_fund"]
    assert equities and funds
    assert all((v.price.status, v.price.issue) == (PriceStatus.UNAVAILABLE, PriceIssue.NOT_CONFIGURED)
               for v in equities)
    assert all(v.price.status == PriceStatus.NOT_SUPPORTED and v.cost_floor_value is not None for v in funds)
    assert all(v.current_value is None and v.unrealized_pnl is None for v in summary.holdings)
    totals = summary.totals_by_currency["INR"]
    assert totals.current_value is None and totals.unpriced_invested_value == totals.invested_value
