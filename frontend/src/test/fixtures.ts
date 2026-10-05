// Synthetic API payloads for tests (no real holdings, names or amounts).
import type {
  BudgetSummary,
  FxRate,
  Holding,
  ImportAttempt,
  NetWorth,
  Portfolio,
  ReportingTotals,
  Totals,
  User,
} from '../types'

export const USER: User = { id: 1, email: 'alice@example.com', reporting_currency: 'INR' }

export const T0 = '2026-06-30T10:00:00Z'

export function totals(overrides: Partial<Totals> = {}): Totals {
  return {
    currency: 'INR',
    invested_value: '1000.00',
    priced_invested_value: '1000.00',
    current_value: '1100.00',
    unrealized_pnl: '100.00',
    unrealized_pnl_pct: '10.00',
    unpriced_invested_value: '0.00',
    cost_floor_value: '0.00',
    counts: { holdings: 1, priced: 1, stale: 0, unavailable: 0, not_supported: 0, currency_mismatch: 0, not_configured: 0 },
    contains_stale_prices: false,
    oldest_price_as_of: T0,
    ...overrides,
  }
}

export function fx(overrides: Partial<FxRate> = {}): FxRate {
  return { currency: 'INR', rate: '1.000000', rate_date: null, status: 'identity', issue: null, ...overrides }
}

export function reporting(overrides: Partial<ReportingTotals> = {}): ReportingTotals {
  return { ...totals(), fx_rates: [fx()], fx_stale: false, ...overrides }
}

export function holding(overrides: Partial<Holding> = {}): Holding {
  return {
    holding_id: 1,
    instrument_name: 'SYNTHETIC EQUITY LTD',
    isin: 'INE000D01019',
    symbol: 'SYNTH',
    asset_class: 'equity',
    source_broker: 'zerodha',
    native_currency: 'INR',
    statement_date: '2026-07-31',
    quantity: '10',
    average_buy_price: '100.0000',
    invested_value: '1000.00',
    price_status: 'fresh',
    price_issue: null,
    price_as_of: T0,
    currency_mismatch: false,
    current_price: '110.0000',
    current_value: '1100.00',
    unrealized_pnl: '100.00',
    unrealized_pnl_pct: '10.00',
    cost_floor_value: null,
    ...overrides,
  }
}

export function portfolioData(overrides: Partial<Portfolio> = {}): Portfolio {
  const t = totals()
  return {
    reporting_currency: 'INR',
    reporting_totals: reporting(),
    fx_required: false,
    fx_unavailable: [],
    contains_stale_prices: false,
    oldest_price_as_of: T0,
    totals_by_currency: { INR: t },
    by_asset_class: { equity: { INR: t } },
    by_broker: { zerodha: { INR: t } },
    holdings: [holding()],
    ...overrides,
  }
}

export const EMPTY_PORTFOLIO: Portfolio = {
  reporting_currency: 'INR', reporting_totals: null, fx_required: false, fx_unavailable: [],
  contains_stale_prices: false, oldest_price_as_of: null, totals_by_currency: {}, by_asset_class: {}, by_broker: {},
  holdings: [],
}

export function netWorthData(overrides: Partial<NetWorth> = {}): NetWorth {
  return {
    reporting_currency: 'INR',
    net_worth: '1849.50',
    portfolio_value: '1100.00',
    cash_position: '749.50',
    cash_by_currency: { INR: '749.50' },
    complete: true,
    unpriced_holdings: 0,
    cost_floor_value: '0.00',
    contains_stale_prices: false,
    oldest_price_as_of: T0,
    fx_stale: false,
    fx_rates: [fx()],
    fx_unavailable: [],
    ...overrides,
  }
}

export function summaryData(overrides: Partial<BudgetSummary> = {}): BudgetSummary {
  return {
    month: '2026-10',
    budgets: [
      { budget_id: 1, category: 'Food', currency: 'INR', budget: '200.00', actual: '150.25', remaining: '49.75',
        utilization_pct: '75.12', over_budget: false },
      { budget_id: 2, category: 'Travel', currency: 'INR', budget: '100.00', actual: '150.00', remaining: '-50.00',
        utilization_pct: '150.00', over_budget: true },
    ],
    unbudgeted: [{ category: 'Gifts', currency: 'INR', actual: '3.00' }],
    ...overrides,
  }
}

export function attempt(overrides: Partial<ImportAttempt> = {}): ImportAttempt {
  return {
    attempt_id: 1,
    result: 'current',
    broker: 'groww',
    asset_class: 'equity',
    statement_date: '2026-09-01',
    file_name: 'stocks.xlsx',
    rows_succeeded: 3,
    rows_failed: 0,
    replaced_existing: false,
    imported_at: T0,
    ...overrides,
  }
}
