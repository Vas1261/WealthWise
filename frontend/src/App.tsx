import { useEffect, useRef, useState } from 'react'
import { ApiError, auth, onSessionEnded, type User } from './api'
import { AuthForm } from './AuthForm'
import { DashboardPage } from './pages/Dashboard'
import { BudgetsPage } from './pages/Budgets'
import { FinancePage } from './pages/Finance'
import { ImportsPage } from './pages/Imports'
import { PortfolioPage } from './pages/Portfolio'
import { SettingsPage } from './pages/Settings'
import { ROUTES, ROUTE_TITLES, hrefFor, useRoute, type Route } from './route'

type Health = { status: string; database: string }

function HealthStatus() {
  const [text, setText] = useState('Checking…')

  useEffect(() => {
    fetch('/health', { cache: 'no-store' })
      .then(async (response) => {
        const body = (await response.json()) as Health
        setText(`API: ${body.status} · Database: ${body.database}`)
      })
      .catch(() => setText('Backend unreachable'))
  }, [])

  return <p className="health" data-testid="health">{text}</p>
}

function Page({ route, user, onUserChange }: { route: Route; user: User; onUserChange: (user: User) => void }) {
  switch (route) {
    case 'portfolio':
      return <PortfolioPage />
    case 'imports':
      return <ImportsPage />
    case 'finance':
      return <FinancePage user={user} />
    case 'budgets':
      return <BudgetsPage />
    case 'settings':
      return <SettingsPage user={user} onUserChange={onUserChange} />
    default:
      return <DashboardPage />
  }
}

function SignedIn({ user, onUserChange, onLogout }: { user: User; onUserChange: (user: User) => void; onLogout: () => void }) {
  const route = useRoute()
  const [logoutError, setLogoutError] = useState<string | null>(null)

  useEffect(() => {
    document.title = `${ROUTE_TITLES[route]} · WealthWise`
  }, [route])

  async function handleLogout() {
    setLogoutError(null)
    try {
      await auth.logout()
      onLogout()
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) onLogout() // session already gone
      else setLogoutError('Logout failed. Please try again.')
    }
  }

  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="app-header">
        <p className="brand">WealthWise</p>
        <nav aria-label="Main">
          <ul>
            {ROUTES.map((r) => (
              <li key={r}>
                <a href={hrefFor(r)} aria-current={r === route ? 'page' : undefined}>{ROUTE_TITLES[r]}</a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="account">
          <span className="muted">{user.email} · {user.reporting_currency}</span>
          <button type="button" onClick={handleLogout}>Log out</button>
          {logoutError && <p role="alert" className="error">{logoutError}</p>}
        </div>
      </header>
      {/* key: every navigation mounts the page afresh, so it always loads current data */}
      <main id="main" tabIndex={-1}>
        <Page key={route} route={route} user={user} onUserChange={onUserChange} />
      </main>
    </>
  )
}

function App() {
  // undefined = still checking the session; null = signed out.
  const [user, setUser] = useState<User | null | undefined>(undefined)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [sessionNotice, setSessionNotice] = useState<string | null>(null)
  const signedIn = useRef(false)

  useEffect(() => {
    signedIn.current = Boolean(user)
  }, [user])

  useEffect(() => {
    auth
      .me()
      .then(setUser)
      .catch(() => setUser(null))
  }, [])

  useEffect(() => {
    // A request still unauthenticated after a refresh: drop everything in memory and ask to log in again.
    onSessionEnded(() => {
      if (!signedIn.current) return
      setUser(null)
      setSessionNotice('Your session ended. Please log in again.')
    })
    return () => onSessionEnded(null)
  }, [])

  function signIn(next: User) {
    setSessionNotice(null)
    setUser(next)
  }

  if (user) {
    return (
      <>
        <SignedIn user={user} onUserChange={setUser} onLogout={() => setUser(null)} />
        <footer><HealthStatus /></footer>
      </>
    )
  }

  return (
    <main className="signed-out">
      <h1>WealthWise</h1>
      {user === undefined && <p role="status">Checking session…</p>}
      {sessionNotice && <p role="status" className="notice warn">{sessionNotice}</p>}
      {user === null && <AuthForm mode={mode} onModeChange={setMode} onLoggedIn={signIn} />}
      <HealthStatus />
    </main>
  )
}

export default App
