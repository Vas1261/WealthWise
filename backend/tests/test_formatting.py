"""Shared API formatter (moved unchanged from api/portfolio.py in Module 9): pins its exact output."""

from decimal import Decimal

import pytest

from app.api import formatting, portfolio
from app.api.formatting import MONEY_PLACES, PERCENT_PLACES, PRICE_PLACES, RATE_PLACES, fixed


def test_place_constants_are_unchanged():
    assert (MONEY_PLACES, PERCENT_PLACES, PRICE_PLACES, RATE_PLACES) == (2, 2, 4, 6)


@pytest.mark.parametrize(("value", "places", "expected"), [
    ("0.125", 2, "0.12"),  # half-even: tie goes to the even digit
    ("0.135", 2, "0.14"),
    ("2.5", 0, "2"),
    ("-0.125", 2, "-0.12"),
    ("-0.001", 2, "0.00"),  # never "-0.00"
    ("-0.00004", 4, "0.0000"),
    ("0", 2, "0.00"),
    ("1100", 2, "1100.00"),
    ("40.12345650", 4, "40.1235"),
    ("0.0105", 6, "0.010500"),
    ("123456789012345678901234567890123456789.995", 2, "123456789012345678901234567890123456790.00"),
])
def test_fixed_rounds_half_even_without_negative_zero(value, places, expected):
    assert fixed(Decimal(value), places) == expected


def test_fixed_keeps_none():
    assert fixed(None, 2) is None


def test_portfolio_uses_the_shared_formatter():
    assert portfolio._fixed is formatting.fixed
