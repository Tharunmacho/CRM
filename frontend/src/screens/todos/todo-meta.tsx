import { addDays, format, nextMonday, setHours, setMinutes } from 'date-fns'
import { BriefcaseBusiness, Building2, Link2, UserRound } from 'lucide-react'
import type { Priority, TodoItem } from '@/shared/model/types'
import { isSameDay } from '@/shared/lib/format'
import { CountryFlag } from '@/shared/ui/country-flag'

export type TabKey = 'today' | 'upcoming' | 'overdue' | 'done'
export type LinkKind = 'candidate' | 'client' | 'order'

export const PRIORITIES: { value: Priority; label: string }[] = [
  { value: 'high', label: 'High' },
  { value: 'normal', label: 'Normal' },
  { value: 'low', label: 'Low' },
]

export const LINK_KINDS: Record<LinkKind, { label: string; prefix: string; icon: typeof UserRound }> = {
  candidate: { label: 'Candidate', prefix: '/candidates/', icon: UserRound },
  client: { label: 'Client', prefix: '/clients/', icon: Building2 },
  order: { label: 'Order', prefix: '/orders/', icon: BriefcaseBusiness },
}

export function linkKindOf(path?: string | null): LinkKind | null {
  if (!path) return null
  return (Object.keys(LINK_KINDS) as LinkKind[]).find((k) => path.startsWith(LINK_KINDS[k].prefix)) ?? null
}

export const linkIdOf = (path: string, kind: LinkKind) => path.slice(LINK_KINDS[kind].prefix.length).split(/[/?#]/)[0]

export const toInputValue = (iso: string) => format(new Date(iso), "yyyy-MM-dd'T'HH:mm")

/* ------------------------------------------------------------------ flags */

/** The country flag from /flags, matched by name, or a globe when the country is unknown or has no flag file. */
export function Flag({ country }: { country?: string | null }) {
  return <CountryFlag country={country} width={18} height={13} globeSize={14} className="td-flag" globeClassName="td-flag-globe" title={country ?? undefined} />
}

export function LinkIcon({ kind }: { kind: LinkKind | null }) {
  const Icon = kind ? LINK_KINDS[kind].icon : Link2
  return <Icon size={14} aria-hidden="true" />
}

/* ------------------------------------------------------------------- time */

const DAY = 86_400_000

export function dayLabel(date: Date, now: number) {
  const today = new Date(now)
  if (isSameDay(date, today)) return 'Today'
  if (isSameDay(date, new Date(now + DAY))) return 'Tomorrow'
  if (isSameDay(date, new Date(now - DAY))) return 'Yesterday'
  return format(date, 'EEEE')
}

export const dayDate = (date: Date, now: number) =>
  format(date, date.getFullYear() === new Date(now).getFullYear() ? 'd MMM' : 'd MMM yyyy')

/** "N days late" for a day or more, otherwise hours or minutes. */
export function lateText(due: Date, now: number) {
  const ms = Math.max(0, now - due.getTime())
  const days = Math.floor(ms / DAY)
  if (days >= 1) return `${days} ${days === 1 ? 'day' : 'days'} late`
  const hours = Math.floor(ms / 3_600_000)
  if (hours >= 1) return `${hours} ${hours === 1 ? 'hour' : 'hours'} late`
  const minutes = Math.max(1, Math.floor(ms / 60_000))
  return `${minutes} min late`
}

/** "in 3 hours", "in 2 days": how far away a pending item is. */
export function untilText(due: Date, now: number) {
  const ms = Math.max(0, due.getTime() - now)
  const days = Math.floor(ms / DAY)
  if (days >= 1) return `in ${days} ${days === 1 ? 'day' : 'days'}`
  const hours = Math.floor(ms / 3_600_000)
  if (hours >= 1) return `in ${hours} ${hours === 1 ? 'hour' : 'hours'}`
  return `in ${Math.max(1, Math.floor(ms / 60_000))} min`
}

/** Short "Today, 15:00" style text, used for the next-up summary. */
export function dueText(item: TodoItem, now: number) {
  const due = new Date(item.dueAt)
  const day = dayLabel(due, now)
  const named = ['Today', 'Tomorrow', 'Yesterday'].includes(day) ? day : `${day.slice(0, 3)} ${dayDate(due, now)}`
  return `${named}, ${format(due, 'HH:mm')}`
}

/* ---------------------------------------------------------- due presets */

export type Preset = 'soon' | 'tomorrow' | 'week'

/** The due time a quick choice stands for: in about 2 hours (never past midnight), tomorrow 09:00, next Monday 09:00. */
export function presetDue(preset: Preset, now: number) {
  const base = new Date(now)
  if (preset === 'tomorrow') return setMinutes(setHours(addDays(base, 1), 9), 0)
  if (preset === 'week') return setMinutes(setHours(nextMonday(base), 9), 0)
  const soon = new Date(now)
  soon.setHours(soon.getHours() + 2, 0, 0, 0)
  // Keep "today" on today: late in the evening it lands at the last minutes of the day.
  if (!isSameDay(soon, base)) return new Date(base.getFullYear(), base.getMonth(), base.getDate(), 23, 59)
  return soon
}

export function presetLabel(preset: Preset, now: number) {
  const due = presetDue(preset, now)
  return `${preset === 'soon' ? 'Today' : preset === 'tomorrow' ? 'Tomorrow' : `Mon ${format(due, 'd MMM')}`}, ${format(due, 'HH:mm')}`
}

/** Groups already-sorted items by calendar day, keeping the order. */
export function groupByDay(items: TodoItem[]) {
  const groups: { key: string; date: Date; items: TodoItem[] }[] = []
  for (const item of items) {
    const date = new Date(item.dueAt)
    const key = format(date, 'yyyy-MM-dd')
    const last = groups[groups.length - 1]
    if (last && last.key === key) last.items.push(item)
    else groups.push({ key, date, items: [item] })
  }
  return groups
}
