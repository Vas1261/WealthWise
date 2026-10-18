"""Portfolio Service orchestration: effective holdings + Module 6 prices -> domain valuation."""

import ast
import socket
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest
from pydantic import SecretStr

from app.core.database import SessionLocal
from app.domain.market import PriceIssue, PriceResult, PriceStatus
from app.integrations.upstox import UpstoxClient
from app.services import market_service, portfolio_service
from app.services.portfolio_service import PortfolioDataError
from tests.portfolio_support import make_user, nh, seed
from tests.test_market_service import FakeUpstox

pytestmark = pytest.mark.usefixtures("migrated_db")

T0 = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)
D1, D2 = date(2026, 7, 31), date(2026, 8, 31)
APP = Path(__file__).resolve().parents[1] / "app"
PORTFOLIO_MODULES = ["domain/portfolio.py", "repositories/portfolio_repository.py", "repositories/import_repository.py",
                     "services/portfolio_service.py", "services/import_service.py", "api/portfolio.py"]


class RecordingMarket:
    """Stands in for market_service.get_prices; records every call and answers per ISIN."""

    def __init__(self, prices: dict[str, PriceResult] | None = None):
        self.prices = prices or {}
        self.calls: list[tuple[list, bool]] = []

    def __call__(self, db, instruments, *, force_refresh=False, **_):
        self.calls.append((list(instruments), force_refresh))
        return [self.prices.get(ref.isin, PriceResult(PriceStatus.UNAVAILABLE, issue=PriceIssue.SERVICE_UNAVAILABLE))
                for ref in instruments]


def fresh(price, currency="INR", at=T0):
    return PriceResult(PriceStatus.FRESH, price=Decimal(price), as_of=at, currency=currency)


@pytest.fixture
def user():
    with SessionLocal() as db:
        return make_user(db, "owner@example.com")


@pytest.fixture
def market(monkeypatch):
    recorder = RecordingMarket()
    monkeypatch.setattr(market_service, "get_prices", recorder)
    return recorder


def portfolio(user, **kwargs):
    with SessionLocal() as db:
        return portfolio_service.get_portfolio(db, user, **kwargs)


def seed_mixed(user_id):
    with SessionLocal() as db:
        seed(db, user_id, "zerodha", "equity", D1, [nh("SYNTHA", "INE000D01019", "SYNTHA", "10", "100"),
                                                    nh("SYNTHB", "INE000E01025", "SYNTHB", "5", "200")])
        seed(db, user_id, "groww", "equity", D1, [nh("Synthetic Alpha Ltd", "INE000D01019", None, "4", "90",
                                                     broker="groww")])
        seed(db, user_id, "groww", "mutual_fund", D1, [nh("Synthetic Fund", None, None, "50", "20",
                                                          asset_class="mutual_fund", broker="groww")])


# --- Market Service interaction ----------------------------------------------------------------------

def test_one_get_prices_call_with_equities_only(user, market):
    seed_mixed(user.id)
    portfolio(user)
    assert len(market.calls) == 1
    refs, force = market.calls[0]
    assert force is False
    assert [(r.isin, r.asset_class) for r in refs] == [
        ("INE000D01019", "equity"), ("INE000D01019", "equity"), ("INE000E01025", "equity")]
    assert all(r.asset_class != "mutual_fund" for r in refs)  # funds are never sent for pricing


def test_forced_refresh_is_passed_to_market_service(user, market):
    seed_mixed(user.id)
    portfolio(user, force_refresh=True)
    assert market.calls[0][1] is True


def test_no_market_call_without_equities(user, market):
    with SessionLocal() as db:
        seed(db, user.id, "groww", "mutual_fund", D1, [nh("Fund", None, None, asset_class="mutual_fund",
                                                          broker="groww")])
    summary = portfolio(user)
    assert market.calls == []
    assert summary.holdings[0].price.status == PriceStatus.NOT_SUPPORTED


