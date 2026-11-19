/**
 * Daily work: the shapes the API returns and the pure rules the screen shares. The list is every candidate the recruiter owns
 * that is still being worked, in one flat list (no daily quota, no morning/evening, no slots, no deadlines); the candidates
 * finished today sit in a "Done today" group at the bottom. No imports, so it runs under node:test.
 */

export type GoogleReview = 'obtained' | 'pending' | null

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
  /** The CRM reference (CND-01092): never shown while a Data Bank ID exists; only the fallback for a row without one. */
  candidateRef: string
  /** The Data Bank ID (CI-078): the one ID shown; null on an older record. */
  candidateCareId: string | null
  /** The order's position, else the candidate's first preferred job, else their first skill. */
  position: string | null
  positionSource: 'order' | 'preference' | 'skill' | null
  positionIsPreferred: boolean
  orderRef: string | null
  /** Advance the candidate paid (stage 8) on this placement; null when none is recorded. Never an invoice figure. */
  advanceAmount: number | null
  /** The currency the advance was recorded in (INR unless another was chosen); null with no advance. */
  advanceCurrency: string | null
  /** Stage 9 Google review: obtained, pending (stage 9 or later, not obtained yet) or null (not applicable yet). */
  googleReview: GoogleReview
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
  /** Advance the candidate paid (stage 8) on this placement; null when none is recorded. Never an invoice figure. */
  advanceAmount: number | null
  /** The currency the advance was recorded in (INR unless another was chosen); null with no advance. */
  advanceCurrency: string | null
  /** Stage 9 Google review: obtained, pending (stage 9 or later, not obtained yet) or null (not applicable yet). */
  googleReview: GoogleReview
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
    { key: 'daily', label: 'Candidates' },
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

// ------------------------------------------------------------------------------------------------ recruiter board

export type RecruiterStatus = 'idle' | 'behind' | 'on-track' | 'none'

/** A recruiter counts as On track once at least this share of today's list (done + to work) is done. */
export const ON_TRACK_SHARE = 1 / 3

/**
 * The status chip on a recruiter's card. There is no quota, deadline or time of day in the product, so the rule only reads the two
 * numbers the API sends (to work, done today), in this order:
 *   1. No candidates at all (to work 0, done 0)                 -> "No candidates"
 *   2. Nothing left to work (everything on the list is done)   -> "On track"
 *   3. Something to work and nothing done today               -> "Idle"
 *   4. Done share (done / (done + to work)) below one third    -> "Behind"
 *   5. Done share of one third or more                          -> "On track"
 * Early in the day most recruiters are therefore Idle; that is the honest answer to "has this person started yet?".
 */
export function recruiterStatus(summary: Pick<DailySummary, 'toWork' | 'doneToday'>): RecruiterStatus {
  const { toWork, doneToday } = summary
  if (toWork + doneToday === 0) return 'none'
  if (toWork === 0) return 'on-track'
  if (doneToday === 0) return 'idle'
  return doneToday / (toWork + doneToday) < ON_TRACK_SHARE ? 'behind' : 'on-track'
}

export const STATUS_LABEL: Record<RecruiterStatus, string> = { idle: 'Idle', behind: 'Behind', 'on-track': 'On track', none: 'No candidates' }

/** Done today as a whole percent of today's list (done + to work); 0 for an empty list. */
export function donePercent(summary: Pick<DailySummary, 'toWork' | 'doneToday'>): number {
  const total = summary.toWork + summary.doneToday
  return total === 0 ? 0 : Math.round((summary.doneToday / total) * 100)
}

const ATTENTION_RANK: Record<RecruiterStatus, number> = { idle: 0, behind: 1, 'on-track': 2, none: 3 }

/**
 * Who needs attention first: Idle, then Behind, then On track, then recruiters with no candidates. Inside a status the one with the
 * most still to work comes first (then the lowest done share, then by name), so the biggest backlog is on top.
 */
export function staffByAttention(staff: DailyStaffSummary[]): DailyStaffSummary[] {
  const rank = (s: DailyStaffSummary) => ATTENTION_RANK[recruiterStatus(s.summary)]
  return [...staff].sort((a, b) => rank(a) - rank(b) || b.summary.toWork - a.summary.toWork
    || donePercent(a.summary) - donePercent(b.summary) || a.name.localeCompare(b.name))
}

