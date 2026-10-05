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