def test_each_holding_keeps_its_own_price_status(user, market):
    seed_mixed(user.id)
    market.prices = {"INE000D01019": fresh("120"),
                     "INE000E01025": PriceResult(PriceStatus.UNAVAILABLE, issue=PriceIssue.NOT_ENTITLED)}
    summary = portfolio(user)
    by_name = {v.position.instrument_name: v for v in summary.holdings}
    assert by_name["SYNTHA"].current_value.amount == Decimal("1200")
    assert by_name["Synthetic Alpha Ltd"].current_value.amount == Decimal("480")  # same ISIN, own position
    assert (by_name["SYNTHB"].current_value, by_name["SYNTHB"].price.issue) == (None, PriceIssue.NOT_ENTITLED)
    fund = by_name["Synthetic Fund"]
    assert (fund.price.status, fund.price.issue, fund.current_value, fund.cost_floor_value.amount) == (
        PriceStatus.NOT_SUPPORTED, PriceIssue.MUTUAL_FUND, None, Decimal("1000"))


def test_totals_and_breakdowns(user, market):
    seed_mixed(user.id)
    market.prices = {"INE000D01019": fresh("120"), "INE000E01025": fresh("150")}
    summary = portfolio(user)
    inr = summary.totals_by_currency["INR"]
    assert inr.invested_value == Decimal("1000") + Decimal("1000") + Decimal("360") + Decimal("1000")
    assert inr.current_value == Decimal("1200") + Decimal("750") + Decimal("480")
    assert inr.unrealized_pnl == inr.current_value - Decimal("2360")
    assert inr.cost_floor_value == Decimal("1000") and inr.unpriced_invested_value == Decimal("1000")
    reporting = summary.reporting_totals
    assert (reporting.invested_value, reporting.current_value) == (inr.invested_value, inr.current_value)
    assert (reporting.unrealized_pnl, reporting.cost_floor_value) == (inr.unrealized_pnl, inr.cost_floor_value)
    assert reporting.fx_rates["INR"].status == "identity" and summary.fx_required is False
    assert set(summary.by_broker) == {"zerodha", "groww"} and set(summary.by_asset_class) == {"equity", "mutual_fund"}


def test_effective_snapshot_used_for_valuation(user, market):
    with SessionLocal() as db:
        seed(db, user.id, "zerodha", "equity", D1, [nh("OLD", "INE000D01019", "SYNTHA", "999", "1")])
        seed(db, user.id, "zerodha", "equity", D2, [nh("NEW", "INE000D01019", "SYNTHA", "2", "10")])
    summary = portfolio(user)
    assert [v.position.instrument_name for v in summary.holdings] == ["NEW"]


def test_other_users_holdings_never_enter_the_calculation(user, market):
    with SessionLocal() as db:
        other = make_user(db, "intruder@example.com")
        seed(db, other.id, "zerodha", "equity", D2, [nh("THEIRS", "INE000E01025", "SYNTHB")])
        seed(db, user.id, "zerodha", "equity", D1, [nh("MINE")])
    summary = portfolio(user)
    assert [v.position.instrument_name for v in summary.holdings] == ["MINE"]
    assert [r.isin for r in market.calls[0][0]] == ["INE000D01019"]


def test_empty_portfolio(user, market):
    summary = portfolio(user)
    assert (summary.holdings, summary.totals_by_currency, market.calls) == ([], {}, [])


def test_reporting_currency_other_than_holdings_requires_fx(market):
    with SessionLocal() as db:
        eur_user = make_user(db, "eur@example.com", reporting_currency="EUR")
        seed(db, eur_user.id, "zerodha", "equity", D1, [nh()])
    market.prices = {"INE000D01019": fresh("110")}
    summary = portfolio(eur_user)
    # FX is disabled in offline tests: no rate, so no converted total and INR is reported as unavailable
    assert (summary.reporting_currency, summary.reporting_totals, summary.fx_required) == ("EUR", None, True)
    assert summary.fx_unavailable == ["INR"]
    assert summary.totals_by_currency["INR"].current_value == Decimal("1100")  # native total stays available


