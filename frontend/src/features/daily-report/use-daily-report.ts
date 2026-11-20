import { useEffect, useRef, useState } from 'react'
import { ApiError } from '@/shared/api/client'
import { liveInterval } from '@/shared/state/live'
import { isPresenceNoise } from '@/shared/state/live-events'
import { dailyReportApi } from './daily-report-api'
import { COALESCE_MS, PRESENCE_NOISE_GAP_MS, createCoalescer, FOCUS_GAP_MS, LIVE_TOPICS, POLL_DOWN_MS, POLL_SAFETY_MS } from './daily-report-live'
import { apiQuery } from './daily-report-view'
import type { DailyQuery, DailyReport } from './daily-report-types'

export interface DailyState {
  /** The newest answer, kept on screen while a new period or filter loads, and while a live refresh runs. */
  data: DailyReport | null
  /** True when `data` answers the question now being asked. */
  current: boolean
  loading: boolean
  error: { message: string; status: number | undefined } | null
  refresh: () => void
  /** When the numbers on screen were read (epoch ms), and how many times they have been read: open lists refetch on a new version. */
  updatedAt: number | null
  version: number
}

/**
 * The report's numbers, kept live. Any change the server announces on leads, candidates, applications, tasks or staff
 * (the app's one live stream) becomes a single refetch 1.5 s after the first event of a burst; if the stream is down
 * it asks every 30 s, and every 60 s as a safety net when it is up. The previous numbers stay on screen the whole
 * time (no skeleton), a hidden tab waits, and coming back to the tab reads again.
 */
export function useDailyReport(query: DailyQuery, sessionId: string, enabled = true): DailyState {
  const key = `${sessionId}|${apiQuery(query)}`
  const [snapshot, setSnapshot] = useState<{ key: string; data: DailyReport; at: number; version: number } | null>(null)
  const [failure, setFailure] = useState<{ key: string; message: string; status: number | undefined } | null>(null)
  const [reading, setReading] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const queryRef = useRef(query)
  queryRef.current = query

  useEffect(() => {
    if (!enabled) return undefined
    let active = true
    let request: AbortController | null = null
    let lastRead = 0
    let rerun = false
    // A change that arrives while a read is running asks for one more read when it ends (that read began before the change).
    const read = async (afterChange = false) => {
      if (request) { if (afterChange) rerun = true; return }
      if (document.visibilityState === 'hidden') return
      const controller = new AbortController(); request = controller
      setReading(key)
      try {
        const data = await dailyReportApi.report(queryRef.current, controller.signal)
        if (active) { setSnapshot(previous => ({ key, data, at: Date.now(), version: (previous?.version ?? 0) + 1 })); setFailure(null) }
      } catch (error) {
        if (active && !controller.signal.aborted) {
          setFailure({ key, message: error instanceof Error ? error.message : 'Unable to load this report.', status: error instanceof ApiError ? error.status : undefined })
        }
      } finally { lastRead = Date.now(); if (request === controller) request = null; if (active) setReading(null); if (rerun && active) { rerun = false; void read() } }
    }
    void read()
    const coalescer = createCoalescer(() => read(true), { delayMs: COALESCE_MS })
    // Staff heartbeats announce `attendance` + `reports` all day (see isPresenceNoise): re-read for those at most every 20 s.
    const afterNoise = createCoalescer(() => read(true), { delayMs: PRESENCE_NOISE_GAP_MS })
    const stop = liveInterval(() => void read(), POLL_DOWN_MS, LIVE_TOPICS, { safetyMs: POLL_SAFETY_MS, debounceMs: 50, onChange: (change) => { if (isPresenceNoise(change)) afterNoise.trigger(); else coalescer.trigger() } })
    const back = () => { if (document.visibilityState === 'visible' && Date.now() - lastRead > FOCUS_GAP_MS) void read() }
    document.addEventListener('visibilitychange', back)
    window.addEventListener('focus', back)
    return () => { active = false; coalescer.cancel(); afterNoise.cancel(); request?.abort(); stop(); document.removeEventListener('visibilitychange', back); window.removeEventListener('focus', back) }
  }, [key, enabled, reload])

  const mine = snapshot && snapshot.key.startsWith(`${sessionId}|`) ? snapshot : null
  return {
    data: mine?.data ?? null,
    current: mine?.key === key,
    loading: reading === key || (enabled && mine?.key !== key && failure?.key !== key),
    error: failure?.key === key ? { message: failure.message, status: failure.status } : null,
    refresh: () => setReload(n => n + 1),
    updatedAt: mine?.at ?? null,
    version: mine?.version ?? 0,
  }
}
