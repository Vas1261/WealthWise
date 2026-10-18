"""Pinned historical FX (FR-20, TC-05): cost dates, historical rates, market vs currency gain. Offline."""

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx2
import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.fx import FxIssue, FxRate, FxStatus, rates_on_or_before
from app.domain.holdings import CostDate, carry_cost_dates
from app.domain.portfolio import HoldingPosition, summarize, value_holding
from app.models import HistoricalFxRate
from app.repositories import portfolio_repository
from app.services import fx_service, import_service, market_service
from tests import test_zerodha_equity_adapter as zerodha
from tests.fake_frankfurter import FakeFrankfurter
from tests.portfolio_support import make_user, nh, seed, zerodha_file
from tests.test_auth import csrf_headers, logged_in_client
from tests.test_portfolio_service import RecordingMarket

JAN, APR, JUN, JUL = date(2026, 1, 31), date(2026, 4, 30), date(2026, 6, 30), date(2026, 7, 31)
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
ZERO = Decimal(0)


def cd(on: date, amount: str) -> CostDate:
    return CostDate(on, Decimal(amount))


def position(qty="10", avg="100", *, currency="INR", cost_dates=()):
    return HoldingPosition(1, "SYNTH", "INE000D01019", "SYNTH", "equity", "zerodha", currency,
                           Decimal(qty), Decimal(avg), JUN, "b", cost_dates)


def fresh(price, currency="INR"):
    return market_service.PriceResult(market_service.PriceStatus.FRESH, price=Decimal(price), as_of=NOW,
                                      currency=currency)


def live(rate):
    return {"INR": FxRate("INR", "EUR", FxStatus.FRESH, rate=Decimal(rate), rate_date=JUN, fetched_at=NOW)}


def hist(rate, on):
    return FxRate("INR", "EUR", FxStatus.HISTORICAL, rate=Decimal(rate), rate_date=on, fetched_at=NOW)


# --- Cost dates carried between holdings statements -------------------------------------------------

def test_first_statement_dates_the_whole_cost_to_it():
    assert carry_cost_dates((), ZERO, ZERO, Decimal(10), Decimal(1000), JUL) == (cd(JUL, "1000"),)


def test_units_still_held_keep_their_dates_and_only_added_cost_is_new():
    before = (cd(JAN, "600"), cd(APR, "400"))  # 10 units costing 1000
    # 5 more bought at 120: 15 units costing 1600
    assert carry_cost_dates(before, Decimal(10), Decimal(1000), Decimal(15), Decimal(1600), JUL) == (
        cd(JAN, "600"), cd(APR, "400"), cd(JUL, "600"))


def test_a_sale_scales_the_earlier_dates_and_dates_nothing_new():
    before = (cd(JAN, "600"), cd(APR, "400"))
    # 4 of 10 sold; the average cost (100) is unchanged, so 6 units cost 600
    assert carry_cost_dates(before, Decimal(10), Decimal(1000), Decimal(6), Decimal(600), JUL) == (
        cd(JAN, "360"), cd(APR, "240"))


def test_bonus_or_split_shares_date_nothing_new():
    before = (cd(JAN, "1000"),)
    assert carry_cost_dates(before, Decimal(10), Decimal(1000), Decimal(20), Decimal(1000), JUL) == before


def test_nothing_paid_means_nothing_to_pin():
    assert carry_cost_dates((), ZERO, ZERO, Decimal(5), ZERO, JUL) == ()


def test_a_weekend_or_holiday_takes_the_latest_earlier_rate():
    series = [(date(2026, 1, 5), Decimal("0.0096")), (date(2026, 1, 2), Decimal("0.0095"))]
    assert rates_on_or_before(series, [date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 1)]) == {
        date(2026, 1, 4): (Decimal("0.0095"), date(2026, 1, 2)),
        date(2026, 1, 5): (Decimal("0.0096"), date(2026, 1, 5)),
    }


# --- Market gain vs currency gain ---------------------------------------------------------------------

def test_gain_splits_into_market_and_currency_gain():
    # INR 1000 paid half on 31 Jan (rate 0.0100) and half on 30 Apr (0.0104); now worth INR 1200 at 0.0105
    valuations = [value_holding(position(cost_dates=(cd(JAN, "500"), cd(APR, "500"))), fresh("120"))]
    pinned = {"INR": {JAN: hist("0.0100", JAN), APR: hist("0.0104", APR)}}
    summary = summarize(valuations, "EUR", live("0.0105"), pinned)
    holding, r = summary.holdings[0], summary.reporting_totals
    assert (holding.pinned_rate, holding.pinned_cost.amount) == (Decimal("0.0102"), Decimal("10.2000"))
    assert r.invested_value_pinned == r.priced_invested_value_pinned == Decimal("10.2000")
    assert r.unrealized_pnl == Decimal("2.1000")  # market gain: price change at today's rate
    assert r.currency_gain == Decimal("0.3000")  # 10.50 at today's rate - 10.20 at the pinned rates
    assert r.total_gain == r.unrealized_pnl + r.currency_gain == r.current_value - Decimal("10.2")
    assert str(r.total_gain_pct).startswith("23.52941176470588235294117647")  # 2.4 / 10.2 x 100
    assert summary.pinned_fx_unavailable == []


