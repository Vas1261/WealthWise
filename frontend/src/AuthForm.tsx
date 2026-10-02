import { useState, type FormEvent } from 'react'
import { ApiError, auth, type User } from './api'

type Mode = 'login' | 'register'

type Props = {
  mode: Mode
  onModeChange: (mode: Mode) => void
  onLoggedIn: (user: User) => void
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return 'Could not reach the server. Please try again.'
}

export function AuthForm({ mode, onModeChange, onLoggedIn }: Props) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const isLogin = mode === 'login'

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      if (isLogin) {
        onLoggedIn(await auth.login(email, password))
      } else {
        await auth.register(email, password)
        setPassword('')
        setNotice('Account created. Please log in.')
        onModeChange('login')
      }
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <h2>{isLogin ? 'Log in' : 'Create an account'}</h2>
      <form onSubmit={handleSubmit}>
        <label>
          Email
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete={isLogin ? 'current-password' : 'new-password'}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {!isLogin && <p className="hint">8–72 bytes (most characters are 1 byte; accented letters and emoji use more).</p>}
        {notice && <p role="status" className="notice">{notice}</p>}
        {error && <p role="alert" className="error">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? 'Please wait…' : isLogin ? 'Log in' : 'Register'}
        </button>
      </form>
      <p>
        {isLogin ? 'No account yet? ' : 'Already registered? '}
        <button type="button" className="link" onClick={() => { setError(null); onModeChange(isLogin ? 'register' : 'login') }}>
          {isLogin ? 'Register' : 'Log in'}
        </button>
      </p>
    </section>
  )
}
