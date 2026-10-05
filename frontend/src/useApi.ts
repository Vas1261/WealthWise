import { useCallback, useEffect, useEffectEvent, useState } from 'react'

export type ApiState<T> = {
  data: T | undefined
  error: unknown
  loading: boolean
  reload: () => void
  /** Replace the data with a fresher copy obtained elsewhere (e.g. a refresh response). */
  setData: (data: T) => void
}

type Settled<T> = { request: string; data: T | undefined; error: unknown }

/**
 * Loads data when the component mounts and whenever `key` changes (or `reload` is called). While
 * reloading, the previous data stays visible. Results from a superseded load are ignored.
 */
export function useApi<T>(load: () => Promise<T>, key = ''): ApiState<T> {
  const [version, setVersion] = useState(0)
  const [settled, setSettled] = useState<Settled<T>>({ request: '', data: undefined, error: null })
  const request = `${version}:${key}`
  const reload = useCallback(() => setVersion((v) => v + 1), [])
  const setData = useCallback((data: T) => setSettled((s) => ({ ...s, data, error: null })), [])
  const run = useEffectEvent(() => load())

  useEffect(() => {
    let active = true
    run().then(
      (data) => active && setSettled({ request, data, error: null }),
      (error: unknown) => active && setSettled((s) => ({ request, data: s.data, error })),
    )
    return () => {
      active = false
    }
  }, [request])

  const loading = settled.request !== request
  return { data: settled.data, error: loading ? null : settled.error, loading, reload, setData }
}
