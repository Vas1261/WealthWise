import { useState, type FormEvent } from 'react'
import { ApiError, finance, reports } from '../api'
import { Amount, Badge, Empty, ErrorNotice, FieldError, Loading, PageHeading, ReportDownloads, TableScroll } from '../components'
import { currentMonth, formatMonth, formatPercent } from '../format'
import { placeError } from '../messages'
import type { Budget, BudgetSummary } from '../types'
import { useApi } from '../useApi'

const BUDGET_FIELDS = { invalid_category: 'category', invalid_amount: 'amount', invalid_month: 'month', budget_exists: 'category' }

/** Budget vs actual for one month; every figure and flag comes from the backend. */
export function BudgetSummaryView({ summary, idPrefix, overFirst = false }: { summary: BudgetSummary; idPrefix: string; overFirst?: boolean }) {
  const lines = overFirst
    ? [...summary.budgets.filter((l) => l.over_budget), ...summary.budgets.filter((l) => !l.over_budget)]
    : summary.budgets
  const month = formatMonth(summary.month)
  return (
    <>
      {lines.length === 0 ? (
        <Empty>No budgets for {month} yet.</Empty>
      ) : (
        <TableScroll labelledBy={`${idPrefix}-caption`}>
          <table>
            <caption id={`${idPrefix}-caption`}>Budget vs actual, {month}</caption>
            <thead>
              <tr>
                <th scope="col">Category</th>
                <th scope="col" className="num">Budget</th>
                <th scope="col" className="num">Spent</th>
                <th scope="col" className="num">Remaining</th>
                <th scope="col">Used</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr key={line.budget_id}>
                  <th scope="row">{line.category}</th>
                  <td className="num"><Amount value={line.budget} currency={line.currency} /></td>
                  <td className="num"><Amount value={line.actual} currency={line.currency} /></td>
                  <td className="num"><Amount value={line.remaining} currency={line.currency} /></td>
                  <td>
                    {/* The bar is full from 100 % on, so its colour has to come from the backend's over_budget flag:
                        with optimum 0, a value up to `low` is drawn as good and one above `high` as bad. */}
                    <meter min={0} max={100} optimum={0} low={line.over_budget ? 1 : 100} high={line.over_budget ? 99 : 100}
                      value={line.utilization_pct} aria-label={`${line.category}: budget used`} />{' '}
                    {formatPercent(line.utilization_pct)}
                  </td>
                  <td>{line.over_budget ? <Badge tone="bad">Over budget</Badge> : <Badge tone="ok">Within budget</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
      {summary.unbudgeted.length > 0 && (
        <TableScroll labelledBy={`${idPrefix}-unbudgeted`}>
          <table>
            <caption id={`${idPrefix}-unbudgeted`}>Spending without a budget, {month}</caption>
            <thead>
              <tr>
                <th scope="col">Category</th>
                <th scope="col" className="num">Spent</th>
              </tr>
            </thead>
            <tbody>
              {summary.unbudgeted.map((u) => (
                <tr key={`${u.category}-${u.currency}`}>
                  <th scope="row">{u.category}</th>
                  <td className="num"><Amount value={u.actual} currency={u.currency} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </>
  )
}

function CreateBudgetForm({ month, onCreated }: { month: string; onCreated: () => void }) {
  const [category, setCategory] = useState('')
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ field: string | null; message: string } | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await finance.createBudget(month, category, amount) // amount exactly as typed (U8)
      setCategory('')
      setAmount('')
      onCreated()
    } catch (caught) {
      const placed = placeError(caught, BUDGET_FIELDS)
      setError(caught instanceof ApiError && caught.code === 'budget_exists'
        ? { ...placed, message: `A budget for this category already exists in ${formatMonth(month)}; edit it below.` }
        : placed)
    } finally {
      setBusy(false)
    }
  }

  const fieldError = (field: string) => (error?.field === field ? error.message : null)
  return (
    <form onSubmit={handleSubmit} className="inline-form" aria-labelledby="budget-new">
      <h3 id="budget-new">New budget for {formatMonth(month)}</h3>
      <label>
        Category
        <input value={category} onChange={(e) => setCategory(e.target.value)} required maxLength={200}
          aria-invalid={fieldError('category') !== null} aria-describedby="budget-category-error" />
      </label>
      <FieldError id="budget-category-error" message={fieldError('category')} />
      <label>
        Amount
        <input value={amount} onChange={(e) => setAmount(e.target.value)} required inputMode="decimal" autoComplete="off"
          aria-invalid={fieldError('amount') !== null} aria-describedby="budget-amount-error" />
      </label>
      <FieldError id="budget-amount-error" message={fieldError('amount')} />
      {error && error.field === null && <p role="alert" className="error">{error.message}</p>}
      <button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Add budget'}</button>
    </form>
  )
}

function BudgetRow({ budget, onChanged }: { budget: Budget; onChanged: () => void }) {
  const [editing, setEditing] = useState(false)
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const errorId = `budget-${budget.id}-error`

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await finance.setBudgetAmount(budget.id, amount) // exactly as typed (U8)
      setEditing(false)
      onChanged()
    } catch (caught) {
      setError(placeError(caught, BUDGET_FIELDS).message)
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!window.confirm(`Delete the ${budget.category} budget for ${formatMonth(budget.month)}?`)) return
    setError(null)
    try {
      await finance.deleteBudget(budget.id)
      onChanged()
    } catch (caught) {
      setError(placeError(caught, {}).message)
    }
  }

  return (
    <tr>
      <th scope="row">{budget.category}</th>
      <td className="num">
        {editing ? (
          <form onSubmit={save} className="row-form">
            <label>
              New amount for {budget.category}
              <input value={amount} onChange={(e) => setAmount(e.target.value)} required inputMode="decimal" autoComplete="off"
                aria-invalid={error !== null} aria-describedby={errorId} />
            </label>
            <button type="submit" disabled={busy}>Save</button>
            <button type="button" onClick={() => { setEditing(false); setError(null) }}>Cancel</button>
          </form>
        ) : (
          <Amount value={budget.amount} currency={budget.currency} />
        )}
        {error && <p id={errorId} role="alert" className="field-error">{error}</p>}
      </td>
      <td>{budget.currency}</td>
      <td className="actions">
        {!editing && (
          <button type="button" onClick={() => { setAmount(budget.amount); setEditing(true) }}
            aria-label={`Edit ${budget.category} budget`}>
            Edit
          </button>
        )}
        <button type="button" onClick={remove} aria-label={`Delete ${budget.category} budget`}>
          Delete
        </button>
      </td>
    </tr>
  )
}

export function BudgetsPage() {
  const [month, setMonth] = useState(currentMonth())
  const budgets = useApi(() => finance.listBudgets(month), month)
  const summary = useApi(() => finance.budgetSummary(month), month)

  function changed() {
    budgets.reload()
    summary.reload()
  }

  return (
    <>
      <PageHeading>Budgets</PageHeading>
      <label className="month-picker">
        Month
        <input type="month" value={month} required onChange={(e) => e.target.value && setMonth(e.target.value)} />
      </label>
      <ReportDownloads title={`Monthly report for ${formatMonth(month)}`} onDownload={(format) => reports.monthly(month, format)} />

      <section aria-labelledby="budget-vs-actual" aria-busy={summary.loading}>
        <h2 id="budget-vs-actual">Budget vs actual</h2>
        {summary.error !== null && <ErrorNotice error={summary.error} onRetry={summary.reload} />}
        {!summary.data && summary.loading && <Loading what="budget summary" />}
        {summary.data && <BudgetSummaryView summary={summary.data} idPrefix="budget-summary" />}
      </section>

      <section aria-labelledby="budget-manage" aria-busy={budgets.loading}>
        <h2 id="budget-manage">Manage budgets</h2>
        <p className="muted">Budgets count expenses only. New budgets use your current reporting currency; existing ones keep theirs.</p>
        <CreateBudgetForm month={month} onCreated={changed} />
        {budgets.error !== null && <ErrorNotice error={budgets.error} onRetry={budgets.reload} />}
        {!budgets.data && budgets.loading && <Loading what="budgets" />}
        {budgets.data && budgets.data.length > 0 && (
          <TableScroll labelledBy="budget-list-caption">
            <table>
              <caption id="budget-list-caption">Budgets for {formatMonth(month)}</caption>
              <thead>
                <tr>
                  <th scope="col">Category</th>
                  <th scope="col" className="num">Amount</th>
                  <th scope="col">Currency</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {budgets.data.map((b) => <BudgetRow key={b.id} budget={b} onChanged={changed} />)}
              </tbody>
            </table>
          </TableScroll>
        )}
      </section>
    </>
  )
}
