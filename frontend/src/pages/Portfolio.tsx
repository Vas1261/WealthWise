import { useState } from 'react'
import { ApiError, portfolio } from '../api'
import {
  Amount,
  Badge,
  Empty,
  ErrorNotice,
  FxRatesTable,
  Loading,
  Notice,
  PageHeading,
  PriceCounts,
  TableScroll,
  TotalsTable,
} from '../components'
import { formatDate, formatDateTime, formatMoney, formatPercent, formatPrice, formatQuantity, label } from '../format'
import { priceIssueText } from '../messages'
import { hrefFor } from '../route'
import type { Holding, Portfolio } from '../types'
import { useApi } from '../useApi'

/** The holding's price state in words; the backend decided every status. */
export function PriceStatus({ holding: h }: { holding: Holding }) {
  if (h.currency_mismatch) return <Badge tone="bad">Price in a different currency: not used</Badge>
  switch (h.price_status) {
    case 'fresh':
      return <Badge tone="ok">Live price · {formatDateTime(h.price_as_of)}</Badge>
    case 'stale':
      return <Badge tone="warn">Stale price · last updated {formatDateTime(h.price_as_of)}</Badge>
    case 'not_supported':
      return <Badge tone="info">Price unavailable: shown at average buy price (floor)</Badge>
    default:
      return <Badge tone="bad">Price unavailable{h.price_issue ? ` · ${priceIssueText(h.price_issue)}` : ''}</Badge>
  }
}

/** Status notices and the reporting-currency totals (shared by the dashboard and the portfolio page). */
export function PortfolioOverview({ data: p, idPrefix }: { data: Portfolio; idPrefix: string }) {
  const r = p.reporting_totals
  if (p.holdings.length === 0) {
    return <Empty>No holdings yet. <a href={hrefFor('imports')}>Import a broker statement</a> to see your portfolio.</Empty>
  }
  return (
    <>
      {p.fx_unavailable.length > 0 && (
        <Notice tone="bad">
          Totals can't be converted to {p.reporting_currency}: no exchange rate for {p.fx_unavailable.join(', ')}.
          Totals in each holding's own currency are shown below.
        </Notice>
      )}
      {p.contains_stale_prices && (
        <Notice>Some prices are stale (the price service could not be reached); oldest from {formatDateTime(p.oldest_price_as_of)}.</Notice>
      )}
      {r && (
        <>
          <dl className="figures">
            <div>
              <dt>Current value (priced holdings)</dt>
              <dd><Amount value={r.current_value} currency={r.currency} /></dd>
            </div>
            <div>
              <dt>Unrealised P&amp;L</dt>
              <dd>
                <Amount value={r.unrealized_pnl} currency={r.currency} /> ({formatPercent(r.unrealized_pnl_pct)})
              </dd>
            </div>
            <div>
              <dt>Cost of priced holdings</dt>
              <dd><Amount value={r.priced_invested_value} currency={r.currency} /></dd>
            </div>
            <div>
              <dt>Total invested</dt>
              <dd><Amount value={r.invested_value} currency={r.currency} /></dd>
            </div>
            <div>
              <dt>Cost of holdings without a price</dt>
              <dd><Amount value={r.unpriced_invested_value} currency={r.currency} /></dd>
            </div>
            <div>
              <dt>Mutual funds at cost (not in current value)</dt>
              <dd><Amount value={r.cost_floor_value} currency={r.currency} /></dd>
            </div>
          </dl>
          {r.current_value === null && <Notice>No holding has a current price, so there is no current value yet.</Notice>}
          <p className="muted">
            <PriceCounts totals={r} />
            {r.oldest_price_as_of && <> · prices as of {formatDateTime(r.oldest_price_as_of)}</>}
          </p>
          {r.fx_stale && <Notice>Exchange rates are stale: the last known rate is used.</Notice>}
          {r.fx_rates.some((rate) => rate.status !== 'identity') && (
            <details>
              <summary>Exchange rates</summary>
              <FxRatesTable id={`${idPrefix}-fx`} rates={r.fx_rates} reportingCurrency={r.currency} />
            </details>
          )}
        </>
      )}
    </>
  )
}

