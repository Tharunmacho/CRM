import type { IngestedAccountSummary, IngestedItem, IngestedParams, IngestedStatus, IngestedStatusFilter, IngestedSummary } from './ingested-api'

/** Pure helpers behind the "From email" intake log (no React, no network), so they can be unit tested. */

// ------------------------------------------------------------------------------ request

/** Query string of GET /inbox/ingested. 'all' account and 'all' status are sent explicitly; the page size is kept within 10..50. */
export function ingestedQuery(params: IngestedParams): string {
  const query = new URLSearchParams()
  query.set('account', params.account || 'all')
  query.set('status', params.status || 'all')
  const text = params.q.trim()
  if (text) query.set('q', text)
  query.set('page', String(Math.max(0, Math.floor(params.page) || 0)))
  query.set('page_size', String(Math.min(50, Math.max(10, Math.floor(params.pageSize) || 25))))
  return query.toString()
}

// ------------------------------------------------------------------------------ status filters and counts

export const STATUS_FILTERS: { key: IngestedStatusFilter; label: string; hint: string }[] = [
  { key: 'all', label: 'All', hint: 'Every email from your connected mailboxes' },
  { key: 'ingested', label: 'Candidates created', hint: 'Emails that created a candidate or added documents to one' },
  { key: 'review', label: 'Needs review', hint: 'Read, but a person should check the result' },
  { key: 'failed', label: 'Failed', hint: 'The attachments could not be read' },
  { key: 'ignored', label: 'Ignored', hint: 'Nothing to make a candidate from' },
  { key: 'pending', label: 'Waiting', hint: 'Queued or being read now' },
]

/** The number on each filter chip; null while the summary has not loaded. "All" is the sum of the five outcomes. */
export function chipCounts(summary: IngestedSummary | null): Record<IngestedStatusFilter, number | null> {
  if (!summary) return { all: null, ingested: null, review: null, failed: null, ignored: null, pending: null }
  const { ingested, review, failed, ignored, pending } = summary
  return { all: ingested + review + failed + ignored + pending, ingested, review, failed, ignored, pending }
}

/** How many emails an account chip shows: its own total, or the overall total for "All accounts". */
export function accountTotal(account: Pick<IngestedAccountSummary, 'ingested' | 'review' | 'failed' | 'ignored' | 'pending'>): number {
  return account.ingested + account.review + account.failed + account.ignored + account.pending
}

/** Items needing a person: review plus failed (what a sidebar badge or a dashboard line would show). */
export const needsAttention = (summary: Pick<IngestedSummary, 'review' | 'failed'> | null) => summary ? summary.review + summary.failed : 0

export const countText = (count: number | null) => count === null ? '–' : count.toLocaleString('en-IN')

// ------------------------------------------------------------------------------ accounts

export type Health = { tone: 'green' | 'amber' | 'grey'; label: string }

/** Green dot: connected and importing. Amber: the mailbox reported a problem. Grey: auto-import is off (or the mailbox is disconnected). */
export function accountHealth(account: Pick<IngestedAccountSummary, 'connectionStatus' | 'autoImport' | 'lastError'>): Health {
  if (account.lastError || account.connectionStatus === 'error') return { tone: 'amber', label: 'Needs attention' }
  if (account.connectionStatus === 'disconnected') return { tone: 'grey', label: 'Disconnected' }
  if (!account.autoImport) return { tone: 'grey', label: 'Auto-import off' }
  return { tone: 'green', label: 'Importing' }
}

/** The chip's hover text: health, last problem and last check. */
export function accountTitle(account: IngestedAccountSummary, now = Date.now()): string {
  const parts = [account.address, accountHealth(account).label]
  if (account.lastError) parts.push(account.lastError)
  if (account.lastSyncAt) parts.push(`Last checked ${relativeTime(account.lastSyncAt, now)}`)
  return parts.join(' · ')
}

// ------------------------------------------------------------------------------ rows

export type BadgeModel = { tone: 'green' | 'amber' | 'red' | 'grey' | 'blue'; label: string; spinner: boolean }

/** The coloured result badge of a row. A candidate made from the email is green; documents added to someone already known read the same colour. */
export function statusBadge(item: Pick<IngestedItem, 'status' | 'created'>): BadgeModel {
  switch (item.status) {
    case 'ingested': return { tone: 'green', label: item.created === false ? 'Documents added' : 'Candidate created', spinner: false }
    case 'review': return { tone: 'amber', label: 'Needs review', spinner: false }
    case 'failed': return { tone: 'red', label: 'Failed', spinner: false }
    case 'ignored': return { tone: 'grey', label: 'Ignored', spinner: false }
    default: return { tone: 'blue', label: 'Waiting', spinner: true }
  }
}

