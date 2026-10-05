"""Reporting-currency conversion of portfolio totals (domain + service + API), offline."""

from datetime import UTC, date, datetime, timedelta
from decimal import Context, Decimal, localcontext

import pytest

from app.domain.fx import FxIssue, FxRate, FxStatus
from app.domain.market import PriceIssue, PriceResult, PriceStatus
from app.domain.portfolio import HoldingPosition, convert_totals, summarize, value_holding
from app.services import fx_service, market_service
from tests.fake_frankfurter import FakeFrankfurter
from tests.portfolio_support import make_user, nh, seed
from tests.test_portfolio_service import RecordingMarket

T0 = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)
_ids = iter(range(1, 10_000))


def position(qty="10", avg="100", *, currency="INR", asset_class="equity", broker="zerodha"):
    return HoldingPosition(next(_ids), "SYNTH", "INE000D01019", "SYNTH", asset_class, broker, currency,
                           Decimal(qty), Decimal(avg), date(2026, 6, 30), "b")


def fresh(price, currency="INR"):
    return PriceResult(PriceStatus.FRESH, price=Decimal(price), as_of=T0, currency=currency)


def fx(base, quote, rate, status=FxStatus.FRESH, issue=None):
    return FxRate(base, quote, status, rate=Decimal(rate) if rate is not None else None,
                  rate_date=date(2026, 6, 30), fetched_at=T0, issue=issue)


FUND = PriceResult(PriceStatus.NOT_SUPPORTED, issue=PriceIssue.MUTUAL_FUND)


# --- Domain conversion ------------------------------------------------------------------------------

def test_single_currency_converted_exactly():
    valuations = [value_holding(position("10", "100"), fresh("120"))]  # INR 1000 -> 1200
    summary = summarize(valuations, "EUR", {"INR": fx("INR", "EUR", "0.0105")})
    r = summary.reporting_totals
    assert (r.currency, r.invested_value, r.current_value, r.unrealized_pnl) == (
        "EUR", Decimal("10.5000"), Decimal("12.6000"), Decimal("2.1000"))
    assert r.unrealized_pnl_pct == Decimal("20")
    assert all(type(x) is Decimal for x in (r.invested_value, r.current_value, r.unrealized_pnl))
    assert summary.totals_by_currency["INR"].current_value == Decimal("1200")  # native totals untouched
    assert (summary.fx_required, summary.fx_unavailable) == (True, [])


def test_multiple_currencies_converted_and_summed_only_after_conversion():
    valuations = [
        value_holding(position("10", "100", currency="INR"), fresh("110", "INR")),  # INR 1000 -> 1100
        value_holding(position("2", "50", currency="USD"), fresh("60", "USD")),  # USD 100 -> 120
        value_holding(position("1", "40", currency="EUR"), fresh("30", "EUR")),  # EUR 40 -> 30
    ]
    rates = {"INR": fx("INR", "EUR", "0.01"), "USD": fx("USD", "EUR", "0.9")}
    r = summarize(valuations, "EUR", rates).reporting_totals
    assert r.invested_value == Decimal("10") + Decimal("90") + Decimal("40")
    assert r.current_value == Decimal("11") + Decimal("108") + Decimal("30")
    assert r.unrealized_pnl == r.current_value - r.priced_invested_value
    assert r.fx_rates["EUR"].status == FxStatus.IDENTITY and r.fx_rates["INR"].rate == Decimal("0.01")


def test_conversion_is_exact_without_rounding():
    valuations = [value_holding(position("3", "33.333333"), fresh("40.123456"))]
    rate = Decimal("0.0104999999999999999999")
    r = summarize(valuations, "USD", {"INR": fx("INR", "USD", rate)}).reporting_totals
    with localcontext(Context(prec=80)):  # the default 28-digit context would round the expected value
        assert r.invested_value == Decimal("99.999999") * rate  # exact product, no quantisation
        assert r.current_value == Decimal("120.370368") * rate
    assert len(r.invested_value.as_tuple().digits) > 28


def test_any_missing_rate_means_no_converted_total():
    valuations = [value_holding(position(currency="INR"), fresh("110")),
                  value_holding(position(currency="USD"), fresh("60", "USD"))]
    for rates in ({"INR": fx("INR", "EUR", "0.01")},
                  {"INR": fx("INR", "EUR", "0.01"),
                   "USD": fx("USD", "EUR", None, FxStatus.UNAVAILABLE, FxIssue.SERVICE_UNAVAILABLE)}):
        summary = summarize(valuations, "EUR", rates)
        assert (summary.reporting_totals, summary.fx_unavailable) == (None, ["USD"])  # never a partial total
        assert set(summary.totals_by_currency) == {"INR", "USD"}  # native totals remain


