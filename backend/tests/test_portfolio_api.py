"""Portfolio API: authentication, CSRF on refresh, user scoping, string serialisation and rounding."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.domain.market import PriceIssue, PriceResult, PriceStatus
from app.main import app
from app.services import market_service, portfolio_service
from tests.portfolio_support import nh, seed
from tests.test_auth import csrf_headers, logged_in_client
from tests.test_portfolio_service import RecordingMarket

pytestmark = pytest.mark.usefixtures("migrated_db")

T0 = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)
D1 = date(2026, 7, 31)


def user_id_of(client: TestClient) -> int:
    return client.get("/api/auth/me").json()["id"]


@pytest.fixture
def market(monkeypatch):
    recorder = RecordingMarket()
    monkeypatch.setattr(market_service, "get_prices", recorder)
    return recorder


@pytest.fixture
def alice():
    return logged_in_client("alice@example.com")


def test_requires_authentication():
    client = TestClient(app)
    assert client.get("/api/portfolio").status_code == 401
    assert client.post("/api/portfolio/refresh", headers=csrf_headers(client)).status_code == 401


def test_refresh_requires_csrf(alice, market):
    assert alice.post("/api/portfolio/refresh").status_code == 403
    assert alice.post("/api/portfolio/refresh", headers={settings.csrf_header_name: "wrong"}).status_code == 403
    assert market.calls == []
    response = alice.post("/api/portfolio/refresh", headers=csrf_headers(alice))
    assert response.status_code == 200
    assert market.calls == []  # empty portfolio: nothing to price


def test_refresh_forces_and_get_does_not(alice, market):
    with SessionLocal() as db:
        seed(db, user_id_of(alice), "zerodha", "equity", D1, [nh()])
    alice.get("/api/portfolio")
    alice.post("/api/portfolio/refresh", headers=csrf_headers(alice))
    assert [force for _, force in market.calls] == [False, True]


def test_empty_portfolio_response(alice, market):
    body = alice.get("/api/portfolio").json()
    assert body == {
        "reporting_currency": "INR", "reporting_totals": None, "fx_required": False, "contains_stale_prices": False,
        "oldest_price_as_of": None, "totals_by_currency": {}, "by_asset_class": {}, "by_broker": {}, "holdings": [],
    }


def test_serialisation_rounding_and_strings(alice, market):
    with SessionLocal() as db:
        seed(db, user_id_of(alice), "zerodha", "equity", D1, [nh(qty="3", avg="33.333333")])
        seed(db, user_id_of(alice), "groww", "mutual_fund", D1, [nh("Synthetic Fund", None, None, "0.125", "1",
                                                                    asset_class="mutual_fund", broker="groww")])
    market.prices = {"INE000D01019": PriceResult(PriceStatus.FRESH, price=Decimal("40.123456"), as_of=T0,
                                                 currency="INR")}
    body = alice.get("/api/portfolio").json()
    fund, equity = body["holdings"]  # ordered by broker, asset class
    assert equity == {
        "holding_id": equity["holding_id"], "instrument_name": "SYNTHA", "isin": "INE000D01019", "symbol": "SYNTHA",
        "asset_class": "equity", "source_broker": "zerodha", "native_currency": "INR", "statement_date": "2026-07-31",
        "quantity": "3.000000",  # stored precision preserved
        "average_buy_price": "33.3333",  # price: 4 dp
        "invested_value": "100.00",  # 99.999999 -> money: 2 dp
        "price_status": "fresh", "price_issue": None, "price_as_of": "2026-06-30T10:00:00Z", "currency_mismatch": False,
        "current_price": "40.1235",
        "current_value": "120.37",  # 120.370368
        "unrealized_pnl": "20.37",  # 20.370369
        "unrealized_pnl_pct": "20.37",  # 20.37037120...
        "cost_floor_value": None,
    }
    assert fund["invested_value"] == "0.12"  # 0.125 rounds half-even, never via float
    assert fund["cost_floor_value"] == "0.12" and fund["current_value"] is None
    assert (fund["price_status"], fund["price_issue"]) == ("not_supported", "mutual_fund")
    totals = body["totals_by_currency"]["INR"]
    assert totals["invested_value"] == "100.12"  # 99.999999 + 0.125 = 100.124999 (total computed before rounding)
    assert totals["current_value"] == "120.37" and totals["cost_floor_value"] == "0.12"
    assert totals["counts"] == {"holdings": 2, "priced": 1, "stale": 0, "unavailable": 0, "not_supported": 1,
                                "currency_mismatch": 0, "not_configured": 0}
    assert body["reporting_totals"] == totals and body["fx_required"] is False

    def numbers_are_strings(node):
        if isinstance(node, dict):
            return all(numbers_are_strings(v) for k, v in node.items() if k not in ("counts", "holding_id"))
        if isinstance(node, list):
            return all(numbers_are_strings(v) for v in node)
        return not isinstance(node, float)

    assert numbers_are_strings(body)


def test_negative_zero_is_never_shown(alice, market):
    with SessionLocal() as db:
        seed(db, user_id_of(alice), "zerodha", "equity", D1, [nh(qty="1", avg="10.0001")])
    market.prices = {"INE000D01019": PriceResult(PriceStatus.FRESH, price=Decimal("10"), as_of=T0, currency="INR")}
    equity = alice.get("/api/portfolio").json()["holdings"][0]
    # P&L -0.0001 and -0.000999...% both round to zero, shown as "0.00", never "-0.00"
    assert equity["unrealized_pnl"] == "0.00" and equity["unrealized_pnl_pct"] == "0.00"


def test_unpriced_holding_serialises_nulls(alice, market):
    with SessionLocal() as db:
        seed(db, user_id_of(alice), "zerodha", "equity", D1, [nh()])
    market.prices = {"INE000D01019": PriceResult(PriceStatus.UNAVAILABLE, issue=PriceIssue.NOT_CONFIGURED)}
    body = alice.get("/api/portfolio").json()
    h = body["holdings"][0]
    assert (h["current_price"], h["current_value"], h["unrealized_pnl"], h["unrealized_pnl_pct"]) == (None,) * 4
    assert (h["price_status"], h["price_issue"]) == ("unavailable", "not_configured")
    t = body["totals_by_currency"]["INR"]
    assert (t["current_value"], t["unrealized_pnl"], t["unpriced_invested_value"]) == (None, None, "1000.00")


def test_client_supplied_user_id_is_ignored_and_users_are_isolated(alice, market):
    bob = logged_in_client("bob@example.com")
    with SessionLocal() as db:
        seed(db, user_id_of(alice), "zerodha", "equity", D1, [nh("ALICE")])
        seed(db, user_id_of(bob), "zerodha", "equity", D1, [nh("BOB", "INE000E01025")])
    for response in (alice.get("/api/portfolio", params={"user_id": user_id_of(bob)}),
                     alice.post("/api/portfolio/refresh", params={"user_id": user_id_of(bob)},
                                json={"user_id": user_id_of(bob)}, headers=csrf_headers(alice))):
        assert [h["instrument_name"] for h in response.json()["holdings"]] == ["ALICE"]
    assert [h["instrument_name"] for h in bob.get("/api/portfolio").json()["holdings"]] == ["BOB"]


def test_invalid_persisted_data_is_a_controlled_409(alice, market, monkeypatch):
    with SessionLocal() as db:
        seed(db, user_id_of(alice), "zerodha", "equity", D1, [nh(qty="7.5", avg="123.45")])
    real = portfolio_service.portfolio_repository.get_effective_positions
    monkeypatch.setattr(portfolio_service.portfolio_repository, "get_effective_positions",
                        lambda db, user_id: [replace(p, average_buy_price=Decimal("-1")) for p in real(db, user_id)])
    response = alice.get("/api/portfolio")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "zerodha equity" in detail and "re-import" in detail
    assert "7.5" not in detail and "123.45" not in detail and "-1" not in detail  # no values echoed
