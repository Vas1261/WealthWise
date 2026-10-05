import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import { onSessionEnded } from '../api'
import { clearCookies } from './fakeApi'

afterEach(() => {
  cleanup()
  clearCookies()
  onSessionEnded(null)
  window.location.hash = ''
})
