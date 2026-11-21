/**
 * Client tracker, management reading: one line per client (openings, in process, placed, fill rate, last activity, status) with its orders and
 * the candidates in each. The server (GET /api/reports/client-tracker/clients, backend app/services/client_tracker_clients.py) does the counting;
 * this file is the screen's side: the types, the status tabs, the search, the wording of dates and the address of the Excel file. No React.
 */
import { STAGE_NAMES } from './tracker-summary'

export type ClientStatus = 'active' | 'stalled' | 'idle'
export type StatusTab = 'all' | 'active' | 'stalled'

export interface OrderCandidate { applicationId: string; candidateId: string; name: string; stageNo: number; stage: string; travelled: boolean }
export interface ClientOrder {
  id: string; ref: string; position: string; country: string
  /** open, on_hold, filled or closed. */
  status: string
  isOpen: boolean; openings: number; placed: number; inProcess: number
  /** Openings still to fill: nothing for an order that is filled or closed. */
  open: number
  deadline: string | null
  candidates: OrderCandidate[]
}
export interface ClientLine {
  id: string; name: string; country: string; orders: ClientOrder[]
  orderCount: number; openOrders: number; openings: number; inProcess: number; placed: number
  fillRate: number | null
  lastActivity: string | null
  active: boolean; stalled: boolean; status: ClientStatus
}
export interface ClientsSummary {
  clients: number; activeClients: number; stalledClients: number; idleClients: number
  openOrders: number; openings: number; inProcess: number; placed: number; fillRate: number | null; stalledDays: number
}
export interface ClientsResult { summary: ClientsSummary; clients: ClientLine[]; generatedAt: string }

export const STATUS_TABS: readonly { key: StatusTab; label: string }[] = [{ key: 'all', label: 'All' }, { key: 'active', label: 'Active' }, { key: 'stalled', label: 'Stalled' }]
export const STATUS_WORDS: Record<ClientStatus, string> = { active: 'Active', stalled: 'Stalled', idle: 'Idle' }
export const STATUS_TONE: Record<ClientStatus, 'green' | 'amber' | 'neutral'> = { active: 'green', stalled: 'amber', idle: 'neutral' }
export const ORDER_STATUS_WORDS: Record<string, string> = { open: 'Open', on_hold: 'On hold', filled: 'Filled', closed: 'Closed' }

/** The clients a status tab and a search keep: Active is everything with something open (stalled ones included), Stalled is the quiet part of it. */
export function filterClients(clients: readonly ClientLine[], tab: StatusTab, query: string): ClientLine[] {
  const text = query.replace(/\s+/g, ' ').trim().toLowerCase()
  return clients.filter((client) => {
    if (tab === 'active' && !client.active) return false
    if (tab === 'stalled' && !client.stalled) return false
    if (!text) return true
    return client.name.toLowerCase().includes(text) || client.country.toLowerCase().includes(text)
      || client.orders.some((order) => `${order.ref} ${order.position}`.toLowerCase().includes(text))
  })
}

/** How many clients each tab holds (searching does not change them: they are the tabs' own totals). */
export function tabCounts(clients: readonly ClientLine[]): Record<StatusTab, number> {
  return { all: clients.length, active: clients.filter((client) => client.active).length, stalled: clients.filter((client) => client.stalled).length }
}

/** 33.333 -> "33.3%", 100 -> "100%", null -> "–" (no order asked for anybody yet). */
export function rateText(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '–'
  return `${Math.round(value * 10) / 10}%`
}

const DAY_MS = 86_400_000
const dayNumber = (day: string) => Math.floor(Date.parse(day + 'T00:00:00Z') / DAY_MS)

/** Whole days between two YYYY-MM-DD agency days (never negative). */
export function daysBetween(from: string, to: string): number {
  return Math.max(0, dayNumber(to) - dayNumber(from))
}

/** "Today", "Yesterday", "5 days ago", "3 weeks ago", "4 months ago"; the dash when nothing has ever happened. `lastDay` and `today` are agency days. */
export function activityText(lastDay: string | null | undefined, today: string): string {
  if (!lastDay) return '–'
  const days = daysBetween(lastDay, today)
  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'
  if (days < 14) return `${days} days ago`
  if (days < 60) return `${Math.floor(days / 7)} weeks ago`
  return `${Math.floor(days / 30)} months ago`
}

/** The candidates of an order grouped by stage, stage 1 first and the travelled ones last: { label, names }. */
export function stageGroups(candidates: readonly OrderCandidate[]): { key: string; label: string; names: string[] }[] {
  const groups = new Map<string, { key: string; no: number; label: string; names: string[] }>()
  for (const candidate of candidates) {
    const key = candidate.travelled ? 'travelled' : String(candidate.stageNo)
    const group = groups.get(key) ?? { key, no: candidate.travelled ? 99 : candidate.stageNo, label: candidate.travelled ? 'Travelled' : STAGE_NAMES[candidate.stageNo] ?? `Stage ${candidate.stageNo}`, names: [] }
    group.names.push(candidate.name)
    groups.set(key, group)
  }
  return [...groups.values()].sort((a, b) => a.no - b.no).map(({ key, label, names }) => ({ key, label, names }))
}

/** The Excel file of the clients the screen shows: same status tab, same search. API path without /api. */
export function clientsPath(tab: StatusTab, query: string, format?: 'xlsx'): string {
  const params = new URLSearchParams()
  if (format) params.set('format', format)
  if (tab !== 'all') params.set('status', tab)
  const text = query.replace(/\s+/g, ' ').trim()
  if (text) params.set('q', text)
  const search = params.toString()
  return `/reports/client-tracker/clients${search ? `?${search}` : ''}`
}
export const clientsFileName = (day: string) => `client-tracker-clients-${day}.xlsx`
