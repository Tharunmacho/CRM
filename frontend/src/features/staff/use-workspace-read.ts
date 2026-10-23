import { useCallback, useEffect, useRef, useState } from 'react'
import { useStore } from '@/shared/state/crm-store'

/** Hide old route counts until the current account's workspace is confirmed. */
export function useWorkspaceRead(scope = '') {
  const store = useStore()
  const [revision, setRevision] = useState(0)
  const key = `${store.me.id}:${scope}:${revision}`
  const latest = useRef(store)
  latest.current = store
  const [result, setResult] = useState<{ key: string; error: string } | null>(null)
  useEffect(() => {
    let active = true
    const load = revision === 0 ? latest.current.ensureFresh() : latest.current.refresh({ keepCurrent: true })
    void load.then(() => { if (active) setResult({ key, error: '' }) })
      .catch(error => { if (active) setResult({ key, error: error instanceof Error ? error.message : 'Current records could not be loaded.' }) })
    return () => { active = false }
  }, [key, revision])
  const refresh = useCallback(() => setRevision(value => value + 1), [])
  return { loading: result?.key !== key, error: result?.key === key ? result.error : '', refresh, revision }
}

/** Deadline labels and buckets advance even when no workspace records change. */
export function useDeadlineClock() {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const tick = () => { if (document.visibilityState !== 'hidden') setNow(Date.now()) }
    const timer = window.setInterval(tick, 30_000)
    window.addEventListener('focus', tick)
    document.addEventListener('visibilitychange', tick)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', tick)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [])
  return now
}
