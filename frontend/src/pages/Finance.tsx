import { useState, type FormEvent } from 'react'
import { finance } from '../api'
import { Amount, Badge, Empty, ErrorNotice, FieldError, Loading, PageHeading, TableScroll } from '../components'
import { formatDate } from '../format'
import { placeError } from '../messages'
import type { Entry, EntryFilters, EntryKind, User } from '../types'
import { useApi } from '../useApi'

const ENTRY_FIELDS = {
  invalid_amount: 'amount',
  invalid_category: 'category',
  invalid_description: 'description',
  invalid_date: 'date',
}

type Draft = { kind: EntryKind; category: string; amount: string; date: string; description: string }

function draftFrom(entry: Entry | null): Draft {
  if (!entry) return { kind: 'expense', category: '', amount: '', date: '', description: '' }
  return { kind: entry.kind, category: entry.category, amount: entry.amount, date: entry.date, description: entry.description ?? '' }
}

function EntryForm({ user, editing, categories, onSaved, onCancel }: {
  user: User
  editing: Entry | null
  categories: string[]
  onSaved: () => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState<Draft>(() => draftFrom(editing))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ field: string | null; message: string } | null>(null)
  const set = (field: keyof Draft) => (event: { target: { value: string } }) =>
    setDraft((d) => ({ ...d, [field]: event.target.value }))

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    // Sent exactly as typed (U8); the backend validates and normalises. No currency is ever sent.
    const body = { kind: draft.kind, category: draft.category, amount: draft.amount, date: draft.date, description: draft.description }
    try {
      if (editing) await finance.replaceEntry(editing.id, body)
      else await finance.createEntry(body)
      if (!editing) setDraft(draftFrom(null))
      onSaved()
    } catch (caught) {
      setError(placeError(caught, ENTRY_FIELDS))
    } finally {
      setBusy(false)
    }
  }

  const fieldError = (field: string) => (error?.field === field ? error.message : null)
  const described = (field: string) => ({
    'aria-invalid': fieldError(field) !== null,
    'aria-describedby': `entry-${field}-error`,
  })
  return (
    <form onSubmit={handleSubmit} aria-labelledby="entry-form-title" className="card">
      <h2 id="entry-form-title">{editing ? 'Edit entry' : 'New entry'}</h2>
      <fieldset>
        <legend>Type</legend>
        <label className="choice">
          <input type="radio" name="kind" value="expense" checked={draft.kind === 'expense'} onChange={set('kind')} /> Expense
        </label>
        <label className="choice">
          <input type="radio" name="kind" value="income" checked={draft.kind === 'income'} onChange={set('kind')} /> Income
        </label>
      </fieldset>
      <label>
        Category
        <input value={draft.category} onChange={set('category')} required list="entry-categories" maxLength={200} {...described('category')} />
      </label>
      <datalist id="entry-categories">
        {categories.map((c) => <option key={c} value={c} />)}
      </datalist>
      <FieldError id="entry-category-error" message={fieldError('category')} />
      <label>
        Amount
        <input value={draft.amount} onChange={set('amount')} required inputMode="decimal" autoComplete="off" {...described('amount')} />
      </label>
      <FieldError id="entry-amount-error" message={fieldError('amount')} />
      <label>
        Date
        <input type="date" value={draft.date} onChange={set('date')} required {...described('date')} />
      </label>
      <FieldError id="entry-date-error" message={fieldError('date')} />
      <label>
        Description (optional)
        <input value={draft.description} onChange={set('description')} maxLength={1000} autoComplete="off" {...described('description')} />
      </label>
      <FieldError id="entry-description-error" message={fieldError('description')} />
      <p className="muted">
        Currency: {editing ? `${editing.currency} (fixed when the entry was created)` : `${user.reporting_currency} (your reporting currency)`}
      </p>
      {error && error.field === null && <p role="alert" className="error">{error.message}</p>}
      <div className="toolbar">
        <button type="submit" disabled={busy}>{busy ? 'Saving…' : editing ? 'Save changes' : 'Add entry'}</button>
        {editing && <button type="button" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  )
}

