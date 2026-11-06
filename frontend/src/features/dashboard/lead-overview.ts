/**
 * Pure logic for the dashboard's "Lead sheet" cards (new today, not contacted, follow-ups due, resumes received,
 * converted, and for admin and manager the unassigned leads). The server decides who is counted and applies the role
 * scope (`GET /api/dashboard` -> `leads`); this file only turns that answer into card text, the lock hint, the
 * list behind a card and the Export CSV rows, so the cards, the list and the export always agree.
 */
import type { DashboardLeadItem, DashboardLeads } from '@/features/reports/report-api'

export type LeadCardKey = 'newToday' | 'notContacted' | 'followUpsDue' | 'resumesReceived' | 'converted' | 'unassigned'
export type LeadPeriod = 'week' | 'month' | 'quarter' | 'year' | 'all'
export type LeadGateView = 'exempt' | 'locked' | 'open' | 'unknown'

/** Shown on the cards a locked staff member cannot work yet (the server refuses the work until the daily tasks are done). */
export const LOCK_HINT = "Finish today's tasks first"

export interface LeadCardInfo {
  key: LeadCardKey
  label: string
  value: number
  detail: string
  /** Draws attention (something is waiting). */
  warn: boolean
  /** True when this card is work the person cannot do yet because of the daily-task gate. */
  locked: boolean
  /** The plain-words rule behind the number, shown on hover and in the list heading. */
  hint: string
}

export const PERIOD_WORDS: Record<LeadPeriod, string> = {
  week: 'in the last 7 days', month: 'in the last 30 days', quarter: 'in the last 90 days', year: 'this year', all: 'in total',
}

export const plural = (count: number, one: string, many = `${one}s`) => `${count.toLocaleString('en')} ${count === 1 ? one : many}`

/** Whose leads the numbers are about, in words ("you", "the whole team", "Asha"). */
export function scopeWords(leads: Pick<DashboardLeads, 'scope'>, personName?: string | null): string {
  return leads.scope === 'mine' ? 'you' : leads.scope === 'person' ? personName || 'the chosen recruiter' : 'the whole team'
}

export function percentText(value: number | null | undefined): string {
  return value == null ? 'No rate yet' : `${value.toLocaleString('en', { maximumFractionDigits: 1 })}%`
}

/** Locked only for staff whose gate is known to be locked; admin and manager are never gated. */
export const isLocked = (gate: LeadGateView): boolean => gate === 'locked'

export function leadCards(leads: DashboardLeads, options: { period: LeadPeriod; gate: LeadGateView }): LeadCardInfo[] {
  const { period } = options
  const locked = isLocked(options.gate)
  const mine = leads.scope === 'mine'
  const converted = leads.converted[period]
  const cards: LeadCardInfo[] = [
    { key: 'newToday', label: 'New leads today', value: leads.newToday, warn: false, locked: false,
      detail: leads.total ? `of ${plural(leads.total, 'lead')} ${mine ? 'assigned to you' : 'on the sheet'}` : 'No leads on the sheet yet',
      hint: 'Leads that answered an ad today (their own created time). Press to see them.' },
    { key: 'notContacted', label: 'Not contacted', value: leads.notContacted, warn: leads.notContacted > 0, locked,
      detail: locked ? LOCK_HINT : leads.notContacted ? 'Assigned, never called' : 'Every assigned lead has had a call',
      hint: 'Assigned leads that are still New and have never been called.' },
    { key: 'followUpsDue', label: 'Follow-ups due', value: leads.followUpsDue, warn: leads.followUpsDue > 0, locked,
      detail: locked ? LOCK_HINT : leads.followUpsDue ? 'Due today or overdue' : 'Nothing due',
      hint: 'Open leads whose follow-up date is today or already past.' },
    { key: 'resumesReceived', label: 'Resumes received', value: leads.resumesReceived, warn: false, locked,
      detail: locked ? LOCK_HINT : leads.resumesReceived ? 'Ready to convert to candidates' : 'None waiting',
      hint: 'Leads that sent a resume and are waiting to be converted to Data Bank candidates.' },
    { key: 'converted', label: 'Converted to candidates', value: converted, warn: false, locked: false,
      detail: leads.conversionPercent == null ? `No rate yet · ${PERIOD_WORDS[period]}` : `${percentText(leads.conversionPercent)} rate · ${PERIOD_WORDS[period]}`,
      hint: `Leads turned into Data Bank candidates ${PERIOD_WORDS[period]}. The rate is every converted lead divided by the ${plural(leads.assigned, 'lead')} assigned.` },
  ]
  if (leads.unassigned != null) {
    cards.push({ key: 'unassigned', label: 'Unassigned leads', value: leads.unassigned, warn: leads.unassigned > 0, locked: false,
      detail: leads.unassigned ? 'Need an owner · assign in the Lead sheet' : 'Every open lead has an owner',
      hint: 'Open leads nobody owns yet. This is the shared pool, whichever recruiter is chosen.' })
  }
  return cards
}

