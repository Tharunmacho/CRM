import { useEffect, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import { liveInterval } from '@/shared/state/live'
import type { AttendanceResponse } from '../attendance/model'
import type { MonthlyAttendance } from '../attendance/policy-model'

const MONTH_RE = /^\d{4}-\d{2}$/
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/

/**
 * Runs `read` now and again every 30 seconds (and whenever the attendance or users data changes elsewhere) until the effect is cleaned up.
 * One read at a time: a change that arrives while one is running asks for another when it ends, so the answer is never older than the change.
 * `read` gets `initial` (the first read of a new choice: the caller clears what it shows) and the abort signal.
 */
function useLive(active: boolean, deps: unknown[], read: (initial: boolean, signal: AbortSignal) => Promise<void>) {
  const latest = useRef(read)
  latest.current = read
  useEffect(() => {
    if (!active) return undefined
    const controller = new AbortController()
    let running = false
    let again = false
    const load = async (initial = false) => {
      if (running) return
      running = true
      try { await latest.current(initial, controller.signal) } finally {
        running = false
        if (again && !controller.signal.aborted) { again = false; void load() }
      }
    }
    void load(true)
    const stop = liveInterval(() => { if (document.visibilityState === 'visible') void load() }, 30_000, ['attendance', 'users'], {
      onChange: () => { if (running) again = true; else void load() },
    })
    return () => { controller.abort(); stop() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, ...deps])
}

export interface MonthState { data: MonthlyAttendance | null; loading: boolean; error: string; loadedAt: string | null }

/** The month's policy record (statuses, shifts, login and logout times, minutes) for everyone, or for one person (`staff`). */
export function useMonthAttendance(month: string, staff: string, reload: number): MonthState {
  const [state, setState] = useState<MonthState>({ data: null, loading: true, error: '', loadedAt: null })
  useLive(MONTH_RE.test(month), [month, staff, reload], async (initial, signal) => {
    if (initial) setState({ data: null, loading: true, error: '', loadedAt: null })
    const [year, monthNumber] = month.split('-')
    const params = new URLSearchParams({ year, month: String(Number(monthNumber)), ...(staff ? { staffId: staff } : {}) })
    try {
      const data = await api.get<MonthlyAttendance>('/attendance/month?' + params, { signal })
      if (!signal.aborted) setState({ data, loading: false, error: '', loadedAt: new Date().toISOString() })
    } catch (cause) {
      if (!signal.aborted) setState((previous) => ({ ...previous, loading: false, error: cause instanceof Error ? cause.message : 'We could not load attendance.' }))
    }
  })
  return state
}

export interface DayState { attendance: AttendanceResponse | null; month: MonthlyAttendance | null; loading: boolean; error: string }

/**
 * One day's check-ins (who logged in and out, and for how long) with the policy record that goes with them (shift, status, late minutes).
 * `shared` is the month the page already holds (`sharedKey`, "2026-11"): when it is the day's month the policy record is taken from it
 * instead of being read a second time.
 */
export function useDayAttendance(date: string, staff: string, reload: number, sharedKey: string, shared: MonthlyAttendance | null): DayState {
  const [state, setState] = useState<DayState>({ attendance: null, month: null, loading: true, error: '' })
  const blocked = useRef('')
  const sharesMonth = sharedKey === date.slice(0, 7)
  useLive(DAY_RE.test(date), [date, staff, reload, sharesMonth], async (initial, signal) => {
    if (initial) setState({ attendance: null, month: null, loading: true, error: '' })
    const [year, monthNumber] = date.split('-')
    const key = `${year}-${monthNumber}|${staff}`
    const sessionsQuery = new URLSearchParams({ date, ...(staff ? { staffId: staff } : {}) })
    const monthQuery = new URLSearchParams({ year, month: String(Number(monthNumber)), ...(staff ? { staffId: staff } : {}) })
    try {
      const [attendance, month] = await Promise.all([
        api.get<AttendanceResponse>('/attendance?' + sessionsQuery, { signal }),
        // The policy record is a bonus: a viewer who may not see it, or a failed read, leaves the sessions part standing
        sharesMonth || blocked.current === key ? Promise.resolve(null) : api.get<MonthlyAttendance>('/attendance/month?' + monthQuery, { signal }).catch(() => {
          if (!signal.aborted) blocked.current = key
          return null
        }),
      ])
      if (!signal.aborted) setState({ attendance, month, loading: false, error: '' })
    } catch (cause) {
      if (!signal.aborted) setState((previous) => ({ ...previous, loading: false, error: cause instanceof Error ? cause.message : 'We could not load attendance.' }))
    }
  })
  // The policy record the rows use: the page's month when it is this day's month, else the one read for the day.
  return { ...state, month: sharesMonth ? shared : state.month }
}
