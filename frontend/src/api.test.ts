import { describe, expect, it, vi } from 'vitest'
import { ApiError, auth, finance, imports, onSessionEnded, portfolio, reports, request } from './api'
import { CSRF, fakeApi } from './test/fakeApi'

describe('CSRF and request options', () => {
  it('sends no CSRF header on GET and the cookie value on every write', async () => {
    const api = fakeApi({
      'GET /api/portfolio': { body: {} },
      'POST /api/portfolio/refresh': { body: {} },
      'PUT /api/finance/budgets/3': { body: {} },
      'DELETE /api/finance/entries/4': { status: 204 },
      'PATCH /api/auth/me': { body: {} },
    })
    await portfolio.get()
    await portfolio.refresh()
    await finance.setBudgetAmount(3, '10')
    await finance.deleteEntry(4)
    await auth.setReportingCurrency('EUR')
    const [get, ...writes] = api.apiCalls()
    expect(get.headers['X-CSRF-Token']).toBeUndefined()
    expect(writes.map((c) => [c.method, c.headers['X-CSRF-Token']])).toEqual([
      ['POST', CSRF], ['PUT', CSRF], ['DELETE', CSRF], ['PATCH', CSRF],
    ])
    expect(api.calls.filter((c) => c.url === '/api/auth/csrf')).toHaveLength(1) // bootstrapped once
  })

  it('never caches responses and always sends same-origin credentials', async () => {
    const api = fakeApi({ 'GET /api/finance/net-worth': { body: {} }, 'POST /api/finance/entries': { body: {} } })
    await finance.netWorth()
    await finance.createEntry({ kind: 'expense', category: 'Food', amount: '1', date: '2026-10-05', description: null })
    for (const call of api.calls) {
      expect(call.cache).toBe('no-store')
      expect(call.credentials).toBe('same-origin')
    }
  })

  it('fails clearly when no CSRF token can be obtained', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 204 }))) // never sets the cookie
    await expect(portfolio.refresh()).rejects.toMatchObject({ status: 0, message: 'Could not obtain a CSRF token' })
  })
})

describe('bodies', () => {
  it('sends JSON with a JSON content type, exactly as given', async () => {
    const api = fakeApi({ 'POST /api/finance/entries': { status: 201, body: {} } })
    const entry = { kind: 'income' as const, category: ' Pay ', amount: ' 1,499.5 ', date: '2026-10-05', description: 'x' }
    await finance.createEntry(entry)
    const [call] = api.apiCalls()
    expect(call.headers['Content-Type']).toBe('application/json')
    expect(call.body).toEqual(entry) // nothing trimmed or converted
  })

  it('uploads FormData with the "file" field and lets the browser set the multipart type', async () => {
    const api = fakeApi({ 'POST /api/imports': { status: 201, body: {} } })
    const file = new File(['synthetic'], 'statement.xlsx')
    await imports.upload(file)
    const [call] = api.apiCalls()
    expect(call.body).toBeInstanceOf(FormData)
    expect((call.body as FormData).get('file')).toBe(file)
    expect(call.headers['Content-Type']).toBeUndefined()
    expect(call.headers['X-CSRF-Token']).toBe(CSRF)
  })

  it('builds query strings only from non-empty filters', async () => {
    const api = fakeApi({ 'GET /api/finance/entries': { body: [] }, 'GET /api/finance/budgets/summary': { body: {} } })
    await finance.listEntries({ start: '2026-10-01', end: '', kind: 'expense', category: 'Food & Drink' })
    await finance.listEntries({})
    await finance.budgetSummary('2026-10')
    expect(api.apiCalls().map((c) => c.url)).toEqual([
      '/api/finance/entries?start=2026-10-01&kind=expense&category=Food+%26+Drink',
      '/api/finance/entries',
      '/api/finance/budgets/summary?month=2026-10',
    ])
  })
})

