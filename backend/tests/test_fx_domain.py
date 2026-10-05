"""Pure FX rules: no mocks, no network."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.fx import FxIssue, FxRate, FxStatus, InvalidRate, identity_rate, is_currency_code, is_fresh, parse_rate

T0 = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("code", ["INR", "EUR", "USD", "XAU"])
def test_valid_codes(code):
    assert is_currency_code(code) is True


@pytest.mark.parametrize("code", ["inr", "Eur", "IN", "INRR", " INR", "INR ", "I1R", "", None, 840, "ÉUR"])
def test_invalid_codes(code):
    assert is_currency_code(code) is False


def test_identity_rate_is_exactly_one():
    rate = identity_rate("INR")
    assert (rate.base, rate.quote, rate.status, rate.rate, rate.usable) == (
        "INR", "INR", FxStatus.IDENTITY, Decimal("1"), True)
    assert type(rate.rate) is Decimal and str(rate.rate) == "1"


@pytest.mark.parametrize(
    ("value", "expected"), [(Decimal("96.14"), "96.14"), (Decimal("0.0000123"), "0.0000123"), (2, "2")]
)
def test_rates_are_exact_decimals(value, expected):
    assert str(parse_rate(value)) == expected


@pytest.mark.parametrize(
    "value", [None, True, "96.14", 96.14, Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), [], {}]
)
def test_invalid_rates_rejected(value):
    with pytest.raises(InvalidRate):
        parse_rate(value)


def test_usable_only_with_a_rate_and_usable_status():
    assert FxRate("USD", "INR", FxStatus.STALE, rate=Decimal("96")).usable is True
    assert FxRate("USD", "INR", FxStatus.UNAVAILABLE, issue=FxIssue.SERVICE_UNAVAILABLE).usable is False
    assert FxRate("USD", "INR", FxStatus.FRESH, rate=None).usable is False


def test_freshness_boundary():
    ttl = timedelta(minutes=60)
    assert is_fresh(T0 - timedelta(minutes=59, seconds=59), T0, ttl) is True
    assert is_fresh(T0 - ttl, T0, ttl) is False
