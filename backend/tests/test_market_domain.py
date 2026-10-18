"""Pure market-data rules: no mocks, no database, no network."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.market import InvalidPrice, Quote, choose_quote, forced_refresh_allowed, is_fresh, parse_price

NOW = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)
NSE_QUOTE = Quote("NSE", "SYNTHA", Decimal("101.25"))
BSE_QUOTE = Quote("BSE", "SYNTHA", Decimal("101.10"))


# --- Listing choice (M3) ------------------------------------------------------------------------

def test_nse_preferred_over_bse():
    assert choose_quote({"BSE": BSE_QUOTE, "NSE": NSE_QUOTE}) == NSE_QUOTE


def test_bse_used_when_no_nse_price():
    assert choose_quote({"BSE": BSE_QUOTE}) == BSE_QUOTE


def test_listed_on_neither_exchange_is_none():
    assert choose_quote({}) is None


# --- Price validation --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"), [(Decimal("1234.56000"), "1234.56000"), (Decimal("0.05"), "0.05"), (7, "7")]
)
def test_valid_prices_become_exact_decimals(value, expected):
    price = parse_price(value)
    assert type(price) is Decimal and str(price) == expected


@pytest.mark.parametrize(
    "value", [None, "", "101.5", 101.5, 0, Decimal("0"), Decimal("-1.5"), Decimal("NaN"), Decimal("Infinity"),
              True, [], {}],
)
def test_invalid_prices_rejected(value):
    # Floats are refused too: the client parses JSON numbers straight into Decimal.
    with pytest.raises(InvalidPrice):
        parse_price(value)


# --- Freshness and forced refresh ------------------------------------------------------------------

def test_freshness_boundary():
    ttl = timedelta(minutes=10)
    assert is_fresh(NOW - timedelta(minutes=9, seconds=59), NOW, ttl) is True
    assert is_fresh(NOW - ttl, NOW, ttl) is False


def test_forced_refresh_floor_is_sixty_seconds():
    assert forced_refresh_allowed(NOW - timedelta(seconds=59), NOW) is False
    assert forced_refresh_allowed(NOW - timedelta(seconds=60), NOW) is True