describe('session refresh', () => {
  it('refreshes once on 401 and retries the request once', async () => {
    const api = fakeApi({
      'GET /api/portfolio': [{ status: 401, body: { detail: 'Not authenticated' } }, { body: { ok: true } }],
      'POST /api/auth/refresh': { status: 204 },
    })
    await expect(request('GET', '/api/portfolio')).resolves.toEqual({ ok: true })
    expect(api.apiCalls().map((c) => `${c.method} ${c.url}`)).toEqual([
      'GET /api/portfolio', 'POST /api/auth/refresh', 'GET /api/portfolio',
    ])
  })

  it('shares one refresh between parallel requests', async () => {
    let refreshes = 0
    const api = fakeApi({
      'GET /api/portfolio': [{ status: 401 }, { body: 'p' }],
      'GET /api/finance/net-worth': [{ status: 401 }, { body: 'n' }],
      'POST /api/auth/refresh': () => {
        refreshes += 1
        return { status: 204 }
      },
    })
    await Promise.all([portfolio.get(), finance.netWorth()])
    expect(refreshes).toBe(1)
    expect(api.apiCalls().filter((c) => c.url === '/api/auth/refresh')).toHaveLength(1)
  })

  it('reports the end of the session when the refresh fails', async () => {
    fakeApi({ 'GET /api/portfolio': { status: 401, body: { detail: 'Not authenticated' } }, 'POST /api/auth/refresh': { status: 401 } })
    const ended = vi.fn()
    onSessionEnded(ended)
    await expect(portfolio.get()).rejects.toMatchObject({ status: 401 })
    expect(ended).toHaveBeenCalledTimes(1)
  })

  it('reports the end of the session when the retry is still unauthenticated', async () => {
    fakeApi({ 'GET /api/portfolio': { status: 401 }, 'POST /api/auth/refresh': { status: 204 } })
    const ended = vi.fn()
    onSessionEnded(ended)
    await expect(portfolio.get()).rejects.toBeInstanceOf(ApiError)
    expect(ended).toHaveBeenCalledTimes(1)
  })

  it('never refreshes for login, register or refresh itself', async () => {
    const api = fakeApi({ 'POST /api/auth/login': { status: 401, body: { detail: 'Invalid email or password' } } })
    const ended = vi.fn()
    onSessionEnded(ended)
    await expect(auth.login('a@example.com', 'wrong-password')).rejects.toMatchObject({
      status: 401, message: 'Invalid email or password',
    })
    expect(api.apiCalls().map((c) => c.url)).toEqual(['/api/auth/login'])
    expect(ended).not.toHaveBeenCalled()
  })

  it('treats a network failure during refresh as a failed refresh', async () => {
    fakeApi({ 'GET /api/portfolio': { status: 401 }, 'POST /api/auth/refresh': new TypeError('Failed to fetch') })
    const ended = vi.fn()
    onSessionEnded(ended)
    await expect(portfolio.get()).rejects.toMatchObject({ status: 401 })
    expect(ended).toHaveBeenCalled()
  })
})

describe('errors', () => {
  it('keeps the backend code, message and body', async () => {
    const body = { detail: 'Amount must be …', code: 'invalid_amount' }
    fakeApi({ 'POST /api/finance/entries': { status: 422, body } })
    const error = await finance
      .createEntry({ kind: 'expense', category: 'Food', amount: 'x', date: '2026-10-05', description: null })
      .catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 422, code: 'invalid_amount', message: 'Amount must be …', body })
  })

  it('uses a generic message for list-shaped (schema) errors and non-JSON bodies', async () => {
    fakeApi({
      'POST /api/finance/budgets': { status: 422, body: { detail: [{ type: 'missing', loc: ['body'], msg: 'x' }], code: 'invalid_request' } },
      'GET /api/portfolio': () => ({ status: 500, body: undefined }),
    })
    await expect(finance.createBudget('2026-10', 'Food', '1')).rejects.toMatchObject({
      message: 'Request failed (HTTP 422)', code: 'invalid_request',
    })
    await expect(portfolio.get()).rejects.toMatchObject({ status: 500, code: null })
  })

  it('passes network failures through as non-ApiErrors', async () => {
    fakeApi({ 'GET /api/imports': new TypeError('Failed to fetch') })
    await expect(imports.history()).rejects.toBeInstanceOf(TypeError)
  })

  it('returns undefined for 204 responses', async () => {
    fakeApi({ 'DELETE /api/finance/budgets/1': { status: 204 } })
    await expect(finance.deleteBudget(1)).resolves.toBeUndefined()
  })
})

describe('report downloads', () => {
  function stubDownloads() {
    const saved: { name: string; type: string }[] = []
    vi.stubGlobal('URL', Object.assign(URL, {
      createObjectURL: vi.fn((blob: Blob) => `blob:${blob.type}`),
      revokeObjectURL: vi.fn(),
    }))
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push({ name: this.download, type: this.href.replace('blob:', '') })
    })
    return saved
  }

  it('saves the file under the name the server gave it, after refreshing an expired session', async () => {
    const saved = stubDownloads()
    const file = () => new Response('%PDF-1.4', {
      headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="wealthwise-monthly-2026-10.pdf"' },
    })
    const api = fakeApi({
      'GET /api/reports/monthly': [{ status: 401, body: { detail: 'Not authenticated' } }, file()],
      'POST /api/auth/refresh': { status: 204 },
    })
    await reports.monthly('2026-10', 'pdf')
    expect(api.apiCalls().map((c) => `${c.method} ${c.url}`)).toEqual([
      'GET /api/reports/monthly?month=2026-10&format=pdf', 'POST /api/auth/refresh',
      'GET /api/reports/monthly?month=2026-10&format=pdf',
    ])
    expect(saved).toEqual([{ name: 'wealthwise-monthly-2026-10.pdf', type: 'application/pdf' }])
  })

  it('throws the backend error and saves nothing when the report fails', async () => {
    const saved = stubDownloads()
    fakeApi({ 'GET /api/reports/portfolio': { status: 409, body: { detail: 'Stored zerodha equity holdings are invalid' } } })
    await expect(reports.portfolio('csv')).rejects.toMatchObject({ status: 409 })
    expect(saved).toEqual([])
  })
})
