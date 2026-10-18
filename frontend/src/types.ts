// Response shapes of the WealthWise API, mirroring the backend schemas field for field.
// Every money, price, percentage and rate value is a decimal string: display it, never do arithmetic on it.

export type Decimal = string

export type User = { id: number; email: string; reporting_currency: string }

export type StatusCounts = {
  holdings: number
  priced: number
  stale: number
  unavailable: number
  not_supported: number
  currency_mismatch: number
  not_configured: number
}

export type Totals = {
  currency: string
  invested_value: Decimal
  priced_invested_value: Decimal
  current_value: Decimal | null
  unrealized_pnl: Decimal | null
  unrealized_pnl_pct: Decimal | null
  unpriced_invested_value: Decimal
  cost_floor_value: Decimal
  counts: StatusCounts
  contains_stale_prices: boolean
  oldest_price_as_of: string | null
}

export type FxStatus = 'identity' | 'fresh' | 'stale' | 'historical' | 'unavailable'

export type FxRate = {
  currency: string
  rate: Decimal | null
  rate_date: string | null
  status: FxStatus
  issue: string | null
}

export type ReportingTotals = Totals & {
  fx_rates: FxRate[]
  fx_stale: boolean
  // Pinned FX (FR-20): cost at the exchange rates of the dates it was paid on; null when a pinned rate is missing.
  invested_value_pinned: Decimal | null
  priced_invested_value_pinned: Decimal | null
  currency_gain: Decimal | null
  total_gain: Decimal | null
  total_gain_pct: Decimal | null
}

export type PriceStatus = 'fresh' | 'stale' | 'unavailable' | 'not_supported'

export type Holding = {
  holding_id: number
  instrument_name: string
  isin: string | null
  symbol: string | null
  asset_class: string
  source_broker: string
  native_currency: string
  statement_date: string
  quantity: Decimal
  average_buy_price: Decimal
  invested_value: Decimal
  price_status: PriceStatus
  price_issue: string | null
  price_as_of: string | null
  currency_mismatch: boolean
  current_price: Decimal | null
  current_value: Decimal | null
  unrealized_pnl: Decimal | null
  unrealized_pnl_pct: Decimal | null
  cost_floor_value: Decimal | null
  pinned_fx_rate: Decimal | null
  pinned_cost: Decimal | null // in the reporting currency
  cost_dates_from: string
  cost_dates_to: string
  cost_date_count: number
}

export type Portfolio = {
  reporting_currency: string
  reporting_totals: ReportingTotals | null
  fx_required: boolean
  fx_unavailable: string[]
  pinned_fx_unavailable: string[]
  contains_stale_prices: boolean
  oldest_price_as_of: string | null
  totals_by_currency: Record<string, Totals>
  by_asset_class: Record<string, Record<string, Totals>>
  by_broker: Record<string, Record<string, Totals>>
  holdings: Holding[]
}

export type MonthTotals = { currency: string; income: Decimal; expenses: Decimal; net: Decimal }

export type MonthPoint = { month: string; totals: MonthTotals[] }

export type RowError = { row: number; field: string; message: string }

export type ImportOutcome = {
  attempt_id: number
  result: 'current' | 'history' | 'failed'
  broker: string
  asset_class: string
  statement_date: string
  file_name: string
  rows_succeeded: number
  rows_failed: number
  replaced_existing: boolean
  errors: RowError[]
}

export type ImportAttempt = {
  attempt_id: number
  result: 'current' | 'history' | 'failed' | 'rejected'
  broker: string | null
  asset_class: string | null
  statement_date: string | null
  file_name: string
  rows_succeeded: number
  rows_failed: number
  replaced_existing: boolean
  imported_at: string
}

export type EntryKind = 'income' | 'expense'

export type Entry = {
  id: number
  kind: EntryKind
  category: string
  amount: Decimal
  currency: string
  date: string
  description: string | null
  created_at: string
  updated_at: string
}

export type EntryInput = {
  kind: EntryKind
  category: string
  amount: string
  date: string
  description: string | null
}

export type EntryFilters = { start?: string; end?: string; kind?: EntryKind; category?: string }

export type Budget = {
  id: number
  month: string
  category: string
  amount: Decimal
  currency: string
  created_at: string
  updated_at: string
}

export type BudgetLine = {
  budget_id: number
  category: string
  currency: string
  budget: Decimal
  actual: Decimal
  remaining: Decimal
  utilization_pct: Decimal
  over_budget: boolean
}

export type BudgetSummary = {
  month: string
  budgets: BudgetLine[]
  unbudgeted: { category: string; currency: string; actual: Decimal }[]
}

export type NetWorth = {
  reporting_currency: string
  net_worth: Decimal | null
  portfolio_value: Decimal | null
  cash_position: Decimal | null
  cash_by_currency: Record<string, Decimal>
  complete: boolean
  unpriced_holdings: number
  cost_floor_value: Decimal | null
  contains_stale_prices: boolean
  oldest_price_as_of: string | null
  fx_stale: boolean
  fx_rates: FxRate[]
  fx_unavailable: string[]
}
