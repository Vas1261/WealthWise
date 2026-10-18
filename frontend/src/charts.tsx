// Charts (Chart.js, as named in the Phase 1 technology table). Charts sit beside the tables, never instead
// of them: every chart has a caption, a text summary for screen readers and a "Show the numbers" table.
//
// The app's rule is that money is never turned into a binary float. Drawing needs numbers, so plot() in
// chartData.ts is the one exception: floats only position marks. Every figure a reader sees in a tooltip or table is the
// backend's exact decimal string, formatted by format.ts.
import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
  type ChartConfiguration,
} from 'chart.js'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { allocationBars, gainSplit, plot, pnlBars, TOP_HOLDINGS, type AllocationView, type Bar } from './chartData'
import { formatMoney, formatMonth, formatPercent } from './format'
import type { Holding, MonthPoint, Portfolio, ReportingTotals } from './types'

Chart.register(BarController, BarElement, CategoryScale, LinearScale, LineController, LineElement, PointElement,
  Tooltip, Legend)

/** Colour roles, read from CSS custom properties so light and dark mode each use their own validated steps. */
type Theme = Record<'series1' | 'series2' | 'series3' | 'gain' | 'loss' | 'total' | 'text' | 'muted' | 'grid' | 'surface', string>

const TOKENS: Record<keyof Theme, string> = {
  series1: '--chart-series-1', series2: '--chart-series-2', series3: '--chart-series-3', gain: '--chart-gain',
  loss: '--chart-loss', total: '--chart-total', text: '--chart-text', muted: '--chart-muted', grid: '--chart-grid',
  surface: '--chart-surface',
}

function readTheme(): Theme {
  const style = getComputedStyle(document.documentElement)
  return Object.fromEntries(
    Object.entries(TOKENS).map(([role, token]) => [role, style.getPropertyValue(token).trim() || '#888']),
  ) as Theme
}

const BAR = { maxBarThickness: 24, borderRadius: 4, borderSkipped: 'start' as const, borderWidth: 0 }
// Axis ticks in plain grouped digits (compact forms such as en-IN "6T" for 6,000 read like trillions).
const tick = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })

function axes(theme: Theme, valueAxis: 'x' | 'y') {
  const value = { grid: { color: theme.grid }, border: { display: false }, ticks: { color: theme.muted, callback: (v: number | string) => tick.format(+v) } }
  const category = { grid: { display: false }, border: { color: theme.grid }, ticks: { color: theme.text } }
  return valueAxis === 'x' ? { x: value, y: category } : { x: category, y: value }
}

/** One Chart.js canvas, rebuilt when its config or the colour scheme changes. */
function ChartCanvas({ build, summary, height }: { build: (theme: Theme) => ChartConfiguration; summary: string; height: number }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [scheme, setScheme] = useState(0)

  useEffect(() => {
    const media = window.matchMedia?.('(prefers-color-scheme: dark)')
    const onChange = () => setScheme((n) => n + 1)
    media?.addEventListener('change', onChange)
    return () => media?.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    const element = canvas.current
    if (!element || !element.getContext('2d')) return // no canvas support (e.g. tests): the table still shows everything
    const chart = new Chart(element, build(readTheme()))
    return () => chart.destroy()
  }, [build, scheme])

  return (
    <div className="chart-canvas" style={{ height }}>
      <canvas ref={canvas} role="img" aria-label={summary} />
    </div>
  )
}

function ChartFigure({ title, note, children, table }: { title: string; note?: ReactNode; children: ReactNode; table: ReactNode }) {
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      {note && <p className="muted">{note}</p>}
      {children}
      <details>
        <summary>Show the numbers</summary>
        {table}
      </details>
    </figure>
  )
}

