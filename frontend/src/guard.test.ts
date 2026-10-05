// Source guards for rules a reviewer could miss: no float conversion of financial values, no browser
// storage, no raw HTML, no console logging of data. Test files are excluded.
import { describe, expect, it } from 'vitest'
import { hrefFor, parseRoute } from './route'

const sources = import.meta.glob(['./**/*.{ts,tsx}', '!./**/*.test.{ts,tsx}', '!./test/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

function offenders(pattern: RegExp): string[] {
  return Object.entries(sources)
    .filter(([, text]) => pattern.test(text.replace(/\/\/.*$/gm, ''))) // ignore comments
    .map(([file]) => file)
}

describe('source guards', () => {
  it('scans the application sources', () => {
    expect(Object.keys(sources)).toEqual(expect.arrayContaining(['./api.ts', './format.ts', './pages/Dashboard.tsx']))
  })

  it('never converts values with Number() or parseFloat()/parseInt()', () => {
    expect(offenders(/\b(Number|parseFloat|parseInt)\s*\(/)).toEqual([])
    expect(offenders(/\bNumber\.parse/)).toEqual([])
  })

  it('never uses browser storage', () => {
    expect(offenders(/\b(localStorage|sessionStorage|indexedDB)\b/)).toEqual([])
  })

  it('never injects raw HTML or logs to the console', () => {
    expect(offenders(/dangerouslySetInnerHTML|innerHTML/)).toEqual([])
    expect(offenders(/\bconsole\./)).toEqual([])
  })
})

describe('hash routes', () => {
  it('maps hashes to the six pages; anything else is the dashboard', () => {
    expect(['#/', '', '#', '#/portfolio', '#/imports', '#/finance', '#/budgets', '#/settings', '#/nope', '#portfolio']
      .map(parseRoute)).toEqual([
      'dashboard', 'dashboard', 'dashboard', 'portfolio', 'imports', 'finance', 'budgets', 'settings', 'dashboard',
      'portfolio',
    ])
    expect([hrefFor('dashboard'), hrefFor('budgets')]).toEqual(['#/', '#/budgets'])
  })
})
