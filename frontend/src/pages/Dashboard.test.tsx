import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { formatMoney } from '../format'
import { fakeApi } from '../test/fakeApi'
import { EMPTY_PORTFOLIO, fx, netWorthData, portfolioData, reporting, summaryData, totals } from '../test/fixtures'
import { DashboardPage } from './Dashboard'

function routes(overrides: Record<string, unknown> = {}) {
  return {
    'GET /api/finance/net-worth': { body: netWorthData() },
    'GET /api/portfolio': { body: portfolioData() },
    'GET /api/finance/budgets/summary': { body: summaryData() },
    'GET /api/finance/monthly-totals': { body: [] },
    ...overrides,
  } as Parameters<typeof fakeApi>[0]
}

const section = (name: RegExp) => screen.getByRole('region', { name })

describe('dashboard', () => {
  it('shows net worth, portfolio value and cash position exactly as sent', async () => {
    fakeApi(routes())
    render(<DashboardPage />)
    const nw = section(/^net worth$/i)
    expect(await within(nw).findByText(formatMoney('1849.50', 'INR'))).toBeTruthy()
    expect(within(nw).getByText(formatMoney('1100.00', 'INR'))).toBeTruthy()
    expect(within(nw).getByText(formatMoney('749.50', 'INR'))).toBeTruthy()
    expect(within(nw).queryByText(/excludes/i)).toBeNull()
    expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeTruthy()
  })

  it('requests the browser-local current month for budgets (U4)', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2027, 0, 15, 12))
    const api = fakeApi(routes())
    render(<DashboardPage />)
    expect(await screen.findByText(/budgets this month \(january 2027\)/i)).toBeTruthy()
    expect(api.apiCalls().map((c) => c.url)).toContain('/api/finance/budgets/summary?month=2027-01')
    vi.useRealTimers()
  })

  it('never shows a missing net worth as zero', async () => {
    fakeApi(routes({
      'GET /api/finance/net-worth': { body: netWorthData({
        reporting_currency: 'EUR', net_worth: null, cash_position: null, portfolio_value: '11.55', complete: false,
        fx_unavailable: ['INR'], cash_by_currency: { INR: '1000.00', EUR: '4.00' },
      }) },
    }))
    render(<DashboardPage />)
    const nw = section(/^net worth$/i)
    expect(await within(nw).findByText(/net worth can't be calculated right now: no exchange rate for INR into EUR/i)).toBeTruthy()
    expect(within(nw).getByText('Unavailable')).toBeTruthy() // cash position
    expect(within(nw).queryByText(formatMoney('0.00', 'EUR'))).toBeNull()
    expect(within(nw).getByText(/cash by currency \(not converted\)/i).textContent).toContain(formatMoney('1000.00', 'INR'))
  })

  it('explains excluded holdings and shows the fund cost floor separately (never added)', async () => {
    fakeApi(routes({
      'GET /api/finance/net-worth': { body: netWorthData({
        net_worth: '10.00', portfolio_value: '0.00', cash_position: '10.00', complete: false, unpriced_holdings: 2,
        cost_floor_value: '100.00',
      }) },
    }))
    render(<DashboardPage />)
    const nw = section(/^net worth$/i)
    expect(await within(nw).findByText(/excludes 2 holdings without a current price/i)).toBeTruthy()
    const floor = within(nw).getByText(/mutual funds at cost \(not included\)/i).closest('div')!
    expect(floor.textContent).toContain(formatMoney('100.00', 'INR'))
    expect(within(nw).getByText(formatMoney('10.00', 'INR'), { selector: '.headline span' })).toBeTruthy()
  })

  it('flags stale prices and stale exchange rates, with the rates listed', async () => {
    fakeApi(routes({
      'GET /api/finance/net-worth': { body: netWorthData({
        reporting_currency: 'EUR', contains_stale_prices: true, fx_stale: true,
        fx_rates: [fx({ currency: 'INR', rate: '0.010500', rate_date: '2026-06-30', status: 'stale', issue: 'rate_limited' }),
          fx({ currency: 'EUR' })],
      }) },
    }))
    render(<DashboardPage />)
    const nw = section(/^net worth$/i)
    expect(await within(nw).findByText(/some prices are stale; oldest from/i)).toBeTruthy()
    expect(within(nw).getByText(/exchange rates are stale/i)).toBeTruthy()
    const rates = within(nw).getByRole('table', { name: /exchange rates into EUR/i })
    expect(within(rates).getByText(/stale \(exchange-rate service is rate limiting requests\)/i)).toBeTruthy()
  })

  it('shows reporting totals and both native-currency breakdowns', async () => {
    fakeApi(routes({
      'GET /api/portfolio': { body: portfolioData({
        by_asset_class: { equity: { INR: totals() }, mutual_fund: { INR: totals({ current_value: null, cost_floor_value: '100.00' }) } },
        by_broker: { zerodha: { INR: totals() }, groww: { INR: totals() } },
      }) },
    }))
    render(<DashboardPage />)
    const pf = section(/^portfolio/i)
    expect(await within(pf).findByText(/unrealised p&l/i)).toBeTruthy()
    const byClass = await screen.findByRole('table', { name: 'By asset class' })
    expect(within(byClass).getAllByRole('row').map((r) => within(r).queryByRole('rowheader')?.textContent))
      .toEqual([undefined, 'Equity', 'Mutual funds'])
    expect(within(screen.getByRole('table', { name: 'By broker' })).getByText('Groww')).toBeTruthy()
    expect(screen.getByText(/in each holding's own currency \(not converted\)/i)).toBeTruthy()
  })

  it('explains unconvertible totals when an exchange rate is missing', async () => {
    fakeApi(routes({
      'GET /api/portfolio': { body: portfolioData({ reporting_currency: 'EUR', reporting_totals: null, fx_required: true, fx_unavailable: ['INR'] }) },
    }))
    render(<DashboardPage />)
    expect(await screen.findByText(/totals can't be converted to EUR: no exchange rate for INR/i)).toBeTruthy()
  })

  it('shows the empty portfolio state with a link to imports', async () => {
    fakeApi(routes({ 'GET /api/portfolio': { body: EMPTY_PORTFOLIO } }))
    render(<DashboardPage />)
    const link = await screen.findByRole('link', { name: /import a broker statement/i })
    expect(link.getAttribute('href')).toBe('#/imports')
  })

  it('lists over-budget categories first and marks them in text', async () => {
    fakeApi(routes())
    render(<DashboardPage />)
    const table = await screen.findByRole('table', { name: /budget vs actual/i })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => within(r).getByRole('rowheader').textContent)).toEqual(['Travel', 'Food'])
    expect(within(rows[0]).getByText('Over budget')).toBeTruthy()
    expect(within(rows[1]).getByText('Within budget')).toBeTruthy()
  })

  it('keeps the other cards working when one request fails', async () => {
    fakeApi(routes({ 'GET /api/finance/budgets/summary': new TypeError('Failed to fetch') }))
    render(<DashboardPage />)
    const budgets = section(/budgets this month/i)
    expect(await within(budgets).findByRole('alert')).toBeTruthy()
    expect(within(budgets).getByText(/couldn't reach the server/i)).toBeTruthy()
    expect(within(budgets).getByRole('button', { name: 'Retry' })).toBeTruthy()
    expect(await within(section(/^net worth$/i)).findByText(formatMoney('1849.50', 'INR'))).toBeTruthy()
  })

  it('turns an invalid-holdings conflict into a re-import hint', async () => {
    const conflict = { status: 409, body: { detail: 'Stored zerodha equity holdings are invalid; re-import the latest zerodha equity statement' } }
    fakeApi(routes({ 'GET /api/finance/net-worth': conflict, 'GET /api/portfolio': conflict }))
    render(<DashboardPage />)
    const nw = section(/^net worth$/i)
    expect(await within(nw).findByText(/re-import the latest zerodha equity statement/i)).toBeTruthy()
    expect(within(nw).getByRole('link', { name: /go to imports/i })).toBeTruthy()
    expect(within(nw).queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('marks sections busy while loading', async () => {
    fakeApi(routes({ 'GET /api/finance/net-worth': () => new Promise(() => {}) }))
    render(<DashboardPage />)
    expect(section(/^net worth$/i).getAttribute('aria-busy')).toBe('true')
    expect(screen.getByText(/loading net worth/i)).toBeTruthy()
    await within(section(/^portfolio/i)).findByText(/unrealised p&l/i)
    expect(section(/^portfolio/i).getAttribute('aria-busy')).toBe('false')
  })

  it('shows "no current value" when nothing is priced', async () => {
    fakeApi(routes({
      'GET /api/portfolio': { body: portfolioData({ reporting_totals: reporting({ current_value: null, unrealized_pnl: null, unrealized_pnl_pct: null }) }) },
    }))
    render(<DashboardPage />)
    expect(await screen.findByText(/no holding has a current price/i)).toBeTruthy()
  })

  it('charts the last 12 months of income and expenses in the reporting currency, with a table', async () => {
    const api = fakeApi(routes({ 'GET /api/finance/monthly-totals': { body: [
      { month: '2026-09', totals: [{ currency: 'INR', income: '5000.00', expenses: '250.50', net: '4749.50' }] },
      { month: '2026-10', totals: [{ currency: 'EUR', income: '10.00', expenses: '0.00', net: '10.00' }] },
    ] } }))
    render(<DashboardPage />)
    const card = section(/income and expenses, last 12 months/i)
    const rows = within(await within(card).findByRole('table')).getAllByRole('row').slice(1)
    expect(rows.map((r) => r.textContent)).toEqual([
      `September 2026${formatMoney('5000.00', 'INR')}${formatMoney('250.50', 'INR')}${formatMoney('4749.50', 'INR')}`,
      'October 2026No entries——'])
    expect(within(card).getByText(/entries in other currencies are not in this chart/i)).toBeTruthy()
    expect(api.apiCalls().find((c) => c.url.startsWith('/api/finance/monthly-totals'))!.url).toMatch(/end=\d{4}-\d{2}&months=12/)
  })
})
