/**
 * Data Bank search and position grouping, as plain functions.
 *
 * The server does the matching (backend/app/services/candidate_search.py): one search box over every
 * detail, ignoring case, where comma-separated terms must ALL match the same candidate. These helpers
 * keep the browser consistent with it: the same term splitting (so "ravi," and "ravi" are one request),
 * and highlighting of exactly the terms that were searched.
 */

/** Same as the server: split on commas, collapse spaces, drop empty and repeated (any case) terms. */
export function searchTerms(query: string): string[] {
  const terms: string[] = []
  for (const part of query.split(',')) {
    const term = part.trim().replace(/\s+/g, ' ')
    if (term && !terms.some(known => known.toLowerCase() === term.toLowerCase())) terms.push(term)
  }
  return terms
}

/** What is sent to the server: typing a trailing comma or space does not make a new request. */
export const normalizeSearch = (query: string): string => searchTerms(query).join(',')

/** Automatic intake keeps a pointer to the message it came from in the same field a person types an agent into. */
const MACHINE_DETAIL = /^(email_message|whatsapp_message|person_review|lead_sheet):/i

/** The agent / referrer recorded for a candidate ("More about the source"); empty when there is none. */
export function agentName(candidate: { sourceDetail?: string | null; source?: string }): string {
  const detail = candidate.sourceDetail?.trim() ?? ''
  const importMarker = candidate.source !== 'referral' && detail.replace(/\s+/g, ' ').toLowerCase() === 'bulk import'
  return MACHINE_DETAIL.test(detail) || importMarker ? '' : detail
}

/** One position (an order) holding resumes, as returned by GET /candidates/position-groups. */
export interface PositionGroup {
  orderId: string | null
  ref: string | null
  title: string | null
  company: string | null
  client: string | null
  country: string | null
  city: string | null
  orderStatus: string | null
  resumes: number
}

export interface PositionGroups {
  groups: PositionGroup[]
  /** Positions that hold at least one matching resume. */
  positions: number
  /** Matching resumes, with or without a position. */
  resumes: number
}

export const NO_POSITION_LABEL = 'No job yet'

/** The key a position is requested by (`position_id`); candidates without an order use "none". */
export const groupKey = (group: Pick<PositionGroup, 'orderId'>): string => group.orderId ?? 'none'

export const groupTitle = (group: Pick<PositionGroup, 'title' | 'orderId'>): string =>
  group.orderId ? group.title?.trim() || 'Job' : NO_POSITION_LABEL

/** Company, client and country on one line; the client is left out when it only repeats the company. */
export function groupMeta(group: Pick<PositionGroup, 'company' | 'client' | 'country' | 'orderId'>): string {
  if (!group.orderId) return 'Not matched to an order'
  const company = group.company?.trim() ?? ''
  const client = group.client?.trim() ?? ''
  return [company, client && client.toLowerCase() !== company.toLowerCase() ? client : '', group.country?.trim() ?? ''].filter(Boolean).join(' · ')
}

/** "3 resumes". */
export const resumeCount = (count: number): string => `${count} ${count === 1 ? 'resume' : 'resumes'}`

export const positionGroupsPath = (filterQuery: string): string => `/candidates/position-groups?${filterQuery}`

/** One page of one position's resumes, under the same search and filters as the groups. */
export const groupResumesPath = (filterQuery: string, key: string, page: number, pageSize: number): string =>
  `/candidates?${filterQuery}&position_id=${encodeURIComponent(key)}&page=${page}&page_size=${pageSize}`

export interface TextPart { text: string; hit: boolean }

/** Splits `text` so every occurrence of a searched term (any case) can be marked. */
export function highlightParts(text: string, terms: string[]): TextPart[] {
  const usable = terms.filter(Boolean)
  if (!text || usable.length === 0) return [{ text, hit: false }]
  const escaped = usable.map(term => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).sort((a, b) => b.length - a.length)
  const pattern = new RegExp(`(${escaped.join('|')})`, 'gi')
  const parts: TextPart[] = []
  let last = 0
  for (const match of text.matchAll(pattern)) {
    const at = match.index ?? 0
    if (at > last) parts.push({ text: text.slice(last, at), hit: false })
    parts.push({ text: match[0], hit: true })
    last = at + match[0].length
  }
  if (last < text.length) parts.push({ text: text.slice(last), hit: false })
  return parts.length ? parts : [{ text, hit: false }]
}
