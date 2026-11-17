/** The dashboard's "Inbox" card, as a pure function so it can be tested. */

/** What GET /inbox?limit=1 carries for the card. The old keys describe all mail; `ingestedCandidates`, `review` and `accounts` (a count) are the newer intake numbers. */
export interface InboxCardData {
  ingestedCandidates?: number | null
  review?: number | null
  /** A number on a server that sends the intake summary; the mailbox list on an older one. */
  accounts?: unknown
  /** What the server sends: {ingestedCandidates, review, accounts} next to the old keys. */
  intake?: { ingestedCandidates?: number | null; review?: number | null; accounts?: unknown } | null
}
type OldAccount = { messageCount?: number; disconnectedAt?: string | null }

const plural = (value: number, one: string, many: string) => `${value.toLocaleString('en-IN')} ${value === 1 ? one : many}`
const count = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null

export interface InboxCard { value: number | null; note: string; detail: string }

/**
 * Big number: candidates ingested from email. Caption: "candidates from email". Second line: "N need review · K accounts" with zero
 * parts left out. A server that does not send `ingestedCandidates` yet keeps the old card (total emails, "N emails · K accounts").
 */
export function inboxCard(data: InboxCardData | null | undefined): InboxCard {
  if (!data) return { value: null, note: 'loading', detail: '' }
  const list = Array.isArray(data.accounts) ? (data.accounts as OldAccount[]).filter((account) => !account.disconnectedAt) : null
  // The server nests the intake numbers under `intake` (its `accounts` key is the mailbox list); a flat shape is still accepted.
  const intake = data.intake ?? data
  const accounts = count(intake.accounts) ?? (list ? list.length : null)
  const ingested = count(intake.ingestedCandidates)
  if (ingested !== null) {
    const review = count(intake.review) ?? 0
    const parts = [review > 0 ? `${review.toLocaleString('en-IN')} ${review === 1 ? 'needs' : 'need'} review` : '', accounts ? plural(accounts, 'account', 'accounts') : ''].filter(Boolean)
    if (ingested === 0 && accounts === 0) return { value: 0, note: 'no email account added', detail: '' }
    return { value: ingested, note: ingested === 1 ? 'candidate from email' : 'candidates from email', detail: parts.join(' · ') }
  }
  // Fallback: the old numbers.
  if (!list) return { value: null, note: 'loading emails', detail: '' }
  const emails = list.reduce((sum, account) => sum + (account.messageCount ?? 0), 0)
  return { value: emails, note: list.length === 0 ? 'no email account added' : `${emails === 1 ? 'email' : 'emails'} · ${plural(list.length, 'account', 'accounts')}`, detail: '' }
}
