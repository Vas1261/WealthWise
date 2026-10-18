"""Normalised holding contract and pure value validation shared by all broker adapters.

No database, HTTP, or file access here; adapters read files and call these functions.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

ASSET_CLASS_EQUITY = "equity"
ASSET_CLASS_MUTUAL_FUND = "mutual_fund"

# Cost-date amounts are weights stored like prices (6 dp); only their proportions matter.
COST_DATE_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class CostDate:
    """Part of a holding's cost basis attributed to one date, for pinning the FX rate of that date (FR-20).

    The amounts of a holding are weights: the pinned rate is their amount-weighted average rate.
    """

    on: date
    amount: Decimal


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
    # Dates the cost was paid on (mutual funds: one per purchase date). Empty: the statement date applies.
    cost_dates: tuple[CostDate, ...] = ()


def effective_cost_dates(cost_dates: Sequence[CostDate], statement_date: date) -> tuple[CostDate, ...]:
    """The dates a cost is pinned to: its cost dates, or else (holdings stored without any) the statement date."""
    return tuple(cost_dates) or (CostDate(statement_date, Decimal(1)),)


def carry_cost_dates(
    previous: Sequence[CostDate],
    previous_quantity: Decimal,
    previous_cost: Decimal,
    quantity: Decimal,
    cost: Decimal,
    statement_date: date,
) -> tuple[CostDate, ...]:
    """Cost dates of a holding in a new holdings statement, given the same holding in the previous statement.

    A snapshot does not show trades, so the change between two statements is read with the
    average-cost method: units still held from before keep their earlier dates (scaled to the cost
    they still carry), and only cost added since then is dated to the new statement. Sales and bonus
    or split shares therefore add nothing new, and buying more adds the extra cost on the new date.
    """
    if cost.quantize(COST_DATE_PLACES) <= 0:
        return ()  # nothing paid (e.g. free shares): nothing to pin
    total = sum((d.amount for d in previous), Decimal(0))
    if total <= 0 or previous_quantity <= 0 or previous_cost <= 0:
        return (CostDate(statement_date, cost.quantize(COST_DATE_PLACES)),)
    carried = min(previous_cost * min(Decimal(1), quantity / previous_quantity), cost)
    dates = [CostDate(d.on, (d.amount * carried / total).quantize(COST_DATE_PLACES)) for d in previous]
    added = (cost - carried).quantize(COST_DATE_PLACES)
    if added > 0:
        dates.append(CostDate(statement_date, added))
    return tuple(d for d in dates if d.amount > 0) or (CostDate(statement_date, cost.quantize(COST_DATE_PLACES)),)


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
