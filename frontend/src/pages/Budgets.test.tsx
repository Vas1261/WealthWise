import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { formatMoney } from '../format'
import { CSRF, fakeApi } from '../test/fakeApi'
import { summaryData } from '../test/fixtures'
import type { Budget } from '../types'
import { BudgetsPage } from './Budgets'

function budget(overrides: Partial<Budget> = {}): Budget {
  return { id: 1, month: '2026-10', category: 'Food', amount: '200.00', currency: 'INR', created_at: 'x', updated_at: 'x', ...overrides }
}

function at(month: Date) {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(month)
}

const newBudget = () => within(screen.getByRole('form', { name: /new budget/i }))

describe('budgets', () => {
  it('defaults to the current local month and shows budget vs actual from the backend', async () => {
    at(new Date(2026, 9, 5))
    const api = fakeApi({
      'GET /api/finance/budgets?month=2026-10': { body: [budget(), budget({ id: 2, category: 'Travel', amount: '100.00' })] },
      'GET /api/finance/budgets/summary?month=2026-10': { body: summaryData() },
    })
    render(<BudgetsPage />)
    expect((screen.getByLabelText(/^month$/i) as HTMLInputElement).value).toBe('2026-10')
    const table = await screen.findByRole('table', { name: 'Budget vs actual, October 2026' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => within(r).getByRole('rowheader').textContent)).toEqual(['Food', 'Travel']) // as sent
    const travel = rows[1]
    expect(within(travel).getByText('Over budget')).toBeTruthy()
    expect(within(travel).getByText(formatMoney('-50.00', 'INR')).className).toContain('negative')
    const meter = within(travel).getByRole('meter', { name: 'Travel: budget used' })
    expect(meter.getAttribute('value')).toBe('150.00') // the backend's string, unconverted
    expect(within(rows[0]).getByText('Within budget')).toBeTruthy()
    const unbudgeted = screen.getByRole('table', { name: /spending without a budget/i })
    expect(within(unbudgeted).getByText('Gifts')).toBeTruthy()
    expect(api.apiCalls().every((c) => c.method === 'GET')).toBe(true)
    vi.useRealTimers()
  })

  it('creates a budget with the amount exactly as typed for the selected month', async () => {
    at(new Date(2026, 9, 5))
    const api = fakeApi({
      'GET /api/finance/budgets': [{ body: [] }, { body: [budget()] }],
      'GET /api/finance/budgets/summary': { body: summaryData({ budgets: [], unbudgeted: [] }) },
      'POST /api/finance/budgets': { status: 201, body: budget() },
    })
    render(<BudgetsPage />)
    expect(await screen.findByText('No budgets for October 2026 yet.')).toBeTruthy()
    await userEvent.type(newBudget().getByLabelText(/category/i), 'Food')
    await userEvent.type(newBudget().getByLabelText(/amount/i), ' 200.5 ')
    await userEvent.click(screen.getByRole('button', { name: 'Add budget' }))
    expect(await screen.findByRole('table', { name: 'Budgets for October 2026' })).toBeTruthy()
    const post = api.apiCalls().find((c) => c.method === 'POST')!
    expect(post.body).toEqual({ month: '2026-10', category: 'Food', amount: ' 200.5 ' })
    expect(post.headers['X-CSRF-Token']).toBe(CSRF)
    vi.useRealTimers()
  })

  it('explains a duplicate budget (409) at the category field', async () => {
    fakeApi({
      'GET /api/finance/budgets': { body: [budget()] },
      'GET /api/finance/budgets/summary': { body: summaryData() },
      'POST /api/finance/budgets': { status: 409, body: { detail: 'A budget for this category and month already exists', code: 'budget_exists' } },
    })
    render(<BudgetsPage />)
    await userEvent.type(newBudget().getByLabelText(/category/i), 'Food')
    await userEvent.type(newBudget().getByLabelText(/amount/i), '5')
    await userEvent.click(screen.getByRole('button', { name: 'Add budget' }))
    const category = newBudget().getByLabelText(/category/i)
    const message = await screen.findByText(/a budget for this category already exists in .*; edit it below\./i)
    expect(category.getAttribute('aria-invalid')).toBe('true')
    expect(category.getAttribute('aria-describedby')).toBe(message.id)
  })

  it('shows an invalid amount at the amount field', async () => {
    fakeApi({
      'GET /api/finance/budgets': { body: [] },
      'GET /api/finance/budgets/summary': { body: summaryData() },
      'POST /api/finance/budgets': { status: 422, body: { detail: 'Amount must be …', code: 'invalid_amount' } },
    })
    render(<BudgetsPage />)
    await userEvent.type(newBudget().getByLabelText(/category/i), 'Food')
    await userEvent.type(newBudget().getByLabelText(/amount/i), '0')
    await userEvent.click(screen.getByRole('button', { name: 'Add budget' }))
    expect(await screen.findByText('Amount must be …')).toBeTruthy()
    expect(newBudget().getByLabelText(/amount/i).getAttribute('aria-invalid')).toBe('true')
  })

  it('edits only the amount, sent exactly as typed, keeping the stored currency', async () => {
    const api = fakeApi({
      'GET /api/finance/budgets': { body: [budget({ currency: 'EUR' })] },
      'GET /api/finance/budgets/summary': { body: summaryData() },
      'PUT /api/finance/budgets/1': { body: budget({ amount: '75.00', currency: 'EUR' }) },
    })
    render(<BudgetsPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Food budget' }))
    const input = screen.getByLabelText(/new amount for food/i)
    await userEvent.clear(input)
    await userEvent.type(input, '75')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByRole('button', { name: 'Edit Food budget' })
    const put = api.apiCalls().find((c) => c.method === 'PUT')!
    expect(put.body).toEqual({ amount: '75' })
    const list = screen.getByRole('table', { name: /budgets for/i })
    expect(within(list).getByText('EUR')).toBeTruthy()
  })

  it('deletes after confirmation only', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    const api = fakeApi({
      'GET /api/finance/budgets': [{ body: [budget()] }, { body: [] }],
      'GET /api/finance/budgets/summary': { body: summaryData() },
      'DELETE /api/finance/budgets/1': { status: 204 },
    })
    render(<BudgetsPage />)
    confirm.mockReturnValueOnce(false)
    await userEvent.click(await screen.findByRole('button', { name: 'Delete Food budget' }))
    expect(api.apiCalls().some((c) => c.method === 'DELETE')).toBe(false)
    confirm.mockReturnValueOnce(true)
    await userEvent.click(screen.getByRole('button', { name: 'Delete Food budget' }))
    await vi.waitFor(() => expect(screen.queryByRole('button', { name: 'Delete Food budget' })).toBeNull())
    expect(api.apiCalls().filter((c) => c.method === 'DELETE')).toHaveLength(1)
  })

  it('reloads budgets and summary for another month', async () => {
    at(new Date(2026, 9, 5))
    const api = fakeApi({
      'GET /api/finance/budgets': { body: [] },
      'GET /api/finance/budgets/summary': (call) => ({ body: summaryData({ month: call.url.slice(-7), budgets: [], unbudgeted: [] }) }),
    })
    render(<BudgetsPage />)
    await screen.findByText('No budgets for October 2026 yet.')
    fireEvent.change(screen.getByLabelText(/^month$/i), { target: { value: '2026-11' } })
    expect(await screen.findByText('No budgets for November 2026 yet.')).toBeTruthy()
    expect(api.apiCalls().map((c) => c.url)).toEqual(expect.arrayContaining([
      '/api/finance/budgets?month=2026-11', '/api/finance/budgets/summary?month=2026-11',
    ]))
    vi.useRealTimers()
  })

  it('shows loading errors with a retry', async () => {
    fakeApi({
      'GET /api/finance/budgets': { body: [] },
      'GET /api/finance/budgets/summary': [new TypeError('offline'), { body: summaryData() }],
    })
    render(<BudgetsPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('table', { name: /budget vs actual/i })).toBeTruthy()
  })
})
