import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type InboxSnapshot } from '@/shared/api/client'
import { createLatestRequest } from '@/shared/api/latest-request'

const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'The inbox request failed. Please try again.'

/** Inbox owns its snapshot; reading mail does not reload the entire CRM. */
export function useInbox(identityId: string, isCurrentSession: () => boolean) {
  const [data, setData] = useState<InboxSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')
  const guard = useRef(isCurrentSession)
  guard.current = isCurrentSession
  const mounted = useRef(false)
  const generation = useRef(0)
  const busyRef = useRef(false)
  const requests = useRef(createLatestRequest<InboxSnapshot>()).current

  const refresh = useCallback(async (afterWrite = false) => {
    const ownsSession = guard.current
    const currentGeneration = generation.current
    if (!mounted.current || !ownsSession() || (busyRef.current && !afterWrite)) return
    setRefreshing(true)
    try {
      await requests[afterWrite ? 'replace' : 'run'](
        (signal) => api.inbox.get({ signal }),
        (snapshot) => {
          if (!mounted.current || generation.current !== currentGeneration || !ownsSession()) return
          setData(snapshot)
          setError('')
        },
        (failure) => {
          if (mounted.current && generation.current === currentGeneration && ownsSession()) setError(errorMessage(failure))
        },
      )
    } finally {
      if (mounted.current && generation.current === currentGeneration && ownsSession()) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [requests])

  useEffect(() => {
    generation.current += 1
    mounted.current = true
    setData(null)
    setLoading(true)
    setError('')
    setActionError('')
    setNotice('')
    const read = () => {
      if (document.visibilityState === 'visible') void refresh().catch(() => {})
    }
    read()
    const timer = window.setInterval(read, 5000)
    document.addEventListener('visibilitychange', read)
    return () => {
      generation.current += 1
      mounted.current = false
      requests.invalidate()
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', read)
    }
  }, [identityId, refresh, requests])

  const act = async (key: string, work: () => Promise<unknown>, success: string) => {
    const ownsSession = guard.current
    if (busyRef.current || !mounted.current || !ownsSession()) return false
    busyRef.current = true
    setBusy(key)
    setActionError('')
    setNotice('')
    requests.invalidate()
    try {
      await work()
      if (!mounted.current || !ownsSession()) return false
      setNotice(success)
      // A successful write and a failed follow-up read have distinct messages.
      await refresh(true).catch(() => {})
      return true
    } catch (failure) {
      if (mounted.current && ownsSession()) setActionError(errorMessage(failure))
      return false
    } finally {
      busyRef.current = false
      if (mounted.current && ownsSession()) setBusy(null)
    }
  }

  return { data, loading, refreshing, error, refresh, busy, actionError, notice, act }
}
