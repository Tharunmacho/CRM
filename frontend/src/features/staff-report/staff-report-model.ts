/**
 * Weekly staff report: the shapes `GET /api/reports/staff-weekly` returns and the pure helpers the screen uses
 * (week arithmetic, labels, sorting, tones). No imports, so it runs under node:test.
 */

export type DayState = 'present' | 'absent' | 'leave' | 'off' | 'pending' | 'untracked' | 'future'
export type StaffStatus = 'worked' | 'no_attendance' | 'pending' | 'not_scheduled'
export type Severity = 'high' | 'medium' | 'low'

export interface DayRow {
  date: string
  state: DayState
  status: string | null
  workedSeconds: number
  activeSeconds: number
  firstIn: string | null
  lastOut: string | null
  missingCheckout: boolean
  expectedMinutes: number
  tasks: { assigned: number; done: number; missed: number }
  stageMoves: number
}

export interface ScorePart { key: 'tasks' | 'hours' | 'stages'; label: string; weight: number; ratio: number | null; detail: string }

export interface StaffRow {
  id: string
  name: string
  email: string
  active: boolean
  status: StaffStatus
  attendance: {
    workedDays: number; absentDays: number; leaveDays: number; offDays: number; pendingDays: number; absentDates: string[]
    hoursWorked: number; activeHours: number; expectedHours: number; hoursPercent: number | null; missingCheckouts: number
  }
  tasks: {
    available: boolean; assigned: number; completed: number; onTime: number; postponed: number; overdue: number; backlog: number
    oldestBacklogDays: number; clearedBacklog: number; completionPercent: number | null; escalations: number; missedUpdates: number
  }
  stages: {
    inStageNow: Record<string, number>; movesByStage: Record<string, number>; moves: number; reached: Record<string, number>
    activeCandidates: number; closed: number; deployedTotal: number
  }
  outcomes: {
    forwarded: number; interviewsScheduled: number; selected: number; visaReceived: number; visaStamped: number; deployed: number
    interviewRejected: number
  }
  stuck: { count: number; oldestDays: number; examples: { candidateId: string; candidate: string; ref: string; stage: number; stageLabel: string; days: number }[] }
  goals: { candidatesPerDay: number; forwardedPerDay: number; stageMovesPerDay: number; scheduledDays: number }
  score: number | null
  scoreParts: ScorePart[]
  days: DayRow[]
}

export interface FunnelRow {
  stage: number
  label: string
  reached: number
  inStageNow: number
  closedHere: number
  movesInPeriod: number
  conversionPercent: number | null
  lostFromPrevious: number | null
}

export interface Highlight { id: string; kind: string; severity: Severity; staffId: string; staffName: string; title: string; detail: string; value: number | null }

export interface CandidateRow {
  applicationId: string
  candidateId: string
  candidate: string
  ref: string
  order: string
  designation: string
  client: string
  status: 'active' | 'closed' | 'deployed'
  closureReason: string | null
  stage: number
  stageLabel: string
  reached: number
  since: string
  daysInStage: number | null
  movedInRange: number
  history: { stage: number; outcome: string | null; at: string }[]
}

export interface StaffWeeklyReport {
  generatedAt: string
  timezone: string
  range: { from: string; to: string; days: string[]; isWeek: boolean; today: string; isCurrent: boolean }
  formula: { weights: Record<string, number>; lines: string[] }
  thresholds: { stuckDays: number; lowCompletionPercent: number; lowHoursPercent: number; backlogMin: number }
  sources: { dailyTasks: { available: boolean; hasData: boolean; goals: Record<string, number> } }
  summary: {
    staffTotal: number; staffWorked: number; staffAbsent: number; staffPending: number; staffNotScheduled: number
    hoursWorked: number; hoursExpected: number; activeHours: number
    tasksAssigned: number; tasksCompleted: number; tasksPostponed: number; tasksOverdue: number; tasksAvailable: boolean
    taskCompletionPercent: number | null; stageMoves: number
    forwarded: number; interviewsScheduled: number; selected: number; visaReceived: number; visaStamped: number; deployed: number
    stuckCandidates: number; averageScore: number | null; highlights: number
  }
  staff: StaffRow[]
  funnel: FunnelRow[]
  highlights: Highlight[]
  notes: string[]
  candidates?: { total: number; shown: number; items: CandidateRow[] }
  moves?: { at: string; stage: number; stageLabel: string; outcome: string | null; candidateId: string; candidate: string; ref: string }[]
}

/* ------------------------------------------------------------------ weeks */

const DAY = 86_400_000

