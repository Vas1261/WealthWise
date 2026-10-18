"""Portfolio valuation and aggregation as pure functions (no database, HTTP, framework or file access).

Rules (Module 7, spec 4.3):
- invested_value = quantity x average_buy_price, always.
- A price is usable only when Module 6 reports FRESH or STALE, a price exists, and its currency is
  the holding's native currency. Then current_value = quantity x price,
  unrealized_pnl = current_value - invested_value, and
  unrealized_pnl_pct = unrealized_pnl / invested_value x 100 (None when invested_value is 0).
- Without a usable price nothing is filled in: current value and P&L stay None.
- Totals are kept per currency and never summed across currencies. Totals P&L compares priced
  current value with the cost of the priced holdings only.
- Mutual funds expose cost_floor_value (= invested value): a labelled floor, never a market value
  and never part of current_value.
- No rounding: arithmetic runs in a high-precision context so products and sums of stored values
  (Numeric(20,6)) are exact; presentation rounds.

Reporting currency (Module 8, spec 2.7): only the top-level per-currency totals are converted, each
multiplied by its rate into the reporting currency (holdings and breakdowns stay native). Converted
P&L compares converted priced current value with converted priced cost, both at today's rate (no
separate FX gain/loss). The mutual-fund cost floor is converted separately and never joins current
value. If any needed rate is unusable there is no converted total at all (never a partial one).

Pinned FX (FR-20): each holding's cost is also converted at the rates of the dates it was paid on (its
cost dates, weighted by amount; without cost dates, its statement date), and those historical rates
never change afterwards. That splits the converted gain into a market gain (price change, today's rate:
the converted P&L above) and a currency gain (today's rate against the pinned rates, on the priced
cost): total gain = converted current value - priced cost at pinned rates. If any pinned rate is
missing, no pinned figure is given at all.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from app.domain.fx import ONE, FxRate, FxStatus, identity_rate
from app.domain.holdings import ASSET_CLASS_MUTUAL_FUND, CostDate, effective_cost_dates
from app.domain.market import PriceIssue, PriceResult, PriceStatus

# 60 significant digits keep products of two Numeric(20,6) values (at most 40 digits) and their sums
# exact; only the percentage division can ever round.
CALCULATION_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN)
HUNDRED = Decimal(100)
ZERO = Decimal(0)

USABLE_PRICE_STATUSES = (PriceStatus.FRESH, PriceStatus.STALE)


class InvalidPositionError(ValueError):
    """A persisted holding violates the import invariants (never silently corrected or dropped)."""

    def __init__(self, holding_id: int, reason: str) -> None:
        super().__init__(f"Holding {holding_id} is invalid: {reason}")
        self.holding_id = holding_id
        self.reason = reason


@dataclass(frozen=True)
class HoldingPosition:
    """One effective holding as persisted (mirrors NormalizedHolding plus snapshot information)."""

    holding_id: int
    instrument_name: str
    isin: str | None
    symbol: str | None
    asset_class: str
    source_broker: str
    native_currency: str
    quantity: Decimal
    average_buy_price: Decimal
    statement_date: date
    import_batch_id: str
    cost_dates: tuple[CostDate, ...] = ()


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class HoldingValuation:
    position: HoldingPosition
    price: PriceResult  # the Module 6 result, unchanged (status, issue, as_of)
    currency_mismatch: bool  # a price exists but in another currency than the holding
    invested_value: Money
    current_price: Decimal | None
    current_value: Money | None
    unrealized_pnl: Money | None
    unrealized_pnl_pct: Decimal | None
    cost_floor_value: Money | None  # mutual funds only: the cost basis as a labelled floor
    pinned_rate: Decimal | None = None  # native -> reporting currency at the cost dates' rates (FR-20)
    pinned_cost: Money | None = None  # invested value at the pinned rate, in the reporting currency

    @property
    def priced(self) -> bool:
        return self.current_value is not None

    @property
    def stale(self) -> bool:
        return self.priced and self.price.status == PriceStatus.STALE


@dataclass(frozen=True)
class CurrencyTotals:
    currency: str
    invested_value: Decimal
    priced_invested_value: Decimal
    current_value: Decimal | None
    unrealized_pnl: Decimal | None
    unrealized_pnl_pct: Decimal | None
    unpriced_invested_value: Decimal
    cost_floor_value: Decimal  # sum over mutual funds; never part of current_value
    holdings: int
    priced: int
    stale: int
    unavailable: int
    not_supported: int
    currency_mismatch: int
    not_configured: int
    oldest_price_as_of: datetime | None

    @property
    def contains_stale_prices(self) -> bool:
        return self.stale > 0


@dataclass(frozen=True)
class ReportingTotals:
    """Top-level totals converted into the reporting currency (Module 8)."""

    currency: str
    invested_value: Decimal
    priced_invested_value: Decimal
    current_value: Decimal | None
    unrealized_pnl: Decimal | None
    unrealized_pnl_pct: Decimal | None
    unpriced_invested_value: Decimal
    cost_floor_value: Decimal  # converted mutual-fund cost floor; never part of current_value
    holdings: int
    priced: int
    stale: int
    unavailable: int
    not_supported: int
    currency_mismatch: int
    not_configured: int
    contains_stale_prices: bool
    oldest_price_as_of: datetime | None
    fx_rates: dict[str, FxRate]  # native currency -> rate into the reporting currency
    # Pinned FX (FR-20); all None when a pinned rate is missing.
    invested_value_pinned: Decimal | None = None  # all holdings' cost at the rates of their cost dates
    priced_invested_value_pinned: Decimal | None = None  # the same for priced holdings only
    currency_gain: Decimal | None = None  # priced cost at today's rate - at pinned rates
    total_gain: Decimal | None = None  # current value - priced cost at pinned rates (market + currency gain)
    total_gain_pct: Decimal | None = None

    @property
    def fx_stale(self) -> bool:
        return any(rate.status == FxStatus.STALE for rate in self.fx_rates.values())


@dataclass(frozen=True)
class PortfolioSummary:
    holdings: list[HoldingValuation]
    totals_by_currency: dict[str, CurrencyTotals]
    by_asset_class: dict[str, dict[str, CurrencyTotals]]  # asset class -> currency -> totals
    by_broker: dict[str, dict[str, CurrencyTotals]]  # broker -> currency -> totals
    reporting_currency: str
    reporting_totals: ReportingTotals | None  # None when there are no holdings or a needed rate is unusable
    fx_required: bool  # holdings in another currency than the reporting currency
    fx_unavailable: list[str]  # native currencies whose rate into the reporting currency is unusable
    pinned_fx_unavailable: list[str] = field(default_factory=list)  # currencies missing a pinned rate

    @property
    def contains_stale_prices(self) -> bool:
        return any(t.contains_stale_prices for t in self.totals_by_currency.values())

    @property
    def oldest_price_as_of(self) -> datetime | None:
        return _oldest(t.oldest_price_as_of for t in self.totals_by_currency.values())


def validate_position(position: HoldingPosition) -> None:
    if position.quantity <= 0:
        raise InvalidPositionError(position.holding_id, "quantity must be greater than zero")
    if position.average_buy_price < 0:
        raise InvalidPositionError(position.holding_id, "average buy price must not be negative")


def value_holding(position: HoldingPosition, price: PriceResult) -> HoldingValuation:
    validate_position(position)
    currency = position.native_currency
    has_price = price.status in USABLE_PRICE_STATUSES and price.price is not None
    currency_mismatch = has_price and price.currency != currency
    current_price: Decimal | None = None
    current: Decimal | None = None
    pnl: Decimal | None = None
    pct: Decimal | None = None
    with localcontext(CALCULATION_CONTEXT):
        invested = position.quantity * position.average_buy_price
        if has_price and not currency_mismatch and price.price is not None:
            current_price = price.price
            current = position.quantity * current_price
            pnl = current - invested
            pct = pnl / invested * HUNDRED if invested != ZERO else None
    is_fund = position.asset_class == ASSET_CLASS_MUTUAL_FUND
    return HoldingValuation(
        position=position,
        price=price,
        currency_mismatch=currency_mismatch,
        invested_value=Money(invested, currency),
        current_price=current_price,
        current_value=Money(current, currency) if current is not None else None,
        unrealized_pnl=Money(pnl, currency) if pnl is not None else None,
        unrealized_pnl_pct=pct,
        cost_floor_value=Money(invested, currency) if is_fund else None,
    )


def currency_totals(currency: str, valuations: Sequence[HoldingValuation]) -> CurrencyTotals:
    """Totals for holdings in one currency (raises if given another currency: never mixed)."""
    if any(v.invested_value.currency != currency for v in valuations):
        raise ValueError(f"currency_totals({currency}) received holdings in another currency")
    priced = [v for v in valuations if v.priced]
    with localcontext(CALCULATION_CONTEXT):
        invested = sum((v.invested_value.amount for v in valuations), ZERO)
        priced_invested = sum((v.invested_value.amount for v in priced), ZERO)
        current = sum((v.current_value.amount for v in priced if v.current_value), ZERO) if priced else None
        pnl = current - priced_invested if current is not None else None
        pct = pnl / priced_invested * HUNDRED if pnl is not None and priced_invested != ZERO else None
        unpriced_invested = invested - priced_invested
        cost_floor = sum((v.cost_floor_value.amount for v in valuations if v.cost_floor_value), ZERO)
    return CurrencyTotals(
        currency=currency,
        invested_value=invested,
        priced_invested_value=priced_invested,
        current_value=current,
        unrealized_pnl=pnl,
        unrealized_pnl_pct=pct,
        unpriced_invested_value=unpriced_invested,
        cost_floor_value=cost_floor,
        holdings=len(valuations),
        priced=len(priced),
        stale=sum(v.stale for v in valuations),
        unavailable=sum(not v.priced and v.price.status != PriceStatus.NOT_SUPPORTED for v in valuations),
        not_supported=sum(v.price.status == PriceStatus.NOT_SUPPORTED for v in valuations),
        currency_mismatch=sum(v.currency_mismatch for v in valuations),
        not_configured=sum(v.price.issue == PriceIssue.NOT_CONFIGURED for v in valuations),
        oldest_price_as_of=_oldest(v.price.as_of for v in priced),
    )


def summarize(
    valuations: Sequence[HoldingValuation],
    reporting_currency: str,
    fx_rates: Mapping[str, FxRate] | None = None,
    pinned_rates: Mapping[str, Mapping[date, FxRate]] | None = None,
) -> PortfolioSummary:
    """fx_rates: native currency -> rate into reporting_currency. The reporting currency itself always
    converts at exactly 1; any other currency without a usable rate leaves reporting_totals None.
    pinned_rates: native currency -> cost date -> historical rate into reporting_currency (None: no pinning)."""
    by_currency = _totals_by(valuations, lambda v: "all")["all"] if valuations else {}
    currencies = set(by_currency)
    reporting_totals, fx_unavailable = convert_totals(by_currency, fx_rates or {}, reporting_currency)
    pinned_unavailable: list[str] = []
    if pinned_rates is not None:
        valuations, pinned_unavailable = pin_costs(valuations, reporting_currency, pinned_rates)
        if reporting_totals is not None and not pinned_unavailable:
            reporting_totals = _with_pinned_totals(reporting_totals, valuations)
    return PortfolioSummary(
        holdings=list(valuations),
        totals_by_currency=by_currency,
        by_asset_class=_totals_by(valuations, lambda v: v.position.asset_class),
        by_broker=_totals_by(valuations, lambda v: v.position.source_broker),
        reporting_currency=reporting_currency,
        reporting_totals=reporting_totals,
        fx_required=bool(currencies - {reporting_currency}),
        fx_unavailable=fx_unavailable,
        pinned_fx_unavailable=pinned_unavailable,
    )


def pin_dates(position: HoldingPosition) -> tuple[CostDate, ...]:
    return effective_cost_dates(position.cost_dates, position.statement_date)


def pinned_rate(dates: Sequence[CostDate], rates: Mapping[date, FxRate]) -> Decimal | None:
    """Amount-weighted average of the rates of the given dates; None if any of them has no usable rate."""
    total = sum((d.amount for d in dates), ZERO)
    if total <= ZERO or any(d.on not in rates or not rates[d.on].usable for d in dates):
        return None
    with localcontext(CALCULATION_CONTEXT):
        return sum((d.amount * _rate(rates[d.on]) for d in dates), ZERO) / total


def pin_costs(
    valuations: Sequence[HoldingValuation],
    reporting_currency: str,
    pinned_rates: Mapping[str, Mapping[date, FxRate]],
) -> tuple[list[HoldingValuation], list[str]]:
    """Each valuation with its cost at pinned rates, and the native currencies missing a pinned rate."""
    pinned: list[HoldingValuation] = []
    missing: set[str] = set()
    for valuation in valuations:
        currency = valuation.invested_value.currency
        rate = ONE if currency == reporting_currency else pinned_rate(
            pin_dates(valuation.position), pinned_rates.get(currency, {})
        )
        if rate is None:
            missing.add(currency)
            pinned.append(valuation)
            continue
        with localcontext(CALCULATION_CONTEXT):
            cost = valuation.invested_value.amount * rate
        pinned.append(replace(valuation, pinned_rate=rate, pinned_cost=Money(cost, reporting_currency)))
    return pinned, sorted(missing)


def _with_pinned_totals(totals: ReportingTotals, valuations: Sequence[HoldingValuation]) -> ReportingTotals:
    with localcontext(CALCULATION_CONTEXT):
        invested = sum((_amount(v.pinned_cost.amount if v.pinned_cost else None) for v in valuations), ZERO)
        priced = sum(
            (_amount(v.pinned_cost.amount if v.pinned_cost else None) for v in valuations if v.priced), ZERO
        )
        current = totals.current_value
        currency_gain = totals.priced_invested_value - priced if current is not None else None
        total_gain = current - priced if current is not None else None
        pct = total_gain / priced * HUNDRED if total_gain is not None and priced != ZERO else None
    return replace(
        totals,
        invested_value_pinned=invested,
        priced_invested_value_pinned=priced,
        currency_gain=currency_gain,
        total_gain=total_gain,
        total_gain_pct=pct,
    )


def convert_totals(
    totals_by_currency: Mapping[str, CurrencyTotals], fx_rates: Mapping[str, FxRate], reporting_currency: str
) -> tuple[ReportingTotals | None, list[str]]:
    """Convert top-level per-currency totals into the reporting currency; (None, missing) if any rate is unusable."""
    if not totals_by_currency:
        return None, []
    rates = {reporting_currency: identity_rate(reporting_currency), **fx_rates}
    unavailable = sorted(
        currency
        for currency in totals_by_currency
        if currency not in rates
        or not rates[currency].usable
        or (rates[currency].base, rates[currency].quote) != (currency, reporting_currency)
    )
    if unavailable:
        return None, unavailable

    def converted(field: Callable[[CurrencyTotals], Decimal]) -> Decimal:
        return sum(
            (field(t) * _rate(rates[c]) for c, t in totals_by_currency.items()), ZERO
        )

    totals = list(totals_by_currency.values())
    with localcontext(CALCULATION_CONTEXT):
        invested = converted(lambda t: t.invested_value)
        priced_invested = converted(lambda t: t.priced_invested_value)
        priced_buckets = [(c, t) for c, t in totals_by_currency.items() if t.current_value is not None]
        current = (
            sum((_amount(t.current_value) * _rate(rates[c]) for c, t in priced_buckets), ZERO)
            if priced_buckets else None
        )
        pnl = current - priced_invested if current is not None else None
        pct = pnl / priced_invested * HUNDRED if pnl is not None and priced_invested != ZERO else None
        unpriced = converted(lambda t: t.unpriced_invested_value)
        cost_floor = converted(lambda t: t.cost_floor_value)
    return (
        ReportingTotals(
            currency=reporting_currency,
            invested_value=invested,
            priced_invested_value=priced_invested,
            current_value=current,
            unrealized_pnl=pnl,
            unrealized_pnl_pct=pct,
            unpriced_invested_value=unpriced,
            cost_floor_value=cost_floor,
            holdings=sum(t.holdings for t in totals),
            priced=sum(t.priced for t in totals),
            stale=sum(t.stale for t in totals),
            unavailable=sum(t.unavailable for t in totals),
            not_supported=sum(t.not_supported for t in totals),
            currency_mismatch=sum(t.currency_mismatch for t in totals),
            not_configured=sum(t.not_configured for t in totals),
            contains_stale_prices=any(t.contains_stale_prices for t in totals),
            oldest_price_as_of=_oldest(t.oldest_price_as_of for t in totals),
            fx_rates={c: rates[c] for c in totals_by_currency},
        ),
        [],
    )


def _rate(rate: FxRate) -> Decimal:
    if rate.rate is None:  # unreachable: only usable rates get here
        raise ValueError("unusable FX rate")
    return rate.rate


def _amount(value: Decimal | None) -> Decimal:
    if value is None:  # unreachable: only priced buckets get here
        raise ValueError("missing amount")
    return value


def _totals_by(
    valuations: Iterable[HoldingValuation], key: Callable[[HoldingValuation], str]
) -> dict[str, dict[str, CurrencyTotals]]:
    """group -> currency -> totals, preserving first-appearance order."""
    groups: dict[str, dict[str, list[HoldingValuation]]] = defaultdict(lambda: defaultdict(list))
    for valuation in valuations:
        groups[key(valuation)][valuation.invested_value.currency].append(valuation)
    return {
        group: {currency: currency_totals(currency, items) for currency, items in by_currency.items()}
        for group, by_currency in groups.items()
    }


def _oldest(timestamps: Iterable[datetime | None]) -> datetime | None:
    present = [t for t in timestamps if t is not None]
    return min(present) if present else None
