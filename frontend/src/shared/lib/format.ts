import { format, formatDistanceToNowStrict, isToday, isTomorrow, isYesterday } from 'date-fns'

export const SLA_HOURS = 48
const WARN_AT = 0.75 // amber from 36h

export type Urgency = 'ok' | 'due' | 'over'

/** Where an open task sits against its 48-hour clock. */
export function slaState(createdAt: string | Date, now: Date = new Date()) {
  const created = new Date(createdAt)
  const dueAt = new Date(created.getTime() + SLA_HOURS * 3600_000)
  const elapsedMs = now.getTime() - created.getTime()
  const totalMs = SLA_HOURS * 3600_000
  const ratio = elapsedMs / totalMs
  const hoursLeft = (dueAt.getTime() - now.getTime()) / 3600_000

  const urgency: Urgency = ratio >= 1 ? 'over' : ratio >= WARN_AT || isToday(dueAt) ? 'due' : 'ok'

  return {
    dueAt,
    urgency,
    /** 0–1, clamped for the bar; may exceed 1 internally. */
    fill: Math.min(ratio, 1),
    overdue: ratio >= 1,
    hoursLeft,
    hoursOverdue: Math.max(0, -hoursLeft),
    label:
      ratio >= 1
        ? `${Math.floor(-hoursLeft)}h overdue`
        : hoursLeft < 1
          ? `${Math.round(hoursLeft * 60)}m left`
          : `${Math.floor(hoursLeft)}h left`,
  }
}

export const urgencyText: Record<Urgency, string> = {
  ok: 'text-ink-500',
  due: 'text-amber-700',
  over: 'text-stamp-700',
}

export const urgencyBar: Record<Urgency, string> = {
  ok: 'bg-navy-600',
  due: 'bg-amber-600',
  over: 'bg-stamp-600',
}

export function dateLabel(value?: string | Date | null) {
  if (!value) return '—'
  return format(new Date(value), 'd MMM yyyy')
}

export function dateTimeLabel(value?: string | Date | null) {
  if (!value) return '—'
  return format(new Date(value), 'd MMM yyyy, HH:mm')
}

/** "Today, 09:14" / "Yesterday, 17:02" / "12 Oct, 08:30" — for activity streams. */
export function smartTime(value: string | Date) {
  const d = new Date(value)
  if (isToday(d)) return `Today, ${format(d, 'HH:mm')}`
  if (isYesterday(d)) return `Yesterday, ${format(d, 'HH:mm')}`
  if (isTomorrow(d)) return `Tomorrow, ${format(d, 'HH:mm')}`
  return format(d, 'd MMM, HH:mm')
}

export function ago(value: string | Date) {
  return formatDistanceToNowStrict(new Date(value), { addSuffix: true })
}

export function money(amount?: number | null, currency = 'AED') {
  if (amount == null) return '—'
  return `${currency} ${amount.toLocaleString('en-US', { minimumFractionDigits: 0 })}`
}

export function initials(name: string) {
  return name
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join('')
}

export function isSameDay(a: string | Date, b: string | Date) {
  const x = new Date(a)
  const y = new Date(b)
  return x.toDateString() === y.toDateString()
}

export function pluralise(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`
}
