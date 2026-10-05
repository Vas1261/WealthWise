"""Presentation rounding shared by the API routes (portfolio, finance).

Money 2 dp, percentages 2 dp, prices 4 dp, FX rates 6 dp, ROUND_HALF_EVEN. Numbers are serialised as
strings so clients never see binary floating point.
"""

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

MONEY_PLACES = 2
PERCENT_PLACES = 2
PRICE_PLACES = 4
RATE_PLACES = 6
_FORMAT_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN)


def fixed(value: Decimal | None, places: int) -> str | None:
    if value is None:
        return None
    with localcontext(_FORMAT_CONTEXT):
        rounded = value.quantize(Decimal(1).scaleb(-places))
    return str(rounded.copy_abs() if rounded.is_zero() else rounded)  # never "-0.00"
