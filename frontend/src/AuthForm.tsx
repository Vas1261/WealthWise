import { useState, type FormEvent } from 'react'
import { ApiError, auth, type User } from './api'
import { FieldError } from './components'

type Mode = 'login' | 'register'

type Props = {
  mode: Mode
  onModeChange: (mode: Mode) => void
  onLoggedIn: (user: User) => void
}

/** True when the API's schema check rejected the email field (a list-shaped 422 that names it). */
function emailRejected(error: ApiError): boolean {
  const detail = (error.body as { detail?: unknown } | null)?.detail
  return error.status === 422 && Array.isArray(detail) &&
    detail.some((item: { loc?: unknown }) => Array.isArray(item?.loc) && item.loc.includes('email'))
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    // The API is stricter than the browser's own email check (it needs a dot after the @, as in
    // name@example.com) and answers with a schema error that carries no message of its own.
    if (emailRejected(error)) return 'Enter a valid email address, for example name@example.com.'
    return error.message
  }
  return 'Could not reach the server. Please try again.'
}

// name@domain.tld: no spaces, commas or semicolons, one @, and an ending of 2+ characters after the last dot
// (so name@gmail.c is refused). The backend still has the final say.
const EMAIL_SHAPE = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;.]{2,}$/
const MAX_PASSWORD_BYTES = 72 // bcrypt's limit; the backend refuses longer passwords rather than truncating them

export function AuthForm({ mode, onModeChange, onLoggedIn }: Props) {
  const [email, setEmail] = useState('')
  const [emailTouched, setEmailTouched] = useState(false)
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const isLogin = mode === 'login'
  const emailValid = EMAIL_SHAPE.test(email.trim())
  const passwordBytes = new TextEncoder().encode(password).length
  const passwordValid = isLogin ? password !== '' : passwordBytes >= 8 && passwordBytes <= MAX_PASSWORD_BYTES
  const emailError = emailTouched && email !== '' && !emailValid ? 'Enter a valid email address, for example name@example.com.' : null
  const passwordError = !isLogin && passwordBytes > MAX_PASSWORD_BYTES ? 'Password is too long.' : null

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
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            onBlur={() => setEmailTouched(true)} aria-invalid={emailError !== null} aria-describedby="auth-email-error" />
        </label>
        <FieldError id="auth-email-error" message={emailError} />
        <label>
          Password
          <input
            type="password"
            autoComplete={isLogin ? 'current-password' : 'new-password'}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={passwordError !== null}
            aria-describedby="auth-password-error"
          />
        </label>
        <FieldError id="auth-password-error" message={passwordError} />
        {!isLogin && !passwordError && <p className="hint">Use at least 8 characters.</p>}
        {notice && <p role="status" className="notice">{notice}</p>}
        {error && <p role="alert" className="error">{error}</p>}
        <button type="submit" disabled={busy || !emailValid || !passwordValid}>
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
