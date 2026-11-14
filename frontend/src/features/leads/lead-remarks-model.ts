/**
 * View-model of the "Assigned to" panel: what the recruiter wrote about a lead (remark cards) and a plain-English
 * timeline of everything done on it, grouped by day. Pure functions only (no React, no clock of their own), so every
 * rule is testable. The server stores a logged call as kind "call" with the summary "<Outcome words>: <remark>" and
 * meta { outcome, from, to, callNo }; saved notes are kind "note" (the summary is the notes text, cut at 300 characters).
 */
import { CALL_OUTCOMES, leadStatus, type LeadTone } from './lead-model'

/** The part of a lead activity this view reads (structurally a `LeadActivity`). */
export interface ActivityLike {
  id: string
  kind: string
  summary?: string | null
  userId?: string | null
  userName?: string | null
  meta?: Record<string, unknown> | null
  createdAt: string
}

export interface RemarkCard {
  id: string
  /** "remark" = a logged call with text, "note" = an edit of the notes, "pinned" = the notes saved on the lead now. */
  kind: 'remark' | 'note' | 'pinned'
  who: string
  at: string | null
  outcome: { value: string; label: string; tone: LeadTone } | null
  text: string
}

export type TimelineIcon = 'call' | 'note' | 'status' | 'assign' | 'import' | 'convert' | 'unconvert' | 'resume' | 'edit' | 'whatsapp' | 'followup' | 'other'

export interface TimelineEvent {
  id: string
  icon: TimelineIcon
  tone: LeadTone
  title: string
  detail: string
  who: string
  at: string
}

export interface TimelineDay { key: string; label: string; events: TimelineEvent[] }

const OUTCOME_TONE: Record<string, LeadTone> = { interested: 'ok', answered: 'info', no_answer: 'warn', wrong_number: 'bad', not_interested: 'quiet' }
const OUTCOME_LINE: Record<string, string> = {
  answered: 'Spoke to the person', interested: 'Called: they are interested', no_answer: 'Called: no answer',
  wrong_number: 'Called: wrong number', not_interested: 'Called: not interested',
}
const NOTES_SUMMARY_LIMIT = 300

const humanize = (value: string) => { const text = value.replace(/[_-]+/g, ' ').trim(); return text.charAt(0).toUpperCase() + text.slice(1) }
const text = (value: unknown) => (typeof value === 'string' ? value.trim() : '')
const time = (value: string) => { const t = new Date(value).getTime(); return Number.isNaN(t) ? 0 : t }
const newestFirst = (a: ActivityLike, b: ActivityLike) => time(b.createdAt) - time(a.createdAt)

/** Outcome chip of a call outcome value; an unknown outcome reads as plain words. */
export function outcomeChip(value?: string | null): { value: string; label: string; tone: LeadTone } | null {
  const key = (value ?? '').trim()
  if (!key) return null
  return { value: key, label: CALL_OUTCOMES.find((item) => item.value === key)?.label ?? humanize(key), tone: OUTCOME_TONE[key] ?? 'quiet' }
}

/** Splits a stored call summary into its outcome and the remark text the staff member typed. */
export function parseCall(activity: ActivityLike): { outcome: string; remark: string } {
  const summary = text(activity.summary)
  const outcome = text(activity.meta?.outcome)
  if (outcome) {
    const prefix = `${outcome.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())}`
    if (summary === prefix) return { outcome, remark: '' }
    if (summary.startsWith(`${prefix}:`)) return { outcome, remark: summary.slice(prefix.length + 1).trim() }
    return { outcome, remark: summary }
  }
  const match = /^([A-Za-z][A-Za-z ]{1,30}?)(?::\s*([\s\S]*))?$/.exec(summary)
  const known = match ? CALL_OUTCOMES.find((item) => item.value === match[1].trim().toLowerCase().replace(/ /g, '_')) : undefined
  return known && match ? { outcome: known.value, remark: (match[2] ?? '').trim() } : { outcome: '', remark: summary }
}

/**
 * The remarks, newest first: the notes saved on the lead now (pinned on top), then every logged call that has remark
 * text, then earlier edits of the notes. A call with no text stays in the timeline only.
 */
export function remarkCards(activities: ActivityLike[], notes?: string | null): RemarkCard[] {
  const sorted = [...activities].sort(newestFirst)
  const saved = text(notes)
  const latestNote = sorted.find((item) => item.kind === 'note' && text(item.summary) !== 'Notes cleared')
  const cards: RemarkCard[] = []
  if (saved) cards.push({ id: 'pinned-notes', kind: 'pinned', who: latestNote?.userName ?? '', at: latestNote?.createdAt ?? null, outcome: null, text: saved })
  for (const item of sorted) {
    if (item.kind === 'call') {
      const { outcome, remark } = parseCall(item)
      if (remark) cards.push({ id: item.id, kind: 'remark', who: item.userName ?? '', at: item.createdAt, outcome: outcomeChip(outcome), text: remark })
    } else if (item.kind === 'note') {
      const body = text(item.summary)
      if (!body || body === 'Notes cleared') continue
      if (saved && saved.slice(0, NOTES_SUMMARY_LIMIT) === body) continue // the pinned card already shows this text
      cards.push({ id: item.id, kind: 'note', who: item.userName ?? '', at: item.createdAt, outcome: null, text: body })
    }
  }
  return cards
}

