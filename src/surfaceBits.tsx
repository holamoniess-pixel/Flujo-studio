import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { AlertTriangle, Boxes, Loader2, RefreshCw } from 'lucide-react'
import { backendFetch } from './backendClient'
import { requestPhaseLabel, toUiFailure } from './frontendState'
import type { FrontendRoute } from './domain'

export type SurfaceProps = { go: (route: FrontendRoute) => void; announce: (message: string) => void; inspect: (kind: string, title: string, detail?: string) => void }

export function useBackendList<T>(path: string | null): { items: T[]; loading: boolean; error: unknown; reload: () => void } {
  const [items, setItems] = useState<T[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const load = useCallback(() => {
    if (!path) { setLoading(false); return }
    setLoading(true)
    setError(null)
    backendFetch<T[]>(path)
      .then(value => setItems(Array.isArray(value) ? value : []))
      .catch(cause => setError(cause))
      .finally(() => setLoading(false))
  }, [path])
  useEffect(() => { load() }, [load])
  return { items, loading, error, reload: load }
}

export function useBackendObject<T>(path: string | null): { data: T | null; loading: boolean; error: unknown; reload: () => void } {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const load = useCallback(() => {
    if (!path) { setLoading(false); return }
    setLoading(true)
    setError(null)
    backendFetch<T>(path)
      .then(value => setData(value))
      .catch(cause => setError(cause))
      .finally(() => setLoading(false))
  }, [path])
  useEffect(() => { load() }, [load])
  return { data, loading, error, reload: load }
}

export function DataStates({ loading, error, empty, retry, loadingLabel, errorMessage, emptyTitle, emptyDetail, children }: {
  loading: boolean
  error: unknown
  empty: boolean
  retry: () => void
  loadingLabel?: string
  errorMessage?: string
  emptyTitle?: string
  emptyDetail?: string
  children: ReactNode
}) {
  if (loading) {
    return <div className="empty-state"><Loader2 className="spin" size={22} /><p>{loadingLabel || 'Loading persisted local state…'}</p></div>
  }
  if (error) {
    const failure = toUiFailure(error, errorMessage || 'The local backend is unavailable.')
    return <div className="empty-state" role="alert" aria-live="assertive"><AlertTriangle size={22} /><strong>{requestPhaseLabel(failure.phase)}</strong><p>{failure.message}</p><small>{failure.code}</small>{failure.retryable && <button className="secondary-button compact-button" onClick={retry}><RefreshCw size={14} /> Retry</button>}</div>
  }
  if (empty) {
    return <div className="empty-state"><Boxes size={22} /><p><strong>{emptyTitle || 'Nothing recorded yet.'}</strong></p><p>{emptyDetail || 'This surface shows persisted local records once they exist.'}</p></div>
  }
  return <>{children}</>
}

export function formatTime(value?: string | null): string {
  return value ? value.slice(11, 19) : '--'
}

export function formatDate(value?: string | null): string {
  return value ? value.slice(0, 10) : '--'
}
