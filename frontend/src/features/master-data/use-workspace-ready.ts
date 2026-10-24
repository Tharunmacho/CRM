import { useEffect, useRef, useState } from 'react'
import { useStore } from '@/shared/state/crm-store'

/** A snapshot this recent is shown straight away while the server confirms it. */
const WARM_MS = 90_000

/**
 * Gate mutations on a current snapshot; explicit retries always ask the server.
 * Revisiting a screen with a recent snapshot renders it immediately and re-reads in the
 * background, so navigating between Clients, Jobs and Companies is not a loading wait.
 * The server still validates every write against its own current records.
 */
export function useWorkspaceReady(key: string | null) {
  const store = useStore()
  const reads = useRef({ ensureFresh: store.ensureFresh, refresh: store.refresh, snapshotAge: store.snapshotAge })
  reads.current = { ensureFresh: store.ensureFresh, refresh: store.refresh, snapshotAge: store.snapshotAge }
  const [loadedKey, setLoadedKey] = useState<string | null>(() => (key && store.snapshotAge() < WARM_MS ? key : null))
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let live = true
    setError('')
    if (!key) { setLoadedKey(null); return }
    const warm = !attempt && reads.current.snapshotAge() < WARM_MS
    if (warm) setLoadedKey(key)
    else setLoadedKey(null)
    const request = attempt ? reads.current.refresh({ keepCurrent: true }) : reads.current.ensureFresh()
    request.then(() => { if (live) setLoadedKey(key) }).catch(cause => {
      // A background re-read that fails keeps showing the records already on screen.
      if (live && !warm) setError(cause instanceof Error ? cause.message : 'The latest records could not be loaded.')
    })
    return () => { live = false }
  }, [key, attempt])
  return { ready: !!key && loadedKey === key, error, retry: () => setAttempt(value => value + 1) }
}
