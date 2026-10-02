import { useEffect, useState } from 'react'
import { ApiError, auth, type User } from './api'
import { AuthForm } from './AuthForm'

type Health = { status: string; database: string }

function HealthStatus() {
  const [text, setText] = useState('Checking…')

  useEffect(() => {
    fetch('/health')
      .then(async (response) => {
        const body = (await response.json()) as Health
        setText(`API: ${body.status} · Database: ${body.database}`)
      })
      .catch(() => setText('Backend unreachable'))
  }, [])

  return <p className="health" data-testid="health">{text}</p>
}

function App() {
  // undefined = still checking the session; null = signed out.
  const [user, setUser] = useState<User | null | undefined>(undefined)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [logoutError, setLogoutError] = useState<string | null>(null)

  useEffect(() => {
    auth
      .me()
      .then(setUser)
      .catch(() => setUser(null))
  }, [])

  async function handleLogout() {
    setLogoutError(null)
    try {
      await auth.logout()
      setUser(null)
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setUser(null) // session already gone
      else setLogoutError('Logout failed. Please try again.')
    }
  }

  return (
    <main>
      <h1>WealthWise</h1>
      {user === undefined && <p>Checking session…</p>}
      {user === null && <AuthForm mode={mode} onModeChange={setMode} onLoggedIn={setUser} />}
      {user && (
        <section className="card" data-testid="home">
          <h2>Welcome</h2>
          <p>
            Signed in as <strong>{user.email}</strong> · Reporting currency: {user.reporting_currency}
          </p>
          {logoutError && <p role="alert" className="error">{logoutError}</p>}
          <button type="button" onClick={handleLogout}>Log out</button>
        </section>
      )}
      <HealthStatus />
    </main>
  )
}

export default App
