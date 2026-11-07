/**
 * Daily work quota: the shapes the API returns and the pure rules the screen and the "complete your tasks first"
 * banner share (countdowns, due tones, progress wording, who still has work). No imports, so it runs under node:test.
 */

export type DailySession = 'morning' | 'evening'

export interface DailyItem {
  id: string
  /** stage: a matched candidate's open stage; call: an owned candidate worked through the initial call. */
  kind: 'stage' | 'call'
  /** Where remarks are kept (the stage task, or the candidate's initial-call task); null when there is none. */
  taskId: string | null
  /** The placement to open in the stage modal; null while the candidate is not matched to an order. */
  applicationId: string | null
  candidateId: string
  ownerId: string
  /** Agency-timezone day the item was allocated, YYYY-MM-DD. */
  workDate: string
  slotNo: number
  session: DailySession
  dueAt: string
  allocatedStage: number | null
  stage: number | null
  status: 'open' | 'done'
  completedAt: string | null
  postponed: boolean
  daysLate: number
  client: string | null
  clientContact: string | null
  /** The order's country, else (not matched yet) the candidate's preferred country from the application form. */
  country: string | null
  countrySource: 'order' | 'preference' | null
  countryIsPreferred: boolean
  /** Current city/address, sent only when no country is known, so the screen can still recognise one in it. */
  locationHint: string | null
  candidate: string
  candidateRef: string
  /** The order's position, else the candidate's first preferred job, else their first skill. */
  position: string | null
  positionSource: 'order' | 'preference' | 'skill' | null
  positionIsPreferred: boolean
  orderRef: string | null
  referral: { source: string; label: string; detail: string | null }
  remark: { body: string; at: string; by: string; count: number } | null
}

export interface DailySummary {
  total: number
  done: number
  open: number
  postponed: number
  overdue: number
  morning: { done: number; total: number }
  evening: { done: number; total: number }
  nextDueAt: string | null
  oldestPostponedDays: number
  complete: boolean
}

export interface GoalBlock {
  candidates: { target: number; morning: number; evening: number; done: number; morningDone: number; eveningDone: number }
  forwardedToClient: { target: number; actual: number }
  stageMoves: { target: number; actual: number }
}

export type CheckpointState = 'met' | 'late' | 'missed' | 'upcoming'

export interface Checkpoint {
  slotNo: number
  session: DailySession
  dueAt: string
  state: CheckpointState
  completedAt: string | null
  candidate: string
}

export interface CheckpointSet {
  points: Checkpoint[]
  expectedByNow: number
  doneToday: number
  behindBy: number
}

/** Fewer candidates today than the goal, and why (so an admin can assign candidates). */
export interface Shortfall {
  target: number
  allocated: number
  reason: string
}

export interface DailyStaffSummary {
  userId: string
  name: string
  active: boolean
  offToday: boolean
  shortfall: Shortfall | null
  summary: DailySummary
  goals?: GoalBlock | null
}

export interface DailyWorkResponse {
  agencyDate: string
  serverTime: string
  dailyQuota: number
  morningQuota: number
  eveningQuota: number
  summary: DailySummary
  items: DailyItem[]
  /** Per recruiter: one person's own goals, or the average per recruiter for a team view. */
  goals: GoalBlock
  goalsMode: 'person' | 'average'
  /** In an average view: the people's targets differ, so the figures are approximate. */
  targetsVary: boolean
  /** How many recruiters the rows/goals cover. */
  staffCount: number
  /** Summed over those recruiters; only ever shown as a labelled total, never as the quota. */
  teamGoals: GoalBlock
  shortfall: Shortfall | null
  offToday: boolean
  /** Hourly checkpoints of one person's day; null when several people are shown together. */
  checkpoints: CheckpointSet | null
  staff?: DailyStaffSummary[]
}

/** The goals as the admin edits them (agency default, or one person's effective goals). */
export interface GoalValues {
  candidates: number
  morning: number
  evening: number
  forwardedToClient: number
  stageMoves: number
}

export interface GoalTable {
  default: GoalValues
  staff: { userId: string; name: string; active: boolean; goals: GoalValues; overrides: string[] }[]
}

