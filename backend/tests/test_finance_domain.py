"""Personal finance domain rules (Module 9): pure functions, no mocks, synthetic values only."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.finance import (
    BudgetRow,
    EntryRow,
    FinanceIssue,
    FinanceValidationError,
    budget_vs_actual,
    cash_by_currency,
    check_date_range,
    convert_amounts,
    in_month,
    month_bounds,
    net_worth,
    normalize_category,
    normalize_description,
    parse_amount,
    parse_date,
    parse_month,
)
from app.domain.fx import FxIssue, FxRate, FxStatus
from app.domain.market import PriceIssue, PriceResult, PriceStatus
from app.domain.portfolio import HoldingPosition, summarize, value_holding

T0 = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)
D = date(2026, 10, 5)


def issue_of(call, *args) -> FinanceIssue:
    with pytest.raises(FinanceValidationError) as raised:
        call(*args)
    return raised.value.issue


def income(amount, currency="INR", day=D, category="Salary"):
    return EntryRow("income", category, Decimal(amount), currency, day)


def expense(amount, currency="INR", day=D, category="Food"):
    return EntryRow("expense", category, Decimal(amount), currency, day)


def fx(base, quote, rate, status=FxStatus.FRESH, issue=None):
    return FxRate(base, quote, status, rate=Decimal(rate) if rate is not None else None,
                  rate_date=date(2026, 6, 30), fetched_at=T0, issue=issue)


# --- Amounts (P11) ---------------------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "expected"), [
    ("0.01", "0.01"), ("1", "1"), ("1.5", "1.5"), ("1499.00", "1499.00"), ("007", "7"),
    ("999999999999999999.99", "999999999999999999.99"), ("10000000000000.01", "10000000000000.01"),
])
def test_valid_amounts_parse_exactly(text, expected):
    assert parse_amount(text) == Decimal(expected)
    assert str(parse_amount(text)) == expected  # no rounding, scale kept


@pytest.mark.parametrize("text", [
    "0", "0.00", "00.0", "-1", "+1", "1.001", "1e3", "1E3", "NaN", "nan", "Infinity", "inf", "-0.01", " 1", "1 ",
    "1\n", "1,000", ".5", "5.", "1.2.3", "", "١", "１", "1_000", "0x10", "9999999999999999999", "1000000000000000000.00", "1.5 ",
])
def test_invalid_amounts_are_rejected(text):
    assert issue_of(parse_amount, text) == FinanceIssue.INVALID_AMOUNT


@pytest.mark.parametrize("value", [1, 1.5, Decimal("1"), None, True, b"1"])
def test_non_string_amounts_are_rejected(value):
    assert issue_of(parse_amount, value) == FinanceIssue.INVALID_AMOUNT


def test_error_never_contains_the_value():
    with pytest.raises(FinanceValidationError) as raised:
        parse_amount("12345.678")
    assert "12345" not in str(raised.value) and str(raised.value) == "invalid_amount"


# --- Categories and descriptions (P5) --------------------------------------------------------------------

def test_category_is_trimmed_and_whitespace_collapsed():
    assert normalize_category("  Food \t and\n Drink  ") == "Food and Drink"


def test_category_matching_is_case_sensitive():
    assert normalize_category("Food") != normalize_category("food")


@pytest.mark.parametrize("text", ["", "   ", "\t\n", "x" * 51, None, 5])
def test_invalid_categories(text):
    assert issue_of(normalize_category, text) == FinanceIssue.INVALID_CATEGORY


def test_category_length_limits():
    assert normalize_category("x") == "x"
    assert normalize_category("x" * 50) == "x" * 50
    assert normalize_category(" " + "x" * 50 + " ") == "x" * 50  # length counts after trimming


def test_description_rules():
    assert normalize_description(None) is None
    assert normalize_description("   ") is None
    assert normalize_description("  lunch  ") == "lunch"
    assert normalize_description("d" * 255) == "d" * 255
    assert issue_of(normalize_description, "d" * 256) == FinanceIssue.INVALID_DESCRIPTION
    assert issue_of(normalize_description, 7) == FinanceIssue.INVALID_DESCRIPTION


# --- Dates and months --------------------------------------------------------------------------------------

def test_dates():
    assert parse_date("2026-10-05") == D
    assert parse_date("2028-02-29") == date(2028, 2, 29)
    assert parse_date("2099-12-31") == date(2099, 12, 31)  # future dates allowed (P6)
    assert parse_date("1900-01-01") == date(1900, 1, 1)


@pytest.mark.parametrize("text", [
    "2026-02-30", "2027-02-29", "2026-13-01", "20261005", "2026-10-5", "05-10-2026", "2026-10-05T00:00:00",
    " 2026-10-05", "1899-12-31", "0000-01-01", "", "today", 1759622400, None,
])
def test_invalid_dates(text):
    assert issue_of(parse_date, text) == FinanceIssue.INVALID_DATE


def test_months():
    assert parse_month("2026-10") == (2026, 10)
    assert parse_month("1900-01") == (1900, 1)
    assert parse_month("9999-12") == (9999, 12)


@pytest.mark.parametrize("text", ["2026-1", "2026-13", "2026-00", "1899-12", "2026/10", "2026-10-01", "", 202610, None])
def test_invalid_months(text):
    assert issue_of(parse_month, text) == FinanceIssue.INVALID_MONTH


def test_month_bounds_roll_over_the_year():
    assert month_bounds(2026, 1) == (date(2026, 1, 1), date(2026, 2, 1))
    assert month_bounds(2026, 12) == (date(2026, 12, 1), date(2027, 1, 1))
    assert month_bounds(9999, 12) == (date(9999, 12, 1), None)


def test_in_month_edges():
    assert in_month(date(2026, 1, 31), 2026, 1)
    assert not in_month(date(2026, 2, 1), 2026, 1)
    assert not in_month(date(2025, 12, 31), 2026, 1)
    assert in_month(date(2028, 2, 29), 2028, 2)
    assert in_month(date(9999, 12, 31), 9999, 12)


def test_date_range():
    check_date_range(D, D)
    check_date_range(None, D)
    check_date_range(D, None)
    assert issue_of(check_date_range, date(2026, 10, 6), D) == FinanceIssue.INVALID_DATE_RANGE


# --- Cash position (P1, P6) -----------------------------------------------------------------------------

def test_cash_is_exact_income_minus_expenses():
    assert cash_by_currency([income("0.10"), income("0.20")]) == {"INR": Decimal("0.30")}
    assert cash_by_currency([income("1000"), expense("250.50")]) == {"INR": Decimal("749.50")}


def test_cash_can_be_negative_or_zero_and_is_kept():
    assert cash_by_currency([expense("10")]) == {"INR": Decimal("-10")}
    assert cash_by_currency([income("10"), expense("10")]) == {"INR": Decimal("0")}


def test_cash_stays_per_currency_and_counts_future_entries():
    rows = [income("100"), expense("40", "EUR"), income("1", day=date(2099, 1, 1)), income("5", "EUR")]
    assert cash_by_currency(rows) == {"INR": Decimal("101"), "EUR": Decimal("-35")}


def test_cash_of_nothing_is_empty():
    assert cash_by_currency([]) == {}


# --- Budgets (P2, P7) -------------------------------------------------------------------------------------

def budget(amount, category="Food", currency="INR", budget_id=1, year=2026, month=10):
    return BudgetRow(budget_id, category, year, month, Decimal(amount), currency)


def test_budget_counts_only_same_month_category_currency_expenses():
    entries = [
        expense("100", day=date(2026, 10, 1)), expense("50.25", day=date(2026, 10, 31)),
        expense("999", day=date(2026, 11, 1)), expense("999", day=date(2026, 9, 30)),
        income("999", category="Food"),  # income never counts
        expense("7", category="food"),  # case-sensitive category
        expense("3", currency="EUR"),  # other currency
    ]
    summary = budget_vs_actual(2026, 10, [budget("200")], entries)
    (line,) = summary.budgets
    assert (line.actual, line.remaining, line.over_budget) == (Decimal("150.25"), Decimal("49.75"), False)
    assert line.utilization_pct == Decimal("75.125")
    assert [(u.category, u.currency, u.actual) for u in summary.unbudgeted] == [
        ("Food", "EUR", Decimal("3")), ("food", "INR", Decimal("7")),
    ]


def test_budget_exactly_spent_is_not_over_but_one_paisa_more_is():
    assert not budget_vs_actual(2026, 10, [budget("100")], [expense("100")]).budgets[0].over_budget
    over = budget_vs_actual(2026, 10, [budget("100")], [expense("100.01")]).budgets[0]
    assert over.over_budget and over.remaining == Decimal("-0.01")


def test_budget_without_expenses_shows_zero_actual():
    line = budget_vs_actual(2026, 10, [budget("100")], []).budgets[0]
    assert (line.actual, line.remaining, line.utilization_pct, line.over_budget) == (0, 100, 0, False)


def test_december_budget_includes_new_years_eve_only():
    entries = [expense("1", day=date(2026, 12, 31)), expense("2", day=date(2027, 1, 1))]
    line = budget_vs_actual(2026, 12, [budget("10", month=12)], entries).budgets[0]
    assert line.actual == Decimal("1")


def test_leap_day_counts_in_february():
    line = budget_vs_actual(2028, 2, [budget("10", year=2028, month=2)], [expense("4", day=date(2028, 2, 29))])
    assert line.budgets[0].actual == Decimal("4")


def test_budget_from_another_month_is_refused():
    with pytest.raises(ValueError):
        budget_vs_actual(2026, 11, [budget("10")], [])


# --- FX conversion and net worth (P3, P4) --------------------------------------------------------------

def test_convert_amounts_identity_and_rates():
    total, missing, rates = convert_amounts(
        {"INR": Decimal("100"), "USD": Decimal("2.5")}, {"USD": fx("USD", "INR", "96.14")}, "INR")
    assert (total, missing) == (Decimal("340.35"), [])
    assert rates["INR"].status == FxStatus.IDENTITY


def test_convert_amounts_never_partial():
    unavailable = FxRate("EUR", "INR", FxStatus.UNAVAILABLE, issue=FxIssue.SERVICE_UNAVAILABLE)
    total, missing, rates = convert_amounts({"INR": Decimal("1"), "EUR": Decimal("1")}, {"EUR": unavailable}, "INR")
    assert (total, missing) == (None, ["EUR"])
    assert rates["EUR"].status == FxStatus.UNAVAILABLE  # shown, with its issue


def test_convert_amounts_rejects_wrong_pair_and_missing_rate():
    assert convert_amounts({"EUR": Decimal("1")}, {"EUR": fx("INR", "EUR", "0.0105")}, "INR")[:2] == (None, ["EUR"])
    assert convert_amounts({"EUR": Decimal("1")}, {}, "INR")[:2] == (None, ["EUR"])


def test_convert_nothing_is_zero():
    assert convert_amounts({}, {}, "EUR") == (Decimal(0), [], {})


_ids = iter(range(1, 10_000))


def position(qty="10", avg="100", *, asset_class="equity", currency="INR"):
    return HoldingPosition(next(_ids), "SYNTH", "INE000D01019", "SYNTH", asset_class, "zerodha", currency,
                           Decimal(qty), Decimal(avg), date(2026, 6, 30), "b")


def fresh(price, status=PriceStatus.FRESH):
    return PriceResult(status, price=Decimal(price), as_of=T0, currency="INR")


UNAVAILABLE = PriceResult(PriceStatus.UNAVAILABLE, issue=PriceIssue.SERVICE_UNAVAILABLE)
FUND = PriceResult(PriceStatus.NOT_SUPPORTED, issue=PriceIssue.MUTUAL_FUND)


def portfolio(valuations, reporting="INR", rates=None):
    return summarize(valuations, reporting, rates or {})


def test_empty_net_worth_is_zero_and_complete():
    n = net_worth(portfolio([]), {}, {})
    assert (n.net_worth, n.portfolio_value, n.cash_position, n.complete, n.unpriced_holdings) == (0, 0, 0, True, 0)
    assert (n.cost_floor_value, n.fx_unavailable, n.fx_rates, n.fx_stale) == (0, [], {}, False)


def test_net_worth_sums_priced_portfolio_and_cash():
    n = net_worth(portfolio([value_holding(position(), fresh("110"))]), {"INR": Decimal("250.50")}, {})
    assert (n.portfolio_value, n.cash_position, n.net_worth, n.complete) == (
        Decimal("1100"), Decimal("250.50"), Decimal("1350.50"), True)


def test_negative_cash_reduces_net_worth():
    n = net_worth(portfolio([value_holding(position(), fresh("110"))]), {"INR": Decimal("-1200")}, {})
    assert n.net_worth == Decimal("-100")


def test_fund_cost_floor_is_shown_but_never_added():
    fund = value_holding(position("5", "20", asset_class="mutual_fund"), FUND)
    n = net_worth(portfolio([fund]), {"INR": Decimal("10")}, {})
    assert (n.portfolio_value, n.cost_floor_value, n.net_worth) == (0, Decimal("100"), Decimal("10"))
    assert (n.complete, n.unpriced_holdings) == (False, 1)


def test_partly_priced_portfolio_is_incomplete():
    valuations = [value_holding(position(), fresh("110")), value_holding(position(), UNAVAILABLE)]
    n = net_worth(portfolio(valuations), {}, {})
    assert (n.portfolio_value, n.net_worth, n.complete, n.unpriced_holdings) == (Decimal("1100"), Decimal("1100"),
                                                                                 False, 1)


def test_stale_price_flag_and_oldest_time_pass_through():
    n = net_worth(portfolio([value_holding(position(), fresh("110", PriceStatus.STALE))]), {}, {})
    assert (n.contains_stale_prices, n.oldest_price_as_of) == (True, T0)


def test_cash_in_other_currency_is_converted_exactly():
    summary = portfolio([value_holding(position(), fresh("110"))], "EUR", {"INR": fx("INR", "EUR", "0.0105")})
    n = net_worth(summary, {"INR": Decimal("100"), "EUR": Decimal("5")}, {"INR": fx("INR", "EUR", "0.0105")})
    assert (n.portfolio_value, n.cash_position) == (Decimal("11.5500"), Decimal("6.0500"))
    assert n.net_worth == Decimal("17.6000")
    assert n.cash_by_currency == {"INR": Decimal("100"), "EUR": Decimal("5")}  # native, unconverted
    assert set(n.fx_rates) == {"INR", "EUR"} and n.fx_rates["EUR"].status == FxStatus.IDENTITY


def test_stale_cash_rate_sets_fx_stale():
    stale = fx("INR", "EUR", "0.0105", FxStatus.STALE, FxIssue.RATE_LIMITED)
    n = net_worth(portfolio([], "EUR"), {"INR": Decimal("100")}, {"INR": stale})
    assert (n.net_worth, n.fx_stale) == (Decimal("1.0500"), True)


def test_unusable_cash_rate_gives_no_total():
    unavailable = FxRate("INR", "EUR", FxStatus.UNAVAILABLE, issue=FxIssue.SERVICE_UNAVAILABLE)
    n = net_worth(portfolio([], "EUR"), {"INR": Decimal("100"), "EUR": Decimal("5")}, {"INR": unavailable})
    assert (n.net_worth, n.cash_position, n.portfolio_value, n.complete) == (None, None, 0, False)
    assert n.fx_unavailable == ["INR"]


def test_unconvertible_portfolio_gives_no_total():
    summary = portfolio([value_holding(position(), fresh("110"))], "EUR", {})
    n = net_worth(summary, {"EUR": Decimal("5")}, {})
    assert (n.net_worth, n.portfolio_value, n.cost_floor_value, n.cash_position) == (None, None, None, Decimal("5"))
    assert (n.fx_unavailable, n.complete, n.unpriced_holdings) == (["INR"], False, 0)


def test_unpriced_counted_even_when_portfolio_cannot_be_converted():
    summary = portfolio([value_holding(position(), UNAVAILABLE)], "EUR", {})
    assert net_worth(summary, {}, {}).unpriced_holdings == 1
