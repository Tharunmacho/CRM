import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import { liveInterval } from '@/shared/state/live'
import { clockSkew, nextCrossing, type DailyWorkResponse } from './daily-work-model'

interface Options {
  /** Supervisors only: one staff id, or "all". Staff always get their own list. */
  staff?: string
  /** Skip the request (for example for a role that is not given a quota). */
  enabled?: boolean
  /** How long live changes are held back so a burst of task updates causes one request. */
  debounceMs?: number
}

/**
 * Today's daily list. Loads on mount and whenever the server announces a task change, plus a safety poll. `skew`
 * is the server clock minus the browser's, so hourly countdowns do not depend on the PC being set correctly.
 */
export function useDailyWork({ staff, enabled = true, debounceMs = 1500 }: Options = {}) {
  const [data, setData] = useState<DailyWorkResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(enabled)
  const [skew, setSkew] = useState(0)
  const generation = useRef(0)
  const staffKey = staff ?? ''

  const load = useCallback(async (signal?: AbortSignal) => {
    const mine = ++generation.current
    try {
      const query = staffKey ? `?staff=${encodeURIComponent(staffKey)}` : ''
      const result = await api.get<DailyWorkResponse>(`/daily-work${query}`, { signal })
      if (signal?.aborted || mine !== generation.current) return
      setData(result)
      setSkew(clockSkew(result.serverTime, Date.now()))
      setError('')
    } catch (e) {
      if (signal?.aborted || mine !== generation.current) return
      setError(e instanceof Error ? e.message : 'Daily tasks could not be loaded.')
    } finally {
      if (!signal?.aborted && mine === generation.current) setLoading(false)
    }
  }, [staffKey])

  useEffect(() => {
    if (!enabled) { setLoading(false); return }
    const controller = new AbortController()
    setLoading(true)
    void load(controller.signal)
    const stop = liveInterval(() => { if (document.visibilityState === 'visible') void load(controller.signal) }, 60_000, ['tasks', 'users', 'candidates', 'applications'], { debounceMs })
    return () => { controller.abort(); stop() }
  }, [enabled, load, debounceMs])

  // The moment an hourly deadline passes, read again: the server records it as overdue and the screen turns red
  // from real data, not only from the local countdown.
  useEffect(() => {
    if (!data) return
    const wait = nextCrossing(data.items, Date.now(), skew)
    if (wait === null) return
    const timer = window.setTimeout(() => { if (document.visibilityState === 'visible') void load() }, Math.min(wait + 1500, 30 * 60_000))
    return () => window.clearTimeout(timer)
  }, [data, skew, load])

  const refresh = useCallback(() => load(), [load])
  return { data, error, loading, skew, refresh }
}

/** A clock that ticks every `intervalMs` while the tab is visible, for live countdowns. */
export function useTicker(intervalMs = 1000, enabled = true) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!enabled) return
    const tick = () => { if (document.visibilityState !== 'hidden') setNow(Date.now()) }
    tick()
    const timer = window.setInterval(tick, intervalMs)
    document.addEventListener('visibilitychange', tick)
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', tick) }
  }, [intervalMs, enabled])
  return now
}
