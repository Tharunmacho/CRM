import type { Task } from '@/shared/model/types'

/** The browser's own zone: the only fallback when the server has not sent `agencyTimezone`. */
export function browserTimeZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

/** Calendar-day comparisons use the agency zone (bootstrap `agencyTimezone`) even on a travelling user's device. */
export function agencyDay(value: string | Date, timeZone?: string) {
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value))
}

export function taskClockStart(task: Task) {
  return task.dueAt ? new Date(new Date(task.dueAt).getTime() - 48 * 3600_000).toISOString() : task.slaStartedAt || task.createdAt
}
