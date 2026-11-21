/**
 * Daily work: the shapes the API returns and the pure rules the screen shares. The list is every candidate the recruiter owns
 * that is still being worked, in one flat list (no daily quota, no morning/evening, no slots, no deadlines); the candidates
 * finished today sit in a "Done today" group at the bottom. No imports, so it runs under node:test.
 */

export type GoogleReview = 'obtained' | 'pending' | null

/** One note of the NOTES column (kind "note" on the server; the Remarks select's remarks are not notes). `byId` is its author (the people who may delete it: the author, the candidate's recruiter, Admin and MD). */
export interface DailyNote { id: string; body: string; at: string; by: string; byId?: string | null }

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
  status: 'open' | 'done' | 'closed'
  completedAt: string | null
  calledAt?: string | null
  stageMovedAt?: string | null
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
  /** Advance the candidate paid (stage 9) on this placement; null when none is recorded. Never an invoice figure. */
  advanceAmount: number | null
  /** The currency the advance was recorded in (INR unless another was chosen); null with no advance. */
  advanceCurrency: string | null
  /** Stage 11 Google review: obtained, pending (stage 11 or later, not obtained yet) or null (not applicable yet). */
  googleReview: GoogleReview
  referral: { source: string; label: string; detail: string | null }
  /** The recorded agency, or null. Older servers may omit this field. */
  agency?: string | null
  /** ALL the candidate's notes (typed in the NOTES column; every day, newest first, at most 100). A note is never the remark. Older servers send none. */
  notes?: DailyNote[]
  /** The notes written today only (the TODAY chip). */
  todayNotes?: DailyNote[]
  remark: { body: string; at: string; by: string; count: number } | null
  /** Date of commencement (DOC) of the placement, ISO "YYYY-MM-DD"; null until it is filled in the application form (an older server sends nothing). */
  commencementDate?: string | null
  /** Date of travel (DOT): the air-ticket stage's travel date, ISO "YYYY-MM-DD"; null until the placement has one (an older server sends nothing). Read-only, filled automatically. */
  travelDate?: string | null
  /** How the placement ended (Declined, Rejected, Travelled); an older server sends nothing, which reads as a current row. */
  outcome?: DailyOutcome | null
  /** The stage's name where a closed placement ended, when the server sends it (else the screen looks it up from `stage`). */
  stageTitle?: string | null
}

export type OutcomeKind = 'current' | 'declined' | 'rejected' | 'travelled'

/** `GET /api/daily-work` item `outcome`: kind, the label of a closed one (null for current), the reason, the stage where it ended and when. */
export interface DailyOutcome {
  kind: OutcomeKind
  label: 'Declined' | 'Rejected' | 'Travelled' | null
  reason: string | null
  stage: number | null
  at: string | null
  /** The name of the person who recorded it, when the server sends it. */
  by?: string | null
}

/** The views of the Tasks list: `?view=` on the page and on `GET /api/daily-work` (current is today's list and the default). */
export type DailyView = 'current' | 'declined' | 'rejected' | 'travelled' | 'all'

export type ViewCounts = Record<OutcomeKind, number>

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
  /** The view the rows are for (an older server sends nothing: current). */
  view?: DailyView
  /** How many candidates each view holds for the same scope (an older server sends nothing). */
  viewCounts?: Partial<ViewCounts>
}

/** One candidate in Follow-up: an active placement at stage 2 to 12, one row per placement. */
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
  calledAt?: string | null
  /** When a stage of this placement was last finished (a reopened one never counts); null when none was. */
  stageMovedAt?: string | null
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
  /** The recorded agency, or null. Older servers may omit this field. */
  agency?: string | null
  orderRef: string | null
  /** Advance the candidate paid (stage 9) on this placement; null when none is recorded. Never an invoice figure. */
  advanceAmount: number | null
  /** The currency the advance was recorded in (INR unless another was chosen); null with no advance. */
  advanceCurrency: string | null
  /** Stage 11 Google review: obtained, pending (stage 11 or later, not obtained yet) or null (not applicable yet). */
  googleReview: GoogleReview
  /** ALL the candidate's notes (typed in the NOTES column; every day, newest first, at most 100). A note is never the remark. Older servers send none. */
  notes?: DailyNote[]
  /** The notes written today only (the TODAY chip). */
  todayNotes?: DailyNote[]
  remark: { body: string; at: string; by: string; count: number } | null
  /** Date of commencement (DOC) of the placement, ISO "YYYY-MM-DD"; null until it is filled in the application form. */
  commencementDate?: string | null
  /** Date of travel (DOT), ISO "YYYY-MM-DD", from the air-ticket stage; null until there is one. */
  travelDate?: string | null
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
 * Follow-up (selected and moving through stages 2 to 12) and Travelled (deployed: the Travelled screen, a link, not a copy).
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

/** The row's button: Complete (a placement to finish a stage on), Open (the application form), View once done today or when the placement ended. */
export function rowAction(item: Pick<DailyItem, 'status' | 'applicationId'> & { outcome?: DailyItem['outcome'] }): 'Complete' | 'Open' | 'View' {
  return item.status === 'done' || isClosed(item) ? 'View' : item.applicationId ? 'Complete' : 'Open'
}

// ------------------------------------------------------------------------------------------------ status views (Declined, Rejected, Travelled)

const OUTCOME_LABEL: Record<Exclude<OutcomeKind, 'current'>, 'Declined' | 'Rejected' | 'Travelled'> = { declined: 'Declined', rejected: 'Rejected', travelled: 'Travelled' }

/** The five views of the Tasks list, in the order of the filter bar; `empty` is the empty-state title and `noun` the word the summary uses. */
export const DAILY_VIEWS: readonly { key: DailyView; label: string; empty: string; noun: string }[] = [
  { key: 'current', label: 'Current process', empty: 'No candidates assigned', noun: 'candidate' },
  { key: 'declined', label: 'Declined', empty: 'No declined candidates', noun: 'declined candidate' },
  { key: 'rejected', label: 'Rejected', empty: 'No rejected candidates', noun: 'rejected candidate' },
  { key: 'travelled', label: 'Travelled', empty: 'No travelled candidates', noun: 'travelled candidate' },
  { key: 'all', label: 'All', empty: 'No candidates', noun: 'candidate' },
]

/** True for a `?view=` value that belongs to this list (the 48-hour list has its own views: open, due, overdue, ...). */
export const isDailyView = (value: string | null | undefined): value is DailyView => DAILY_VIEWS.some((view) => view.key === value)

/** The view in the URL; anything missing or unknown is the current process (today's list). */
export const parseView = (value: string | null | undefined): DailyView => (isDailyView(value) ? value : 'current')

/** The `view` query value to send: nothing for the current process, so its request is the one every other screen already shares. */
export const viewParam = (view: DailyView): string | null => (view === 'current' ? null : view)

/**
 * The server path of a list: `/daily-work?staff=all&view=declined`. `staff` and `view` are left out when they are the default, and
 * `view` only goes to `/daily-work` (Follow-up has no views).
 */
export function listRequest(path: string, staff?: string, view: DailyView = 'current'): string {
  const parts: string[] = []
  if (staff) parts.push(`staff=${encodeURIComponent(staff)}`)
  const named = path === '/daily-work' ? viewParam(view) : null
  if (named) parts.push(`view=${named}`)
  return parts.length ? `${path}?${parts.join('&')}` : path
}

/** How a row ended. A row without an `outcome` (an older server, a follow-up row) or with one this screen does not know is a current row. */
export function outcomeOf(item: { outcome?: Partial<DailyOutcome> | null }): DailyOutcome {
  const raw = item.outcome
  const kind = raw?.kind
  if (kind !== 'declined' && kind !== 'rejected' && kind !== 'travelled') return { kind: 'current', label: null, reason: null, stage: null, at: null }
  const stage = typeof raw?.stage === 'number' && Number.isFinite(raw.stage) && raw.stage > 0 ? Math.floor(raw.stage) : null
  return { kind, label: OUTCOME_LABEL[kind], reason: raw?.reason?.trim() || null, stage, at: raw?.at ?? null, by: raw?.by?.trim() || null }
}

/** A row whose placement ended (Declined, Rejected or Travelled): nothing is worked on it any more. */
export const isClosed = (item: { outcome?: Partial<DailyOutcome> | null }): boolean => outcomeOf(item).kind !== 'current'

/** "at 5 · Offer letter": where the placement ended (the stage number and its name); '' when no stage is known. `titleOf` looks the name up. */
export function outcomeStageText(outcome: Pick<DailyOutcome, 'stage'>, titleOf: (stage: number) => string | undefined = () => undefined): string {
  if (!outcome.stage) return ''
  const title = titleOf(outcome.stage)
  return title ? `at ${outcome.stage} · ${title}` : `at ${outcome.stage}`
}

