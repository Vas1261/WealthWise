"""Normalised holding contract and pure value validation shared by all broker adapters.

No database, HTTP, or file access here; adapters read files and call these functions.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

ASSET_CLASS_EQUITY = "equity"
ASSET_CLASS_MUTUAL_FUND = "mutual_fund"


@dataclass(frozen=True)
class NormalizedHolding:
    """Spec 2.4: the one internal holding record every adapter produces."""

    instrument_name: str
    isin: str | None
    symbol: str | None
    asset_class: str  # "equity" | "mutual_fund"
    quantity: Decimal
    average_buy_price: Decimal
    native_currency: str  # e.g. "INR"
    source_broker: str  # "zerodha" | "groww"
    import_batch_id: str


@dataclass(frozen=True)
class RowError:
    """A validation failure for one field of one spreadsheet row (1-based, as shown in Excel).

    Messages never echo the cell value, so they are safe to log.
    """

    row_number: int
    field: str
    message: str


@dataclass(frozen=True)
class ParseResult:
    statement_date: date
    holdings: list[NormalizedHolding] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)

    @property
    def failed_row_count(self) -> int:
        return len({error.row_number for error in self.errors})


class InvalidValue(ValueError):
    pass


# Plain digits, or digits with thousands separators (international 1,234,567 or Indian 12,34,567).
_NUMBER = re.compile(r"[+-]?(\d+|\d{1,3}(,\d{2,3})+)(\.\d+)?")
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")


def parse_decimal(value: object) -> Decimal:
    """Convert a spreadsheet cell to an exact Decimal; never coerces bad input to zero."""
    if isinstance(value, bool) or value is None:
        raise InvalidValue("is missing or not a number")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # repr() gives the shortest round-trip form, e.g. 31.45 -> Decimal("31.45"), not its binary expansion.
        result = Decimal(repr(value))
    elif isinstance(value, str) and _NUMBER.fullmatch(value.strip()):
        result = Decimal(value.strip().replace(",", ""))
    else:
        raise InvalidValue("is not a number")
    if not result.is_finite():
        raise InvalidValue("is not a finite number")
    return result


def parse_positive_decimal(value: object) -> Decimal:
    result = parse_decimal(value)
    if result <= 0:
        raise InvalidValue("must be greater than zero")
    return result


def parse_non_negative_decimal(value: object) -> Decimal:
    result = parse_decimal(value)
    if result < 0:
        raise InvalidValue("must not be negative")
    return result


def parse_required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidValue("is missing")
    return value.strip()


def parse_isin(value: object) -> str:
    isin = parse_required_text(value)
    if not _ISIN.fullmatch(isin):
        raise InvalidValue("is not a valid 12-character ISIN")
    return isin
