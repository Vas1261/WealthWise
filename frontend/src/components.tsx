import { useEffect, useRef, type ReactNode } from 'react'
import { errorText, fxIssueText } from './messages'
import { formatDate, formatMoney, formatPercent, formatRate, isNegative, label } from './format'
import type { FxRate, Totals } from './types'

/** The page's single h1; it takes focus when the page opens so screen readers announce navigation. */
export function PageHeading({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLHeadingElement>(null)
  useEffect(() => ref.current?.focus(), [])
  return <h1 ref={ref} tabIndex={-1}>{children}</h1>
}

export function Loading({ what }: { what: string }) {
  return <p role="status" className="muted">Loading {what}…</p>
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>
}

export function ErrorNotice({ error, onRetry, children }: { error: unknown; onRetry?: () => void; children?: ReactNode }) {
  return (
    <div role="alert" className="notice error">
      <p>{errorText(error)}</p>
      {children}
      {onRetry && <button type="button" onClick={onRetry}>Retry</button>}
    </div>
  )
}

export type Tone = 'ok' | 'warn' | 'bad' | 'info'

/** A status label: always readable text, colour only reinforces it. */
export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>
}

export function Notice({ tone = 'warn', children }: { tone?: Tone; children: ReactNode }) {
  return <p className={`notice ${tone}`}>{children}</p>
}

/** A money value right-aligned, with a "negative" style when the text starts with a minus sign. */
export function Amount({ value, currency }: { value: string | null | undefined; currency: string }) {
  return <span className={isNegative(value) ? 'num negative' : 'num'}>{formatMoney(value, currency)}</span>
}

/** Horizontally scrollable, keyboard-focusable wrapper for wide tables. */
export function TableScroll({ labelledBy, children }: { labelledBy: string; children: ReactNode }) {
  return (
    <div className="table-scroll" role="region" aria-labelledby={labelledBy} tabIndex={0}>
      {children}
    </div>
  )
}

function fxStatusBadge(rate: FxRate) {
  switch (rate.status) {
    case 'identity':
      return <Badge tone="ok">Same currency</Badge>
    case 'fresh':
      return <Badge tone="ok">Current</Badge>
    case 'stale':
      return <Badge tone="warn">Stale{rate.issue ? ` (${fxIssueText(rate.issue)})` : ''}</Badge>
    default:
      return <Badge tone="bad">Unavailable{rate.issue ? ` (${fxIssueText(rate.issue)})` : ''}</Badge>
  }
}

export function FxRatesTable({ rates, reportingCurrency, id }: { rates: FxRate[]; reportingCurrency: string; id: string }) {
  return (
    <TableScroll labelledBy={id}>
      <table>
        <caption id={id}>Exchange rates into {reportingCurrency}</caption>
        <thead>
          <tr>
            <th scope="col">Currency</th>
            <th scope="col" className="num">{reportingCurrency} per unit</th>
            <th scope="col">Rate date</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {rates.map((rate) => (
            <tr key={rate.currency}>
              <th scope="row">{rate.currency}</th>
              <td className="num">{formatRate(rate.rate)}</td>
              <td>{formatDate(rate.rate_date)}</td>
              <td>{fxStatusBadge(rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  )
}

/** Rows of per-group, per-currency totals (breakdowns and native totals stay in native currency, U3). */
export function TotalsTable({ id, caption, groups }: { id: string; caption: string; groups: Record<string, Record<string, Totals>> }) {
  const rows = Object.entries(groups).flatMap(([group, byCurrency]) =>
    Object.values(byCurrency).map((totals) => ({ group, totals })),
  )
  return (
    <TableScroll labelledBy={id}>
      <table>
        <caption id={id}>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Group</th>
            <th scope="col">Currency</th>
            <th scope="col" className="num">Invested</th>
            <th scope="col" className="num">Current value (priced)</th>
            <th scope="col" className="num">Unrealised P&amp;L</th>
            <th scope="col" className="num">P&amp;L %</th>
            <th scope="col" className="num">Mutual funds at cost</th>
            <th scope="col">Prices</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ group, totals: t }) => (
            <tr key={`${group}-${t.currency}`}>
              <th scope="row">{label(group)}</th>
              <td>{t.currency}</td>
              <td className="num"><Amount value={t.invested_value} currency={t.currency} /></td>
              <td className="num"><Amount value={t.current_value} currency={t.currency} /></td>
              <td className="num"><Amount value={t.unrealized_pnl} currency={t.currency} /></td>
              <td className="num">{formatPercent(t.unrealized_pnl_pct)}</td>
              <td className="num"><Amount value={t.cost_floor_value} currency={t.currency} /></td>
              <td><PriceCounts totals={t} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  )
}

/** "3 of 4 priced · 1 stale" from the backend's counts (no arithmetic beyond showing them). */
export function PriceCounts({ totals }: { totals: Totals }) {
  const c = totals.counts
  const parts = [`${c.priced} of ${c.holdings} priced`]
  if (c.stale) parts.push(`${c.stale} stale`)
  if (c.not_supported) parts.push(`${c.not_supported} mutual fund${c.not_supported === 1 ? '' : 's'} at cost`)
  if (c.unavailable) parts.push(`${c.unavailable} unavailable`)
  return <span>{parts.join(' · ')}</span>
}

/** A field's error message, linked to its input through aria-describedby. */
export function FieldError({ id, message }: { id: string; message: string | null }) {
  return message ? <p id={id} role="alert" className="field-error">{message}</p> : null
}