def test_rate_for_wrong_pair_is_not_used():
    valuations = [value_holding(position(currency="INR"), fresh("110"))]
    wrong = {"INR": fx("INR", "USD", "0.012")}  # quote is not the reporting currency
    assert summarize(valuations, "EUR", wrong).fx_unavailable == ["INR"]


def test_stale_rate_is_used_and_flagged():
    valuations = [value_holding(position(), fresh("110"))]
    stale = fx("INR", "EUR", "0.0105", FxStatus.STALE, FxIssue.SERVICE_UNAVAILABLE)
    r = summarize(valuations, "EUR", {"INR": stale}).reporting_totals
    assert r.fx_stale is True and r.current_value == Decimal("1100") * Decimal("0.0105")


def test_unpriced_bucket_contributes_cost_but_no_current_value():
    valuations = [value_holding(position("10", "100", currency="INR"), fresh("120")),
                  value_holding(position("5", "10", currency="USD"),
                                PriceResult(PriceStatus.UNAVAILABLE, issue=PriceIssue.NOT_CONFIGURED))]
    r = summarize(valuations, "EUR", {"INR": fx("INR", "EUR", "0.01"), "USD": fx("USD", "EUR", "0.9")}).reporting_totals
    assert r.current_value == Decimal("12")  # INR only
    assert r.priced_invested_value == Decimal("10") and r.unpriced_invested_value == Decimal("45")
    assert r.unrealized_pnl == Decimal("2")  # against priced cost only


def test_nothing_priced_gives_no_converted_current_value():
    valuations = [value_holding(position(), PriceResult(PriceStatus.UNAVAILABLE, issue=PriceIssue.NOT_CONFIGURED))]
    r = summarize(valuations, "EUR", {"INR": fx("INR", "EUR", "0.01")}).reporting_totals
    assert (r.current_value, r.unrealized_pnl, r.unrealized_pnl_pct) == (None, None, None)
    assert r.invested_value == Decimal("10")


def test_fund_cost_floor_converted_separately_and_never_current_value():
    valuations = [value_holding(position("10", "100"), fresh("120")),
                  value_holding(position("50", "20", asset_class="mutual_fund", broker="groww"), FUND)]
    r = summarize(valuations, "EUR", {"INR": fx("INR", "EUR", "0.01")}).reporting_totals
    assert r.cost_floor_value == Decimal("10")  # 1000 INR of fund cost
    assert r.current_value == Decimal("12")  # equity only; fund cost floor never added
    assert r.not_supported == 1


def test_price_currency_mismatch_is_not_fixed_by_fx():
    mismatched = value_holding(position(currency="INR"), fresh("1.3", "USD"))  # price in USD for an INR holding
    rates = {"INR": fx("INR", "EUR", "0.01"), "USD": fx("USD", "EUR", "0.9")}
    r = summarize([mismatched], "EUR", rates).reporting_totals
    assert (mismatched.current_value, r.current_value, r.currency_mismatch) == (None, None, 1)


def test_empty_portfolio_converts_nothing():
    assert convert_totals({}, {}, "EUR") == (None, [])


def test_breakdowns_and_holdings_stay_native():
    summary = summarize([value_holding(position(), fresh("110"))], "EUR", {"INR": fx("INR", "EUR", "0.01")})
    assert set(summary.by_broker["zerodha"]) == {"INR"} and summary.holdings[0].current_value.currency == "INR"


# --- Service and API integration (fake Frankfurter, no network) ------------------------------------------

@pytest.fixture
def frankfurter(monkeypatch):
    fake = FakeFrankfurter()
    monkeypatch.setattr(fx_service, "default_client", fake.client)
    return fake


@pytest.fixture
def market(monkeypatch):
    recorder = RecordingMarket({"INE000D01019": fresh("110")})
    monkeypatch.setattr(market_service, "get_prices", recorder)
    return recorder


@pytest.mark.usefixtures("migrated_db")
def test_service_converts_for_non_inr_reporting_currency(frankfurter, market):
    from app.core.database import SessionLocal
    from app.services import portfolio_service

    with SessionLocal() as db:
        user = make_user(db, "eur@example.com", reporting_currency="EUR")
        seed(db, user.id, "zerodha", "equity", date(2026, 7, 31), [nh(qty="10", avg="100")])
        seed(db, user.id, "groww", "mutual_fund", date(2026, 7, 31),
             [nh("Fund", None, None, "5", "20", asset_class="mutual_fund", broker="groww")])
        summary = portfolio_service.get_portfolio(db, user)
        portfolio_service.get_portfolio(db, user, force_refresh=True)  # refresh must not force FX
    r = summary.reporting_totals
    assert (r.currency, r.current_value, r.cost_floor_value) == ("EUR", Decimal("1100") * Decimal("0.0105"),
                                                                 Decimal("100") * Decimal("0.0105"))
    assert frankfurter.rate_calls() == ["/v2/rate/INR/EUR"]  # once: cached, not forced by refresh
    assert len(market.calls) == 2 and all(r.asset_class == "equity" for r in market.calls[0][0])
    fund = next(v for v in summary.holdings if v.position.asset_class == "mutual_fund")
    assert (fund.price.status, fund.current_value) == (PriceStatus.NOT_SUPPORTED, None)