const statusName = (value: unknown) => leadStatus(typeof value === 'string' ? value : '').label

/** One activity as a plain-English event (the remark text itself lives on the cards, not here). */
export function timelineEvent(item: ActivityLike): TimelineEvent {
  const base = { id: item.id, who: item.userName ?? '', at: item.createdAt }
  const summary = text(item.summary)
  switch (item.kind) {
    case 'call': {
      const { outcome, remark } = parseCall(item)
      const from = text(item.meta?.from)
      const to = text(item.meta?.to)
      const moved = from && to && from !== to ? `Status ${statusName(from)} → ${statusName(to)}` : ''
      const detail = [remark ? 'Remark added' : '', moved].filter(Boolean).join(' · ')
      return { ...base, icon: 'call', tone: OUTCOME_TONE[outcome] ?? 'info', title: OUTCOME_LINE[outcome] ?? 'Added remarks', detail }
    }
    case 'note':
      return { ...base, icon: 'note', tone: 'info', title: summary === 'Notes cleared' ? 'Cleared the notes' : 'Updated the notes', detail: '' }
    case 'status': {
      const from = text(item.meta?.from)
      const to = text(item.meta?.to)
      return { ...base, icon: 'status', tone: 'info', title: from && to ? `Changed status from ${statusName(from)} to ${statusName(to)}` : summary || 'Changed the status', detail: '' }
    }
    case 'assign': return { ...base, icon: 'assign', tone: 'info', title: summary || 'Lead assigned', detail: '' }
    case 'import': return { ...base, icon: 'import', tone: 'quiet', title: 'Lead imported', detail: summary && !/^imported/i.test(summary) ? summary : '' }
    case 'convert': return { ...base, icon: 'convert', tone: 'ok', title: summary || 'Converted to a candidate', detail: '' }
    case 'unconvert': return { ...base, icon: 'unconvert', tone: 'warn', title: 'Moved back to Lead', detail: summary && !/^moved back/i.test(summary) ? summary : '' }
    case 'resume': return { ...base, icon: 'resume', tone: 'ok', title: 'Uploaded a resume', detail: summary.replace(/^Resume uploaded:\s*/i, '') }
    case 'edit': return { ...base, icon: 'edit', tone: 'quiet', title: summary || 'Edited the details', detail: '' }
    case 'whatsapp': return { ...base, icon: 'whatsapp', tone: 'ok', title: 'Opened WhatsApp', detail: summary }
    case 'followup': return { ...base, icon: 'followup', tone: 'warn', title: summary || 'Set a follow-up', detail: '' }
    default: return { ...base, icon: 'other', tone: 'quiet', title: summary || humanize(item.kind || 'activity'), detail: '' }
  }
}

const pad = (value: number) => String(value).padStart(2, '0')
const dayKey = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const startOfDay = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
const DAY_MS = 86_400_000

/** "Today", "Yesterday", otherwise "Monday, 4 Nov 2026" (the year is dropped for the current year). */
export function dayLabel(value: string, now: Date): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Earlier'
  const gap = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS)
  if (gap === 0) return 'Today'
  if (gap === 1) return 'Yesterday'
  return `${WEEKDAYS[date.getDay()]}, ${date.getDate()} ${MONTHS[date.getMonth()]}${date.getFullYear() === now.getFullYear() ? '' : ` ${date.getFullYear()}`}`
}

/** Everything done on the lead, newest first, grouped by calendar day (the local day of the viewer). */
export function timelineDays(activities: ActivityLike[], now: Date): TimelineDay[] {
  const days: TimelineDay[] = []
  for (const item of [...activities].sort(newestFirst)) {
    const date = new Date(item.createdAt)
    const key = Number.isNaN(date.getTime()) ? 'unknown' : dayKey(date)
    let day = days[days.length - 1]
    if (!day || day.key !== key) { day = { key, label: dayLabel(item.createdAt, now), events: [] }; days.push(day) }
    day.events.push(timelineEvent(item))
  }
  return days
}

/** "just now", "5 min ago", "3 h ago", "Yesterday", "4 days ago"; an older date reads as "4 Nov 2026". */
export function relativeTime(value: string | null | undefined, now: Date): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const seconds = Math.round((now.getTime() - date.getTime()) / 1000)
  if (seconds < 45) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const gap = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS)
  if (gap === 0) return `${Math.round(minutes / 60)} h ago`
  if (gap === 1) return 'Yesterday'
  if (gap < 7) return `${gap} days ago`
  return `${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`
}

/** The strip at the top: calls made and remarks written (from the lead's own counter when the server sent one). */
export function remarkStats(activities: ActivityLike[], cards: RemarkCard[], callCount?: number | null): { calls: number; remarks: number } {
  return { calls: callCount ?? activities.filter((item) => item.kind === 'call').length, remarks: cards.length }
}