export function validDay(value: string | null | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T12:00:00Z`)
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value
}

export function addDays(day: string, days: number): string {
  return new Date(Date.parse(`${day}T12:00:00Z`) + days * DAY).toISOString().slice(0, 10)
}

/** Monday of the week that contains `day`. */
export function mondayOf(day: string): string {
  const weekday = (new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7 // Monday = 0
  return addDays(day, -weekday)
}

export function weekOf(day: string): { from: string; to: string } {
  const from = mondayOf(day)
  return { from, to: addDays(from, 6) }
}

/** Previous (-1) or next (+1) week of a Monday-to-Sunday range; a custom range moves by its own length. */
export function shiftRange(range: { from: string; to: string }, direction: -1 | 1): { from: string; to: string } {
  const length = Math.round((Date.parse(`${range.to}T12:00:00Z`) - Date.parse(`${range.from}T12:00:00Z`)) / DAY) + 1
  return { from: addDays(range.from, direction * length), to: addDays(range.to, direction * length) }
}

export function isWholeWeek(range: { from: string; to: string }): boolean {
  return mondayOf(range.from) === range.from && addDays(range.from, 6) === range.to
}

const monthDay = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })
const withYear = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
export const weekdayName = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString('en-GB', { weekday: 'short', timeZone: 'UTC' })

export function rangeLabel(range: { from: string; to: string }): string {
  return range.from === range.to ? withYear(range.from) : `${monthDay(range.from)} to ${withYear(range.to)}`
}

/* ------------------------------------------------------------------ labels */

export function formatHours(hours: number | null | undefined): string {
  if (hours == null) return '-'
  return `${hours.toLocaleString('en-GB', { maximumFractionDigits: 1 })} h`
}

export function formatSeconds(seconds: number): string {
  if (!seconds) return '0 h'
  const minutes = Math.round(seconds / 60)
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return h ? (m ? `${h} h ${m} m` : `${h} h`) : `${m} m`
}

export function formatPercent(value: number | null | undefined): string {
  return value == null ? '-' : `${value.toLocaleString('en-GB', { maximumFractionDigits: 1 })}%`
}

export function clockTime(iso: string | null, timeZone?: string): string {
  return iso ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone }) : ''
}

export const STATE_LABEL: Record<DayState, string> = {
  present: 'Present', absent: 'Absent', leave: 'On leave', off: 'Day off', pending: 'Not in yet', untracked: 'Not tracked', future: 'Upcoming',
}

export const STATUS_LABEL: Record<StaffStatus, string> = {
  worked: 'Worked', no_attendance: 'Did not work', pending: 'Not in yet', not_scheduled: 'Not scheduled',
}

export type Tone = 'good' | 'warn' | 'bad' | 'muted'

export function statusTone(status: StaffStatus): Tone {
  return status === 'worked' ? 'good' : status === 'no_attendance' ? 'bad' : 'muted'
}

export function stateTone(state: DayState): Tone {
  return state === 'present' ? 'good' : state === 'absent' ? 'bad' : state === 'pending' ? 'warn' : 'muted'
}

/** Score bands: 75 and over is good, 50 to 74 needs a look, below 50 is a conversation. */
export function scoreTone(score: number | null): Tone {
  return score == null ? 'muted' : score >= 75 ? 'good' : score >= 50 ? 'warn' : 'bad'
}

export function severityRank(severity: Severity): number {
  return severity === 'high' ? 0 : severity === 'medium' ? 1 : 2
}

/* ------------------------------------------------------------------ sorting */

export type SortKey = 'name' | 'score' | 'hours' | 'completion' | 'moves' | 'forwarded' | 'visa' | 'deployed' | 'stuck' | 'absent'

const sortValue: Record<SortKey, (row: StaffRow) => number | string> = {
  name: row => row.name.toLowerCase(),
  score: row => row.score ?? -1,
  hours: row => row.attendance.hoursWorked,
  completion: row => row.tasks.completionPercent ?? -1,
  moves: row => row.stages.moves,
  forwarded: row => row.outcomes.forwarded,
  visa: row => row.outcomes.visaReceived,
  deployed: row => row.outcomes.deployed,
  stuck: row => row.stuck.count,
  absent: row => row.attendance.absentDays,
}

export function sortStaff(rows: StaffRow[], key: SortKey, direction: 'asc' | 'desc'): StaffRow[] {
  const factor = direction === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    const left = sortValue[key](a)
    const right = sortValue[key](b)
    const order = typeof left === 'string' || typeof right === 'string' ? String(left).localeCompare(String(right)) : left - right
    return order * factor || a.name.localeCompare(b.name)
  })
}

/* ------------------------------------------------------------------ candidates and funnel */

export type CandidateFilter = 'all' | 'active' | 'stuck' | 'moved' | 'closed' | 'deployed'

export function filterCandidates(items: CandidateRow[], filter: CandidateFilter, stuckDays: number): CandidateRow[] {
  switch (filter) {
    case 'active': return items.filter(item => item.status === 'active')
    case 'stuck': return items.filter(item => item.status === 'active' && (item.daysInStage ?? 0) >= stuckDays)
    case 'moved': return items.filter(item => item.movedInRange > 0)
    case 'closed': return items.filter(item => item.status === 'closed')
    case 'deployed': return items.filter(item => item.status === 'deployed')
    default: return items
  }
}

/** Width (0 to 100) of a funnel bar relative to the first stage, with a sliver so a non-zero stage never vanishes. */
export function funnelWidth(reached: number, first: number): number {
  if (!first || !reached) return 0
  return Math.max(2, Math.min(100, Math.round((reached / first) * 100)))
}

export const CLOSURE_LABEL: Record<string, string> = {
  prescreen_failed: 'Failed prescreening', not_interested: 'Not interested', resume_not_received: 'Resume not received',
  interview_rejected: 'Rejected at interview', medical_unfit: 'Medically unfit', unmapped: 'Removed from the order',
}

/** The "x of y" text used for hours and tasks cells. */
export function ofText(done: number, total: number): string {
  return `${done} of ${total}`
}

/** True when the screen should treat a result as showing real task data (the tables exist and rows were allocated). */
export function hasTaskData(report: Pick<StaffWeeklyReport, 'sources'>): boolean {
  return report.sources.dailyTasks.available && report.sources.dailyTasks.hasData
}