@pytest.mark.usefixtures("migrated_db")
def test_inr_user_needs_no_fx_request(frankfurter, market):
    from app.core.database import SessionLocal
    from app.services import portfolio_service

    with SessionLocal() as db:
        user = make_user(db, "inr@example.com")
        seed(db, user.id, "zerodha", "equity", date(2026, 7, 31), [nh()])
        summary = portfolio_service.get_portfolio(db, user)
    assert frankfurter.calls == [] and summary.reporting_totals.fx_rates["INR"].status == FxStatus.IDENTITY


@pytest.mark.usefixtures("migrated_db")
def test_service_unavailable_rate_gives_null_reporting_totals(frankfurter, market):
    from app.core.database import SessionLocal
    from app.services import portfolio_service

    frankfurter.rates.pop(("INR", "EUR"))  # 422 for this pair
    with SessionLocal() as db:
        user = make_user(db, "eur2@example.com", reporting_currency="EUR")
        seed(db, user.id, "zerodha", "equity", date(2026, 7, 31), [nh()])
        summary = portfolio_service.get_portfolio(db, user)
    assert (summary.reporting_totals, summary.fx_unavailable) == (None, ["INR"])
    assert summary.totals_by_currency["INR"].current_value == Decimal("1100")


@pytest.mark.usefixtures("migrated_db")
def test_api_serialises_converted_totals_and_fx_details(frankfurter, market):
    from app.core.database import SessionLocal
    from tests.test_auth import csrf_headers, logged_in_client

    client = logged_in_client("eurapi@example.com")
    with SessionLocal() as db:
        from app.repositories import user_repository

        user = user_repository.get_by_email(db, "eurapi@example.com")
        user.reporting_currency = "EUR"
        db.commit()
        seed(db, user.id, "zerodha", "equity", date(2026, 7, 31), [nh(qty="10", avg="100")])
    body = client.get("/api/portfolio").json()
    r = body["reporting_totals"]
    assert (r["currency"], r["invested_value"], r["current_value"], r["unrealized_pnl"], r["unrealized_pnl_pct"]) == (
        "EUR", "10.50", "11.55", "1.05", "10.00")
    assert r["fx_rates"] == [{"currency": "INR", "rate": "0.010500", "rate_date": "2026-06-30", "status": "fresh",
                              "issue": None}]
    assert (r["fx_stale"], body["fx_required"], body["fx_unavailable"]) == (False, True, [])
    assert body["totals_by_currency"]["INR"]["current_value"] == "1100.00"
    refreshed = client.post("/api/portfolio/refresh", headers=csrf_headers(client)).json()
    assert refreshed["reporting_totals"]["current_value"] == "11.55"
    assert frankfurter.rate_calls() == ["/v2/rate/INR/EUR"]


def test_stale_rate_flows_to_api_flag():
    stale = fx("INR", "EUR", "0.0105", FxStatus.STALE, FxIssue.RATE_LIMITED)
    summary = summarize([value_holding(position(), fresh("110"))], "EUR", {"INR": stale})
    assert summary.reporting_totals.fx_stale and summary.reporting_totals.fx_rates["INR"].issue == FxIssue.RATE_LIMITED
    assert summary.reporting_totals.oldest_price_as_of == T0 - timedelta(0)


@pytest.mark.usefixtures("migrated_db")
def test_api_exposes_unavailable_fx_and_keeps_native_totals(frankfurter, market):
    from app.core.database import SessionLocal
    from app.repositories import user_repository
    from tests.test_auth import logged_in_client

    frankfurter.rates.pop(("INR", "EUR"))  # Frankfurter answers 422 for this pair
    client = logged_in_client("eurmissing@example.com")
    with SessionLocal() as db:
        user = user_repository.get_by_email(db, "eurmissing@example.com")
        user.reporting_currency = "EUR"
        db.commit()
        seed(db, user.id, "zerodha", "equity", date(2026, 7, 31), [nh(qty="10", avg="100")])
    body = client.get("/api/portfolio").json()
    assert (body["reporting_totals"], body["fx_required"], body["fx_unavailable"]) == (None, True, ["INR"])
    assert body["totals_by_currency"]["INR"]["current_value"] == "1100.00"  # native totals stay available
