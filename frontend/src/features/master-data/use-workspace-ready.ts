import { useEffect, useRef, useState } from 'react'
import { useStore } from '@/shared/state/crm-store'

/** Gate mutations on a current snapshot; explicit retries always ask the server. */
export function useWorkspaceReady(key: string | null) {
  const store = useStore()
  const reads = useRef({ ensureFresh: store.ensureFresh, refresh: store.refresh })
  reads.current = { ensureFresh: store.ensureFresh, refresh: store.refresh }
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let live = true
    setLoadedKey(null); setError('')
    if (!key) return
    const request = attempt ? reads.current.refresh({ keepCurrent: true }) : reads.current.ensureFresh()
    request.then(() => { if (live) setLoadedKey(key) }).catch(cause => {
      if (live) setError(cause instanceof Error ? cause.message : 'The latest records could not be loaded.')
    })
    return () => { live = false }
  }, [key, attempt])
  return { ready: !!key && loadedKey === key, error, retry: () => setAttempt(value => value + 1) }
}