def test_tc05_a_live_rate_change_never_moves_the_pinned_cost():
    valuations = [value_holding(position(), fresh("110"))]  # no cost dates: pinned at the statement date
    pinned = {"INR": {JUN: hist("0.0100", JUN)}}
    before = summarize(valuations, "EUR", live("0.0105"), pinned).reporting_totals
    after = summarize(valuations, "EUR", live("0.0090"), pinned).reporting_totals
    assert before.invested_value_pinned == after.invested_value_pinned == Decimal("10.0000")
    assert before.invested_value != after.invested_value


def test_a_missing_pinned_rate_gives_no_pinned_figure_at_all():
    valuations = [value_holding(position(cost_dates=(cd(JAN, "500"), cd(APR, "500"))), fresh("120"))]
    summary = summarize(valuations, "EUR", live("0.0105"), {"INR": {JAN: hist("0.0100", JAN)}})
    r = summary.reporting_totals
    assert summary.pinned_fx_unavailable == ["INR"] and summary.holdings[0].pinned_cost is None
    assert (r.invested_value_pinned, r.currency_gain, r.total_gain) == (None, None, None)
    assert r.current_value is not None  # the live conversion is unaffected


def test_holdings_in_the_reporting_currency_pin_at_exactly_one():
    summary = summarize([value_holding(position(currency="EUR"), fresh("110", "EUR"))], "EUR", {}, {})
    r = summary.reporting_totals
    assert summary.holdings[0].pinned_rate == Decimal(1)
    assert (r.currency_gain, r.total_gain) == (ZERO, r.unrealized_pnl)


def test_unpriced_holdings_count_in_pinned_cost_but_not_in_gains():
    unpriced = market_service.PriceResult(market_service.PriceStatus.UNAVAILABLE)
    valuations = [value_holding(position(), fresh("110")), value_holding(position("5", "20"), unpriced)]
    r = summarize(valuations, "EUR", live("0.0105"), {"INR": {JUN: hist("0.0100", JUN)}}).reporting_totals
    assert (r.invested_value_pinned, r.priced_invested_value_pinned) == (Decimal("11.0000"), Decimal("10.0000"))
    assert r.currency_gain == Decimal("10.5") - Decimal("10")


# --- FX service: historical rates are fetched once and kept --------------------------------------------

@pytest.fixture
def frankfurter(monkeypatch):
    fake = FakeFrankfurter()
    monkeypatch.setattr(fx_service, "default_client", fake.client)
    return fake


@pytest.mark.usefixtures("migrated_db")
def test_historical_rates_are_fetched_in_one_request_and_never_change(frankfurter):
    days = [date(2026, 1, 4), date(2026, 1, 5)]
    frankfurter.history[("INR", "EUR")] = {"2026-01-02": "0.0095", "2026-01-05": "0.0096"}
    with SessionLocal() as db:
        first = fx_service.get_historical_rates(db, "INR", "EUR", days, now=NOW)
        frankfurter.history[("INR", "EUR")] = {"2026-01-02": "0.5", "2026-01-05": "0.5"}  # a later revision
        second = fx_service.get_historical_rates(db, "INR", "EUR", days, now=NOW)
    assert first == second
    sunday = first[date(2026, 1, 4)]
    assert (sunday.status, sunday.rate, sunday.rate_date) == (FxStatus.HISTORICAL, Decimal("0.0095"), date(2026, 1, 2))
    assert frankfurter.history_calls() == ["/v2/rates"]


@pytest.mark.usefixtures("migrated_db")
def test_a_failed_fetch_pins_nothing_and_is_tried_again_later(frankfurter):
    frankfurter.history[("INR", "EUR")] = httpx2.Response(503)
    with SessionLocal() as db:
        failed = fx_service.get_historical_rates(db, "INR", "EUR", [JUN], now=NOW)[JUN]
        frankfurter.history[("INR", "EUR")] = {"2026-06-30": "0.0101"}
        pinned = fx_service.get_historical_rates(db, "INR", "EUR", [JUN], now=NOW)[JUN]
    assert (failed.status, failed.issue) == (FxStatus.UNAVAILABLE, FxIssue.SERVICE_UNAVAILABLE)
    assert (pinned.status, pinned.rate) == (FxStatus.HISTORICAL, Decimal("0.0101"))