function Filters({ onApply }: { onApply: (filters: EntryFilters) => void }) {
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [kind, setKind] = useState<'' | EntryKind>('')
  const [category, setCategory] = useState('')

  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onApply({ start, end, kind: kind || undefined, category })
  }

  function clear() {
    setStart('')
    setEnd('')
    setKind('')
    setCategory('')
    onApply({})
  }

  return (
    <form onSubmit={apply} className="inline-form" aria-labelledby="filters-title">
      <h3 id="filters-title">Filter entries</h3>
      <label>From <input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
      <label>To <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
      <label>
        Type
        <select value={kind} onChange={(e) => setKind(e.target.value as '' | EntryKind)}>
          <option value="">All</option>
          <option value="income">Income</option>
          <option value="expense">Expense</option>
        </select>
      </label>
      <label>Category <input value={category} onChange={(e) => setCategory(e.target.value)} /></label>
      <div className="toolbar">
        <button type="submit">Apply filters</button>
        <button type="button" onClick={clear}>Clear</button>
      </div>
    </form>
  )
}

export function FinancePage({ user }: { user: User }) {
  // Filters live in component state only: never in the URL (they can contain personal categories).
  const [filters, setFilters] = useState<EntryFilters>({})
  const entries = useApi(() => finance.listEntries(filters), JSON.stringify(filters))
  const [editing, setEditing] = useState<Entry | null>(null)
  const [rowError, setRowError] = useState<unknown>(null)
  const categories = [...new Set((entries.data ?? []).map((e) => e.category))].sort()

  async function remove(entry: Entry) {
    if (!window.confirm('Delete this entry? This cannot be undone.')) return
    setRowError(null)
    try {
      await finance.deleteEntry(entry.id)
      if (editing?.id === entry.id) setEditing(null)
      entries.reload()
    } catch (caught) {
      setRowError(caught)
    }
  }

  return (
    <>
      <PageHeading>Finance</PageHeading>
      <EntryForm
        key={editing?.id ?? 'new'}
        user={user}
        editing={editing}
        categories={categories}
        onSaved={() => { setEditing(null); entries.reload() }}
        onCancel={() => setEditing(null)}
      />
      <section aria-labelledby="entries-title" aria-busy={entries.loading}>
        <h2 id="entries-title">Income and expenses</h2>
        <Filters onApply={setFilters} />
        {rowError !== null && <ErrorNotice error={rowError} />}
        {entries.error !== null && <ErrorNotice error={entries.error} onRetry={entries.reload} />}
        {!entries.data && entries.loading && <Loading what="entries" />}
        {entries.data && entries.data.length === 0 && <Empty>No entries{Object.values(filters).some(Boolean) ? ' match these filters' : ' yet'}.</Empty>}
        {entries.data && entries.data.length > 0 && (
          <TableScroll labelledBy="entries-caption">
            <table>
              <caption id="entries-caption">Entries (newest first)</caption>
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Type</th>
                  <th scope="col">Category</th>
                  <th scope="col">Description</th>
                  <th scope="col" className="num">Amount</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {entries.data.map((e) => (
                  <tr key={e.id}>
                    <th scope="row">{formatDate(e.date)}</th>
                    <td>{e.kind === 'income' ? <Badge tone="ok">Income</Badge> : <Badge tone="info">Expense</Badge>}</td>
                    <td>{e.category}</td>
                    <td>{e.description ?? ''}</td>
                    <td className="num"><Amount value={e.amount} currency={e.currency} /></td>
                    <td className="actions">
                      <button type="button" onClick={() => setEditing(e)} aria-label={`Edit entry from ${formatDate(e.date)}`}>
                        Edit
                      </button>
                      <button type="button" onClick={() => remove(e)} aria-label={`Delete entry from ${formatDate(e.date)}`}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </section>
    </>
  )
}