function DataTable({ caption, head, rows }: { caption: string; head: string[]; rows: ReactNode[][] }) {
  return (
    <table>
      <caption>{caption}</caption>
      <thead>
        <tr>{head.map((h, i) => <th key={h} scope="col" className={i > 0 ? 'num' : undefined}>{h}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((cells, r) => (
          <tr key={r}>
            {cells.map((cell, i) => i === 0 ? <th key={i} scope="row">{cell}</th> : <td key={i} className="num">{cell}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// --- Allocation ------------------------------------------------------------------------------------------------

const ALLOCATION_VIEWS: [AllocationView, string][] = [['holding', 'By holding'], ['broker', 'By broker'], ['asset_class', 'By asset class']]

export function AllocationChart({ data }: { data: Portfolio }) {
  const [view, setView] = useState<AllocationView>('holding')
  const result = allocationBars(data, view)
  if (!result) return <p className="muted">The allocation chart needs all holdings in one currency; see the totals tables.</p>
  const { currency, bars, total } = result
  return (
    <ChartFigure
      title={`Where your money is: amount invested (${currency})`}
      note={view === 'holding' && total > TOP_HOLDINGS ? `The ${TOP_HOLDINGS} largest of ${total} holdings; the holdings table lists all.` : undefined}
      table={<DataTable caption="Amount invested" head={['Name', 'Invested']} rows={bars.map((b) => [b.name, formatMoney(b.value, currency)])} />}
    >
      <div className="toolbar" role="group" aria-label="Allocation view">
        {ALLOCATION_VIEWS.map(([key, text]) => (
          <button key={key} type="button" aria-pressed={view === key} onClick={() => setView(key)}>{text}</button>
        ))}
      </div>
      <AllocationBars key={`${view}-${bars.map((b) => `${b.name}=${b.value}`).join()}`} bars={bars} currency={currency}
        summary={`Bar chart of amount invested, ${ALLOCATION_VIEWS.find(([k]) => k === view)![1].toLowerCase()}; largest: ${bars[0]?.name ?? 'none'}.`} />
    </ChartFigure>
  )
}

function AllocationBars({ bars, currency, summary }: { bars: Bar[]; currency: string; summary: string }) {
  const [build] = useState(() => (theme: Theme): ChartConfiguration => ({
    type: 'bar',
    data: {
      labels: bars.map((b) => b.name),
      datasets: [{ label: 'Amount invested', data: bars.map((b) => plot(b.value)), backgroundColor: theme.series1, ...BAR }],
    },
    options: {
      indexAxis: 'y', maintainAspectRatio: false, animation: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (item) => formatMoney(bars[item.dataIndex].value, currency) } },
      },
      scales: axes(theme, 'x'),
    },
  }))
  return <ChartCanvas build={build} height={bars.length * 28 + 40} summary={summary} />
}

// --- Gain or loss per holding ---------------------------------------------------------------------------------

export function PnlChart({ holdings }: { holdings: Holding[] }) {
  const groups = [...pnlBars(holdings)]
  if (groups.length === 0) return <p className="muted">No holding has a current price yet, so there is no gain or loss to chart.</p>
  return (
    <>
      {groups.map(([currency, list]) => (
        <PnlCurrencyChart key={`${currency}-${list.map((h) => h.holding_id).join()}`} currency={currency} list={list} />
      ))}
    </>
  )
}

function PnlCurrencyChart({ currency, list }: { currency: string; list: Holding[] }) {
  const negative = (h: Holding) => h.unrealized_pnl!.startsWith('-')
  const [build] = useState(() => (theme: Theme): ChartConfiguration => ({
    type: 'bar',
    data: {
      labels: list.map((h) => h.instrument_name),
      datasets: [{
        label: 'Unrealised gain or loss', data: list.map((h) => plot(h.unrealized_pnl)),
        backgroundColor: list.map((h) => (negative(h) ? theme.loss : theme.gain)), ...BAR,
      }],
    },
    options: {
      indexAxis: 'y', maintainAspectRatio: false, animation: false,
      plugins: {
        legend: {
          display: true, position: 'top', align: 'start',
          labels: {
            color: theme.text, boxWidth: 12,
            generateLabels: () => [
              { text: 'Gain', fillStyle: theme.gain, strokeStyle: theme.gain, lineWidth: 0, fontColor: theme.text },
              { text: 'Loss', fillStyle: theme.loss, strokeStyle: theme.loss, lineWidth: 0, fontColor: theme.text },
            ],
          },
          onClick: () => {},
        },
        tooltip: {
          callbacks: {
            label: (item) => {
              const h = list[item.dataIndex]
              return `${formatMoney(h.unrealized_pnl, currency)} (${formatPercent(h.unrealized_pnl_pct)})`
            },
          },
        },
      },
      scales: axes(theme, 'x'),
    },
  }))
  const gains = list.filter((h) => !negative(h)).length
  return (
    <ChartFigure
      title={`Unrealised gain or loss per holding (${currency})`}
      note="Priced holdings only, largest gain first. Mutual funds have no market price here."
      table={<DataTable caption={`Unrealised gain or loss (${currency})`} head={['Holding', 'Gain or loss', '%']}
        rows={list.map((h) => [h.instrument_name, formatMoney(h.unrealized_pnl, currency), formatPercent(h.unrealized_pnl_pct)])} />}
    >
      <ChartCanvas build={build} height={list.length * 26 + 70}
        summary={`Bar chart: ${gains} of ${list.length} priced holdings show a gain; largest gain ${list[0].instrument_name}, largest loss ${list[list.length - 1].instrument_name}.`} />
    </ChartFigure>
  )
}

// --- Gain split (pinned FX, FR-20) -------------------------------------------------------------------------------

export function GainSplitChart({ totals: r }: { totals: ReportingTotals }) {
  const split = gainSplit(r)
  const [build] = useState(() => (theme: Theme): ChartConfiguration => ({
    type: 'bar',
    data: {
      labels: (split?.bars ?? []).map((b) => b.name),
      datasets: [{
        label: 'Gain', data: (split?.bars ?? []).map((b) => plot(b.value)),
        backgroundColor: (split?.bars ?? []).map((b) => (b.name === 'Total gain' ? theme.total : b.value.startsWith('-') ? theme.loss : theme.gain)),
        ...BAR,
      }],
    },
    options: {
      indexAxis: 'y', maintainAspectRatio: false, animation: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (item) => formatMoney(split!.bars[item.dataIndex].value, r.currency) } },
      },
      scales: axes(theme, 'x'),
    },
  }))
  if (!split) return null
  const [market, currencyGain, total] = split.bars
  return (
    <ChartFigure
      title={`Where the gain comes from: price vs exchange rate (${r.currency})`}
      note={`Priced holdings. Cost ${formatMoney(split.cost, r.currency)} at the exchange rates of the dates it was paid; current value ${formatMoney(split.value, r.currency)} at today's rate. Market gain + currency gain = total gain.`}
      table={<DataTable caption={`Gain split (${r.currency})`} head={['Figure', 'Amount']}
        rows={[['Cost at purchase-time rates', formatMoney(split.cost, r.currency)],
          ...split.bars.map((b) => [b.name, formatMoney(b.value, r.currency)]),
          ['Current value', formatMoney(split.value, r.currency)]]} />}
    >
      <ChartCanvas build={build} height={3 * 30 + 50}
        summary={`Bar chart: market gain ${formatMoney(market.value, r.currency)}, currency gain ${formatMoney(currencyGain.value, r.currency)}, total gain ${formatMoney(total.value, r.currency)}.`} />
    </ChartFigure>
  )
}

// --- Income vs expenses over time ----------------------------------------------------------------------------------

export function IncomeExpensesChart({ points, currency }: { points: MonthPoint[]; currency: string }) {
  const rows = points.map((p) => ({ month: p.month, totals: p.totals.find((t) => t.currency === currency) ?? null }))
  const otherCurrencies = points.some((p) => p.totals.some((t) => t.currency !== currency))
  const short = new Intl.DateTimeFormat(undefined, { month: 'short', year: '2-digit', timeZone: 'UTC' })
  const [build] = useState(() => (theme: Theme): ChartConfiguration => ({
    type: 'bar',
    data: {
      labels: rows.map((r) => short.format(new Date(`${r.month}-01T00:00:00Z`))),
      datasets: [
        { type: 'bar', label: 'Income', data: rows.map((r) => plot(r.totals?.income ?? null)), backgroundColor: theme.series1, ...BAR },
        { type: 'bar', label: 'Expenses', data: rows.map((r) => plot(r.totals?.expenses ?? null)), backgroundColor: theme.series2, ...BAR },
        {
          type: 'line', label: 'Net (income minus expenses)', data: rows.map((r) => plot(r.totals?.net ?? null)),
          borderColor: theme.series3, backgroundColor: theme.series3, borderWidth: 2, pointRadius: 4,
          pointBorderColor: theme.surface, pointBorderWidth: 2, spanGaps: false,
        },
      ],
    },
    options: {
      maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top', align: 'start', labels: { color: theme.text, boxWidth: 12 } },
        tooltip: {
          callbacks: {
            label: (item) => {
              const t = rows[item.dataIndex].totals
              const value = t === null ? null : [t.income, t.expenses, t.net][item.datasetIndex]
              return `${item.dataset.label}: ${value === null ? 'no entries' : formatMoney(value, currency)}`
            },
          },
        },
      },
      scales: axes(theme, 'y'),
    },
  }))
  if (rows.every((r) => r.totals === null)) {
    return <p className="muted">No income or expenses in the last {points.length} months yet.</p>
  }
  return (
    <ChartFigure
      title={`Income and expenses per month (${currency})`}
      note={otherCurrencies ? `Entries in other currencies are not in this chart; the Finance page lists them.` : undefined}
      table={<DataTable caption={`Income and expenses per month (${currency})`} head={['Month', 'Income', 'Expenses', 'Net']}
        rows={rows.map((r) => [formatMonth(r.month), ...(r.totals
          ? [formatMoney(r.totals.income, currency), formatMoney(r.totals.expenses, currency), formatMoney(r.totals.net, currency)]
          : ['No entries', '—', '—'])])} />}
    >
      <ChartCanvas build={build} height={280}
        summary={`Bar chart of monthly income and expenses with a net line, ${formatMonth(rows[0].month)} to ${formatMonth(rows[rows.length - 1].month)}.`} />
    </ChartFigure>
  )
}
