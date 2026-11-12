/**
 * The Data Bank candidate table: one column set shared by the flat "All resumes" table, the rows inside each job group and
 * the mobile cards, so they can never drift apart. Pure (no React, no store), so it is unit-tested on its own.
 *
 * `share` is the column's part of the row in percent (they add up to 100). The table turns a share into a minimum width of
 * `PX_PER_SHARE` px, so the whole table needs no more than `BANK_TABLE_MIN_WIDTH` px before it gives way to cards.
 */

export interface BankTableColumn {
  key: 'candidate' | 'contact' | 'agent' | 'owner' | 'stage' | 'status' | 'actions'
  header: string
  /** Server sort key; the header becomes a sort button when set. */
  sortKey?: string
  /** Percent of the row. */
  share: number
  align: 'left' | 'right'
}

export const BANK_TABLE_COLUMNS: readonly BankTableColumn[] = [
  { key: 'candidate', header: 'Candidate', sortKey: 'name', share: 24, align: 'left' },
  { key: 'contact', header: 'Contact', sortKey: 'phone', share: 19, align: 'left' },
  { key: 'agent', header: 'Agent', share: 12, align: 'left' },
  { key: 'owner', header: 'Recruiter', sortKey: 'owner', share: 11, align: 'left' },
  { key: 'stage', header: 'Stage', sortKey: 'stage', share: 12, align: 'left' },
  { key: 'status', header: 'Status', sortKey: 'status', share: 13, align: 'left' },
  { key: 'actions', header: 'Actions', share: 9, align: 'right' },
]

/** Pixels of minimum width per share point. */
export const PX_PER_SHARE = 8
export const bankColumnWidth = (column: Pick<BankTableColumn, 'share'>): number => column.share * PX_PER_SHARE
export const bankSharesTotal = (columns: readonly Pick<BankTableColumn, 'share'>[] = BANK_TABLE_COLUMNS): number => columns.reduce((sum, column) => sum + column.share, 0)
export const BANK_TABLE_MIN_WIDTH = bankSharesTotal() * PX_PER_SHARE

/** The CSS grid template of a group row: the same shares as the table's columns. */
export const bankGridTemplate = (): string => BANK_TABLE_COLUMNS.map((column) => `minmax(0, ${column.share}fr)`).join(' ')

/* ---------------------------------------------------------------- the candidate */

/** The name the intake gives a resume it could not read a name from. */
export const PLACEHOLDER_NAME = 'Name needs review'

/** A blank name or the intake placeholder: shown muted with a "Needs review" pill instead of as a person's name. */
export function isPlaceholderName(name?: string | null): boolean {
  const text = (name ?? '').trim().toLowerCase()
  return text === '' || text === PLACEHOLDER_NAME.toLowerCase()
}

/** Internal pointers the intake stores in the source detail ("email_message:3f9c...", "person_review:..."): never shown to a person. */
const POINTER = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)+:\S*$/i

/** The agent or agency a profile came through, or '' when none was recorded (or only an internal pointer is stored). */
export function agentLabel(detail?: string | null, source?: string): string {
  const text = (detail ?? '').trim()
  const importMarker = source !== 'referral' && text.replace(/\s+/g, ' ').toLowerCase() === 'bulk import'
  return POINTER.test(text) || importMarker ? '' : text
}

/** "REF-1234 · 05 Jan 1990": the muted line under the name. Either part may be missing. */
export function candidateSubline(ref?: string | null, dobLabel?: string | null): string {
  const short = (ref ?? '').trim()
  return [short.length > 12 ? `${short.slice(0, 12)}…` : short, dobLabel ?? ''].filter(Boolean).join(' · ')
}

/* ---------------------------------------------------------------- job groups */

/** The group of candidates that have no job (the app calls positions "jobs"). */
export const NOT_MATCHED_JOB_LABEL = 'Not matched to a job'

export interface GroupHeadingInput {
  orderId?: string | null
  title?: string | null
  company?: string | null
  client?: string | null
  country?: string | null
}

/** Job title in bold; "Not matched to a job" for the group with no job. */
export const groupHeading = (group: Pick<GroupHeadingInput, 'orderId' | 'title'>): string =>
  group.orderId ? group.title?.trim() || 'Job' : NOT_MATCHED_JOB_LABEL

/** The muted part of a group header: the client (company), left out when it only repeats itself, plus the country. Empty for the group with no job. */
export function groupClientLine(group: GroupHeadingInput): string {
  if (!group.orderId) return ''
  const company = group.company?.trim() ?? ''
  const client = group.client?.trim() ?? ''
  return [company, client && client.toLowerCase() !== company.toLowerCase() ? client : '', group.country?.trim() ?? ''].filter(Boolean).join(' · ')
}
