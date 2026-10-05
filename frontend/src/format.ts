// Display-only formatting. Values from the API are decimal strings and are passed to Intl as strings:
// ES2023 Intl.NumberFormat formats decimal strings exactly, so no amount ever goes through a binary float.
// Never convert a money/price/percentage value with Number() or parseFloat(), and never do arithmetic here.

export const MISSING = '—'

type Locale = string | undefined // undefined = the browser's locale (decision U7)

function asNumeric(value: string): Intl.StringNumericLiteral {
  return value as Intl.StringNumericLiteral
}

function decimal(value: string, minFraction: number, maxFraction: number, locale: Locale): string {
  return new Intl.NumberFormat(locale, { minimumFractionDigits: minFraction, maximumFractionDigits: maxFraction })
    .format(asNumeric(value))
}

/** Money with exactly two decimals (the backend already rounded it). */
export function formatMoney(value: string | null | undefined, currency: string, locale?: Locale): string {
  if (value === null || value === undefined) return MISSING
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 2,
    }).format(asNumeric(value))
  } catch {
    return `${decimal(value, 2, 2, locale)} ${currency}` // unknown currency code: plain number + code
  }
}

/** A price as sent (four decimals). */
export function formatPrice(value: string | null | undefined, currency: string, locale?: Locale): string {
  if (value === null || value === undefined) return MISSING
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency', currency, minimumFractionDigits: 4, maximumFractionDigits: 4,
    }).format(asNumeric(value))
  } catch {
    return `${decimal(value, 4, 4, locale)} ${currency}`
  }
}

/** A percentage already multiplied by 100 by the backend ("10.00" -> "10.00 %"). */
export function formatPercent(value: string | null | undefined, locale?: Locale): string {
  if (value === null || value === undefined) return MISSING
  return `${decimal(value, 2, 2, locale)} %`
}

/** Quantities keep their stored precision (up to six decimals), without trailing zeros. */
export function formatQuantity(value: string, locale?: Locale): string {
  return decimal(value, 0, 6, locale)
}

/** FX rates as sent (six decimals). */
export function formatRate(value: string | null, locale?: Locale): string {
  return value === null ? MISSING : decimal(value, 6, 6, locale)
}

/** A calendar date ("2026-10-05"), shown without any time-zone shift. */
export function formatDate(value: string | null | undefined, locale?: Locale): string {
  if (!value) return MISSING
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(`${value}T00:00:00Z`))
}

/** A timestamp from the API (UTC), shown in the browser's local time. */
export function formatDateTime(value: string | null | undefined, locale?: Locale): string {
  if (!value) return MISSING
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

/** "2026-10" -> "October 2026". */
export function formatMonth(value: string, locale?: Locale): string {
  return new Intl.DateTimeFormat(locale, { month: 'long', year: 'numeric', timeZone: 'UTC' })
    .format(new Date(`${value}-01T00:00:00Z`))
}

/** A sign check for styling only (the text already carries the sign). */
export function isNegative(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.startsWith('-')
}

/** The browser-local current month as "YYYY-MM" (decision U4). */
export function currentMonth(today: Date = new Date()): string {
  return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`
}

const WORDS: Record<string, string> = {
  all: 'All holdings',
  equity: 'Equity',
  mutual_fund: 'Mutual funds',
  zerodha: 'Zerodha',
  groww: 'Groww',
}

/** Human labels for backend enum values (asset classes, brokers). */
export function label(value: string | null | undefined): string {
  if (!value) return MISSING
  return WORDS[value] ?? value
}
