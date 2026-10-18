// Chart data shaping, kept apart from the chart components. Nothing here is shown to a reader as a number:
// plot() turns the backend's decimal strings into floats only to position and order marks (see charts.tsx).
import { label } from './format'
import type { Holding, Portfolio, ReportingTotals } from './types'

/** Geometry only (see the note at the top): the single place a decimal string becomes a float. */
export function plot(value: string | null): number | null {
  return value === null ? null : Number(value)
}

export type AllocationView = 'holding' | 'broker' | 'asset_class'
export const TOP_HOLDINGS = 10

export type Bar = { name: string; value: string }

/** Bars for the chosen view, largest first; null when holdings are in more than one currency (no common unit). */
export function allocationBars(p: Portfolio, view: AllocationView): { currency: string; bars: Bar[]; total: number } | null {
  const currencies = Object.keys(p.totals_by_currency)
  if (currencies.length !== 1) return null
  const currency = currencies[0]
  const all: Bar[] = view === 'holding'
    ? p.holdings.map((h) => ({ name: h.instrument_name, value: h.invested_value }))
    : Object.entries(view === 'broker' ? p.by_broker : p.by_asset_class)
      .map(([group, byCurrency]) => ({ name: label(group), value: byCurrency[currency].invested_value }))
  const bars = [...all].sort((a, b) => (plot(b.value) ?? 0) - (plot(a.value) ?? 0))
  return { currency, bars: view === 'holding' ? bars.slice(0, TOP_HOLDINGS) : bars, total: all.length }
}

export function pnlBars(holdings: Holding[]): Map<string, Holding[]> {
  const byCurrency = new Map<string, Holding[]>()
  for (const h of holdings) {
    if (h.unrealized_pnl === null) continue
    byCurrency.set(h.native_currency, [...(byCurrency.get(h.native_currency) ?? []), h])
  }
  for (const [currency, list] of byCurrency) {
    byCurrency.set(currency, list.sort((a, b) => (plot(b.unrealized_pnl) ?? 0) - (plot(a.unrealized_pnl) ?? 0)))
  }
  return byCurrency
}

/** The gain split (pinned FX, FR-20) as the backend sent it; null when a pinned rate or price is missing. */
export function gainSplit(r: ReportingTotals): { cost: string; value: string; bars: Bar[] } | null {
  if (r.priced_invested_value_pinned === null || r.unrealized_pnl === null || r.currency_gain === null ||
    r.total_gain === null || r.current_value === null) return null
  return {
    cost: r.priced_invested_value_pinned,
    value: r.current_value,
    bars: [
      { name: 'Market gain', value: r.unrealized_pnl },
      { name: 'Currency gain', value: r.currency_gain },
      { name: 'Total gain', value: r.total_gain },
    ],
  }
}
