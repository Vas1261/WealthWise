import { useState, type FormEvent } from 'react'
import { auth } from '../api'
import { FieldError, PageHeading } from '../components'
import { errorText } from '../messages'
import type { User } from '../types'

export function SettingsPage({ user, onUserChange }: { user: User; onUserChange: (user: User) => void }) {
  const [currency, setCurrency] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await auth.setReportingCurrency(currency) // validated by the backend (format and Frankfurter support)
      const refreshed = await auth.me() // re-read the profile from the server
      onUserChange(refreshed)
      setCurrency('')
      setNotice(
        `Reporting currency is now ${refreshed.reporting_currency}. Totals and net worth are shown in it; new finance ` +
        'entries and budgets use it, and existing ones keep their own currency.',
      )
    } catch (caught) {
      setError(errorText(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeading>Settings</PageHeading>
      <section className="card" aria-labelledby="account-title">
        <h2 id="account-title">Account</h2>
        <dl className="figures">
          <div><dt>Email</dt><dd>{user.email}</dd></div>
          <div><dt>Reporting currency</dt><dd>{user.reporting_currency}</dd></div>
        </dl>
      </section>
      <form onSubmit={handleSubmit} className="card" aria-labelledby="currency-title">
        <h2 id="currency-title">Change reporting currency</h2>
        <label>
          New reporting currency (three-letter ISO code, e.g. EUR)
          <input
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
            required
            maxLength={3}
            autoComplete="off"
            aria-invalid={error !== null}
            aria-describedby="currency-error"
          />
        </label>
        <FieldError id="currency-error" message={error} />
        {notice && <p role="status" className="notice ok">{notice}</p>}
        <button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Change currency'}</button>
      </form>
    </>
  )
}
