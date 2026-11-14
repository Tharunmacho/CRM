import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import { liveInterval } from '@/shared/state/live'
import type { DailyWorkResponse, FollowUpResponse } from './daily-work-model'

interface Options {
  /** Supervisors only: one recruiter id, or "all". Recruiters always get their own list. */
  staff?: string
  /** Skip the request (for example for a role that has no list). */
  enabled?: boolean
  /** How long live changes are held back so a burst of task updates causes one request. */
  debounceMs?: number
}

/**
 * The Daily work list. Loads on mount and whenever the server announces a task, candidate or application change, plus a
 * safety poll. There are no deadlines, so there is nothing to re-read at a given moment.
 */
const CACHE = { ttlMs: 3_000, tags: ['tasks', 'users', 'candidates', 'applications'] } as const

/** Today's candidates (`/daily-work`). */
export const useDailyWork = (options: Options = {}) => useRecruiterList<DailyWorkResponse>('/daily-work', options)

/** Follow-up (`/follow-up`): the same scoping, live topics and caching. A stage finished or an application closed moves a candidate between the two lists. */
export const useFollowUp = (options: Options = {}) => useRecruiterList<FollowUpResponse>('/follow-up', options)

function useRecruiterList<T>(path: string, { staff, enabled = true, debounceMs = 1500 }: Options) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(enabled)
  const generation = useRef(0)
  const staffKey = staff ?? ''

  const load = useCallback(async (signal?: AbortSignal, fresh = false) => {
    const mine = ++generation.current
    try {
      const query = staffKey ? `?staff=${encodeURIComponent(staffKey)}` : ''
      const result = await api.get<T>(`${path}${query}`, { signal, cache: CACHE, fresh })
      if (signal?.aborted || mine !== generation.current) return
      setData(result)
      setError('')
    } catch (e) {
      if (signal?.aborted || mine !== generation.current) return
      setError(e instanceof Error ? e.message : 'Your candidates could not be loaded.')
    } finally {
      if (!signal?.aborted && mine === generation.current) setLoading(false)
    }
  }, [staffKey, path])

  useEffect(() => {
    if (!enabled) { setLoading(false); return }
    const controller = new AbortController()
    setLoading(true)
    void load(controller.signal)
    const stop = liveInterval(() => { if (document.visibilityState === 'visible') void load(controller.signal) }, 60_000, ['tasks', 'users', 'candidates', 'applications'], { debounceMs })
    return () => { controller.abort(); stop() }
  }, [enabled, load, debounceMs])

  const refresh = useCallback(() => load(undefined, true), [load])
  return { data, error, loading, refresh }
}

/** A clock that ticks every `intervalMs` while the tab is visible (the Lead sheet's banner counts down with it). */
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
