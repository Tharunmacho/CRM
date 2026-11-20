import { useCallback, useEffect, useRef, useState } from 'react'
import { createFirstValueFilter } from '@/shared/lib/first-value'
import { fetchIngested, fetchIngestedSummary, ingestedUnavailable, type IngestedPage, type IngestedParams, type IngestedSummary } from './ingested-api'

const SUMMARY_POLL_MS = 30_000
const errorText = (cause: unknown) => cause instanceof Error ? cause.message : 'We could not load the email log. Check your internet and try again.'

/**
 * Header counts and per-mailbox totals of the intake log. Read on mount and whenever `revision` changes (for example after an
 * auto-import switch), then every 30 s while the tab is visible and the log is on screen (`active`). `unavailable` is true when the
 * server has no such endpoint yet, so the screen can fall back to the plain mail view.
 */
export function useIngestedSummary(identityId: string, active: boolean, revision: number) {
  const [data, setData] = useState<IngestedSummary | null>(null)
  const [error, setError] = useState('')
  const [unavailable, setUnavailable] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const controller = useRef<AbortController | null>(null)

  const read = useCallback(async () => {
    controller.current?.abort()
    const next = new AbortController()
    controller.current = next
    try {
      const result = await fetchIngestedSummary(next.signal)
      if (next.signal.aborted) return
      setData(result); setError(''); setUnavailable(false)
    } catch (cause) {
      if (next.signal.aborted) return
      if (ingestedUnavailable(cause)) setUnavailable(true)
      else setError(errorText(cause))
    } finally {
      if (!next.signal.aborted) setLoaded(true)
    }
  }, [])

  useEffect(() => { setData(null); setLoaded(false); setUnavailable(false) }, [identityId])
  useEffect(() => { void read() }, [read, identityId, revision])
  useEffect(() => {
    if (!active) return undefined
    const tick = () => { if (document.visibilityState === 'visible') void read() }
    const timer = window.setInterval(tick, SUMMARY_POLL_MS)
    document.addEventListener('visibilitychange', tick)
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', tick) }
  }, [active, read])
  useEffect(() => () => controller.current?.abort(), [])

  return { data, error, unavailable, loaded, refresh: read }
}

/**
 * One page of the intake log. A change of filter, page or search aborts the read still in flight; the previous page stays on screen
 * (`data` is not cleared) until the new one arrives, so the list never blanks. `stamp` re-reads the page when the summary moved.
 */
export function useIngestedList(params: IngestedParams, enabled: boolean, identityId: string, revision: number, stamp: string) {
  const [data, setData] = useState<IngestedPage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const { account, status, q, page, pageSize } = params
  // The summary answers while the first page is still on its way; that first stamp is not news and must not repeat the page read.
  const stamps = useRef(createFirstValueFilter()).current
  const settledStamp = stamps(stamp, identityId)
  useEffect(() => { setData(null) }, [identityId])
  useEffect(() => {
    if (!enabled) { setLoading(false); return undefined }
    const controller = new AbortController()
    setLoading(true)
    fetchIngested({ account, status, q, page, pageSize }, controller.signal)
      .then((result) => { if (!controller.signal.aborted) { setData(result); setError('') } })
      .catch((cause) => { if (!controller.signal.aborted) setError(errorText(cause)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [enabled, identityId, account, status, q, page, pageSize, revision, settledStamp, reload])
  const refresh = useCallback(() => setReload((value) => value + 1), [])
  return { data, loading, error, refresh }
}