/** Rows appended to the dashboard's Export CSV. */
export function leadCsvRows(leads: DashboardLeads, period: LeadPeriod): [string, number | string][] {
  const rows: [string, number | string][] = [
    ['Lead sheet: new leads today', leads.newToday], ['Lead sheet: not contacted', leads.notContacted],
    ['Lead sheet: follow-ups due', leads.followUpsDue], ['Lead sheet: resumes received', leads.resumesReceived],
    [`Lead sheet: converted to candidates (${PERIOD_WORDS[period]})`, leads.converted[period]],
    ['Lead sheet: converted in total', leads.convertedTotal], ['Lead sheet: assigned leads', leads.assigned],
    ['Lead sheet: conversion rate (%)', leads.conversionPercent == null ? '' : leads.conversionPercent],
  ]
  if (leads.unassigned != null) rows.push(['Lead sheet: unassigned leads', leads.unassigned])
  return rows
}

/** Heading and the "which date" column of the small list under a pressed card. */
export const LIST_LOOK: Record<LeadCardKey, { title: string; when: string; empty: string }> = {
  newToday: { title: 'New leads today', when: 'Arrived', empty: 'No new leads today.' },
  notContacted: { title: 'Not contacted', when: 'Assigned', empty: 'Every assigned lead has had a call.' },
  followUpsDue: { title: 'Follow-ups due', when: 'Follow-up', empty: 'No follow-ups are due.' },
  resumesReceived: { title: 'Resumes received', when: 'Resume received', empty: 'No resumes are waiting to be converted.' },
  converted: { title: 'Converted to candidates', when: 'Converted', empty: 'No leads were converted in this period.' },
  unassigned: { title: 'Unassigned leads', when: 'Arrived', empty: 'Every open lead has an owner.' },
}

/** "Showing the 8 newest of 120" under a list; null when the list is complete. */
export function listNote(total: number, shown: number): string | null {
  return total > shown ? `Showing the first ${shown.toLocaleString('en')} of ${total.toLocaleString('en')}. Open the Lead sheet for the rest.` : null
}

/** The API path of the list behind a card (staff never send a person; admin and manager may). */
export function listPath(card: LeadCardKey, period: LeadPeriod, staffId: string): string {
  const query = new URLSearchParams({ card })
  if (card === 'converted') query.set('period', period)
  if (staffId) query.set('staff_id', staffId)
  return `/dashboard/leads?${query}`
}

const DAY = 86_400_000

/** "Today", "Yesterday", "3 days ago", "in 2 days" for the date column; empty when there is no date. */
export function whenLabel(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return ''
  const at = Date.parse(iso)
  if (!Number.isFinite(at)) return ''
  const days = Math.round((startOfDay(now) - startOfDay(at)) / DAY)
  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'
  if (days === -1) return 'Tomorrow'
  return days > 0 ? `${days} days ago` : `in ${-days} days`
}

function startOfDay(ms: number): number {
  const d = new Date(ms)
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

/** One line under a lead's name: the position and the country it is for. */
export function leadSubline(item: Pick<DashboardLeadItem, 'position' | 'country'>): string {
  return [item.position, item.country].filter(Boolean).join(' · ') || 'No position yet'
}
