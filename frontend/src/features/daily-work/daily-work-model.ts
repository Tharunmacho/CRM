/**
 * Daily work: the shapes the API returns and the pure rules the screen shares. The list is every candidate the recruiter owns
 * that is still being worked, in one flat list (no daily quota, no morning/evening, no slots, no deadlines); the candidates
 * finished today sit in a "Done today" group at the bottom. No imports, so it runs under node:test.
 */

export interface DailyItem {
  /** The candidate id: one row per candidate. */
  id: string
  /** stage: on an active placement (opens the stage); call: not matched to an order yet (opens the application form). */
  kind: 'stage' | 'call'
  /** Where remarks are kept (the stage task, or the candidate's initial-call task); null when there is none. */
  taskId: string | null
  /** The placement to open in the stage modal; null while the candidate is not matched to an order. */
  applicationId: string | null
  candidateId: string
  ownerId: string
  stage: number | null
  /** A stage of the placement is waiting for the recruiter (these rows are listed first). */
  waiting: boolean
  /** open: still to work; done: a stage was finished or the initial call answered today. */
  status: 'open' | 'done'
  completedAt: string | null
  client: string | null
  clientContact: string | null
  /** The order's country, else (not matched yet) the candidate's preferred country from the application form. */
  country: string | null
  countrySource: 'order' | 'preference' | null
  countryIsPreferred: boolean
  /** Current city/address, sent only when no country is known, so the screen can still recognise one in it. */
  locationHint: string | null
  candidate: string
  /** The CRM reference (CND-01092): shown second and muted next to the Care India ID. */
  candidateRef: string
  /** The Care India ID (CI-26-27-001): the primary identifier; null on an older record. */
  candidateCareId: string | null
  /** The order's position, else the candidate's first preferred job, else their first skill. */
  position: string | null
  positionSource: 'order' | 'preference' | 'skill' | null
  positionIsPreferred: boolean
  orderRef: string | null
  referral: { source: string; label: string; detail: string | null }
  remark: { body: string; at: string; by: string; count: number } | null
}

export interface DailySummary {
  /** To work plus done today. */
  total: number
  toWork: number
  doneToday: number
}

export interface DailyStaffSummary {
  userId: string
  name: string
  active: boolean
  summary: DailySummary
}

export interface DailyWorkResponse {
  agencyDate: string
  serverTime: string
  /** How many recruiters the rows and the summary cover. */
  staffCount: number
  summary: DailySummary
  items: DailyItem[]
  /** More candidates exist than the rows sent (the counts stay exact). */
  truncated: boolean
  staff?: DailyStaffSummary[]
}

/** One candidate in Follow-up: an active placement at stage 2 to 11, one row per placement. */
export interface FollowUpItem {
  /** The application id. */
  id: string
  applicationId: string
  /** The open stage task a new remark goes on; null when there is none. */
  taskId: string | null
  candidateId: string
  ownerId: string
  stage: number
  /** The latest of the last stage completed, the last remark, the last draft and the placement's creation. */
  lastUpdateAt: string
  client: string | null
  clientContact: string | null
  country: string | null
  countrySource: 'order' | 'preference' | null
  countryIsPreferred: boolean
  locationHint: string | null
  position: string | null
  positionSource: 'order' | 'preference' | 'skill' | null
  positionIsPreferred: boolean
  candidate: string
  candidateRef: string
  candidateCareId: string | null
  orderRef: string | null
  remark: { body: string; at: string; by: string; count: number } | null
}

export interface FollowUpResponse {
  serverTime: string
  staffCount: number
  summary: { total: number }
  items: FollowUpItem[]
  truncated: boolean
  staff?: { userId: string; name: string; active: boolean; summary: { total: number } }[]
}

/** "3 days ago", "today": how long ago a follow-up row is (calendar days between two moments, never negative). */
export function staleLabel(lastIso: string, nowMs: number): string {
  const last = new Date(lastIso).getTime()
  if (!Number.isFinite(last)) return ''
  const days = Math.max(0, Math.floor((nowMs - last) / 86_400_000))
  return days === 0 ? 'today' : days === 1 ? '1 day ago' : `${days} days ago`
}

export type BucketKey = 'daily' | 'follow-up' | 'travelled'

/**
 * The three buckets a candidate moves through, as the Tasks tab strip shows them: Today's candidates (prescreening still to do),
 * Follow-up (selected and moving through stages 2 to 11) and Travelled (deployed: the Travelled screen, a link, not a copy).
 * The counts are the candidates still to work today and the follow-up candidates; Travelled carries none.
 */
export function bucketTabs(counts: { today: number | null; followUp: number | null }): { key: BucketKey; label: string }[] {
  return [
    { key: 'daily', label: tabLabel("Today's candidates", counts.today) },
    { key: 'follow-up', label: tabLabel('Follow-up', counts.followUp) },
    { key: 'travelled', label: 'Travelled' },
  ]
}

/** The tab label with its count: "Follow-up (12)"; just the name while the count is not known yet. */
export function tabLabel(name: string, count: number | null | undefined): string {
  return typeof count === 'number' ? `${name} (${count})` : name
}

/** "12 to work · 3 done today": the header summary of a list. */
export function summaryLine(summary: Pick<DailySummary, 'toWork' | 'doneToday'>): string {
  return `${summary.toWork} to work · ${summary.doneToday} done today`
}

