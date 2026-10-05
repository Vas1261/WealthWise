import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { CSRF, fakeApi } from '../test/fakeApi'
import { USER } from '../test/fixtures'
import { SettingsPage } from './Settings'

async function change(code: string) {
  await userEvent.type(screen.getByLabelText(/new reporting currency/i), code)
  await userEvent.click(screen.getByRole('button', { name: 'Change currency' }))
}

describe('settings', () => {
  it('shows the account and current reporting currency', () => {
    fakeApi({})
    render(<SettingsPage user={USER} onUserChange={() => {}} />)
    expect(screen.getByText('alice@example.com')).toBeTruthy()
    expect(screen.getByText('INR')).toBeTruthy()
  })

  it('PATCHes the code as typed with CSRF, then re-reads the profile', async () => {
    const onUserChange = vi.fn()
    const api = fakeApi({
      'PATCH /api/auth/me': { body: { ...USER, reporting_currency: 'EUR' } },
      'GET /api/auth/me': { body: { ...USER, reporting_currency: 'EUR' } },
    })
    render(<SettingsPage user={USER} onUserChange={onUserChange} />)
    await change('EUR')
    expect((await screen.findByRole('status')).textContent).toMatch(/reporting currency is now EUR/i)
    const [patch, me] = api.apiCalls()
    expect([patch.method, patch.body, patch.headers['X-CSRF-Token']]).toEqual(['PATCH', { reporting_currency: 'EUR' }, CSRF])
    expect([me.method, me.url]).toEqual(['GET', '/api/auth/me'])
    expect(onUserChange).toHaveBeenCalledWith({ ...USER, reporting_currency: 'EUR' })
  })

  it.each([
    [422, 'invalid_currency', 'Use a three-letter upper-case ISO currency code, e.g. INR'],
    [422, 'unsupported_currency', 'This currency is not supported for conversion'],
    [503, 'currency_validation_unavailable', 'Currencies cannot be verified right now; please try again later'],
  ])('shows the %s %s message at the field and keeps the old currency', async (status, code, detail) => {
    const onUserChange = vi.fn()
    fakeApi({ 'PATCH /api/auth/me': { status, body: { detail, code } } })
    render(<SettingsPage user={USER} onUserChange={onUserChange} />)
    await change('eur')
    const input = screen.getByLabelText(/new reporting currency/i)
    expect((await screen.findByRole('alert')).textContent).toBe(detail)
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect((input as HTMLInputElement).value).toBe('eur')
    expect(onUserChange).not.toHaveBeenCalled()
  })
})
