// Plain-text wording for backend codes and failed requests (no components here).
import { ApiError } from './api'

/** Human text for a failed request. Backend messages are fixed strings that never echo submitted values. */
export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return error.message
    if (error.status >= 500 && error.status !== 503) return 'The server had a problem. Please try again.'
    return error.message
  }
  return "Couldn't reach the server. Check your connection and try again."
}

const FX_ISSUES: Record<string, string> = {
  disabled: 'currency conversion is turned off on this server',
  invalid_currency: 'invalid currency code',
  unsupported_currency: 'currency not supported for conversion',
  service_unavailable: 'exchange-rate service unreachable',
  rate_limited: 'exchange-rate service is rate limiting requests',
  invalid_response: 'exchange-rate service sent an invalid response',
}

export function fxIssueText(issue: string | null): string {
  return issue ? (FX_ISSUES[issue] ?? issue) : ''
}

/** Where a failed write's message belongs: the field named by the backend's error code, or the form. */
export function placeError(error: unknown, fields: Record<string, string>): { field: string | null; message: string } {
  const code = error instanceof ApiError ? error.code : null
  if (code === 'invalid_request') return { field: null, message: 'Some fields are missing or have the wrong type.' }
  return { field: code ? (fields[code] ?? null) : null, message: errorText(error) }
}

const PRICE_ISSUES: Record<string, string> = {
  not_configured: "live prices aren't configured on this server",
  service_unavailable: 'price service unreachable',
  rate_limited: 'price service is rate limiting requests',
  request_budget_exhausted: 'price request limit reached; try again shortly',
  credentials_rejected: 'price service rejected the API key',
  not_entitled: "not covered by the server's price plan",
  symbol_not_found: 'no listing found for this ISIN',
  symbol_ambiguous: 'more than one possible listing',
  invalid_response: 'price service sent an invalid response',
  missing_isin: 'no ISIN to look up',
  mutual_fund: 'mutual funds are not priced',
}

export function priceIssueText(issue: string | null): string {
  return issue ? (PRICE_ISSUES[issue] ?? issue) : ''
}
