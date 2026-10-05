// A fake backend for component and client tests: stubs global fetch, records every call, and answers
// from a route table ("METHOD /path" or "METHOD /path?query"). All data is synthetic.
import { vi } from 'vitest'

export type Call = {
  method: string
  url: string
  headers: Record<string, string>
  body: unknown
  cache: RequestCache | undefined
  credentials: RequestCredentials | undefined
}

export type Reply = { status?: number; body?: unknown } | Error
type Route = Reply | Reply[] | ((call: Call) => Reply | Promise<Reply>)

export const CSRF = 'test-csrf-token'

export function json(body: unknown, status = 200): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

export function clearCookies(): void {
  for (const cookie of document.cookie.split('; ')) {
    const name = cookie.split('=')[0]
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT`
  }
}

export function fakeApi(routes: Record<string, Route>) {
  const calls: Call[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input)
    const method = init.method ?? 'GET'
    const body = typeof init.body === 'string' ? JSON.parse(init.body) : init.body
    const call: Call = {
      method, url, body, headers: { ...(init.headers as Record<string, string>) }, cache: init.cache,
      credentials: init.credentials,
    }
    calls.push(call)
    if (url === '/api/auth/csrf') {
      document.cookie = `ww_csrf=${CSRF}`
      return new Response(null, { status: 204 })
    }
    if (url === '/health') return json({ status: 'ok', database: 'ok' })
    const route = routes[`${method} ${url}`] ?? routes[`${method} ${url.split('?')[0]}`]
    if (route === undefined) return json({ detail: 'Not Found' }, 404)
    let reply: Reply
    if (Array.isArray(route)) reply = route.length > 1 ? route.shift()! : route[0]
    else reply = typeof route === 'function' ? await route(call) : route
    if (reply instanceof Error) throw reply
    if (reply.status === 204) return new Response(null, { status: 204 })
    return json(reply.body ?? null, reply.status ?? 200)
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    calls,
    fetchMock,
    /** Calls to the API itself (not the CSRF bootstrap or health check). */
    apiCalls: () => calls.filter((c) => c.url !== '/api/auth/csrf' && c.url !== '/health'),
  }
}