@pytest.mark.usefixtures("migrated_db")
def test_a_date_without_any_earlier_rate_is_unavailable(frankfurter):
    frankfurter.history[("INR", "EUR")] = {}
    with SessionLocal() as db:
        rate = fx_service.get_historical_rates(db, "INR", "EUR", [JUN], now=NOW)[JUN]
        assert db.execute(select(HistoricalFxRate)).scalars().all() == []
    assert (rate.status, rate.issue) == (FxStatus.UNAVAILABLE, FxIssue.NO_RATE_FOR_DATE)


@pytest.mark.usefixtures("migrated_db")
def test_same_currency_needs_no_request(frankfurter):
    with SessionLocal() as db:
        assert fx_service.get_historical_rates(db, "INR", "INR", [JUN])[JUN].status == FxStatus.IDENTITY
    assert frankfurter.calls == []


# --- Import: cost dates carried over and rates pinned at import time ------------------------------------

def _zerodha(tmp_path, name, qty, avg, as_on):
    holding = zerodha.holding(**{"Quantity Available": qty, "Average Price": avg})
    return zerodha_file(tmp_path / name, holdings=[holding], as_on=as_on)


@pytest.mark.usefixtures("migrated_db")
def test_import_carries_equity_cost_dates_and_pins_their_rates(tmp_path, frankfurter):
    with SessionLocal() as db:
        user_id = make_user(db, "carry@example.com", reporting_currency="EUR").id
    with SessionLocal() as db:
        import_service.import_statement(db, user_id, _zerodha(tmp_path, "a.xlsx", 10.0, 100.0, "2026-01-31"), "a.xlsx")
        import_service.import_statement(db, user_id, _zerodha(tmp_path, "b.xlsx", 15.0, 120.0, "2026-07-31"), "b.xlsx")
        (position,) = effective_positions(db, user_id)
        pinned = db.execute(select(HistoricalFxRate.requested_date).order_by(HistoricalFxRate.requested_date)).all()
    # 10 units costing 1000 carried from January; the 800 added by July (1800 - 1000) dated to July
    assert position.cost_dates == (cd(JAN, "1000"), cd(JUL, "800"))
    assert [row[0] for row in pinned] == [JAN, JUL]  # pinned when each statement was imported


@pytest.mark.usefixtures("migrated_db")
def test_import_succeeds_when_rates_cannot_be_pinned(tmp_path, frankfurter):
    frankfurter.history[("INR", "EUR")] = httpx2.Response(503)
    with SessionLocal() as db:
        user_id = make_user(db, "offline@example.com", reporting_currency="EUR").id
        outcome = import_service.import_statement(db, user_id, _zerodha(tmp_path, "a.xlsx", 10.0, 100.0, "2026-06-30"),
                                                  "a.xlsx")
    assert outcome.current


def effective_positions(db, user_id):
    return portfolio_repository.get_effective_positions(db, user_id)


# --- TC-05 through the API ----------------------------------------------------------------------------------

@pytest.mark.usefixtures("migrated_db")
def test_tc05_api_pinned_value_identical_before_and_after_a_live_rate_change(frankfurter, monkeypatch):
    monkeypatch.setattr(market_service, "get_prices", RecordingMarket({"INE000D01019": fresh("110")}))
    client = logged_in_client("tc05@example.com")
    response = client.patch("/api/auth/me", json={"reporting_currency": "EUR"}, headers=csrf_headers(client))
    assert response.status_code == 200
    with SessionLocal() as db:
        seed(db, client.get("/api/auth/me").json()["id"], "zerodha", "equity", JUN, [nh(qty="10", avg="100")])
    frankfurter.history[("INR", "EUR")] = {"2026-06-30": "0.0100"}
    frankfurter.rates[("INR", "EUR")] = "0.0105"
    before = client.get("/api/portfolio").json()
    frankfurter.rates[("INR", "EUR")] = "0.0090"
    fx_service.clear_cache()
    after = client.get("/api/portfolio").json()

    assert before["reporting_totals"]["invested_value"] != after["reporting_totals"]["invested_value"]  # live
    for body in (before, after):
        totals, holding = body["reporting_totals"], body["holdings"][0]
        assert (totals["invested_value_pinned"], holding["pinned_cost"], holding["pinned_fx_rate"]) == (
            "10.00", "10.00", "0.010000")
        assert (holding["cost_dates_from"], holding["cost_date_count"]) == ("2026-06-30", 1)
    gains = before["reporting_totals"]["currency_gain"], after["reporting_totals"]["currency_gain"]
    assert gains == ("0.50", "-1.00")  # 10.50 - 10.00, then 9.00 - 10.00
    assert before["pinned_fx_unavailable"] == after["pinned_fx_unavailable"] == []
