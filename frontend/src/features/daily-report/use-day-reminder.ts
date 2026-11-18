import { useCallback, useEffect, useState } from 'react'
import { useStore } from '@/shared/state/crm-store'
import { liveInterval } from '@/shared/state/live'
import { dailyReportApi } from './daily-report-api'
import { AGENCY_TIMEZONE, dayActivity, dayInZone, REMINDER_DISMISS_KEY, reminderVisible } from './daily-report-view'
import type { MyDay } from './daily-report-types'

const CACHE_MS = 60_000
/** The last answer of GET /mine for today, kept 60 s so the Tasks screen and the Dashboard do not each ask. */
let cache: { key: string; at: number; value: MyDay } | null = null

function readDismissed(): string | null {
  try { return window.localStorage.getItem(REMINDER_DISMISS_KEY) } catch { return null }
}
function writeDismissed(day: string) {
  try { window.localStorage.setItem(REMINDER_DISMISS_KEY, day) } catch { /* blocked storage: the banner just comes back on the next visit */ }
}

/**
 * Whether to remind a recruiter that today's report is not submitted: from 17:00 agency time, only if they have done anything
 * today, and until they dismiss it for the day. Reads their own day from /reports/daily-work/mine (cached for 60 s), reads it again
 * when anyone submits (the live `daily_report` topic), and checks the clock once a minute. A server without the report shows nothing.
 */
export function useDayReminder() {
  const store = useStore()
  const recruiter = !store.can('all_reports')
  const timeZone = store.agencyTimezone || AGENCY_TIMEZONE
  const [now, setNow] = useState(() => Date.now())
  const [mine, setMine] = useState<MyDay | null>(null)
  const [dismissed, setDismissed] = useState<string | null>(() => readDismissed())
  const today = dayInZone(now, timeZone)
  const key = `${store.me.id}|${today}`

  useEffect(() => {
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') setNow(Date.now()) }, 60_000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!recruiter) return undefined
    let active = true
    const controller = new AbortController()
    const load = async (force: boolean) => {
      if (!force && cache && cache.key === key && Date.now() - cache.at < CACHE_MS) { setMine(cache.value); return }
      try {
        const value = await dailyReportApi.mine(today, controller.signal)
        cache = { key, at: Date.now(), value }
        if (active) setMine(value)
      } catch { /* no report on this server, or offline: no reminder */ }
    }
    void load(false)
    const stop = liveInterval(() => load(true), CACHE_MS, ['daily_report'], { safetyMs: CACHE_MS, debounceMs: 800 })
    return () => { active = false; controller.abort(); stop() }
  }, [recruiter, key, today])

  const show = !!mine && reminderVisible({
    recruiter, now, timeZone, today, dismissedDay: dismissed,
    submitted: mine.submitted ?? !!mine.submittedAt, activity: dayActivity(mine.figures),
  })
  const dismiss = useCallback(() => { writeDismissed(today); setDismissed(today) }, [today])
  return { show, dismiss }
}
