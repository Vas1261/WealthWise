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

// jsdom has no canvas: report "no 2D context" quietly, so charts skip drawing and their tables still render.
HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext
