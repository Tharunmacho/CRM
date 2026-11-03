/**
 * Daily work quota: the shapes the API returns and the pure rules the screen and the "complete your tasks first"
 * banner share (countdowns, due tones, progress wording, who still has work). No imports, so it runs under node:test.
 */

export type DailySession = 'morning' | 'evening'

export interface DailyItem {
  id: string
  taskId: string
  applicationId: string
  candidateId: string
  ownerId: string
  /** Agency-timezone day the item was allocated, YYYY-MM-DD. */
  workDate: string
  slotNo: number
  session: DailySession
  dueAt: string
  allocatedStage: number | null
  stage: number
  status: 'open' | 'done'
  completedAt: string | null
  postponed: boolean
  daysLate: number
  client: string
  clientContact: string
  country: string | null
  candidate: string
  candidateRef: string
  position: string
  orderRef: string
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

export interface DailyStaffSummary {
  userId: string
  name: string
  active: boolean
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
  goals: GoalBlock
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
