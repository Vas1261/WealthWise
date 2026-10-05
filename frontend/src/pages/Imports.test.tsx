import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { CSRF, fakeApi } from '../test/fakeApi'
import { attempt } from '../test/fixtures'
import type { ImportOutcome } from '../types'
import { ImportsPage } from './Imports'

const FILE = new File(['synthetic workbook'], 'stocks.xlsx', {
  type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
})

function outcome(overrides: Partial<ImportOutcome> = {}): ImportOutcome {
  return {
    attempt_id: 9, result: 'current', broker: 'groww', asset_class: 'equity', statement_date: '2026-09-01',
    file_name: 'stocks.xlsx', rows_succeeded: 3, rows_failed: 0, replaced_existing: false, errors: [], ...overrides,
  }
}

async function upload() {
  await userEvent.upload(screen.getByLabelText(/statement file/i), FILE)
  await userEvent.click(screen.getByRole('button', { name: 'Import' }))
}

describe('import center', () => {
  it('uploads the chosen file as multipart form data with CSRF and reloads the history', async () => {
    const api = fakeApi({
      'GET /api/imports': [{ body: [] }, { body: [attempt({ attempt_id: 9 })] }],
      'POST /api/imports': { status: 201, body: outcome() },
    })
    render(<ImportsPage />)
    expect(await screen.findByText('No imports yet.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Import' }).hasAttribute('disabled')).toBe(true) // nothing chosen yet
    await upload()
    const status = await screen.findByRole('status')
    expect(status.textContent).toMatch(/imported: the groww equity statement of .* is now your current groww equity statement/i)
    expect(status.textContent).toContain('3 rows imported, 0 with errors.')
    const post = api.apiCalls().find((c) => c.method === 'POST')!
    expect((post.body as FormData).get('file')).toBe(FILE)
    expect(post.headers['X-CSRF-Token']).toBe(CSRF)
    expect(await screen.findByRole('table', { name: /import history/i })).toBeTruthy()
  })

  it('says when an import replaced the same statement and lists skipped rows', async () => {
    fakeApi({
      'GET /api/imports': { body: [] },
      'POST /api/imports': { status: 201, body: outcome({ replaced_existing: true, rows_failed: 1,
        errors: [{ row: 12, field: 'ISIN', message: 'ISIN is missing' }] }) },
    })
    render(<ImportsPage />)
    await upload()
    const status = await screen.findByRole('status')
    expect(status.textContent).toMatch(/replaced an earlier import of the same statement/)
    const rows = within(status).getByRole('table', { name: /rows that were skipped/i })
    expect(within(rows).getByText('ISIN is missing')).toBeTruthy()
  })

  it('explains a statement stored as history', async () => {
    fakeApi({ 'GET /api/imports': { body: [] }, 'POST /api/imports': { status: 201, body: outcome({ result: 'history' }) } })
    render(<ImportsPage />)
    await upload()
    expect((await screen.findByRole('status')).textContent).toMatch(/stored as history: a statement dated on or after this one is already current/i)
  })

  it('shows the row errors of a failed import (422 with the result body)', async () => {
    fakeApi({
      'GET /api/imports': { body: [] },
      'POST /api/imports': { status: 422, body: outcome({ result: 'failed', rows_succeeded: 0, rows_failed: 2, errors: [
        { row: 7, field: 'Quantity', message: 'Quantity must be a number' },
        { row: 8, field: 'ISIN', message: 'ISIN is missing' },
      ] }) },
    })
    render(<ImportsPage />)
    await upload()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/not imported: the groww equity statement of .* has no valid rows\. nothing was changed\./i)
    const table = within(alert).getByRole('table', { name: /rows that could not be imported/i })
    expect(within(table).getAllByRole('row').slice(1).map((r) => r.textContent)).toEqual([
      '7QuantityQuantity must be a number', '8ISINISIN is missing',
    ])
  })

  it.each([
    [422, { detail: 'The file is not a supported broker statement', code: 'unsupported_statement' }, /not a supported broker statement/],
    [422, { detail: 'The file matches more than one statement format', code: 'ambiguous_statement' }, /more than one statement format/],
    [415, { detail: 'Upload a broker statement as an .xlsx file', code: 'not_xlsx' }, /upload a broker statement as an \.xlsx file/i],
    [413, { detail: 'The file is larger than 5 MB', code: 'file_too_large' }, /larger than 5 MB/],
  ])('shows the backend message for HTTP %s rejections', async (status, body, message) => {
    fakeApi({ 'GET /api/imports': { body: [] }, 'POST /api/imports': { status, body } })
    render(<ImportsPage />)
    await upload()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(message)
    expect(alert.textContent).toMatch(/^Not imported:/)
  })

  it('handles a network failure during upload', async () => {
    fakeApi({ 'GET /api/imports': { body: [] }, 'POST /api/imports': new TypeError('Failed to fetch') })
    render(<ImportsPage />)
    await upload()
    expect((await screen.findByRole('alert')).textContent).toMatch(/couldn't reach the server/i)
  })

  it('lists the history newest first, as returned, with every result explained', async () => {
    fakeApi({ 'GET /api/imports': { body: [
      attempt({ attempt_id: 4, result: 'rejected', broker: null, asset_class: null, statement_date: null, file_name: 'other.xlsx', rows_succeeded: 0 }),
      attempt({ attempt_id: 3, result: 'failed', file_name: 'bad.xlsx', rows_succeeded: 0, rows_failed: 2 }),
      attempt({ attempt_id: 2, result: 'history', file_name: 'old.xlsx' }),
      attempt({ attempt_id: 1, result: 'current', file_name: 'stocks.xlsx', replaced_existing: true }),
    ] } })
    render(<ImportsPage />)
    const table = await screen.findByRole('table', { name: 'Import history (newest first)' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => r.querySelectorAll('td')[0].textContent)).toEqual(['other.xlsx', 'bad.xlsx', 'old.xlsx', 'stocks.xlsx'])
    const results = rows.map((r) => r.lastElementChild!.textContent)
    expect(results[0]).toBe('Rejected not a supported broker statement')
    expect(results[1]).toBe('Failed no valid rows; nothing stored')
    expect(results[2]).toBe('History older than the statement already current; stored, not used')
    expect(results[3]).toBe('Current became the current statement when imported; replaced an earlier import')
    expect(rows[0].querySelectorAll('td')[1].textContent).toBe('—') // unknown broker for a rejected file
    expect(within(table).getByRole('columnheader', { name: 'Result at import time' })).toBeTruthy()
  })

  it('shows a history loading error with a retry', async () => {
    fakeApi({ 'GET /api/imports': [new TypeError('offline'), { body: [] }] })
    render(<ImportsPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('No imports yet.')).toBeTruthy()
  })

  it('accepts only .xlsx files in the picker and labels it', async () => {
    fakeApi({ 'GET /api/imports': { body: [] } })
    render(<ImportsPage />)
    const input = screen.getByLabelText(/statement file \(\.xlsx\)/i)
    expect(input.getAttribute('type')).toBe('file')
    expect(input.getAttribute('accept')).toContain('.xlsx')
  })
})
