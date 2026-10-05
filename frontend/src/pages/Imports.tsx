import { useState, type FormEvent } from 'react'
import { ApiError, imports } from '../api'
import { Badge, Empty, ErrorNotice, Loading, PageHeading, TableScroll, type Tone } from '../components'
import { formatDate, formatDateTime, label } from '../format'
import { errorText } from '../messages'
import type { ImportAttempt, ImportOutcome, RowError } from '../types'
import { useApi } from '../useApi'

const RESULTS: Record<ImportAttempt['result'], { tone: Tone; text: string; help: string }> = {
  current: { tone: 'ok', text: 'Current', help: 'became the current statement when imported' },
  history: { tone: 'info', text: 'History', help: 'older than the statement already current; stored, not used' },
  failed: { tone: 'bad', text: 'Failed', help: 'no valid rows; nothing stored' },
  rejected: { tone: 'bad', text: 'Rejected', help: 'not a supported broker statement' },
}

function isFailedImport(body: unknown): body is ImportOutcome {
  return typeof body === 'object' && body !== null && (body as { result?: unknown }).result === 'failed'
}

function RowErrors({ errors, caption }: { errors: RowError[]; caption: string }) {
  return (
    <TableScroll labelledBy="row-errors-caption">
      <table>
        <caption id="row-errors-caption">{caption}</caption>
        <thead>
          <tr>
            <th scope="col" className="num">Row</th>
            <th scope="col">Column</th>
            <th scope="col">Problem</th>
          </tr>
        </thead>
        <tbody>
          {errors.map((e, i) => (
            <tr key={`${e.row}-${e.field}-${i}`}>
              <td className="num">{e.row}</td>
              <td>{e.field}</td>
              <td>{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  )
}

function Outcome({ outcome }: { outcome: ImportOutcome }) {
  const what = `${label(outcome.broker)} ${label(outcome.asset_class).toLowerCase()} statement of ${formatDate(outcome.statement_date)}`
  const counts = `${outcome.rows_succeeded} row${outcome.rows_succeeded === 1 ? '' : 's'} imported, ${outcome.rows_failed} with errors.`
  if (outcome.result === 'failed') {
    return (
      <div role="alert" className="notice error">
        <p><strong>Not imported:</strong> the {what} has no valid rows. Nothing was changed.</p>
        <RowErrors errors={outcome.errors} caption="Rows that could not be imported" />
      </div>
    )
  }
  return (
    <div role="status" className={`notice ${outcome.rows_failed ? 'warn' : 'ok'}`}>
      {outcome.result === 'current' ? (
        <p>
          <strong>Imported:</strong> the {what} is now your current {label(outcome.broker)} {label(outcome.asset_class).toLowerCase()} statement
          {outcome.replaced_existing && ' (it replaced an earlier import of the same statement)'}.
        </p>
      ) : (
        <p>
          <strong>Stored as history:</strong> a statement dated on or after this one is already current, so your
          portfolio is unchanged ({what}).
        </p>
      )}
      <p>{counts}</p>
      {outcome.errors.length > 0 && <RowErrors errors={outcome.errors} caption="Rows that were skipped" />}
    </div>
  )
}

function UploadForm({ onImported }: { onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<ImportOutcome | null>(null)
  const [error, setError] = useState<unknown>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file) return
    setBusy(true)
    setOutcome(null)
    setError(null)
    try {
      setOutcome(await imports.upload(file))
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 422 && isFailedImport(caught.body)) setOutcome(caught.body)
      else setError(caught)
    } finally {
      setBusy(false)
      onImported() // every attempt (including failed and rejected ones) appears in the history
    }
  }

  return (
    <form onSubmit={handleSubmit} className="card" aria-labelledby="upload-title">
      <h2 id="upload-title">Import a broker statement</h2>
      <p className="muted">
        Supported: Zerodha equity holdings, Groww stock holdings and Groww mutual fund order history (.xlsx, up to 5 MB).
        Importing the same statement again replaces it.
      </p>
      <label>
        Statement file (.xlsx)
        <input
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={(e) => { setFile(e.target.files?.[0] ?? null); setOutcome(null); setError(null) }}
        />
      </label>
      <button type="submit" disabled={!file || busy}>{busy ? 'Importing…' : 'Import'}</button>
      <div aria-live="polite">
        {outcome && <Outcome outcome={outcome} />}
      </div>
      {error !== null && (
        <div role="alert" className="notice error">
          <p><strong>Not imported:</strong> {errorText(error)}</p>
        </div>
      )}
    </form>
  )
}

function History({ attempts }: { attempts: ImportAttempt[] }) {
  if (attempts.length === 0) return <Empty>No imports yet.</Empty>
  return (
    <TableScroll labelledBy="history-caption">
      <table>
        <caption id="history-caption">Import history (newest first)</caption>
        <thead>
          <tr>
            <th scope="col">Imported</th>
            <th scope="col">File</th>
            <th scope="col">Broker</th>
            <th scope="col">Type</th>
            <th scope="col">Statement date</th>
            <th scope="col" className="num">Rows imported</th>
            <th scope="col" className="num">Rows with errors</th>
            <th scope="col">Result at import time</th>
          </tr>
        </thead>
        <tbody>
          {attempts.map((a) => {
            const result = RESULTS[a.result]
            return (
              <tr key={a.attempt_id}>
                <th scope="row">{formatDateTime(a.imported_at)}</th>
                <td>{a.file_name}</td>
                <td>{label(a.broker)}</td>
                <td>{label(a.asset_class)}</td>
                <td>{formatDate(a.statement_date)}</td>
                <td className="num">{a.rows_succeeded}</td>
                <td className="num">{a.rows_failed}</td>
                <td>
                  <Badge tone={result.tone}>{result.text}</Badge> <span className="sub-inline">{result.help}</span>
                  {a.replaced_existing && <span className="sub-inline">; replaced an earlier import</span>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </TableScroll>
  )
}

export function ImportsPage() {
  const history = useApi(imports.history)
  return (
    <>
      <PageHeading>Imports</PageHeading>
      <UploadForm onImported={history.reload} />
      <section aria-labelledby="history-title" aria-busy={history.loading}>
        <h2 id="history-title">Import history</h2>
        <p className="muted">Your current statements are shown on the Portfolio page with their statement dates.</p>
        {history.error !== null && <ErrorNotice error={history.error} onRetry={history.reload} />}
        {!history.data && history.loading && <Loading what="import history" />}
        {history.data && <History attempts={history.data} />}
      </section>
    </>
  )
}