/** The visible lines under a closed row's chip: WHERE ("at 5 · Offer letter"), WHY (the reason), WHEN ("19 Nov 2026") and, when the server sends it, WHO ("by Asha"). */
export function outcomeDetail(outcome: DailyOutcome, titleOf?: (stage: number) => string | undefined, timeZone?: string): { where: string; why: string; when: string; by: string; line: string } {
  const where = outcomeStageText(outcome, titleOf)
  const why = outcome.reason ?? ''
  const when = docLabel(dayOf(outcome.at, timeZone))
  const by = outcome.by ? `by ${outcome.by}` : ''
  return { where, why, when, by, line: [why, when, by].filter(Boolean).join(' · ') }
}

export interface ViewTab { key: DailyView; label: string; count: number | null }

/**
 * The filter bar's tabs with their counts. The server counts the current process as today's list; the screen's own current rows also hold the
 * follow-up placements, so `currentShown` (the count the screen last showed, when it knows it) wins for Current process and All. A count the
 * server did not send is null (the tab then shows just its name).
 */
export function viewTabs(counts: Partial<ViewCounts> | null | undefined, currentShown?: number | null): ViewTab[] {
  const num = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null)
  const current = num(currentShown) ?? num(counts?.current)
  const closed = [num(counts?.declined), num(counts?.rejected), num(counts?.travelled)]
  const all = current === null || closed.some((value) => value === null) ? null : current + (closed as number[]).reduce((sum, value) => sum + value, 0)
  const count = (key: DailyView): number | null => key === 'current' ? current : key === 'all' ? all : num(counts?.[key])
  return DAILY_VIEWS.map((view) => ({ key: view.key, label: view.label, count: count(view.key) }))
}

/** "Current process (12)": a tab's text; just the name while the count is not known. */
export const viewTabText = (tab: Pick<ViewTab, 'label' | 'count'>): string => (tab.count === null ? tab.label : `${tab.label} (${tab.count})`)

/** Where the arrow keys take the focus in the filter bar: Left and Right wrap around, Home and End jump; null for any other key. */
export function nextViewIndex(key: string, index: number, length: number): number | null {
  if (length <= 0) return null
  if (key === 'ArrowRight' || key === 'ArrowDown') return (index + 1) % length
  if (key === 'ArrowLeft' || key === 'ArrowUp') return (index - 1 + length) % length
  if (key === 'Home') return 0
  if (key === 'End') return length - 1
  return null
}

/** The panel summary under the title: "12 candidates · 3 recruiters" for the current process, "4 declined candidates" for Declined. */
export function viewSummary(view: DailyView, count: number, recruiters?: number): string {
  const noun = DAILY_VIEWS.find((item) => item.key === view)?.noun ?? 'candidate'
  const base = `${count} ${noun}${count === 1 ? '' : 's'}${view === 'all' ? ' · all statuses' : ''}`
  return typeof recruiters === 'number' ? `${base} · ${recruiters} ${recruiters === 1 ? 'recruiter' : 'recruiters'}` : base
}

/** The empty-state title of a view ("No declined candidates"). */
export const viewEmptyTitle = (view: DailyView): string => DAILY_VIEWS.find((item) => item.key === view)?.empty ?? 'No candidates'

/** The Export button's words for a view: "Export Excel" for the current process, "Export Excel · Declined" for the others. */
export const exportLabel = (view: DailyView): string => (view === 'current' ? 'Export Excel' : `Export Excel · ${DAILY_VIEWS.find((item) => item.key === view)?.label ?? ''}`)

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

/** Older responses can identify an agency only through the explicitly recorded referrer. */
export function agencyLabel(item: Pick<DailyItem, 'referral'> & { agency?: string | null }): string {
  if (item.agency !== undefined) return item.agency?.trim() ?? ''
  return item.referral.source === 'referral' ? item.referral.detail?.trim() ?? '' : ''
}

/** A client exists only through an order, so an unmatched candidate has none; say that instead of a bare dash. */
export function clientCell(item: Pick<DailyItem, 'client' | 'kind'>): { text: string; hint: string } {
  if (item.client) return { text: item.client, hint: '' }
  return { text: '', hint: item.kind === 'call' ? 'Not matched to an order yet' : 'No client recorded' }
}

