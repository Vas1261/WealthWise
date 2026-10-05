// A tiny hash router (decision U1): six fixed pages, back/forward and deep links work,
// and the server never needs rewrite rules. Only page names ever appear in the URL.
import { useSyncExternalStore } from 'react'

export const ROUTES = ['dashboard', 'portfolio', 'imports', 'finance', 'budgets', 'settings'] as const
export type Route = (typeof ROUTES)[number]

export const ROUTE_TITLES: Record<Route, string> = {
  dashboard: 'Dashboard',
  portfolio: 'Portfolio',
  imports: 'Imports',
  finance: 'Finance',
  budgets: 'Budgets',
  settings: 'Settings',
}

/** "#/portfolio" -> "portfolio"; anything unknown (or empty) is the dashboard. */
export function parseRoute(hash: string): Route {
  const name = hash.replace(/^#\/?/, '')
  return (ROUTES as readonly string[]).includes(name) ? (name as Route) : 'dashboard'
}

export function hrefFor(route: Route): string {
  return route === 'dashboard' ? '#/' : `#/${route}`
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener('hashchange', onChange)
  return () => window.removeEventListener('hashchange', onChange)
}

export function useRoute(): Route {
  return useSyncExternalStore(subscribe, () => parseRoute(window.location.hash))
}
