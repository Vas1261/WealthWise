from decimal import Decimal

import pytest

from app.domain.holdings import InvalidValue, parse_decimal, parse_isin, parse_positive_decimal


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (12, Decimal("12")),
        (12.0, Decimal("12.0")),
        (31.45, Decimal("31.45")),
        (413.2219, Decimal("413.2219")),
        ("2750", Decimal("2750")),
        (" 2,750 ", Decimal("2750")),
        ("1,234,567.89", Decimal("1234567.89")),
        ("12,34,567.5", Decimal("1234567.5")),  # Indian digit grouping
        ("-263.40", Decimal("-263.40")),
    ],
)
def test_parse_decimal_is_exact(value, expected):
    result = parse_decimal(value)
    assert result == expected and str(result) == str(expected)


@pytest.mark.parametrize(
    "value",
    [None, True, False, "", "abc", "1,2", "1,2345", "1e5", "1.2.3", float("nan"), float("inf"), Decimal("1")],
)
def test_parse_decimal_rejects_non_numbers(value):
    with pytest.raises(InvalidValue):
        parse_decimal(value)


@pytest.mark.parametrize("value", [0, 0.0, "-1", "0.00"])
def test_parse_positive_decimal_rejects_zero_and_negative(value):
    with pytest.raises(InvalidValue):
        parse_positive_decimal(value)


@pytest.mark.parametrize("value", ["INE000B01026", "INF000Z01AB3", "US0000000001"])
def test_parse_isin_accepts_well_formed(value):
    assert parse_isin(value) == value


@pytest.mark.parametrize(
    "value",
    [None, "", "INE000B0102", "INE000B010266", "ine000b01026", "1NE000B01026", "INE000B0102X", 12345],
)
def test_parse_isin_rejects_malformed(value):
    with pytest.raises(InvalidValue):
        parse_isin(value)
