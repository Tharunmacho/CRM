import { useEffect, useState } from 'react'
import { agencyDay } from '@/shared/lib/task-time'

/** How often the date is looked at again while the page stays open (the check is a clock reading, nothing is requested). */
export const DAY_CHECK_MS = 15_000

/**
 * What a day or month choice becomes when the agency's day changes: a choice that was the old "today" follows the new one (so a page left
 * open overnight is on today, not on yesterday), a choice somebody picked by hand stays where it is.
 */
export function followDay(current: string, previousToday: string, today: string): string {
  return current === previousToday ? today : current
}

/**
 * Today's date in the agency timezone ("2026-11-23"), kept true while the page stays open: it is read again every 15 seconds and the moment the
 * tab or window comes back, so the first look after midnight (or after the laptop slept) is already the new day and every report that
 * is built from it asks the server for the new day. Without this the date was only worked out when something else happened to redraw the page.
 */
export function useAgencyToday(timeZone: string, now: () => Date = () => new Date()): string {
  const [today, setToday] = useState(() => agencyDay(now(), timeZone))
  useEffect(() => {
    const check = () => setToday(agencyDay(now(), timeZone))
    check()
    const timer = window.setInterval(check, DAY_CHECK_MS)
    document.addEventListener('visibilitychange', check)
    window.addEventListener('focus', check)
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', check); window.removeEventListener('focus', check) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeZone])
  return today
}
