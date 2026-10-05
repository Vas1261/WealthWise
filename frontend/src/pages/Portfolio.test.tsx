import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { formatMoney } from '../format'
import { CSRF, fakeApi } from '../test/fakeApi'
import { fx, holding, portfolioData, reporting, totals } from '../test/fixtures'
import { PortfolioPage } from './Portfolio'

function holdingsRows() {
  const table = screen.getByRole('table', { name: /holdings/i })
  return within(table).getAllByRole('row').slice(1)
}

describe('portfolio page', () => {
  it('shows each price status in words', async () => {
    fakeApi({ 'GET /api/portfolio': { body: portfolioData({ holdings: [
      holding({ holding_id: 1, instrument_name: 'FRESH CO' }),
      holding({ holding_id: 2, instrument_name: 'STALE CO', price_status: 'stale' }),
      holding({ holding_id: 3, instrument_name: 'DOWN CO', price_status: 'unavailable', price_issue: 'service_unavailable',
        price_as_of: null, current_price: null, current_value: null, unrealized_pnl: null, unrealized_pnl_pct: null }),
      holding({ holding_id: 4, instrument_name: 'NOKEY CO', price_status: 'unavailable', price_issue: 'not_configured',
        price_as_of: null, current_price: null, current_value: null, unrealized_pnl: null, unrealized_pnl_pct: null }),
      holding({ holding_id: 5, instrument_name: 'Synthetic Fund', isin: null, symbol: null, asset_class: 'mutual_fund',
        source_broker: 'groww', price_status: 'not_supported', price_issue: 'mutual_fund', price_as_of: null,
        current_price: null, current_value: null, unrealized_pnl: null, unrealized_pnl_pct: null, cost_floor_value: '100.00' }),
      holding({ holding_id: 6, instrument_name: 'USD CO', currency_mismatch: true, price_as_of: null, current_price: null,
        current_value: null, unrealized_pnl: null, unrealized_pnl_pct: null }),
    ] }) } })
    render(<PortfolioPage />)
    await screen.findByRole('table', { name: /holdings/i })
    const status = holdingsRows().map((r) => r.lastElementChild!.textContent)
    expect(status[0]).toMatch(/^Live price · /)
    expect(status[1]).toMatch(/^Stale price · last updated /)
    expect(status[2]).toBe('Price unavailable · price service unreachable')
    expect(status[3]).toBe("Price unavailable · live prices aren't configured on this server")
    expect(status[4]).toBe('Price unavailable: shown at average buy price (floor)')
    expect(status[5]).toBe('Price in a different currency: not used')
  })

  it('never shows a missing current value as zero and keeps the fund floor in its own column', async () => {
    fakeApi({ 'GET /api/portfolio': { body: portfolioData({ holdings: [
      holding({ instrument_name: 'Synthetic Fund', asset_class: 'mutual_fund', price_status: 'not_supported',
        price_as_of: null, current_price: null, current_value: null, unrealized_pnl: null, unrealized_pnl_pct: null,
        cost_floor_value: '100.00' }),
    ] }) } })
    render(<PortfolioPage />)
    await screen.findByRole('table', { name: /holdings/i })
    const table = screen.getByRole('table', { name: /holdings/i })
    const headers = within(table).getAllByRole('columnheader').map((h) => h.textContent)
    const cells = holdingsRows()[0].querySelectorAll('td')
    const cell = (name: string) => cells[headers.indexOf(name) - 1].textContent // first column is the row header
    expect(cell('Current value')).toBe('—')
    expect(cell('Unrealised P&L')).toBe('—')
    expect(cell('At cost (mutual funds)')).toBe(formatMoney('100.00', 'INR'))
  })

  it('shows reporting totals with FX details and native totals by currency', async () => {
    fakeApi({ 'GET /api/portfolio': { body: portfolioData({
      reporting_currency: 'EUR', fx_required: true,
      reporting_totals: reporting({ currency: 'EUR', current_value: '11.55', invested_value: '10.50', fx_stale: true,
        fx_rates: [fx({ currency: 'INR', rate: '0.010500', rate_date: '2026-06-30', status: 'stale', issue: 'service_unavailable' })] }),
    }) } })
    render(<PortfolioPage />)
    expect(await screen.findByText(formatMoney('11.55', 'EUR'))).toBeTruthy()
    expect(screen.getByText(/exchange rates are stale/i)).toBeTruthy()
    const rates = screen.getByRole('table', { name: /exchange rates into EUR/i })
    expect(within(rates).getByText('0.010500')).toBeTruthy()
    const native = screen.getByRole('table', { name: /totals in each currency \(not converted\)/i })
    expect(within(native).getByText(formatMoney('1100.00', 'INR'))).toBeTruthy()
  })

  it('falls back to native totals when the reporting conversion is unavailable', async () => {
    fakeApi({ 'GET /api/portfolio': { body: portfolioData({ reporting_currency: 'EUR', reporting_totals: null,
      fx_required: true, fx_unavailable: ['INR'] }) } })
    render(<PortfolioPage />)
    expect(await screen.findByText(/totals can't be converted to EUR: no exchange rate for INR/i)).toBeTruthy()
    expect(screen.getByRole('table', { name: /totals in each currency/i })).toBeTruthy()
  })

  it('shows stale-price notices with the oldest price time', async () => {
    fakeApi({ 'GET /api/portfolio': { body: portfolioData({ contains_stale_prices: true,
      totals_by_currency: { INR: totals({ contains_stale_prices: true, counts: { ...totals().counts, stale: 1 } }) } }) } })
    render(<PortfolioPage />)
    expect(await screen.findByText(/some prices are stale \(the price service could not be reached\); oldest from/i)).toBeTruthy()
    expect(screen.getAllByText(/1 of 1 priced · 1 stale/).length).toBeGreaterThan(0)
  })

  it('refreshes prices with a CSRF-protected POST and shows the new data', async () => {
    let release!: () => void
    const api = fakeApi({
      'GET /api/portfolio': { body: portfolioData() },
      'POST /api/portfolio/refresh': () => new Promise((resolve) => {
        release = () => resolve({ body: portfolioData({ reporting_totals: reporting({ current_value: '1200.00' }) }) })
      }),
    })
    render(<PortfolioPage />)
    const button = await screen.findByRole('button', { name: 'Refresh prices' })
    await userEvent.click(button)
    expect(screen.getByRole('button', { name: 'Refreshing…' }).hasAttribute('disabled')).toBe(true)
    release()
    expect(await screen.findByText(formatMoney('1200.00', 'INR'))).toBeTruthy()
    const refresh = api.apiCalls().find((c) => c.url === '/api/portfolio/refresh')!
    expect([refresh.method, refresh.headers['X-CSRF-Token']]).toEqual(['POST', CSRF])
    expect(api.apiCalls().filter((c) => c.url === '/api/portfolio')).toHaveLength(1) // no extra GET
    expect(screen.getByRole('button', { name: 'Refresh prices' }).hasAttribute('disabled')).toBe(false)
  })

  it('shows a refresh failure without losing the data on screen', async () => {
    fakeApi({ 'GET /api/portfolio': { body: portfolioData() }, 'POST /api/portfolio/refresh': new TypeError('offline') })
    render(<PortfolioPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Refresh prices' }))
    expect(await screen.findByText(/couldn't reach the server/i)).toBeTruthy()
    expect(screen.getByRole('table', { name: /holdings/i })).toBeTruthy()
  })

  it('handles invalid stored holdings (409) with a link to imports', async () => {
    fakeApi({ 'GET /api/portfolio': { status: 409, body: {
      detail: 'Stored groww mutual_fund holdings are invalid; re-import the latest groww mutual_fund statement' } } })
    render(<PortfolioPage />)
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/re-import the latest groww mutual_fund statement/)
    expect(within(alert).getByRole('link', { name: /go to imports/i }).getAttribute('href')).toBe('#/imports')
  })

  it('offers a retry after a network error', async () => {
    const api = fakeApi({ 'GET /api/portfolio': [new TypeError('offline'), { body: portfolioData() }] })
    render(<PortfolioPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('table', { name: /holdings/i })).toBeTruthy()
    expect(api.apiCalls().filter((c) => c.url === '/api/portfolio')).toHaveLength(2)
  })

  it('uses captions and row headers for its tables', async () => {
    fakeApi({ 'GET /api/portfolio': { body: portfolioData() } })
    render(<PortfolioPage />)
    await screen.findByRole('table', { name: /holdings/i })
    for (const name of [/holdings/i, /by asset class/i, /by broker/i, /totals in each currency/i]) {
      const table = screen.getByRole('table', { name })
      expect(within(table).getAllByRole('rowheader').length).toBeGreaterThan(0)
      expect(table.closest('[role="region"]')?.getAttribute('tabindex')).toBe('0')
    }
    expect(screen.getByText('Prices are cached for a few minutes; a refresh fetches new ones at most once a minute.')).toBeTruthy()
  })
})
