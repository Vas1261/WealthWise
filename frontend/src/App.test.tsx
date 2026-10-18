import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from './App'
import { fakeApi } from './test/fakeApi'
import { EMPTY_PORTFOLIO, USER, netWorthData, summaryData } from './test/fixtures'

const SIGNED_IN = {
  'GET /api/auth/me': { body: USER },
  'GET /api/finance/net-worth': { body: netWorthData() },
  'GET /api/portfolio': { body: EMPTY_PORTFOLIO },
  'GET /api/finance/budgets/summary': { body: summaryData() },
  'GET /api/finance/monthly-totals': { body: [] },
  'GET /api/imports': { body: [] },
  'GET /api/finance/entries': { body: [] },
  'GET /api/finance/budgets': { body: [] },
}

function go(hash: string) {
  act(() => {
    window.location.hash = hash
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

describe('app shell', () => {
  it('shows the login form when there is no session (and no session-ended notice)', async () => {
    fakeApi({ 'GET /api/auth/me': { status: 401, body: { detail: 'Not authenticated' } }, 'POST /api/auth/refresh': { status: 401 } })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Log in' })).toBeTruthy()
    expect(screen.queryByText(/your session ended/i)).toBeNull()
  })

  it('shows six pages in labelled navigation with the current one marked', async () => {
    fakeApi(SIGNED_IN)
    render(<App />)
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    const links = within(nav).getAllByRole('link')
    expect(links.map((l) => [l.textContent, l.getAttribute('href')])).toEqual([
      ['Dashboard', '#/'], ['Portfolio', '#/portfolio'], ['Imports', '#/imports'], ['Finance', '#/finance'],
      ['Budgets', '#/budgets'], ['Settings', '#/settings'],
    ])
    expect(within(nav).getByRole('link', { name: 'Dashboard' }).getAttribute('aria-current')).toBe('page')
    expect(screen.getByRole('link', { name: 'Skip to content' }).getAttribute('href')).toBe('#main')
    expect(screen.getByRole('main').id).toBe('main')
  })

  it('routes by hash, moves focus to the page heading and updates the title', async () => {
    fakeApi(SIGNED_IN)
    render(<App />)
    await screen.findByRole('heading', { level: 1, name: 'Dashboard' })
    for (const [hash, title] of [['#/imports', 'Imports'], ['#/finance', 'Finance'], ['#/budgets', 'Budgets'],
      ['#/settings', 'Settings'], ['#/portfolio', 'Portfolio'], ['#/unknown', 'Dashboard']]) {
      go(hash)
      const heading = await screen.findByRole('heading', { level: 1, name: title })
      expect(document.activeElement).toBe(heading)
      expect(document.title).toBe(`${title} · WealthWise`)
      const current = within(screen.getByRole('navigation')).getByRole('link', { current: 'page' })
      expect(current.textContent).toBe(title)
    }
  })

  it('drops all data and asks to log in again when the session ends', async () => {
    fakeApi({
      ...SIGNED_IN,
      'GET /api/imports': { status: 401, body: { detail: 'Not authenticated' } },
      'POST /api/auth/refresh': { status: 401, body: { detail: 'Session expired or invalid' } },
    })
    render(<App />)
    await screen.findByRole('heading', { level: 1, name: 'Dashboard' })
    go('#/imports')
    expect(await screen.findByText('Your session ended. Please log in again.')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Log in' })).toBeTruthy()
    expect(screen.queryByRole('navigation')).toBeNull()
    expect(screen.queryByText(USER.email)).toBeNull()
  })

  it('logs out with a CSRF-protected POST and returns to the login form', async () => {
    const api = fakeApi({ ...SIGNED_IN, 'POST /api/auth/logout': { status: 204 } })
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Log out' }))
    expect(await screen.findByRole('heading', { name: 'Log in' })).toBeTruthy()
    const logout = api.apiCalls().find((c) => c.url === '/api/auth/logout')!
    expect(logout.headers['X-CSRF-Token']).toBeTruthy()
  })

  it('says so when the database is behind the migrations instead of reporting it as ok', async () => {
    fakeApi({ ...SIGNED_IN, 'GET /health': { status: 503, body: { status: 'degraded', database: 'migration_pending' } } })
    render(<App />)
    const warning = await screen.findByRole('alert')
    expect(warning.textContent).toMatch(/database is behind this version of the app/i)
    expect(warning.textContent).toMatch('alembic upgrade head')
    expect(screen.queryByText(/Database: ok/)).toBeNull()
  })

  it('shows no server status line while the server is healthy, and a warning when it cannot be reached', async () => {
    fakeApi(SIGNED_IN)
    const { unmount } = render(<App />)
    await screen.findByRole('heading', { level: 1, name: 'Dashboard' })
    expect(screen.queryByTestId('health')).toBeNull()
    expect(screen.queryByText(/API: ok/)).toBeNull()
    unmount()

    fakeApi({ ...SIGNED_IN, 'GET /health': new TypeError('Failed to fetch') })
    render(<App />)
    expect((await screen.findByTestId('health')).textContent).toMatch(/server cannot be reached/i)
  })

  it('checks the email format in the form and sends nothing until it is valid', async () => {
    const api = fakeApi({ 'GET /api/auth/me': { status: 401 }, 'POST /api/auth/refresh': { status: 401 } })
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Register' }))
    expect(screen.getByText('Use at least 8 characters.')).toBeTruthy()
    for (const typo of ['someone@gmail.com,', 'someone@localhost', 'some one@gmail.com', 'someone@gmail.c']) {
      await userEvent.clear(screen.getByLabelText('Email'))
      await userEvent.type(screen.getByLabelText('Email'), typo)
      await userEvent.type(screen.getByLabelText('Password'), 'a-synthetic-password')
      await userEvent.tab() // leave the field
      expect(screen.getByText('Enter a valid email address, for example name@example.com.')).toBeTruthy()
      expect((screen.getByRole('button', { name: 'Register' }) as HTMLButtonElement).disabled).toBe(true)
      await userEvent.clear(screen.getByLabelText('Password'))
    }
    expect(api.apiCalls().filter((c) => c.method === 'POST' && c.url === '/api/auth/register')).toEqual([])
  })

  it('says a password over the 72-byte limit is too long and keeps Register disabled', async () => {
    fakeApi({ 'GET /api/auth/me': { status: 401 }, 'POST /api/auth/refresh': { status: 401 } })
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Register' }))
    await userEvent.type(screen.getByLabelText('Email'), 'someone@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'x'.repeat(73))
    expect(screen.getByText('Password is too long.')).toBeTruthy()
    expect(screen.queryByText('Use at least 8 characters.')).toBeNull()
    expect((screen.getByRole('button', { name: 'Register' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('still explains an address that only the API rejects instead of "Request failed (HTTP 422)"', async () => {
    fakeApi({
      'GET /api/auth/me': { status: 401 },
      'POST /api/auth/refresh': { status: 401 },
      // The form's shape check passes, but the API's stricter validator refuses consecutive dots.
      'POST /api/auth/register': { status: 422, body: { detail: [{
        type: 'value_error', loc: ['body', 'email'], input: 'some..one@example.com',
        msg: 'value is not a valid email address: An email address cannot have two periods in a row.',
      }] } },
    })
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Register' }))
    await userEvent.type(screen.getByLabelText('Email'), 'some..one@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'a-synthetic-password')
    await userEvent.click(screen.getByRole('button', { name: 'Register' }))
    expect((await screen.findByRole('alert')).textContent).toBe('Enter a valid email address, for example name@example.com.')
  })

  it('logs in and opens the dashboard', async () => {
    fakeApi({
      ...SIGNED_IN,
      'GET /api/auth/me': { status: 401 },
      'POST /api/auth/refresh': { status: 401 },
      'POST /api/auth/login': { body: USER },
    })
    render(<App />)
    await userEvent.type(await screen.findByLabelText('Email'), USER.email)
    await userEvent.type(screen.getByLabelText('Password'), 'a-synthetic-password')
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard' })).toBeTruthy()
  })
})
