"""Pure market-data rules: no mocks, no database, no network."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.holdings import InvalidValue
from app.domain.market import (
    RequestBudget,
    SymbolCandidate,
    SymbolStatus,
    choose_candidate,
    forced_refresh_allowed,
    is_fresh,
    parse_price,
    symbol_lookup_due,
)

NOW = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)


def listing(symbol="SYNTHA", mic="XNSE", *, country="India", currency="INR") -> SymbolCandidate:
    exchange = {"XNSE": "NSE", "XBOM": "BSE"}.get(mic, "OTHER")
    return SymbolCandidate(symbol, f"{symbol} Synthetic Ltd", exchange, mic, country, currency, "Common Stock")


# --- Candidate selection (M2/M3) ---------------------------------------------------------------

def test_single_nse_listing_is_resolved():
    choice = choose_candidate([listing()])
    assert (choice.status, choice.candidate) == (SymbolStatus.RESOLVED, listing())


def test_nse_preferred_over_bse():
    choice = choose_candidate([listing("SYNTHA-B", "XBOM"), listing("SYNTHA", "XNSE")])
    assert choice.candidate.mic_code == "XNSE"


def test_bse_used_when_no_nse_listing():
    assert choose_candidate([listing("532999", "XBOM")]).candidate.mic_code == "XBOM"


def test_same_symbol_listed_twice_on_one_exchange_is_not_ambiguous():
    assert choose_candidate([listing(), listing()]).status == SymbolStatus.RESOLVED


def test_two_distinct_symbols_on_the_preferred_exchange_are_ambiguous():
    choice = choose_candidate([listing("SYNTHA"), listing("SYNTHB"), listing("SYNTHA", "XBOM")])
    assert (choice.status, choice.candidate) == (SymbolStatus.AMBIGUOUS, None)


@pytest.mark.parametrize(
    "candidate",
    [
        listing(country="United States"),
        listing(currency="USD"),
        listing(mic="XNAS"),
        listing(mic="XLON", country="United Kingdom", currency="GBP"),
    ],
    ids=["foreign-country", "foreign-currency", "other-exchange", "foreign-listing"],
)
def test_non_indian_or_non_nse_bse_listings_are_ignored(candidate):
    assert choose_candidate([candidate]).status == SymbolStatus.NOT_FOUND


def test_no_candidates_is_not_found():
    assert choose_candidate([]).status == SymbolStatus.NOT_FOUND


def test_ticker_search_requires_exact_symbol_match():
    candidates = [listing("SYNTHAB"), listing("SYNTHA", "XBOM")]
    choice = choose_candidate(candidates, ticker="SYNTHA")
    assert (choice.status, choice.candidate.mic_code) == (SymbolStatus.RESOLVED, "XBOM")  # SYNTHAB is not SYNTHA
    assert choose_candidate([listing("SYNTHAB")], ticker="SYNTHA").status == SymbolStatus.NOT_FOUND
    assert choose_candidate([listing("syntha")], ticker="SYNTHA").status == SymbolStatus.NOT_FOUND


def test_never_matches_by_name_similarity():
    similar = SymbolCandidate("OTHER", "SYNTHA Synthetic Ltd", "NSE", "XNSE", "India", "INR", "Common Stock")
    assert choose_candidate([similar], ticker="SYNTHA").status == SymbolStatus.NOT_FOUND


# --- Price validation --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [("1234.56000", "1234.56000"), ("0.05", "0.05"), (" 101.5 ", "101.5"), (412.3871, "412.3871"), (7, "7")],
)
def test_valid_prices_become_exact_decimals(value, expected):
    price = parse_price(value)
    assert type(price) is Decimal and str(price) == expected


@pytest.mark.parametrize("value", [None, "", "abc", "0", "0.00", "-1.5", "NaN", "Infinity", float("nan"), True, [], {}])
def test_invalid_prices_rejected(value):
    with pytest.raises(InvalidValue):
        parse_price(value)


# --- Freshness, forced refresh, lookup retry -------------------------------------------------------

def test_freshness_boundary():
    ttl = timedelta(minutes=10)
    assert is_fresh(NOW - timedelta(minutes=9, seconds=59), NOW, ttl) is True
    assert is_fresh(NOW - ttl, NOW, ttl) is False


def test_forced_refresh_floor_is_sixty_seconds():
    assert forced_refresh_allowed(NOW - timedelta(seconds=59), NOW) is False
    assert forced_refresh_allowed(NOW - timedelta(seconds=60), NOW) is True


@pytest.mark.parametrize(("status", "age_days", "due"), [
    (SymbolStatus.RESOLVED, 365, False),
    (SymbolStatus.NOT_FOUND, 6, False),
    (SymbolStatus.NOT_FOUND, 7, True),
    (SymbolStatus.AMBIGUOUS, 6, False),
    (SymbolStatus.AMBIGUOUS, 8, True),
])
def test_symbol_lookup_retry(status, age_days, due):
    assert symbol_lookup_due(status, NOW - timedelta(days=age_days), NOW) is due


# --- Request budget ------------------------------------------------------------------------------

def test_request_budget_allows_limit_per_rolling_minute():
    budget = RequestBudget(3)
    assert [budget.try_spend(NOW + timedelta(seconds=s)) for s in (0, 10, 20, 30)] == [True, True, True, False]
    assert budget.try_spend(NOW + timedelta(seconds=59)) is False
    assert budget.try_spend(NOW + timedelta(seconds=60)) is True  # the first request left the window
