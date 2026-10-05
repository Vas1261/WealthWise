import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { formatMoney } from '../format'
import { CSRF, fakeApi } from '../test/fakeApi'
import { USER } from '../test/fixtures'
import type { Entry } from '../types'
import { FinancePage } from './Finance'

function entry(overrides: Partial<Entry> = {}): Entry {
  return {
    id: 1, kind: 'expense', category: 'Food', amount: '1499.50', currency: 'INR', date: '2026-10-05',
    description: 'lunch', created_at: '2026-10-05T10:00:00Z', updated_at: '2026-10-05T10:00:00Z', ...overrides,
  }
}

/** The entry form (as opposed to the filter form, which also has a category field). */
const entryForm = () => within(screen.getByRole('form', { name: /^(new|edit) entry$/i }))

async function fill(label: RegExp, value: string, scope: 'entry' | 'filters' = 'entry') {
  const container = scope === 'entry' ? entryForm() : within(screen.getByRole('form', { name: /filter entries/i }))
  const input = container.getByLabelText(label)
  await userEvent.clear(input)
  if (value) await userEvent.type(input, value)
}

describe('finance entries', () => {
  it('creates an entry with the amount exactly as typed and no currency', async () => {
    const api = fakeApi({
      'GET /api/finance/entries': [{ body: [] }, { body: [entry()] }],
      'POST /api/finance/entries': { status: 201, body: entry() },
    })
    render(<FinancePage user={USER} />)
    expect(await screen.findByText('No entries yet.')).toBeTruthy()
    await userEvent.click(screen.getByLabelText('Income'))
    await fill(/^category/i, ' Salary ')
    await fill(/^amount/i, ' 999999999999999999.99 ')
    await fill(/^date/i, '2026-10-05')
    await fill(/description/i, 'October')
    await userEvent.click(screen.getByRole('button', { name: 'Add entry' }))
    await screen.findByRole('table', { name: /entries/i })
    const post = api.apiCalls().find((c) => c.method === 'POST')!
    expect(post.body).toEqual({
      kind: 'income', category: ' Salary ', amount: ' 999999999999999999.99 ', date: '2026-10-05', description: 'October',
    })
    expect(post.headers['X-CSRF-Token']).toBe(CSRF)
    expect(screen.getByText(/currency: INR \(your reporting currency\)/i)).toBeTruthy()
  })

  it('lists entries newest first as sent, with exact amounts and their own currency', async () => {
    fakeApi({ 'GET /api/finance/entries': { body: [
      entry({ id: 2, date: '2026-10-06', amount: '999999999999999999.99', currency: 'EUR', kind: 'income', category: 'Pay' }),
      entry({ id: 1 }),
    ] } })
    render(<FinancePage user={USER} />)
    const table = await screen.findByRole('table', { name: 'Entries (newest first)' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(within(rows[0]).getByText(formatMoney('999999999999999999.99', 'EUR'))).toBeTruthy()
    expect(within(rows[0]).getByText('Income')).toBeTruthy()
    expect(within(rows[1]).getByText(formatMoney('1499.50', 'INR'))).toBeTruthy()
  })

  it('edits with a complete PUT that never sends a currency, and shows the fixed currency', async () => {
    const api = fakeApi({
      'GET /api/finance/entries': { body: [entry({ currency: 'EUR' })] },
      'PUT /api/finance/entries/1': { body: entry() },
    })
    render(<FinancePage user={USER} />)
    await userEvent.click(await screen.findByRole('button', { name: /edit entry from/i }))
    expect(screen.getByRole('heading', { name: 'Edit entry' })).toBeTruthy()
    expect((entryForm().getByLabelText(/^amount/i) as HTMLInputElement).value).toBe('1499.50')
    expect(screen.getByText(/currency: EUR \(fixed when the entry was created\)/i)).toBeTruthy()
    await fill(/^amount/i, '12.3')
    await fill(/description/i, '')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await screen.findByRole('heading', { name: 'New entry' })
    const put = api.apiCalls().find((c) => c.method === 'PUT')!
    expect(put.body).toEqual({ kind: 'expense', category: 'Food', amount: '12.3', date: '2026-10-05', description: '' })
    expect(put.body).not.toHaveProperty('currency')
  })

  it('deletes only after native confirmation', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    const api = fakeApi({
      'GET /api/finance/entries': [{ body: [entry()] }, { body: [] }],
      'DELETE /api/finance/entries/1': { status: 204 },
    })
    render(<FinancePage user={USER} />)
    confirm.mockReturnValueOnce(false)
    await userEvent.click(await screen.findByRole('button', { name: /delete entry from/i }))
    expect(api.apiCalls().some((c) => c.method === 'DELETE')).toBe(false)
    confirm.mockReturnValueOnce(true)
    await userEvent.click(screen.getByRole('button', { name: /delete entry from/i }))
    expect(await screen.findByText('No entries yet.')).toBeTruthy()
    expect(api.apiCalls().filter((c) => c.method === 'DELETE').map((c) => c.headers['X-CSRF-Token'])).toEqual([CSRF])
    expect(confirm.mock.calls[0][0]).not.toMatch(/1499|lunch/) // the prompt shows no amounts or descriptions
  })

  it.each([
    ['invalid_amount', /^amount/i, 'Amount must be a string with a positive number of at most 18 whole digits and 2 decimal places'],
    ['invalid_category', /^category/i, 'Category must be 1 to 50 characters'],
    ['invalid_date', /^date/i, 'Dates must be real calendar dates in YYYY-MM-DD form between 1900 and 9999'],
    ['invalid_description', /description/i, 'Description must be at most 255 characters'],
  ])('shows %s at its field and keeps what was typed', async (code, field, detail) => {
    fakeApi({ 'GET /api/finance/entries': { body: [] }, 'POST /api/finance/entries': { status: 422, body: { detail, code } } })
    render(<FinancePage user={USER} />)
    await screen.findByText('No entries yet.')
    await fill(/^category/i, 'Food')
    await fill(/^amount/i, '1.001')
    await fill(/^date/i, '2026-10-05')
    await userEvent.click(screen.getByRole('button', { name: 'Add entry' }))
    const input = entryForm().getByLabelText(field)
    expect(await screen.findByText(detail)).toBeTruthy()
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(document.getElementById(input.getAttribute('aria-describedby')!)!.textContent).toBe(detail)
    expect((entryForm().getByLabelText(/^amount/i) as HTMLInputElement).value).toBe('1.001')
  })

  it('shows schema errors as a form-level message', async () => {
    fakeApi({ 'GET /api/finance/entries': { body: [] },
      'POST /api/finance/entries': { status: 422, body: { detail: [{ type: 'missing', loc: ['body', 'kind'], msg: 'x' }], code: 'invalid_request' } } })
    render(<FinancePage user={USER} />)
    await screen.findByText('No entries yet.')
    await fill(/^category/i, 'Food')
    await fill(/^amount/i, '1')
    await fill(/^date/i, '2026-10-05')
    await userEvent.click(screen.getByRole('button', { name: 'Add entry' }))
    expect((await screen.findByRole('alert')).textContent).toBe('Some fields are missing or have the wrong type.')
  })

  it('filters through the API and never puts filters in the URL', async () => {
    const api = fakeApi({
      'GET /api/finance/entries': { body: [] },
      'GET /api/finance/entries?start=2026-10-01&end=2026-10-31&kind=expense&category=Food': { body: [entry()] },
    })
    render(<FinancePage user={USER} />)
    await screen.findByText('No entries yet.')
    await fill(/^from/i, '2026-10-01', 'filters')
    await fill(/^to/i, '2026-10-31', 'filters')
    await userEvent.selectOptions(screen.getByLabelText(/^type$/i), 'expense')
    const filterCategory = within(screen.getByRole('form', { name: /filter entries/i })).getByLabelText(/category/i)
    await userEvent.type(filterCategory, 'Food')
    await userEvent.click(screen.getByRole('button', { name: 'Apply filters' }))
    expect(await screen.findByRole('table', { name: /entries/i })).toBeTruthy()
    expect(window.location.hash).toBe('')
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(await screen.findByText('No entries yet.')).toBeTruthy()
    expect(api.apiCalls().map((c) => c.url).at(-1)).toBe('/api/finance/entries')
  })

  it('reports an invalid filter range from the backend', async () => {
    fakeApi({
      'GET /api/finance/entries': { body: [] },
      'GET /api/finance/entries?start=2026-10-31&end=2026-10-01': { status: 422, body: { detail: 'start must not be after end', code: 'invalid_date_range' } },
    })
    render(<FinancePage user={USER} />)
    await screen.findByText('No entries yet.')
    await fill(/^from/i, '2026-10-31', 'filters')
    await fill(/^to/i, '2026-10-01', 'filters')
    await userEvent.click(screen.getByRole('button', { name: 'Apply filters' }))
    expect((await screen.findByRole('alert')).textContent).toMatch('start must not be after end')
  })
})