const PENDING_FALLBACK: Record<IngestedStatus, string> = {
  ingested: '',
  review: 'Open the email to check the result.',
  failed: 'The attachments could not be read. Open the email to try again.',
  ignored: 'No candidate was made from this email.',
  pending: 'Reading the attachments.',
}

/** The identity part of an ingested row: "CI-103 · Anil Kumar". Falls back to the candidate ref, or to the name alone. */
export function candidateLabel(candidate: IngestedItem['candidate']): string {
  if (!candidate) return ''
  const id = (candidate.careId || candidate.ref || '').trim()
  const name = (candidate.name || '').trim()
  return [id, name].filter(Boolean).join(' · ')
}

/** The sentence a row shows next to its badge: for an ingested email the candidate; for everything else the backend note (with a plain fallback). */
export function resultText(item: IngestedItem): { headline: string; detail: string } {
  const badge = statusBadge(item)
  if (item.status === 'ingested') {
    const who = candidateLabel(item.candidate)
    const filed = item.created === false && item.documentsFiled ? `${item.documentsFiled} ${item.documentsFiled === 1 ? 'document' : 'documents'} filed` : ''
    return { headline: who ? `${badge.label} · ${who}` : badge.label, detail: item.note?.trim() || filed }
  }
  return { headline: badge.label, detail: item.note?.trim() || PENDING_FALLBACK[item.status] }
}

/** Sender name for the avatar and the title line; the address when there is no name. */
export const senderName = (from: IngestedItem['from']) => (from.name || '').trim() || from.address

/** "Anil Kumar <anil@mail.com>", or just the address. */
export const fromLine = (from: IngestedItem['from']) => from.name?.trim() ? `${from.name.trim()} <${from.address}>` : from.address

/** At most `max` attachment names, and how many more there are ("+2"). */
export function attachmentChips(item: Pick<IngestedItem, 'attachments' | 'attachmentCount'>, max = 3): { shown: string[]; extra: number } {
  const names = item.attachments.map((file) => file.name)
  const total = Math.max(item.attachmentCount || 0, names.length)
  const shown = names.slice(0, max)
  return { shown, extra: Math.max(0, total - shown.length) }
}

// ------------------------------------------------------------------------------ paging

export function pageCount(total: number, pageSize: number): number {
  return pageSize > 0 ? Math.max(1, Math.ceil(total / pageSize)) : 1
}

/** "Showing 1-25 of 312"; "No emails" when there are none. */
export function pagingText(page: number, pageSize: number, total: number, shown?: number): string {
  if (total <= 0) return 'No emails'
  const from = page * pageSize + 1
  const to = Math.min(total, page * pageSize + (shown ?? pageSize))
  if (from > total) return `Showing 0 of ${total.toLocaleString('en-IN')}`
  return `Showing ${from.toLocaleString('en-IN')}-${to.toLocaleString('en-IN')} of ${total.toLocaleString('en-IN')}`
}

export const canPrev = (page: number) => page > 0
export const canNext = (page: number, pageSize: number, total: number) => (page + 1) * pageSize < total

// ------------------------------------------------------------------------------ time

/** "just now", "5 min ago", "3 h ago", "Yesterday", "4 d ago", then a plain date. Unusable input gives an empty string. */
export function relativeTime(value: string | Date | null | undefined, now = Date.now()): string {
  if (!value) return ''
  const then = new Date(value).getTime()
  if (!Number.isFinite(then)) return ''
  const seconds = Math.round((now - then) / 1000)
  if (seconds < 45) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.floor(hours / 24)
  if (days === 1) return 'Yesterday'
  if (days < 7) return `${days} d ago`
  return new Date(then).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: new Date(then).getFullYear() === new Date(now).getFullYear() ? undefined : 'numeric' })
}

/** The exact time for a tooltip. */
export function exactTime(value: string | Date | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? date.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''
}

// ------------------------------------------------------------------------------ remembered tab

export type InboxMode = 'from' | 'mail'
export const MODE_KEY = 'crm.inbox.mode'

/** The remembered top-level tab; storage may be missing, blocked or hold junk, in which case the default "From email" applies. */
export function readMode(storage: Pick<Storage, 'getItem'> | null | undefined): InboxMode {
  try { return storage?.getItem(MODE_KEY) === 'mail' ? 'mail' : 'from' } catch { return 'from' }
}
export function writeMode(storage: Pick<Storage, 'setItem'> | null | undefined, mode: InboxMode): void {
  try { storage?.setItem(MODE_KEY, mode) } catch { /* private window or blocked storage: the choice just is not remembered */ }
}