/** The team's numbers added up from the per-recruiter list (it always covers everyone, whatever recruiter the list below is filtered to). */
export function teamTotals(staff: DailyStaffSummary[]): DailySummary {
  const toWork = staff.reduce((sum, s) => sum + s.summary.toWork, 0)
  const doneToday = staff.reduce((sum, s) => sum + s.summary.doneToday, 0)
  return { total: toWork + doneToday, toWork, doneToday }
}

/** How many recruiters are in each status. */
export function statusCounts(staff: DailyStaffSummary[]): Record<RecruiterStatus, number> {
  const counts: Record<RecruiterStatus, number> = { idle: 0, behind: 0, 'on-track': 0, none: 0 }
  for (const s of staff) counts[recruiterStatus(s.summary)] += 1
  return counts
}

/** The list's title for the current filter: "Everyone's candidates" / "Gayathri's candidates" (the first name only reads best). */
export function listTitle(person: string, noun = 'candidates'): string {
  const first = person.trim().split(/\s+/)[0]
  return first ? `${first}'s ${noun}` : `Everyone's ${noun}`
}

/** Follow-up board: the recruiter with the most candidates first (then by name); recruiters with none last. */
export function followUpByCount(staff: { userId: string; name: string; active: boolean; summary: { total: number } }[]) {
  return [...staff].sort((a, b) => b.summary.total - a.summary.total || a.name.localeCompare(b.name))
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

/** The AMOUNT column: the advance collected as money ("₹5,000.00"), or null when none is recorded (the cell shows a dash). */
export function advanceLabel(item: Pick<DailyItem, 'advanceAmount' | 'advanceCurrency'>): string | null {
  if (typeof item.advanceAmount !== 'number' || !Number.isFinite(item.advanceAmount) || item.advanceAmount <= 0) return null
  const currency = (item.advanceCurrency || 'INR').toUpperCase()
  try {
    return new Intl.NumberFormat('en-IN', { style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(item.advanceAmount)
  } catch {
    return `${currency} ${item.advanceAmount.toFixed(2)}`
  }
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

/** The remark box: one line (36px) when idle, growing with the text to about four lines (100px), then it scrolls inside. */
export const REMARK_MIN = 36
export const REMARK_MAX = 100

/** The box height for a given text height (`scrollHeight`) and whether it has to scroll: never below one line, never above four. */
export function remarkBox(contentHeight: number): { height: number; scroll: boolean } {
  const wanted = Number.isFinite(contentHeight) ? Math.ceil(contentHeight) : REMARK_MIN
  return { height: Math.min(REMARK_MAX, Math.max(REMARK_MIN, wanted)), scroll: wanted > REMARK_MAX }
}

const SHORT_COUNTRIES: Record<string, string> = {
  'united arab emirates': 'UAE', 'saudi arabia': 'KSA', 'kingdom of saudi arabia': 'KSA', 'united kingdom': 'UK', 'united states': 'USA',
  'united states of america': 'USA', 'republic of korea': 'S. Korea', 'south korea': 'S. Korea',
}

/** The short form recruiters use for the longest Gulf/English names so a pill stays compact; the full name is the tooltip. */
export function countryShort(name: string): string {
  return SHORT_COUNTRIES[name.trim().toLowerCase().replace(/\s+/g, ' ')] ?? name.trim()
}


/* ---------------------------------------------------------------- one list per recruiter: what was done today */

/** What happened to a candidate today: the initial call was answered, a stage was finished, a remark was written. */
export interface TodayWork { called: boolean; moved: boolean; remarked: boolean }

export interface TaskRow extends DailyItem { today: TodayWork }

/** The calendar day ("2026-11-18") of a moment on the agency clock; '' when the moment is not a date. */
export function dayOf(iso: string | null | undefined, timeZone?: string): string {
  const date = new Date(iso ?? '')
  if (!Number.isFinite(date.getTime())) return ''
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
}

/** A follow-up row (one per placement) in the shape of a daily row, so both lists share every cell. */
export function followUpAsDaily(item: FollowUpItem): DailyItem {
  return {
    ...item, kind: 'stage', waiting: false, status: 'open', completedAt: null,
    referral: { source: '', label: '', detail: null },
  } as DailyItem
}

/**
 * One row per candidate a recruiter holds: the daily list (initial calls and waiting stages, with what was finished today) plus
 * the follow-up list (placements moving through stages 2 to 11). A candidate in both appears once, from the daily list.
 * Candidates nobody touched today come first; inside each half the ones with a stage waiting, then the furthest along.
 */
export function taskRows(daily: DailyItem[], followUp: FollowUpItem[], today: string, timeZone?: string): TaskRow[] {
  const merged = new Map<string, DailyItem>()
  for (const item of daily) merged.set(item.candidateId, item)
  for (const item of followUp) if (!merged.has(item.candidateId)) merged.set(item.candidateId, followUpAsDaily(item))
  const rows = [...merged.values()].map((item): TaskRow => ({
    ...item,
    today: {
      called: item.status === 'done' && item.kind === 'call',
      moved: item.status === 'done' && item.kind === 'stage',
      remarked: !!item.remark && dayOf(item.remark.at, timeZone) === today,
    },
  }))
  const touched = (row: TaskRow) => (row.today.called || row.today.moved || row.today.remarked ? 1 : 0)
  return rows.sort((a, b) => touched(a) - touched(b) || Number(b.waiting) - Number(a.waiting) || (b.stage ?? 0) - (a.stage ?? 0))
}

/**
 * What a recruiter holds and did: `called` is the candidates they called or wrote a remark on today (a remark is the daily
 * update of a call), `moved` the stages finished today, `remarks` the remarks written today. The `*All` figures are the same
 * over all time: candidates ever called or remarked, and the stages their candidates have been through (a candidate now at
 * stage 8 has finished seven).
 */
export interface RecruiterWork { assigned: number; called: number; moved: number; remarks: number; untouched: number; calledAll: number; movedAll: number }

/** Per recruiter: candidates held, called today, stages moved today, remarks written today, and candidates nothing happened to. */
export function recruiterWork(rows: TaskRow[]): Map<string, RecruiterWork> {
  const work = new Map<string, RecruiterWork>()
  for (const row of rows) {
    const entry = work.get(row.ownerId) ?? { ...NO_WORK }
    entry.assigned += 1
    // A stage is never finished without speaking to the candidate, so a stage moved today counts as a call even when no remark was typed.
    if (row.today.called || row.today.remarked || row.today.moved) entry.called += 1
    // Past the first call (matched to an order) or at least one remark ever: this candidate has been called.
    if (row.today.called || row.today.remarked || row.kind === 'stage' || (row.remark?.count ?? 0) > 0) entry.calledAll += 1
    if (row.today.moved) entry.moved += 1
    if (row.kind === 'stage') entry.movedAll += Math.max(0, (row.stage ?? 0) - 1)
    if (row.today.remarked) entry.remarks += 1
    if (!row.today.called && !row.today.moved && !row.today.remarked) entry.untouched += 1
    work.set(row.ownerId, entry)
  }
  return work
}

export const NO_WORK: RecruiterWork = { assigned: 0, called: 0, moved: 0, remarks: 0, untouched: 0, calledAll: 0, movedAll: 0 }


export type WorkStatus = 'idle' | 'working' | 'done' | 'none'

/** Idle: nothing happened to any candidate yet. Done: every candidate has been touched. Otherwise working. */
export function workStatus(work: RecruiterWork): WorkStatus {
  if (work.assigned === 0) return 'none'
  if (work.untouched === 0) return 'done'
  return work.untouched === work.assigned ? 'idle' : 'working'
}

export const WORK_LABEL: Record<WorkStatus, string> = { idle: 'Nothing done yet', working: 'In progress', done: 'All touched', none: 'No candidates' }

/** Whose card comes first: nothing done yet (largest list first), then in progress (most still untouched first), then all touched. */
export function peopleByWork<T extends { userId: string; name: string }>(people: T[], work: Map<string, RecruiterWork>): T[] {
  const rank: Record<WorkStatus, number> = { idle: 0, working: 1, done: 2, none: 3 }
  const of = (id: string) => work.get(id) ?? NO_WORK
  return [...people].sort((a, b) => rank[workStatus(of(a.userId))] - rank[workStatus(of(b.userId))]
    || of(b.userId).untouched - of(a.userId).untouched || a.name.localeCompare(b.name))
}
