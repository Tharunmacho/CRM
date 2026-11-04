/**
 * Pure logic for the dashboard's "Pipeline overview" cards (offer letter, visa, departure, idle).
 * The server decides who is counted and applies the role scope; this file only turns that answer into card
 * text, list tabs and export rows, so the numbers on the cards, the list under them and the export always agree.
 */
import type { DashboardOverview, OverviewItem } from '@/features/reports/report-api'

export type OverviewKey = 'offer' | 'visa' | 'departure' | 'idle'
export type OverviewView = 'current' | 'done' | 'all'

export interface OverviewCardInfo {
  key: OverviewKey
  label: string
  value: number
  detail: string
  /** True when the card should draw attention (something is stuck). */
  warn: boolean
  /** The plain-words rule behind the number, shown on hover and in the list heading. */
  hint: string
}
export interface OverviewTab { view: OverviewView; label: string; count: number }

export const plural = (count: number, one: string, many = `${one}s`) => `${count.toLocaleString('en')} ${count === 1 ? one : many}`

export function overviewCards(overview: DashboardOverview): OverviewCardInfo[] {
  const { offer, visa, departure, idle } = overview
  return [
    { key: 'offer', label: 'Overall offer letter', value: offer.current, warn: false,
      detail: offer.issued ? `${plural(offer.issued, 'offer')} issued in total` : 'No offers issued yet',
      hint: 'Candidates waiting at the Offer Letter stage right now. The line below counts every offer letter that has been completed.' },
    { key: 'visa', label: 'Overall visa', value: visa.current, warn: false,
      detail: visa.received || visa.stamped ? `${plural(visa.received, 'visa')} received · ${visa.stamped.toLocaleString('en')} stamped` : 'No visas received yet',
      hint: 'Candidates waiting at the Visa or Visa Stamping stage right now. The line below counts visas received (stage 9) and stamped (stage 10).' },
    { key: 'departure', label: 'Overall departure', value: departure.current, warn: false,
      detail: departure.departed ? `${plural(departure.departed, 'candidate')} departed in total` : 'No one has departed yet',
      hint: 'Candidates waiting at the Ticket stage, ready to travel. The line below counts candidates who have departed (deployed).' },
    { key: 'idle', label: 'Overall idle stage', value: idle.count, warn: idle.count > 0,
      detail: idle.count ? `No progress for ${overview.idleDays}+ days · longest ${plural(idle.longestDays, 'day')}` : `Nobody has waited ${overview.idleDays}+ days`,
      hint: `Active candidates with no stage completed for ${overview.idleDays} or more days (counted from when they were added to the order if no stage was ever completed). Same rule as the weekly staff report.` },
  ]
}

const TAB_LABELS: Record<Exclude<OverviewKey, 'idle'>, { current: string; done: string }> = {
  offer: { current: 'Waiting for the offer letter', done: 'Offer letter issued' },
  visa: { current: 'In visa processing', done: 'Visa received or stamped' },
  departure: { current: 'Waiting to travel', done: 'Departed' },
}

/** The rows of one card for the chosen tab. Idle has a single list. */
export function overviewRows(overview: DashboardOverview, key: OverviewKey, view: OverviewView): OverviewItem[] {
  const items = overview[key].items
  return view === 'all' || key === 'idle' ? items : items.filter(item => item.state === view)
}

export function overviewTabs(overview: DashboardOverview, key: OverviewKey): OverviewTab[] {
  if (key === 'idle') return []
  const items = overview[key].items
  const waiting = items.filter(item => item.state === 'current').length
  const done = items.length - waiting
  return [
    { view: 'current', label: TAB_LABELS[key].current, count: waiting },
    { view: 'done', label: TAB_LABELS[key].done, count: done },
    { view: 'all', label: 'All', count: items.length },
  ]
}

/** Opens on the people still waiting; falls back to history when nobody is waiting. */
export function defaultView(overview: DashboardOverview, key: OverviewKey): OverviewView {
  if (key === 'idle') return 'all'
  return overview[key].items.some(item => item.state === 'current') ? 'current' : 'all'
}

/** "Showing the first N of M" note when the server capped a long list; null when the list is complete. */
export function cappedNote(overview: DashboardOverview, key: OverviewKey): string | null {
  const list = overview[key]
  return list.itemsTotal > list.items.length ? `Showing the first ${list.items.length.toLocaleString('en')} of ${list.itemsTotal.toLocaleString('en')}. Choose a recruiter to narrow the list.` : null
}

/** How long a row has been waiting, or how long ago it was finished. */
export function waitLabel(item: Pick<OverviewItem, 'state' | 'days'>): string {
  const days = item.days <= 0 ? 'Today' : item.days === 1 ? '1 day' : `${item.days} days`
  if (item.state === 'current') return item.days <= 0 ? 'Since today' : days
  return item.days <= 0 ? 'Done today' : `${days} ago`
}

/** Rows appended to the dashboard's Export CSV. */
export function overviewCsvRows(overview: DashboardOverview): [string, number][] {
  const { offer, visa, departure, idle } = overview
  return [
    ['Offer letter: waiting now', offer.current], ['Offer letters issued in total', offer.issued],
    ['Visa: in processing now', visa.current], ['Visas received in total', visa.received], ['Visas stamped in total', visa.stamped],
    ['Departure: waiting to travel', departure.current], ['Departed in total', departure.departed],
    [`Idle stage: no progress for ${overview.idleDays}+ days`, idle.count], ['Idle stage: longest wait (days)', idle.longestDays],
  ]
}
