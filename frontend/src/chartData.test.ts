import { describe, expect, it } from 'vitest'
import { allocationBars, gainSplit, pnlBars, TOP_HOLDINGS } from './chartData'
import { holding, portfolioData, reporting, totals } from './test/fixtures'

describe('chart data', () => {
  it('shows the largest holdings first, at most ten, by amount invested', () => {
    const holdings = Array.from({ length: 12 }, (_, i) => holding({ holding_id: i, instrument_name: `H${i}`, invested_value: `${i * 10}.00` }))
    const result = allocationBars(portfolioData({ holdings }), 'holding')!
    expect(result.bars).toHaveLength(TOP_HOLDINGS)
    expect(result.bars.slice(0, 2)).toEqual([{ name: 'H11', value: '110.00' }, { name: 'H10', value: '100.00' }])
    expect([result.currency, result.total]).toEqual(['INR', 12])
  })

  it('groups by broker or asset class with readable names', () => {
    const p = portfolioData({ by_broker: { zerodha: { INR: totals({ invested_value: '300.00' }) }, groww: { INR: totals({ invested_value: '900.00' }) } } })
    expect(allocationBars(p, 'broker')!.bars).toEqual([{ name: 'Groww', value: '900.00' }, { name: 'Zerodha', value: '300.00' }])
  })

  it('draws no allocation when holdings are in more than one currency', () => {
    expect(allocationBars(portfolioData({ totals_by_currency: { INR: totals(), EUR: totals({ currency: 'EUR' }) } }), 'holding')).toBeNull()
  })

  it('orders priced holdings from largest gain to largest loss, per currency', () => {
    const groups = pnlBars([
      holding({ holding_id: 1, unrealized_pnl: '-50.00' }), holding({ holding_id: 2, unrealized_pnl: '120.00' }),
      holding({ holding_id: 3, unrealized_pnl: null }), holding({ holding_id: 4, native_currency: 'USD', unrealized_pnl: '5.00' }),
    ])
    expect([...groups.keys()]).toEqual(['INR', 'USD'])
    expect(groups.get('INR')!.map((h) => h.holding_id)).toEqual([2, 1])
  })

  it('splits the gain into market, currency and total exactly as the backend sent it', () => {
    const split = gainSplit(reporting({ priced_invested_value_pinned: '10.20', unrealized_pnl: '2.10', currency_gain: '-0.30',
      total_gain: '1.80', current_value: '12.00' }))!
    expect(split).toEqual({ cost: '10.20', value: '12.00', bars: [
      { name: 'Market gain', value: '2.10' }, { name: 'Currency gain', value: '-0.30' }, { name: 'Total gain', value: '1.80' }] })
    expect(gainSplit(reporting({ currency_gain: null }))).toBeNull()
  })
})