function HoldingsTable({ holdings }: { holdings: Holding[] }) {
  return (
    <TableScroll labelledBy="holdings-caption">
      <table>
        <caption id="holdings-caption">Holdings (values in each holding's own currency)</caption>
        <thead>
          <tr>
            <th scope="col">Instrument</th>
            <th scope="col">Broker</th>
            <th scope="col">Type</th>
            <th scope="col">Statement date</th>
            <th scope="col" className="num">Quantity</th>
            <th scope="col" className="num">Average buy price</th>
            <th scope="col" className="num">Invested</th>
            <th scope="col" className="num">Price</th>
            <th scope="col" className="num">Current value</th>
            <th scope="col" className="num">Unrealised P&amp;L</th>
            <th scope="col" className="num">P&amp;L %</th>
            <th scope="col" className="num">At cost (mutual funds)</th>
            <th scope="col">Price status</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((h) => (
            <tr key={h.holding_id}>
              <th scope="row">
                {h.instrument_name}
                <span className="sub">{[h.symbol, h.isin].filter(Boolean).join(' · ')}</span>
              </th>
              <td>{label(h.source_broker)}</td>
              <td>{label(h.asset_class)}</td>
              <td>{formatDate(h.statement_date)}</td>
              <td className="num">{formatQuantity(h.quantity)}</td>
              <td className="num">{formatPrice(h.average_buy_price, h.native_currency)}</td>
              <td className="num"><Amount value={h.invested_value} currency={h.native_currency} /></td>
              <td className="num">{formatPrice(h.current_price, h.native_currency)}</td>
              <td className="num"><Amount value={h.current_value} currency={h.native_currency} /></td>
              <td className="num"><Amount value={h.unrealized_pnl} currency={h.native_currency} /></td>
              <td className="num">{formatPercent(h.unrealized_pnl_pct)}</td>
              <td className="num">{h.cost_floor_value === null ? '—' : formatMoney(h.cost_floor_value, h.native_currency)}</td>
              <td><PriceStatus holding={h} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  )
}

export function PortfolioError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const conflict = error instanceof ApiError && error.status === 409
  return (
    <ErrorNotice error={error} onRetry={conflict ? undefined : onRetry}>
      {conflict && <p><a href={hrefFor('imports')}>Go to Imports</a> to upload the latest statement.</p>}
    </ErrorNotice>
  )
}

export function PortfolioPage() {
  const state = useApi(portfolio.get)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<unknown>(null)
  const p = state.data

  async function handleRefresh() {
    setRefreshing(true)
    setRefreshError(null)
    try {
      state.setData(await portfolio.refresh())
    } catch (error) {
      setRefreshError(error)
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <>
      <PageHeading>Portfolio</PageHeading>
      <div className="toolbar">
        <button type="button" onClick={handleRefresh} disabled={refreshing || state.loading} aria-describedby="refresh-help">
          {refreshing ? 'Refreshing…' : 'Refresh prices'}
        </button>
        <span id="refresh-help" className="muted">
          Prices are cached for a few minutes; a refresh fetches new ones at most once a minute.
        </span>
      </div>
      {refreshError !== null && <PortfolioError error={refreshError} onRetry={handleRefresh} />}
      <section aria-labelledby="pf-summary" aria-busy={state.loading || refreshing}>
        <h2 id="pf-summary">Summary{p ? ` (${p.reporting_currency})` : ''}</h2>
        {state.error !== null && <PortfolioError error={state.error} onRetry={state.reload} />}
        {!p && state.loading && <Loading what="portfolio" />}
        {p && <PortfolioOverview data={p} idPrefix="pf" />}
      </section>
      {p && p.holdings.length > 0 && (
        <>
          <section aria-labelledby="pf-native">
            <h2 id="pf-native">Totals by currency</h2>
            <TotalsTable id="pf-native-caption" caption="Totals in each currency (not converted)" groups={{ all: p.totals_by_currency }} />
          </section>
          <section aria-labelledby="pf-breakdowns">
            <h2 id="pf-breakdowns">Breakdowns</h2>
            <TotalsTable id="pf-asset-caption" caption="By asset class (native currency)" groups={p.by_asset_class} />
            <TotalsTable id="pf-broker-caption" caption="By broker (native currency)" groups={p.by_broker} />
          </section>
          <section aria-labelledby="pf-holdings">
            <h2 id="pf-holdings">Holdings</h2>
            <HoldingsTable holdings={p.holdings} />
          </section>
        </>
      )}
    </>
  )
}
