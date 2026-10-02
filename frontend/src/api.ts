// Auth tokens live in HttpOnly cookies that this code can never read. The only cookie
// read here is the CSRF token, which is echoed back in a header (double-submit pattern).
// These names must match the backend's CSRF_COOKIE_NAME / CSRF_HEADER_NAME.
const CSRF_COOKIE = 'ww_csrf'
const CSRF_HEADER = 'X-CSRF-Token'

export type User = { id: number; email: string; reporting_currency: string }

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function readCsrfCookie(): string | undefined {
  const prefix = `${CSRF_COOKIE}=`
  const entry = document.cookie.split('; ').find((cookie) => cookie.startsWith(prefix))
  return entry?.slice(prefix.length)
}

async function csrfToken(): Promise<string> {
  if (!readCsrfCookie()) await fetch('/api/auth/csrf', { credentials: 'same-origin' })
  const token = readCsrfCookie()
  if (!token) throw new ApiError(0, 'Could not obtain a CSRF token')
  return token
}

async function send(method: string, path: string, body?: unknown): Promise<Response> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers[CSRF_HEADER] = await csrfToken()
  return fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
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

const NO_REFRESH_RETRY = new Set(['/api/auth/login', '/api/auth/register', '/api/auth/refresh'])

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response = await send(method, path, body)
  if (response.status === 401 && !NO_REFRESH_RETRY.has(path) && (await refreshSession())) {
    response = await send(method, path, body) // access token expired; retry once after refresh
  }
  if (!response.ok) {
    const detail = await response
      .json()
      .then((data: { detail?: unknown }) => (typeof data.detail === 'string' ? data.detail : null))
      .catch(() => null)
    throw new ApiError(response.status, detail ?? `Request failed (HTTP ${response.status})`)
  }
  return (response.status === 204 ? undefined : await response.json()) as T
}

export const auth = {
  me: () => request<User>('GET', '/api/auth/me'),
  register: (email: string, password: string) => request<User>('POST', '/api/auth/register', { email, password }),
  login: (email: string, password: string) => request<User>('POST', '/api/auth/login', { email, password }),
  logout: () => request<void>('POST', '/api/auth/logout'),
}