# --- Invalid persisted data ------------------------------------------------------------------------

def test_invalid_persisted_holding_raises_controlled_error_before_any_market_call(user, market, monkeypatch):
    seed_mixed(user.id)
    real = portfolio_service.portfolio_repository.get_effective_positions

    def corrupt(db, user_id):
        from dataclasses import replace
        positions = real(db, user_id)
        return [replace(positions[0], quantity=Decimal("0")), *positions[1:]]

    monkeypatch.setattr(portfolio_service.portfolio_repository, "get_effective_positions", corrupt)
    with pytest.raises(PortfolioDataError) as raised:
        portfolio(user)
    assert (raised.value.broker, raised.value.asset_class) == ("groww", "equity")
    assert market.calls == []  # no API credits spent on corrupt data


# --- Through the real Module 6 service (fake Upstox transport, no network) ----------------------------

def test_through_market_service_duplicate_isins_priced_once(user, monkeypatch):
    seed_mixed(user.id)
    fake = FakeUpstox()
    fake.quotes = {"NSE_EQ|INE000D01019": ("SYNTHA", 120), "NSE_EQ|INE000E01025": ("SYNTHB", 150)}
    client = UpstoxClient(SecretStr("unit-test-token-not-a-real-secret-0003"),
                          transport=httpx2.MockTransport(fake.handle))
    monkeypatch.setattr(market_service, "default_client", lambda: client)
    summary = portfolio(user)
    assert len(fake.calls) == 1  # one request for the whole portfolio
    assert fake.requested_isins() == ["INE000D01019", "INE000E01025"]  # two distinct ISINs, three positions
    assert {v.price.status for v in summary.holdings if v.position.asset_class == "equity"} == {PriceStatus.FRESH}
    portfolio(user)  # second request inside the cache window: no new HTTP calls
    assert len(fake.calls) == 1


def test_not_configured_and_no_network_or_fx_calls(user, monkeypatch):
    seed_mixed(user.id)

    def no_network(*args, **kwargs):
        raise AssertionError("outbound network call attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    summary = portfolio(user)  # the token is blank in tests: Module 6 reports NOT_CONFIGURED without requests
    equities = [v for v in summary.holdings if v.position.asset_class == "equity"]
    assert {(v.price.status, v.price.issue) for v in equities} == {(PriceStatus.UNAVAILABLE, PriceIssue.NOT_CONFIGURED)}
    assert summary.totals_by_currency["INR"].not_configured == 3
    assert summary.totals_by_currency["INR"].current_value is None


def test_stale_prices_flow_into_totals(user, market):
    seed_mixed(user.id)
    old = T0 - timedelta(days=1)
    market.prices = {"INE000D01019": PriceResult(PriceStatus.STALE, price=Decimal("110"), as_of=old, currency="INR",
                                                 issue=PriceIssue.SERVICE_UNAVAILABLE),
                     "INE000E01025": fresh("150")}
    summary = portfolio(user)
    assert summary.contains_stale_prices and summary.oldest_price_as_of == old
    assert summary.totals_by_currency["INR"].stale == 2  # both positions of the stale ISIN


# --- Architecture --------------------------------------------------------------------------------------

def _imports(relative: str) -> set[str]:
    tree = ast.parse((APP / relative).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("module", PORTFOLIO_MODULES)
def test_portfolio_code_never_bypasses_market_service_or_does_fx(module):
    imports = _imports(module)
    assert not any("integrations" in name or "upstox" in name or "httpx" in name for name in imports)
    assert "frankfurter" not in (APP / module).read_text().lower()


def test_portfolio_domain_is_pure():
    allowed_roots = {"collections", "dataclasses", "datetime", "decimal", "app"}
    imports = _imports("domain/portfolio.py")
    assert {name.split(".")[0] for name in imports} <= allowed_roots
    assert all(name.startswith("app.domain") for name in imports if name.startswith("app"))
