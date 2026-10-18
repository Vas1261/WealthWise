import { finance, portfolio } from '../api'
import { AllocationChart, IncomeExpensesChart } from '../charts'
import { Amount, ErrorNotice, FxRatesTable, Loading, Notice, PageHeading, TotalsTable } from '../components'
import { currentMonth, formatDateTime, formatMoney, formatMonth } from '../format'
import { hrefFor } from '../route'
import type { NetWorth } from '../types'
import { useApi } from '../useApi'
import { BudgetSummaryView } from './Budgets'
import { PortfolioError, PortfolioOverview } from './Portfolio'

/** Net worth exactly as the backend reports it; a missing figure is never shown as zero. */
export function NetWorthView({ data: n }: { data: NetWorth }) {
  const cash = Object.entries(n.cash_by_currency)
  return (
    <>
      {n.net_worth === null ? (
        <Notice tone="bad">
          Net worth can't be calculated right now: no exchange rate for {n.fx_unavailable.join(', ')} into{' '}
          {n.reporting_currency}.
        </Notice>
      ) : (
        <p className="headline">
          <span className="visually-hidden">Net worth: </span>
          <Amount value={n.net_worth} currency={n.reporting_currency} />
        </p>
      )}
      <dl className="figures">
        <div>
          <dt>Portfolio value (priced holdings)</dt>
          <dd>{n.portfolio_value === null ? 'Unavailable' : <Amount value={n.portfolio_value} currency={n.reporting_currency} />}</dd>
        </div>
        <div>
          <dt>Cash position (income minus expenses)</dt>
          <dd>{n.cash_position === null ? 'Unavailable' : <Amount value={n.cash_position} currency={n.reporting_currency} />}</dd>
        </div>
        {n.cost_floor_value !== null && n.cost_floor_value !== '0.00' && (
          <div>
            <dt>Mutual funds at cost (not included)</dt>
            <dd><Amount value={n.cost_floor_value} currency={n.reporting_currency} /></dd>
          </div>
        )}
      </dl>
      {!n.complete && n.net_worth !== null && n.unpriced_holdings > 0 && (
        <Notice>
          Excludes {n.unpriced_holdings} holding{n.unpriced_holdings === 1 ? '' : 's'} without a current price. Mutual
          funds have no market price here and are shown at cost instead, separately.
        </Notice>
      )}
      {n.contains_stale_prices && (
        <Notice>Some prices are stale; oldest from {formatDateTime(n.oldest_price_as_of)}.</Notice>
      )}
      {n.fx_stale && <Notice>Exchange rates are stale: the last known rate is used.</Notice>}
      {(cash.length > 1 || (cash.length === 1 && cash[0][0] !== n.reporting_currency)) && (
        <p className="muted">
          Cash by currency (not converted):{' '}
          {cash.map(([currency, amount]) => formatMoney(amount, currency)).join(' · ')}
        </p>
      )}
      {n.fx_rates.some((rate) => rate.status !== 'identity') && (
        <details>
          <summary>Exchange rates</summary>
          <FxRatesTable id="nw-fx" rates={n.fx_rates} reportingCurrency={n.reporting_currency} />
        </details>
      )}
    </>
  )
}

export function DashboardPage() {
  const month = currentMonth() // browser-local month (U4)
  const netWorth = useApi(finance.netWorth)
  const pf = useApi(portfolio.get)
  const budgets = useApi(() => finance.budgetSummary(month), month)
  const monthly = useApi(() => finance.monthlyTotals(month), month)

  return (
    <>
      <PageHeading>Dashboard</PageHeading>
      <div className="dashboard">
        <section className="card" aria-labelledby="dash-nw" aria-busy={netWorth.loading}>
          <h2 id="dash-nw">Net worth</h2>
          {netWorth.error !== null && <PortfolioError error={netWorth.error} onRetry={netWorth.reload} />}
          {!netWorth.data && netWorth.loading && <Loading what="net worth" />}
          {netWorth.data && <NetWorthView data={netWorth.data} />}
        </section>

        <section className="card" aria-labelledby="dash-pf" aria-busy={pf.loading}>
          <h2 id="dash-pf">Portfolio{pf.data ? ` (${pf.data.reporting_currency})` : ''}</h2>
          {pf.error !== null && <PortfolioError error={pf.error} onRetry={pf.reload} />}
          {!pf.data && pf.loading && <Loading what="portfolio" />}
          {pf.data && <PortfolioOverview data={pf.data} idPrefix="dash" />}
          {pf.data && pf.data.holdings.length > 0 && <p><a href={hrefFor('portfolio')}>View all holdings</a></p>}
        </section>

        <section className="card wide" aria-labelledby="dash-breakdowns" aria-busy={pf.loading}>
          <h2 id="dash-breakdowns">Breakdowns</h2>
          <p className="muted">In each holding's own currency (not converted).</p>
          {pf.data && pf.data.holdings.length > 0 ? (
            <>
              <AllocationChart data={pf.data} />
              <TotalsTable id="dash-asset-caption" caption="By asset class" groups={pf.data.by_asset_class} />
              <TotalsTable id="dash-broker-caption" caption="By broker" groups={pf.data.by_broker} />
            </>
          ) : (
            !pf.loading && pf.error === null && <p className="muted">Nothing to break down yet.</p>
          )}
        </section>

        <section className="card wide" aria-labelledby="dash-monthly" aria-busy={monthly.loading}>
          <h2 id="dash-monthly">Income and expenses, last 12 months</h2>
          {monthly.error !== null && <ErrorNotice error={monthly.error} onRetry={monthly.reload} />}
          {!monthly.data && monthly.loading && <Loading what="monthly totals" />}
          {monthly.data && netWorth.data && (
            <IncomeExpensesChart points={monthly.data} currency={netWorth.data.reporting_currency} />
          )}
          <p><a href={hrefFor('finance')}>Record income and expenses</a></p>
        </section>

        <section className="card wide" aria-labelledby="dash-budgets" aria-busy={budgets.loading}>
          <h2 id="dash-budgets">Budgets this month ({formatMonth(month)})</h2>
          {budgets.error !== null && <ErrorNotice error={budgets.error} onRetry={budgets.reload} />}
          {!budgets.data && budgets.loading && <Loading what="budgets" />}
          {budgets.data && <BudgetSummaryView summary={budgets.data} idPrefix="dash-budget" overFirst />}
          <p><a href={hrefFor('budgets')}>Manage budgets</a></p>
        </section>
      </div>
    </>
  )
}
