import { format } from 'date-fns'
import { BriefcaseBusiness, Building2, Globe2, Link2, UserRound } from 'lucide-react'
import type { Priority, TodoItem } from '@/shared/model/types'
import { isSameDay } from '@/shared/lib/format'

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

const FLAGS: [string, string[]][] = [
  ['ae', ['united arab emirates', 'uae', 'emirates', 'dubai', 'abu dhabi', 'sharjah']],
  ['sa', ['saudi']],
  ['qa', ['qatar']],
  ['kw', ['kuwait']],
  ['om', ['oman']],
  ['bh', ['bahrain']],
  ['de', ['germany']],
  ['gb', ['united kingdom', 'uk', 'britain', 'england']],
  ['in', ['india']],
  ['sg', ['singapore']],
  ['us', ['united states', 'usa', 'america']],
]

export const countryCode = (country?: string | null) => {
  const key = (country ?? '').trim().toLowerCase()
  if (!key) return null
  return FLAGS.find(([, names]) => names.some((name) => key === name || key.includes(name)))?.[0] ?? null
}

/** The country flag from /flags, matched by name, or a globe when the country is unknown or not shipped. */
export function Flag({ country }: { country?: string | null }) {
  const code = countryCode(country)
  return code
    ? <img src={`/flags/${code}.svg`} alt="" width={18} height={13} className="td-flag" loading="lazy" title={country ?? undefined} />
    : <Globe2 size={14} aria-hidden="true" className="td-flag-globe" />
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