export type DueTone = 'done' | 'ok' | 'soon' | 'over'

/** Server minus browser clock, in ms, so countdowns stay right when the PC clock is wrong. */
export function clockSkew(serverTime: string, receivedAtMs: number): number {
  const server = new Date(serverTime).getTime()
  return Number.isFinite(server) ? server - receivedAtMs : 0
}

export function msUntil(dueAt: string, nowMs: number, skewMs = 0): number {
  return new Date(dueAt).getTime() - (nowMs + skewMs)
}

const pad = (n: number) => String(n).padStart(2, '0')

/** "12m", "1h 05m", "1d 3h": the length of a span, never negative. */
export function spanLabel(ms: number): string {
  const minutes = Math.max(0, Math.floor(Math.abs(ms) / 60_000))
  if (minutes < 1) return '<1m'
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${pad(minutes % 60)}m`
  const days = Math.floor(hours / 24)
  return `${days}d ${hours % 24}h`
}

/** "in 42m" while time is left, "15m overdue" after the deadline. */
export function countdownLabel(ms: number): string {
  return ms > 0 ? `in ${spanLabel(ms)}` : `${spanLabel(ms)} overdue`
}

/** Colour state of an item's timer: red once late, amber in the last 15 minutes. */
export function dueTone(item: Pick<DailyItem, 'status' | 'dueAt'>, nowMs: number, skewMs = 0): DueTone {
  if (item.status === 'done') return 'done'
  const left = msUntil(item.dueAt, nowMs, skewMs)
  if (left <= 0) return 'over'
  return left <= 15 * 60_000 ? 'soon' : 'ok'
}

export function postponedLabel(daysLate: number): string {
  if (daysLate <= 0) return 'Postponed'
  return daysLate === 1 ? 'Postponed 1 day' : `Postponed ${daysLate} days`
}

export function sessionLabel(session: DailySession): string {
  return session === 'morning' ? 'Morning' : 'Evening'
}

/** "Morning 2/3 · Evening 0/2" for the day's own quota. */
export function sessionProgress(summary: Pick<DailySummary, 'morning' | 'evening'>): string {
  return `Morning ${summary.morning.done}/${summary.morning.total} · Evening ${summary.evening.done}/${summary.evening.total}`
}

export type PromptState = 'none' | 'done' | 'overdue' | 'postponed' | 'open'

/** What the banner on every page should say about this person's daily work. */
export function promptState(summary: DailySummary | null | undefined): PromptState {
  if (!summary || summary.total === 0) return 'none'
  if (summary.complete) return 'done'
  if (summary.overdue > 0) return 'overdue'
  if (summary.postponed > 0) return 'postponed'
  return 'open'
}

/** The banner sentence; names what is left, the late count and the backlog. */
export function promptMessage(summary: DailySummary): string {
  const left = summary.open
  const parts = [`${left} ${left === 1 ? 'candidate' : 'candidates'} left to finish today (${summary.done} of ${summary.total} done).`]
  if (summary.postponed > 0) parts.push(`${summary.postponed} carried over from earlier days.`)
  if (summary.overdue > 0) parts.push(`${summary.overdue} past ${summary.overdue === 1 ? 'its' : 'their'} hourly deadline.`)
  return parts.join(' ')
}

/** Percentage of the day's list that is finished, 0 to 100. */
export function percentDone(summary: Pick<DailySummary, 'done' | 'total'>): number {
  return summary.total ? Math.round((summary.done / summary.total) * 100) : 0
}

/** The agency-clock time of a deadline, "11:00". */
export function clockTime(iso: string, timeZone?: string): string {
  const date = new Date(iso)
  if (!Number.isFinite(date.getTime())) return '--:--'
  return new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}

/**
 * What a supervisor needs first: who has the most overdue work, then the most postponed, then the least finished.
 * Staff with nothing allocated sort last.
 */
export function staffNeedingAttention(staff: DailyStaffSummary[]): DailyStaffSummary[] {
  const score = (s: DailyStaffSummary) => s.summary.total === 0 ? -1 : s.summary.overdue * 1000 + s.summary.postponed * 10 + s.summary.open
  return [...staff].sort((a, b) => score(b) - score(a) || a.name.localeCompare(b.name))
}

/** Referral column text: the referrer when someone referred the candidate, otherwise how the CV arrived. */
export function referralLabel(referral: DailyItem['referral']): string {
  if (referral.source === 'referral') return referral.detail?.trim() || 'Referral'
  return referral.label
}

/** Share of a goal reached, 0 to 100; a goal of 0 means "no goal" and shows an empty bar. */
export function goalPercent(actual: number, target: number): number {
  return target > 0 ? Math.min(100, Math.round((actual / target) * 100)) : 0
}

export type GoalState = 'none' | 'done' | 'progress'

export function goalState(actual: number, target: number): GoalState {
  if (target <= 0) return 'none'
  return actual >= target ? 'done' : 'progress'
}

/** One sentence on whether the hourly checkpoints are being met: nothing to say before the first deadline. */
export function pacingMessage(cp: CheckpointSet | null | undefined): string {
  if (!cp || cp.points.length === 0 || cp.expectedByNow === 0) return ''
  if (cp.behindBy > 0) {
    return `Behind the hourly pace: ${cp.expectedByNow} ${cp.expectedByNow === 1 ? 'candidate should' : 'candidates should'} be finished by now, ${cp.doneToday} done.`
  }
  return cp.doneToday > cp.expectedByNow ? `Ahead of the hourly pace: ${cp.doneToday} done, ${cp.expectedByNow} due by now.` : 'On the hourly pace.'
}

/** Milliseconds until the next open deadline passes, so the screen can re-read exactly when something turns late. */
export function nextCrossing(items: Pick<DailyItem, 'status' | 'dueAt'>[], nowMs: number, skewMs = 0): number | null {
  let best: number | null = null
  for (const item of items) {
    if (item.status !== 'open') continue
    const left = msUntil(item.dueAt, nowMs, skewMs)
    if (left > 0 && (best === null || left < best)) best = left
  }
  return best
}

export function checkpointLabel(state: CheckpointState): string {
  return { met: 'On time', late: 'Finished late', missed: 'Missed', upcoming: 'Coming up' }[state]
}

/** "3 of 10 done across 2 staff" for a team, "3 of 5 done" for one person. */
export function progressLine(summary: Pick<DailySummary, 'done' | 'total'>, staffCount: number, team: boolean): string {
  const base = `${summary.done} of ${summary.total} done`
  return team ? `${base} across ${staffCount} ${staffCount === 1 ? 'staff member' : 'staff'}` : base
}

/** The quota card: what each recruiter gets, never the sum. */
export function quotaLine(quota: { daily: number; morning: number; evening: number }, varies: boolean): { value: string; detail: string } {
  return {
    value: `${varies ? '~' : ''}${quota.daily}`,
    detail: `${quota.morning} morning · ${quota.evening} evening each${varies ? ' (goals differ by person)' : ''}`,
  }
}

/** Small secondary text under the quota: the summed team figure, clearly a total. */
export function teamTotalLine(totalCandidates: number, staffCount: number): string {
  return `${totalCandidates} across ${staffCount} ${staffCount === 1 ? 'recruiter' : 'recruiters'} today`
}

/** One decimal at most, no trailing ".0": averages read as "1.5" and whole numbers as "2". */
export function fmtCount(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

/** Staff board cell for a person's candidate count, with the reason when it is short or empty. */
export function candidatesCell(summary: Pick<DailySummary, 'done' | 'total'>, shortfall: Shortfall | null, offToday: boolean): { text: string; warn: boolean; note: string } {
  if (offToday) return { text: '-', warn: false, note: 'Day off' }
  if (summary.total === 0) return { text: '0', warn: true, note: shortfall?.reason ?? 'No open candidates to allocate' }
  return { text: `${summary.done}/${summary.total}`, warn: !!shortfall, note: shortfall ? `Only ${shortfall.allocated} of ${shortfall.target} new candidates today` : '' }
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