/**
 * The small line under the client name: the order's country (or, for a candidate not matched to an order yet, their preferred country),
 * as plain text with no flag. '' when no country is known, so nothing is drawn. A country read from the candidate's current city is not
 * shown: that is where they live, not where the job is.
 */
export function clientCountry(item: Pick<DailyItem, 'country' | 'countrySource' | 'locationHint'>): { text: string; hint: string } {
  const cell = countryCell(item)
  if (cell.source !== 'order' && cell.source !== 'preference') return { text: '', hint: '' }
  return { text: cell.names.map((name) => name.replace(/\s+/g, ' ')).join(', '), hint: cell.hint }
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
export interface TodayWork { called: boolean; moved: boolean; remarked: boolean; calledAt?: string; movedAt?: string }

export interface TaskRow extends DailyItem { today: TodayWork }

export interface TaskColumn { key: string; title: string; width: number; /** The header's tooltip (the title is a short code). */ tip?: string; left?: boolean; supervisor?: boolean; quiet?: boolean; /** Only a column of a roomy panel: below it the same content sits inside another cell (see TASK_LIST_COLUMNS). */ roomy?: boolean }

/** A panel narrower than this shows every candidate as a labelled card instead of a table row (daily-work.css, tasks-table.css). */
export const TASK_CARD_BREAKPOINT = 1080

/**
 * The columns of the Tasks list, in the order the owner asked for: S NO, DOC (date of commencement), DOT (date of travel, read-only), CLIENT (the name, and under it the country in
 * a smaller muted line), JOB, CANDIDATE, Recruiter (supervisors only), AGENCY, STAGE, TODAY, REMARKS, NOTES and the untitled actions cell. Every
 * header is one short word so it never wraps. The table never scrolls sideways: its columns are these widths as percentages of the panel
 * (columnLayout), so it fills the panel at any width from 1080px (below that the Tasks list is a stack of cards). Every column is centred (header and
 * cells), the actions included (the 72px Open / Complete / View button, the 4px gap and the 36px row menu fill their 116px cell).
 *
 * Every width is its longest word plus the cell's 13px (6px padding on both sides and the 1px column line), measured in Poppins at the table's
 * sizes: CLIENT "International" (86px at 13px / 600), CANDIDATE "EMAYAVARAMBAN" (121px), JOB "SUPERVISOR" (78px), AGENCY "LAVOROTECH" (86px), Recruiter
 * "Srivathsala" (74px), STAGE "Prescreening" (77px in its 16px chip), TODAY the whole "Moved 10:45" chip (75px + 16px chip padding), and the headers S NO (29px),
 * CANDIDATE (70px) and RECRUITER (65px). DOC and REMARKS are the two with give: the empty "dd/mm/yyyy" hint may lose its last pixels and the remark
 * dropdown clips a long remark to two lines (its full text is the tooltip). DOT (date of travel, read-only) is 60px: "09 Jul 2026" breaks at its space into "09 Jul" over "2026", so a whole new column costs 60px, not 90; REMARKS and NOTES gave it the room (82px and 100px: they are the two columns that take the spare width of a wider panel, up to REMARKS_MAX / NOTES_MAX, and their text wraps and scrolls inside the cell).
 *
 * NOTES is a column of every table. TODAY is a column only when the panel has room for the whole plan (`taskPlanWidth(supervisor, true)`: 1221px for a
 * supervisor, 1134px for a recruiter); a narrower table needs only `taskPlanWidth(supervisor, false)` (1117px / 1030px: a 1440px window gives the
 * table 1111px, the columns share the 0.5% they lack) and shows the TODAY chip inside the STAGE cell, under the stage chip and its dashes. A phone card has a TODAY row of its own.
 */
export const TASK_LIST_COLUMNS: readonly TaskColumn[] = [
  { key: 'no', title: 'S NO', width: 44 },
  { key: 'doc', title: 'DOC', width: 90 },
  { key: 'dot', title: 'DOT', width: 60, tip: 'Date of travel' },
  { key: 'client', title: 'CLIENT', width: 99 },
  { key: 'position', title: 'JOB', width: 92 },
  { key: 'candidate', title: 'CANDIDATE', width: 134 },
  { key: 'staff', title: 'Recruiter', width: 87, supervisor: true },
  { key: 'agency', title: 'AGENCY', width: 99 },
  { key: 'stage', title: 'STAGE', width: 106 },
  { key: 'today', title: 'TODAY', width: 104, roomy: true },
  { key: 'remarks', title: 'REMARKS', width: 82 },
  { key: 'notes', title: 'NOTES', width: 100 },
  { key: 'actions', title: 'Actions', width: 124, quiet: true },
]

/** The columns one person sees: everyone but supervisors lose the Recruiter column (their list is their own); `roomForToday` keeps the TODAY column. */
export const taskColumnsFor = (supervisor: boolean, roomForToday = true, closed: ClosedRows = 'none'): TaskColumn[] => {
  const columns = TASK_LIST_COLUMNS.filter((column) => (supervisor || !column.supervisor) && (roomForToday || !column.roomy) && !(closed === 'all' && column.key === 'today'))
  return closed === 'none' ? columns : columns.map((column) => (column.key === 'stage' ? { ...column, width: ENDED_STAGE_WIDTH } : column))
}

/**
 * A row whose placement ended shows its outcome in STAGE (a one-line pill, the stage name, the reason, "date · by whom"): that cell needs ENDED_STAGE_WIDTH px
 * instead of the 106px of a progress pill. A list with such rows (`some`: the All tab) has the wider STAGE column; a list of nothing but ended rows
 * (`all`: Declined, Rejected, Travelled) has no TODAY column either (nothing is done on them today), and its spare room goes to REMARKS and NOTES as always.
 */
export type ClosedRows = 'none' | 'some' | 'all'
export const ENDED_STAGE_WIDTH = 150
export const closedRowsOf = (rows: readonly { outcome?: Partial<DailyOutcome> | null }[]): ClosedRows => {
  const closed = rows.filter((row) => isClosed(row)).length
  return closed === 0 ? 'none' : closed === rows.length ? 'all' : 'some'
}

/**
 * Remarks and Notes are what people read and write on this list, so they get ALL the room the panel has beyond the minimum plan (the columns above
 * are each their longest word and no less): the spare pixels go to REMARKS and NOTES (2 : 3), up to REMARKS_MAX / NOTES_MAX px, and only what is
 * left after that is spread over every column. On a panel at or below the plan nothing changes: every column keeps its measured width.
 * Returns each column's width in px for a panel `panelWidth` px wide (0 = not measured yet: the plan as it is).
 */
export const REMARKS_MAX = 240
export const NOTES_MAX = 330
export function widthsWithSpare(columns: readonly TaskColumn[], panelWidth: number): Record<string, number> {
  const widths: Record<string, number> = Object.fromEntries(columns.map((column) => [column.key, column.width]))
  const plan = columns.reduce((total, column) => total + column.width, 0)
  const spare = panelWidth > plan ? panelWidth - plan : 0
  if (spare <= 0) return widths
  const hasRemarks = 'remarks' in widths
  const hasNotes = 'notes' in widths
  if (hasRemarks && hasNotes) {
    widths.remarks += Math.min(Math.floor(spare * 0.4), REMARKS_MAX - widths.remarks)
    widths.notes += Math.min(Math.floor(spare * 0.6), NOTES_MAX - widths.notes)
  } else if (hasNotes) widths.notes += Math.min(spare, NOTES_MAX - widths.notes)
  return widths
}

/** The plan's width in px for one person's columns: the panel width from which they all keep their measured room. */
export const taskPlanWidth = (supervisor: boolean, roomForToday: boolean): number => taskColumnsFor(supervisor, roomForToday).reduce((total, column) => total + column.width, 0)

/**
 * Where the TODAY chip is for a panel this wide (px; 0 = not measured yet): its own `column` when the whole plan fits, a `row` of a phone card under
 * 1080px, otherwise inside the `stage` cell.
 */
export function todayPlace(supervisor: boolean, panelWidth: number): 'column' | 'stage' | 'row' {
  if (panelWidth >= taskPlanWidth(supervisor, true)) return 'column'
  return panelWidth > 0 && panelWidth < TASK_CARD_BREAKPOINT ? 'row' : 'stage'
}

/** The notes of a candidate, newest first whatever order they arrived in (equal times keep the arrival order). */
export function notesNewestFirst<T extends { at: string }>(notes: readonly T[] | null | undefined): T[] {
  const time = (note: T) => { const value = Date.parse(note.at); return Number.isFinite(value) ? value : 0 }
  return (notes ?? []).map((note, index) => ({ note, index })).sort((a, b) => time(b.note) - time(a.note) || a.index - b.index).map((entry) => entry.note)
}

/** When a note was written: "11:40" today, "18 Nov 09:15" on an earlier day (agency clock; `now` is injectable for tests). */
export function noteWhen(iso: string, timeZone?: string, now: Date | string = new Date()): string {
  const clock = clockTime(iso, timeZone)
  const day = dayOf(iso, timeZone)
  if (!day) return clock
  const today = dayOf(typeof now === 'string' ? now : now.toISOString(), timeZone)
  if (day === today) return clock
  const [, month, date] = day.split('-')
  return `${Number(date)} ${SHORT_MONTHS[Number(month) - 1]} ${clock}`
}

/** What typing a note does: the note appears at once (`pending`), is sent, and `settled` runs when the server and the list have answered (the screen drops the pending copy then). */
export interface NoteHooks { pending?: (note: DailyNote) => void; settled?: (note: DailyNote) => void }

/** Send a typed note: nothing for an empty text; otherwise show it at once, call `add`, and report `{ sent }` or the server's own words in `error` (the text comes back so the box can keep it). */
export async function sendNote(
  typed: string, add: (body: string) => Promise<unknown>, hooks: NoteHooks = {}, author = 'You', now: Date = new Date(),
): Promise<{ sent: boolean; body: string; error?: string }> {
  const body = noteText(typed)
  if (!body) return { sent: false, body: '' }
  const note: DailyNote = { id: `${PENDING_NOTE}${now.getTime()}`, body, at: now.toISOString(), by: author || 'You', byId: null }
  hooks.pending?.(note)
  try { await add(body); return { sent: true, body } }
  catch (failure) { return { sent: false, body, error: failure instanceof Error && failure.message ? failure.message : 'The note could not be saved. Please try again.' } }
  finally { hooks.settled?.(note) }
}

/** The id prefix of a note that is shown but not saved yet: it has no delete button. */
export const PENDING_NOTE = 'pending-'

/** The text of a typed note, ready to send: trimmed, never empty (null), at most 10000 characters like the server's limit. */
export function noteText(typed: string): string | null {
  const text = typed.trim().slice(0, 10000)
  return text || null
}

export interface TodayChip { key: 'called' | 'moved'; text: string }

/** Activity times for calls and stage movement. The table adds the note time on a notes-only day. */
export function todayChips(row: { today: TodayWork; completedAt: string | null }, timeZone?: string): TodayChip[] {
  const chips: TodayChip[] = []
  const calledAt = row.today.calledAt || row.completedAt
  if (row.today.called) chips.push({ key: 'called', text: `Called${calledAt ? ` ${clockTime(calledAt, timeZone)}` : ''}` })
  const movedAt = row.today.movedAt || row.completedAt
  if (row.today.moved) chips.push({ key: 'moved', text: `Stage moved${movedAt ? ` ${clockTime(movedAt, timeZone)}` : ''}` })
  return chips
}

/** True when nothing at all happened to the candidate today: no call, no stage, no remark. */
export const nothingYet = (today: TodayWork): boolean => !today.called && !today.moved && !today.remarked

const SHORT_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** The DOC cell: "2026-11-19" as "19 Nov 2026"; '' when there is no valid date (the screen then offers "Add date"). */
export function docLabel(iso: string | null | undefined): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '')
  if (!match) return ''
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const real = new Date(Date.UTC(year, month - 1, day))
  if (real.getUTCFullYear() !== year || real.getUTCMonth() !== month - 1 || real.getUTCDate() !== day) return ''
  return `${day} ${SHORT_MONTHS[month - 1]} ${year}`
}

