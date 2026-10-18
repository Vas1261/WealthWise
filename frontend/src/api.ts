// Auth tokens live in HttpOnly cookies that this code can never read. The only cookie
// read here is the CSRF token, which is echoed back in a header (double-submit pattern).
// These names must match the backend's CSRF_COOKIE_NAME / CSRF_HEADER_NAME.
// Nothing is ever written to localStorage/sessionStorage, and responses are never cached.
import type {
  Budget,
  BudgetSummary,
  Entry,
  EntryFilters,
  EntryInput,
  ImportAttempt,
  ImportOutcome,
  MonthPoint,
  NetWorth,
  Portfolio,
  User,
} from './types'

export type { User } from './types'

const CSRF_COOKIE = 'ww_csrf'
const CSRF_HEADER = 'X-CSRF-Token'

export class ApiError extends Error {
  readonly status: number
  /** The backend's machine-readable code (e.g. "invalid_amount"), when it sent one. */
  readonly code: string | null
  /** The parsed JSON error body (e.g. a failed import with its row errors), when there was one. */
  readonly body: unknown

  constructor(status: number, message: string, code: string | null = null, body: unknown = null) {
    super(message)
    this.status = status
    this.code = code
    this.body = body
  }
}

function readCsrfCookie(): string | undefined {
  const prefix = `${CSRF_COOKIE}=`
  const entry = document.cookie.split('; ').find((cookie) => cookie.startsWith(prefix))
  return entry?.slice(prefix.length)
}

async function csrfToken(): Promise<string> {
  if (!readCsrfCookie()) await fetch('/api/auth/csrf', { credentials: 'same-origin', cache: 'no-store' })
  const token = readCsrfCookie()
  if (!token) throw new ApiError(0, 'Could not obtain a CSRF token')
  return token
}

type Body = FormData | object | undefined

async function send(method: string, path: string, body?: Body): Promise<Response> {
  const headers: Record<string, string> = {}
  // FormData sets its own multipart Content-Type (with the boundary); JSON bodies are declared here.
  if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers[CSRF_HEADER] = await csrfToken()
  return fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    credentials: 'same-origin',
    cache: 'no-store',
  })
}

// One shared refresh at a time: two parallel refreshes would present the same refresh
// token twice, which the backend treats as replay and revokes the session.
let refreshInFlight: Promise<boolean> | null = null

function refreshSession(): Promise<boolean> {
  refreshInFlight ??= send('POST', '/api/auth/refresh')
    .then((response) => response.ok)
    .catch(() => false)
    .finally(() => {
      refreshInFlight = null
    })
  return refreshInFlight
}

// Called when a request is still unauthenticated after a refresh attempt: the session is over.
let sessionEndedHandler: (() => void) | null = null

export function onSessionEnded(handler: (() => void) | null): void {
  sessionEndedHandler = handler
}

const NO_REFRESH_RETRY = new Set(['/api/auth/login', '/api/auth/register', '/api/auth/refresh'])

async function errorFrom(response: Response): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => null)
  const fields = typeof body === 'object' && body !== null ? (body as { detail?: unknown; code?: unknown }) : {}
  const message = typeof fields.detail === 'string' ? fields.detail : `Request failed (HTTP ${response.status})`
  const code = typeof fields.code === 'string' ? fields.code : null
  return new ApiError(response.status, message, code, body)
}

/** Sends the request; on 401 refreshes the session once and retries. Throws ApiError unless the response is OK. */
async function authorized(method: string, path: string, body?: Body): Promise<Response> {
  let response = await send(method, path, body)
  if (response.status === 401 && !NO_REFRESH_RETRY.has(path)) {
    if (await refreshSession()) {
      response = await send(method, path, body) // access token expired; retry once after refresh
    }
    if (response.status === 401) sessionEndedHandler?.()
  }
  if (!response.ok) throw await errorFrom(response)
  return response
}

export async function request<T>(method: string, path: string, body?: Body): Promise<T> {
  const response = await authorized(method, path, body)
  return (response.status === 204 ? undefined : await response.json()) as T
}

/** Downloads a file response and saves it under the file name the server gave it. */
export async function download(path: string): Promise<void> {
  const response = await authorized('GET', path)
  const name = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') ?? '')?.[1] ?? 'wealthwise-report'
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

function query(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value)
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const auth = {
  me: () => request<User>('GET', '/api/auth/me'),
  register: (email: string, password: string) => request<User>('POST', '/api/auth/register', { email, password }),
  login: (email: string, password: string) => request<User>('POST', '/api/auth/login', { email, password }),
  logout: () => request<void>('POST', '/api/auth/logout'),
  setReportingCurrency: (reporting_currency: string) =>
    request<User>('PATCH', '/api/auth/me', { reporting_currency }),
}

export const portfolio = {
  get: () => request<Portfolio>('GET', '/api/portfolio'),
  refresh: () => request<Portfolio>('POST', '/api/portfolio/refresh'),
}

export const imports = {
  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ImportOutcome>('POST', '/api/imports', form)
  },
  history: () => request<ImportAttempt[]>('GET', '/api/imports'),
}

export const finance = {
  listEntries: (filters: EntryFilters = {}) => request<Entry[]>('GET', `/api/finance/entries${query(filters)}`),
  createEntry: (entry: EntryInput) => request<Entry>('POST', '/api/finance/entries', entry),
  replaceEntry: (id: number, entry: EntryInput) => request<Entry>('PUT', `/api/finance/entries/${id}`, entry),
  deleteEntry: (id: number) => request<void>('DELETE', `/api/finance/entries/${id}`),
  listBudgets: (month: string) => request<Budget[]>('GET', `/api/finance/budgets${query({ month })}`),
  createBudget: (month: string, category: string, amount: string) =>
    request<Budget>('POST', '/api/finance/budgets', { month, category, amount }),
  setBudgetAmount: (id: number, amount: string) => request<Budget>('PUT', `/api/finance/budgets/${id}`, { amount }),
  deleteBudget: (id: number) => request<void>('DELETE', `/api/finance/budgets/${id}`),
  budgetSummary: (month: string) => request<BudgetSummary>('GET', `/api/finance/budgets/summary${query({ month })}`),
  netWorth: () => request<NetWorth>('GET', '/api/finance/net-worth'),
  monthlyTotals: (end: string, months = 12) =>
    request<MonthPoint[]>('GET', `/api/finance/monthly-totals${query({ end, months: String(months) })}`),
}

export type ReportFormat = 'pdf' | 'csv'

export const reports = {
  monthly: (month: string, format: ReportFormat) => download(`/api/reports/monthly${query({ month, format })}`),
  portfolio: (format: ReportFormat) => download(`/api/reports/portfolio${query({ format })}`),
}
