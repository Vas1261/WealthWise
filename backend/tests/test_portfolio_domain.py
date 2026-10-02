"""Pure portfolio valuation and aggregation: no mocks, no database, no network."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.market import PriceIssue, PriceResult, PriceStatus
from app.domain.portfolio import (
    HoldingPosition,
    InvalidPositionError,
    currency_totals,
    summarize,
    value_holding,
)

T0 = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)
_next_id = iter(range(1, 10_000))


def position(qty="10", avg="100", *, asset_class="equity", broker="zerodha", currency="INR", isin="INE000D01019",
             symbol="SYNTHA", name="SYNTHA") -> HoldingPosition:
    return HoldingPosition(next(_next_id), name, isin, symbol, asset_class, broker, currency, Decimal(qty),
                           Decimal(avg), date(2026, 6, 30), "batch-1")


def fresh(price, currency="INR", at=T0):
    return PriceResult(PriceStatus.FRESH, price=Decimal(price), as_of=at, currency=currency)


def stale(price, currency="INR", at=T0, issue=PriceIssue.SERVICE_UNAVAILABLE):
    return PriceResult(PriceStatus.STALE, price=Decimal(price), as_of=at, currency=currency, issue=issue)


def unavailable(issue=PriceIssue.SERVICE_UNAVAILABLE):
    return PriceResult(PriceStatus.UNAVAILABLE, issue=issue)


NOT_SUPPORTED = PriceResult(PriceStatus.NOT_SUPPORTED, issue=PriceIssue.MUTUAL_FUND)


# --- Per-holding valuation ------------------------------------------------------------------------

def test_fresh_price_values_holding():
    v = value_holding(position("10", "100"), fresh("125.50"))
    assert v.invested_value.amount == Decimal("1000")
    assert (v.current_price, v.current_value.amount, v.unrealized_pnl.amount) == (
        Decimal("125.50"), Decimal("1255.00"), Decimal("255.00"))
    assert v.unrealized_pnl_pct == Decimal("25.5")
    assert v.current_value.currency == v.invested_value.currency == "INR"
    assert v.priced and not v.stale and v.cost_floor_value is None


@pytest.mark.parametrize(
    ("price", "pnl", "pct"), [("80", "-200", "-20"), ("100", "0", "0"), ("100.01", "0.10", "0.01")]
)
def test_negative_zero_and_small_pnl(price, pnl, pct):
    v = value_holding(position("10", "100"), fresh(price))
    assert (v.unrealized_pnl.amount, v.unrealized_pnl_pct) == (Decimal(pnl), Decimal(pct))


def test_stale_price_is_used_and_marked():
    v = value_holding(position("2", "50"), stale("60", at=T0 - timedelta(days=2)))
    assert (v.current_value.amount, v.priced, v.stale) == (Decimal("120"), True, True)
    assert v.price.as_of == T0 - timedelta(days=2)


@pytest.mark.parametrize(
    "price",
    [
        unavailable(PriceIssue.SERVICE_UNAVAILABLE),
        unavailable(PriceIssue.NOT_CONFIGURED),
        unavailable(PriceIssue.NOT_ENTITLED),
        unavailable(PriceIssue.REQUEST_BUDGET_EXHAUSTED),
        unavailable(PriceIssue.SYMBOL_NOT_FOUND),
        PriceResult(PriceStatus.FRESH, price=None, as_of=T0, currency="INR"),  # malformed: status without price
    ],
)
def test_unusable_price_leaves_value_and_pnl_unset(price):
    v = value_holding(position("10", "100"), price)
    assert (v.current_price, v.current_value, v.unrealized_pnl, v.unrealized_pnl_pct) == (None, None, None, None)
    assert v.invested_value.amount == Decimal("1000")  # cost is still known
    assert not v.priced


def test_price_in_another_currency_is_not_used():
    v = value_holding(position("10", "100"), fresh("1.2", currency="USD"))
    assert v.currency_mismatch is True
    assert (v.current_value, v.unrealized_pnl, v.unrealized_pnl_pct) == (None, None, None)


def test_mutual_fund_has_cost_floor_but_no_market_value():
    v = value_holding(position("12.5", "40", asset_class="mutual_fund", broker="groww", isin=None, symbol=None),
                      NOT_SUPPORTED)
    assert (v.current_price, v.current_value, v.unrealized_pnl, v.unrealized_pnl_pct) == (None, None, None, None)
    assert v.invested_value.amount == v.cost_floor_value.amount == Decimal("500.0")
    assert not v.priced


def test_zero_average_buy_price_is_valid_with_undefined_percentage():
    v = value_holding(position("5", "0"), fresh("30"))
    assert (v.invested_value.amount, v.current_value.amount, v.unrealized_pnl.amount) == (
        Decimal("0"), Decimal("150"), Decimal("150"))
    assert v.unrealized_pnl_pct is None


@pytest.mark.parametrize(("qty", "avg"), [("0", "10"), ("-1", "10"), ("0.000000", "10"), ("5", "-0.01")])
def test_invalid_position_raises_instead_of_being_corrected(qty, avg):
    with pytest.raises(InvalidPositionError):
        value_holding(position(qty, avg), fresh("10"))


def test_values_are_exact_decimals_without_internal_rounding():
    v = value_holding(position("0.1", "0.1"), fresh("0.3"))
    assert (v.invested_value.amount, v.current_value.amount, v.unrealized_pnl.amount) == (
        Decimal("0.01"), Decimal("0.03"), Decimal("0.02"))
    big = value_holding(position("99999999999999.999999", "99999999999999.999999"), fresh("12345678901234.567891"))
    # 40 significant digits, exact (Python's default 28-digit context would round this product).
    assert big.invested_value.amount == Decimal("9999999999999999999800000000.000000000001")
    assert str(value_holding(position("3", "1"), fresh("1.123456")).current_value.amount) == "3.370368"
    assert all(type(x) is Decimal for x in (v.invested_value.amount, v.current_value.amount, v.unrealized_pnl_pct))


# --- Aggregation -------------------------------------------------------------------------------------

def test_totals_use_priced_cost_as_pnl_base_and_keep_unpriced_cost_separate():
    valuations = [
        value_holding(position("10", "100"), fresh("120")),  # +200
        value_holding(position("5", "200"), stale("180", at=T0 - timedelta(hours=3))),  # -100
        value_holding(position("1", "1000"), unavailable()),
        value_holding(position("100", "10", asset_class="mutual_fund", broker="groww", isin=None), NOT_SUPPORTED),
    ]
    t = currency_totals("INR", valuations)
    assert t.invested_value == Decimal("4000")
    assert t.priced_invested_value == Decimal("2000")
    assert t.current_value == Decimal("2100")  # never includes unpriced holdings or the fund's cost floor
    assert t.unrealized_pnl == Decimal("100")
    assert t.unrealized_pnl_pct == Decimal("5")  # 100 / 2000, not 100 / 4000
    assert t.unpriced_invested_value == Decimal("2000")
    assert t.cost_floor_value == Decimal("1000")
    assert (t.holdings, t.priced, t.stale, t.unavailable, t.not_supported) == (4, 2, 1, 1, 1)
    assert t.contains_stale_prices and t.oldest_price_as_of == T0 - timedelta(hours=3)


def test_totals_with_no_priced_holdings():
    t = currency_totals("INR", [value_holding(position(), unavailable(PriceIssue.NOT_CONFIGURED)),
                                value_holding(position(asset_class="mutual_fund", isin=None), NOT_SUPPORTED)])
    assert (t.current_value, t.unrealized_pnl, t.unrealized_pnl_pct, t.oldest_price_as_of) == (None, None, None, None)
    assert (t.invested_value, t.unpriced_invested_value, t.not_configured) == (Decimal("2000"), Decimal("2000"), 1)


def test_totals_pct_is_none_when_priced_cost_is_zero():
    t = currency_totals("INR", [value_holding(position("5", "0"), fresh("30"))])
    assert (t.current_value, t.unrealized_pnl, t.unrealized_pnl_pct) == (Decimal("150"), Decimal("150"), None)


def test_currency_totals_refuses_mixed_currencies():
    with pytest.raises(ValueError):
        currency_totals("INR", [value_holding(position(currency="EUR"), fresh("1", currency="EUR"))])


def test_currencies_are_never_summed_together():
    summary = summarize([
        value_holding(position("10", "100", currency="INR"), fresh("110", currency="INR")),
        value_holding(position("2", "50", currency="EUR"), fresh("55", currency="EUR")),
    ], reporting_currency="INR")
    assert set(summary.totals_by_currency) == {"INR", "EUR"}
    assert summary.totals_by_currency["INR"].current_value == Decimal("1100")
    assert summary.totals_by_currency["EUR"].current_value == Decimal("110")
    assert (summary.reporting_totals, summary.fx_required) == (None, True)


def test_reporting_totals_only_when_every_holding_is_in_reporting_currency():
    inr = summarize([value_holding(position(), fresh("110"))], reporting_currency="INR")
    assert inr.reporting_totals is inr.totals_by_currency["INR"] and inr.fx_required is False
    eur_user = summarize([value_holding(position(), fresh("110"))], reporting_currency="EUR")
    assert (eur_user.reporting_totals, eur_user.fx_required) == (None, True)


def test_empty_portfolio():
    summary = summarize([], reporting_currency="INR")
    assert (summary.holdings, summary.totals_by_currency, summary.reporting_totals, summary.fx_required) == (
        [], {}, None, False)
    assert summary.oldest_price_as_of is None and summary.contains_stale_prices is False


def test_breakdowns_by_asset_class_and_broker_add_up():
    valuations = [
        value_holding(position("1", "100", broker="zerodha"), fresh("150")),
        value_holding(position("2", "100", broker="groww"), fresh("90")),
        value_holding(position("3", "10", asset_class="mutual_fund", broker="groww", isin=None), NOT_SUPPORTED),
    ]
    summary = summarize(valuations, reporting_currency="INR")
    assert set(summary.by_asset_class) == {"equity", "mutual_fund"}
    assert set(summary.by_broker) == {"zerodha", "groww"}
    for groups in (summary.by_asset_class, summary.by_broker):
        assert sum(g["INR"].invested_value for g in groups.values()) == summary.totals_by_currency["INR"].invested_value
    assert summary.by_broker["groww"]["INR"].current_value == Decimal("180")  # fund adds no market value
    assert summary.by_asset_class["mutual_fund"]["INR"].current_value is None


def test_same_isin_at_two_brokers_stays_two_positions():
    a = value_holding(position("1", "100", broker="zerodha"), fresh("120"))
    b = value_holding(position("4", "110", broker="groww", symbol=None, name="SYNTHA Synthetic Ltd"), fresh("120"))
    summary = summarize([a, b], reporting_currency="INR")
    assert len(summary.holdings) == 2
    assert summary.by_broker["zerodha"]["INR"].invested_value == Decimal("100")
    assert summary.by_broker["groww"]["INR"].invested_value == Decimal("440")


def test_summary_stale_flags_and_oldest_timestamp():
    summary = summarize([
        value_holding(position(), fresh("10", at=T0)),
        value_holding(position(), stale("10", at=T0 - timedelta(days=1))),
    ], reporting_currency="INR")
    assert summary.contains_stale_prices is True
    assert summary.oldest_price_as_of == T0 - timedelta(days=1)
