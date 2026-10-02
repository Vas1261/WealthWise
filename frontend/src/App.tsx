import { useEffect, useState } from 'react'

type Health = { status: string; database: string }

type HealthState =
  | { kind: 'loading' }
  | { kind: 'ok'; health: Health }
  | { kind: 'error'; message: string }

function App() {
  const [state, setState] = useState<HealthState>({ kind: 'loading' })

  useEffect(() => {
    fetch('/health')
      .then(async (response) => {
        const body = (await response.json()) as Health
        setState(response.ok ? { kind: 'ok', health: body } : { kind: 'error', message: `HTTP ${response.status}: ${body.status}` })
      })
      .catch((error: unknown) => setState({ kind: 'error', message: `Backend unreachable: ${String(error)}` }))
  }, [])

  return (
    <main>
      <h1>WealthWise</h1>
      <section aria-live="polite">
        <h2>Backend health</h2>
        {state.kind === 'loading' && <p>Checking…</p>}
        {state.kind === 'ok' && (
          <p data-testid="health">
            API: <strong>{state.health.status}</strong> · Database: <strong>{state.health.database}</strong>
          </p>
        )}
        {state.kind === 'error' && <p role="alert">{state.message}</p>}
      </section>
    </main>
  )
}

export default App