/** One recruiter's cell on the board; says plainly when there is nothing at all. */
export function staffSummaryLine(summary: DailySummary): string {
  return summary.total === 0 ? 'No candidates' : summaryLine(summary)
}

/** Who has the most left to work first (then the most done today, then by name); recruiters with no candidates last. */
export function staffByWorkLeft(staff: DailyStaffSummary[]): DailyStaffSummary[] {
  return [...staff].sort((a, b) => b.summary.toWork - a.summary.toWork || b.summary.doneToday - a.summary.doneToday || a.name.localeCompare(b.name))
}

export interface DailyGroupSet { toWork: DailyItem[]; done: DailyItem[] }

/** The two groups, each keeping the server's order (waiting first, then newest; done: latest first). */
export function splitItems(items: DailyItem[]): DailyGroupSet {
  return { toWork: items.filter((item) => item.status !== 'done'), done: items.filter((item) => item.status === 'done') }
}

/** The agency-clock time of a moment, "11:20". */
export function clockTime(iso: string, timeZone?: string): string {
  const date = new Date(iso)
  if (!Number.isFinite(date.getTime())) return '--:--'
  return new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}

/** Referral column text: the referrer when someone referred the candidate, otherwise how the CV arrived. */
export function referralLabel(referral: DailyItem['referral']): string {
  if (referral.source === 'referral') return referral.detail?.trim() || 'Referral'
  return referral.label
}

/** The row's button: Complete (a placement to finish a stage on), Open (the application form), View once done today. */
export function rowAction(item: Pick<DailyItem, 'status' | 'applicationId'>): 'Complete' | 'Open' | 'View' {
  return item.status === 'done' ? 'View' : item.applicationId ? 'Complete' : 'Open'
}

// ------------------------------------------------------------------------------------------------ table cells

export type CellSource = 'order' | 'preference' | 'location' | 'skill' | 'none'

export interface CountryCell {
  /** One or more countries (a candidate may prefer several); empty when none is known. */
  names: string[]
  source: CellSource
  /** Tooltip explaining where the value comes from, or why there is none. */
  hint: string
}

/** Several preferred countries are stored as one text ("UAE, Qatar"); split them without breaking "Dubai, UAE" style places. */
export function splitCountries(value: string): string[] {
  return value.split(/[,;/|\n]+/).map((part) => part.trim()).filter(Boolean)
}

/**
 * What the Country column shows. An order's country wins; otherwise the candidate's preferred country; otherwise a country
 * recognised in their current location (`recognise` is the app's country lookup, passed in so this file stays import-free);
 * otherwise nothing, with the honest reason (nationality is never used).
 */
export function countryCell(item: Pick<DailyItem, 'country' | 'countrySource' | 'locationHint'>, recognise: (text: string) => unknown = () => null): CountryCell {
  if (item.country?.trim()) {
    const preferred = item.countrySource === 'preference'
    const names = preferred ? splitCountries(item.country) : [item.country.trim()]
    return {
      names, source: preferred ? 'preference' : 'order',
      hint: preferred ? "Candidate's preferred country (from the application form), not from an order" : "The order's country",
    }
  }
  const segments = (item.locationHint ?? '').split(/[,\n]+/).map((part) => part.trim()).filter(Boolean).reverse()
  const found = segments.find((part) => recognise(part))
  if (found) return { names: [found], source: 'location', hint: "From the candidate's current location: no preferred country saved yet" }
  return { names: [], source: 'none', hint: 'No preferred country saved yet' }
}

export interface PositionCell { text: string; source: CellSource; hint: string }

export function positionCell(item: Pick<DailyItem, 'position' | 'positionSource'>): PositionCell {
  if (!item.position?.trim()) return { text: '', source: 'none', hint: 'No job preference saved yet' }
  if (item.positionSource === 'preference') return { text: item.position, source: 'preference', hint: "Candidate's preferred job (from the application form), not from an order" }
  if (item.positionSource === 'skill') return { text: item.position, source: 'skill', hint: "No job preference saved: shown from the candidate's skills" }
  return { text: item.position, source: 'order', hint: "The order's job" }
}

/** A client exists only through an order, so an unmatched candidate has none; say that instead of a bare dash. */
export function clientCell(item: Pick<DailyItem, 'client' | 'kind'>): { text: string; hint: string } {
  if (item.client) return { text: item.client, hint: '' }
  return { text: '', hint: item.kind === 'call' ? 'Not matched to an order yet' : 'No client recorded' }
}

/** Number of colours in the country pill palette (`dw-ctry-0` ... in daily-work.css). */
export const COUNTRY_TONES = 8

/** A stable colour for a country: the same name always gets the same pill, whatever the casing. */
export function countryTone(name: string): number {
  const key = name.trim().toLowerCase()
  let hash = 0
  for (const ch of key) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  return hash % COUNTRY_TONES
}

const SHORT_COUNTRIES: Record<string, string> = {
  'united arab emirates': 'UAE', 'saudi arabia': 'KSA', 'kingdom of saudi arabia': 'KSA', 'united kingdom': 'UK', 'united states': 'USA',
  'united states of america': 'USA', 'republic of korea': 'S. Korea', 'south korea': 'S. Korea',
}

/** The short form recruiters use for the longest Gulf/English names so a pill stays compact; the full name is the tooltip. */
export function countryShort(name: string): string {
  return SHORT_COUNTRIES[name.trim().toLowerCase().replace(/\s+/g, ' ')] ?? name.trim()
}