/** The server path of the Excel export for the same list the screen shows: everyone (supervisors) or one recruiter, in the chosen view (nothing added for the current process). */
export function exportPath(staff?: string, view: DailyView = 'current'): string {
  const named = viewParam(view)
  return `/daily-work/export?format=xlsx&staff=${encodeURIComponent(staff || 'all')}${named ? `&view=${named}` : ''}`
}

/** "tasks-2026-11-19.xlsx", "tasks-asha-devi-2026-11-19.xlsx" for one recruiter's list, "tasks-declined-2026-11-19.xlsx" for another view. */
export function exportFileName(day: string, person?: string, view: DailyView = 'current'): string {
  const slug = (person ?? '').normalize('NFKD').replace(/\p{M}+/gu, '').replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/g, '').toLowerCase().slice(0, 40)
  return `tasks${slug ? `-${slug}` : ''}${viewParam(view) ? `-${view}` : ''}-${day}.xlsx`
}

/** The calendar day ("2026-11-18") of a moment on the agency clock; '' when the moment is not a date. */
export function dayOf(iso: string | null | undefined, timeZone?: string): string {
  const date = new Date(iso ?? '')
  if (!Number.isFinite(date.getTime())) return ''
  const parts = new Intl.DateTimeFormat('en', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(date)
  const part = (name: Intl.DateTimeFormatPartTypes) => parts.find((field) => field.type === name)?.value
  return `${part('year')}-${part('month')}-${part('day')}`
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
 * the follow-up list (placements moving through stages 2 to 12). A candidate in both appears once, from the daily list.
 * Candidates nobody touched today come first; inside each half the ones with a stage waiting, then the furthest along.
 */
export function taskRows(daily: DailyItem[], followUp: FollowUpItem[], today: string, timeZone?: string): TaskRow[] {
  const merged = new Map<string, DailyItem>()
  const movedToday = new Map<string, string>()
  const recordMove = (id: string, at?: string | null) => {
    if (!at || dayOf(at, timeZone) !== today) return
    const previous = movedToday.get(id)
    if (!previous || Date.parse(at) > Date.parse(previous)) movedToday.set(id, at)
  }
  for (const item of daily) {
    merged.set(item.candidateId, item)
    recordMove(item.candidateId, item.stageMovedAt)
  }
  for (const item of followUp) {
    if (!merged.has(item.candidateId)) merged.set(item.candidateId, followUpAsDaily(item))
    // A placement in Follow-up whose stage was finished today (stage 2 to 12) is "stage moved" today, like a finished prescreening.
    recordMove(item.candidateId, item.stageMovedAt)
  }
  const rows = [...merged.values()].map((item): TaskRow => isClosed(item)
    // A placement that ended has nothing done on it today, whatever else the server says.
    ? { ...item, today: { called: false, moved: false, remarked: false } }
    : {
      ...item,
      today: {
        called: item.calledAt === undefined ? item.status === 'done' && item.kind === 'call' : !!item.calledAt && dayOf(item.calledAt, timeZone) === today,
        calledAt: item.calledAt || undefined,
        moved: movedToday.has(item.candidateId) || (item.stageMovedAt === undefined && item.status === 'done' && item.kind === 'stage'),
        movedAt: movedToday.get(item.candidateId),
        remarked: !!item.todayNotes?.length || (!!item.remark && dayOf(item.remark.at, timeZone) === today),
      },
    })
  const touched = (row: TaskRow) => (row.today.called || row.today.moved || row.today.remarked ? 1 : 0)
  rows.sort((a, b) => touched(a) - touched(b) || Number(b.waiting) - Number(a.waiting) || (b.stage ?? 0) - (a.stage ?? 0))
  return sortByDoc(rows)
}

/** The day of a DOC as a number (UTC midnight) so dates compare; null for no date or one that is not a real day (2026-02-30). */
export function docTime(iso: string | null | undefined): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '')
  if (!match) return null
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const real = new Date(Date.UTC(year, month - 1, day))
  return real.getUTCFullYear() === year && real.getUTCMonth() === month - 1 && real.getUTCDate() === day ? real.getTime() : null
}

/**
 * The Tasks order: by DOC (date of commencement), the OLDEST first and the newest last; a candidate with no date (or an impossible one) goes to the END,
 * after every dated one. Rows with the same day, and the ones with none, keep the order they came in (a stable sort, whatever the engine).
 */
export function sortByDoc<T extends { commencementDate?: string | null }>(rows: readonly T[]): T[] {
  return rows.map((row, index) => ({ row, index, time: docTime(row.commencementDate) }))
    .sort((a, b) => (a.time === null ? 1 : 0) - (b.time === null ? 1 : 0) || (a.time ?? 0) - (b.time ?? 0) || a.index - b.index)
    .map((entry) => entry.row)
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
    if (isClosed(row)) continue   // a placement that ended is nobody's work today: it is on no recruiter's card
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
