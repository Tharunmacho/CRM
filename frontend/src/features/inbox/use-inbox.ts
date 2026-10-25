import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type InboxSnapshot } from '@/shared/api/client'
import { createLatestRequest } from '@/shared/api/latest-request'
import { liveInterval } from '@/shared/state/live'

const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'The inbox request failed. Please try again.'

/** The last snapshot per user and query, so coming back to the Inbox paints at once while it refreshes. */
const lastSnapshot = new Map<string, InboxSnapshot>()

/** Inbox owns its snapshot; reading mail does not reload the entire CRM. */
export function useInbox(identityId: string, isCurrentSession: () => boolean, search = '') {
  const [data, setData] = useState<InboxSnapshot | null>(() => lastSnapshot.get(`${identityId}?${search}`) ?? null)
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
  const snapshotRef = useRef(data)
  snapshotRef.current = data
  const lastReadAt = useRef(0)
  const requests = useRef(createLatestRequest<InboxSnapshot>()).current

  const refresh = useCallback(async (afterWrite = false) => {
    const ownsSession = guard.current
    const currentGeneration = generation.current
    if (!mounted.current || !ownsSession() || (busyRef.current && !afterWrite)) return
    setRefreshing(true)
    lastReadAt.current = Date.now()
    try {
      await requests[afterWrite ? 'replace' : 'run'](
        (signal) => search ? api.get<InboxSnapshot>(`/inbox?${search}`, { signal }) : api.inbox.get({ signal }),
        (snapshot) => {
          if (!mounted.current || generation.current !== currentGeneration || !ownsSession()) return
          if (!search.includes('message_id=')) lastSnapshot.set(`${identityId}?${search}`, snapshot)
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
  }, [requests, search, identityId])
  // A mailbox/filter can change while a write is in flight. Its completion
  // must refresh the scope now on screen, not the query captured at submission.
  const latestRefresh = useRef(refresh)
  latestRefresh.current = refresh

  // A different user or mailbox clears the list at once; a new search, filter or page keeps the old rows until the new ones arrive.
  const mailboxOf = (query: string) => new URLSearchParams(query).get('account_ids') ?? ''
  const shownFor = useRef(`${identityId}|${mailboxOf(search)}`)
  useEffect(() => {
    generation.current += 1
    mounted.current = true
    const scope = `${identityId}|${mailboxOf(search)}`
    if (shownFor.current !== scope) { shownFor.current = scope; setData(null) }
    setLoading(true)
    setError('')
    setActionError('')
    setNotice('')
    const read = () => {
      if (document.visibilityState === 'visible') void refresh().catch(() => {})
    }
    read()
    // A mail/intake change re-reads at once (replacing a read that began earlier); otherwise the old rhythm applies,
    // but only while the live stream is down.
    const stopLive = liveInterval(() => {
      const processing = snapshotRef.current?.jobs.some(job => ['pending', 'processing', 'retry'].includes(job.status))
      if (Date.now() - lastReadAt.current >= (processing ? 5000 : 15_000)) read()
    }, 5000, ['mail', 'whatsapp', 'documents'], {
      onChange: () => { if (document.visibilityState === 'visible') void refresh(true).catch(() => {}) },
    })
    document.addEventListener('visibilitychange', read)
    return () => {
      generation.current += 1
      mounted.current = false
      requests.invalidate()
      stopLive()
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
      await latestRefresh.current(true).catch(() => {})
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
