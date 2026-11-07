/**
 * Search and list text for the Jobs page. Pure functions (no React) so the rules are unit tested.
 *
 * Same rules as the Clients page: the query is split on spaces and commas, every term must match (AND), and a term is a
 * case- and accent-insensitive piece of any text field. The fold and the terms are the Clients page's own.
 */
import { foldText, searchTerms } from '../clients/client-search'

export interface JobSearchRecord {
  title?: string | null
  /** The job's reference, for example ORD-2026-00002. */
  ref?: string | null
  /** The client's contact person. */
  contact?: string | null
  company?: string | null
  country?: string | null
  city?: string | null
  /** The status as the page shows it ("Open", "On hold" ...), so typing a status word finds the jobs in it. */
  statuses?: string[]
  skills?: string[]
}

function fields(record: JobSearchRecord): string[] {
  return [record.title, record.ref, record.contact, record.company, record.country, record.city, ...(record.statuses ?? []), ...(record.skills ?? [])]
    .map(foldText).filter(Boolean)
}

/** Whether the job matches the query. A blank query matches everything. */
export function matchesJob(record: JobSearchRecord, query: string | null | undefined): boolean {
  const terms = searchTerms(query)
  if (terms.length === 0) return true
  const texts = fields(record)
  return terms.every((term) => texts.some((text) => text.includes(term)))
}

const STATUS_TEXT: Record<string, string> = { open: 'Open', on_hold: 'On hold', filled: 'Filled', closed: 'Closed' }

/**
 * The words that name a job's status on screen. An open job past its deadline shows as "Overdue", and still answers to "open".
 */
export function statusWords(status: string, overdue: boolean): string[] {
  const text = STATUS_TEXT[status] ?? status
  return overdue ? ['Overdue', text] : [text]
}

/** "3 of 5 filled": the deployed people against the openings (never more than the openings). */
export function openingsText(deployed: number, openings: number): string {
  const total = Math.max(0, Math.round(openings))
  const done = Math.max(0, Math.min(total, Math.round(deployed)))
  return `${done} of ${total} filled`
}

/** Share of the openings that are deployed, 0 to 100. */
export function openingsShare(deployed: number, openings: number): number {
  if (!(openings > 0)) return 0
  return Math.max(0, Math.min(100, Math.round((deployed / openings) * 100)))
}

/**
 * Old bookmarks and links: /jobs?designation=<id> and /jobs?company=<id> (and ?view=, ?tab=) came from the screens that no longer
 * exist. They become a plain search for the job title or the company name, so the link still lands on the right jobs.
 */
export function legacyJobsQuery(
  params: { designation?: string | null; company?: string | null },
  lookup: { designationTitle: (id: string) => string | undefined; companyName: (id: string) => string | undefined },
): string {
  const title = params.designation ? lookup.designationTitle(params.designation) : undefined
  if (title) return title
  const company = params.company ? lookup.companyName(params.company) : undefined
  return company ?? ''
}

/* ---------------------------------------------------------------- shared text matching */

/** Every term of the query must be a piece of one of the texts (case and accent blind). A blank query matches. */
export function matchesTerms(texts: (string | null | undefined)[], query: string | null | undefined): boolean {
  const terms = searchTerms(query)
  if (terms.length === 0) return true
  const folded = texts.map(foldText).filter(Boolean)
  return terms.every((term) => folded.some((text) => text.includes(term)))
}

/** The items whose texts match the query, in their own order. */
export function filterByText<T>(items: readonly T[], query: string | null | undefined, textsOf: (item: T) => (string | null | undefined)[]): T[] {
  if (searchTerms(query).length === 0) return [...items]
  return items.filter((item) => matchesTerms(textsOf(item), query))
}

/* ---------------------------------------------------------------------- filters */

/** The status pills of the Jobs list, labelled as the page shows them. */
export const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: '', label: 'All' }, { value: 'open', label: 'Open' }, { value: 'filled', label: 'Filled' }, { value: 'on_hold', label: 'On hold' }, { value: 'closed', label: 'Closed' },
]

/** The status in the address (?status=), or '' (all) when it is missing or not one of the pills. */
export function parseStatusFilter(value: string | null | undefined): string {
  return STATUS_FILTERS.some((item) => item.value && item.value === value) ? (value as string) : ''
}

export interface JobFilters {
  /** '' for all, else open, filled, on_hold or closed. */
  status: string
  /** A company id (the client entry chosen in the filter), '' for all clients. */
  client: string
}

export const hasJobFilters = (filters: JobFilters) => !!filters.status || !!filters.client

/** Whether a job passes the filters. An open job past its deadline still counts as open. */
export function passesFilters(job: { status: string; companyId: string }, filters: JobFilters): boolean {
  if (filters.status && job.status !== filters.status) return false
  if (filters.client && job.companyId !== filters.client) return false
  return true
}
