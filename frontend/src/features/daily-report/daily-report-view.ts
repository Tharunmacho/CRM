/**
 * Pure helpers of the Reports page: date presets in the agency's time, the column model of the three tables
 * (Daily report, Lead sheet, Stages), the Total row and its "vs yesterday" text, the day-close and partial-period
 * lines, queries, paging and the CSV of a drill-down list. No React, no imports except types, so node can test it as it
 * is (tests/daily-report-view.test.mjs).
 */
import type { DailyMetric, DailyPeriod, DailyQuery, DailyReport, DrillRow, Figures, LeadSheetSummary, MetricKey, MyDay, StaffRow } from './daily-report-types'

/** The agency works in India; the server's own zone (bootstrap agencyTimezone) overrides this wherever the screen has it. */
export const AGENCY_TIMEZONE = 'Asia/Kolkata'

/** The seven recruitment figures in the order the business reads them, from the first screening to a worker who has flown. */
export const METRIC_ORDER: readonly MetricKey[] = ['prescreened', 'forwarded', 'shortlisted', 'interviews', 'offers', 'visas', 'travelled']
export const LEADS_KEY: MetricKey = 'leadsContacted'
export const ALL_METRIC_KEYS: readonly MetricKey[] = [LEADS_KEY, ...METRIC_ORDER]

/** Used only when the server did not send a label. */
export const METRIC_LABELS: Record<MetricKey, string> = {
  leadsContacted: 'Leads contacted',
  prescreened: 'Resumes prescreened',
  forwarded: 'Resumes forwarded to clients',
  shortlisted: 'Workers shortlisted by clients',
  interviews: 'Interviews conducted',
  offers: 'Offer letters received',
  visas: 'Visas received',
  travelled: 'Workers travelled',
}
export const METRIC_SHORT: Record<MetricKey, string> = {
  leadsContacted: 'Contacted', prescreened: 'Prescreened', forwarded: 'Forwarded', shortlisted: 'Shortlisted',
  interviews: 'Interviews', offers: 'Offers', visas: 'Visas', travelled: 'Travelled',
}

/* ---------------------------------------------------------------- dates (agency time) */

export const isDay = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T12:00:00Z`)) && new Date(`${value}T12:00:00Z`).toISOString().slice(0, 10) === value

/** The calendar day (YYYY-MM-DD) of a moment in a time zone: the agency day, never the browser's own. */
export function dayInZone(value: Date | string | number, timeZone: string = AGENCY_TIMEZONE): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value))
}

export function shiftDay(day: string, days: number): string {
  const date = new Date(`${day}T12:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const weekday = (day: string) => new Date(`${day}T12:00:00Z`).getUTCDay()

/** The period buttons. `range` is not a button: it is a range named in an older link, shown as it is with no button pressed. */
export type RangePreset = 'today' | 'yesterday' | 'week' | 'month' | 'total' | 'range'
export const PRESETS: { key: Exclude<RangePreset, 'range'>; label: string }[] = [
  { key: 'today', label: 'Today' }, { key: 'yesterday', label: 'Yesterday' }, { key: 'week', label: 'This week' }, { key: 'month', label: 'This month' }, { key: 'total', label: 'Total' },
]
export interface RangeChoice { preset: RangePreset; from: string; to: string }

/** The server holds a range of at most 366 days; Total asks it for everything (period=all) and, if it cannot, gets this many days. */
export const FALLBACK_DAYS = 366

/** Weeks start on Monday; "this week" and "this month" run up to today. */
export function presetRange(preset: Exclude<RangePreset, 'range' | 'total'>, today: string): { from: string; to: string } {
  if (preset === 'today') return { from: today, to: today }
  if (preset === 'yesterday') { const day = shiftDay(today, -1); return { from: day, to: day } }
  if (preset === 'week') return { from: shiftDay(today, -((weekday(today) + 6) % 7)), to: today }
  return { from: `${today.slice(0, 8)}01`, to: today }
}

/** A range whose ends are the wrong way round is put right; a day in the future is held at today. */
export function fixCustomRange(from: string, to: string, today: string): { from: string; to: string } {
  let a = isDay(from) ? from : today
  let b = isDay(to) ? to : today
  if (a > today) a = today
  if (b > today) b = today
  return a <= b ? { from: a, to: b } : { from: b, to: a }
}

/**
 * The dates the screen works with: a preset follows today, a range from an older link is kept as it was. Total is "everything":
 * the query then says period=all and ignores these dates (see apiQuery); `allOk` false means the server refused that, so Total
 * becomes the last 366 days.
 */
export function resolveRange(choice: RangeChoice, today: string, allOk = true): { from: string; to: string } {
  if (choice.preset === 'range') return fixCustomRange(choice.from, choice.to, today)
  if (choice.preset === 'total') return { from: allOk ? '2000-01-01' : shiftDay(today, -(FALLBACK_DAYS - 1)), to: today }
  return presetRange(choice.preset, today)
}
/** Total, and the server has not refused it: the report is asked for with period=all. */
export const asksAllTime = (choice: RangeChoice, allOk = true) => choice.preset === 'total' && allOk

export const DEFAULT_CHOICE: RangeChoice = { preset: 'today', from: '', to: '' }

/** What was saved in this browser (a JSON string) turned back into a choice; anything odd, and a saved Custom range from before, gives Today. */
export function parseChoice(raw: string | null | undefined): RangeChoice {
  if (!raw) return DEFAULT_CHOICE
  try {
    const value = JSON.parse(raw) as Partial<RangeChoice> | null
    const preset = PRESETS.find(item => item.key === value?.preset)?.key
    return preset ? { preset, from: '', to: '' } : DEFAULT_CHOICE
  } catch { return DEFAULT_CHOICE }
}
export const serialiseChoice = (choice: RangeChoice) => JSON.stringify(choice)

/** A range named in the address (an older "View staff report" link carries ?from=&to=&staff=): a custom range, or nothing. */
export function choiceFromParams(from: string | null, to: string | null, today: string): RangeChoice | null {
  return isDay(from) && isDay(to) ? { preset: 'range', ...fixCustomRange(from, to, today) } : null
}

/** "Mon 17 Nov" and, when the year is not this one, "Mon 17 Nov 2025". */
export function dayLabel(day: string, today?: string): string {
  if (!isDay(day)) return ''
  const year = today && day.slice(0, 4) !== today.slice(0, 4) ? ` ${day.slice(0, 4)}` : ''
  return `${WEEKDAYS[weekday(day)]} ${Number(day.slice(8))} ${MONTHS[Number(day.slice(5, 7)) - 1]}${year}`
}
/** "17 Nov" for a short label. */
export function shortDay(day: string): string { return isDay(day) ? `${Number(day.slice(8))} ${MONTHS[Number(day.slice(5, 7)) - 1]}` : day }

export function rangeLabel(from: string, to: string, today?: string): string {
  return from === to ? dayLabel(from, today) : `${dayLabel(from, today)} to ${dayLabel(to, today)}`
}
export const isSingleDay = (from: string, to: string) => from === to
/** A recruiter can submit for today or yesterday, no other day. */
export const canSubmitDay = (day: string, today: string) => day === today || day === shiftDay(today, -1)

/** The clock time of a moment in the agency's zone: "18:42". */
export function clockTime(value: string | Date | null | undefined, timeZone: string = AGENCY_TIMEZONE): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date)
}

export const pctText = (value: number | null | undefined) => (value == null || !Number.isFinite(value) ? '–' : `${Math.round(value)}%`)
export const numberText = (value: number | null | undefined) => (value == null || !Number.isFinite(value) ? '–' : Math.round(value).toLocaleString('en-IN'))

/* ---------------------------------------------------------------- the column model */

export interface TableCol {
  id: string
  label: string
  /** The definition, shown as the title of the heading. */
  info?: string
  /** The drill-down metric a number of this column opens; none means the number is plain text. */
  drill?: string
  /** Shown bold in the strongest text colour (overdue tasks), never in a warning colour. */
  strong?: boolean
  /** Width in px in the table's shared column template (header and every row use it). */
  width?: number
  /** The heading over the numbers in the table; the summary band above it uses the longer `label`. */
  head?: string
}
export type Cells = Record<string, number | null>

const col = (id: string, label: string, extra: Partial<TableCol> = {}): TableCol => ({ id, label, ...extra })

const LEAD_INFO = {
  contacted: 'Distinct leads the person called, or whose status they changed, in the period.',
  interested: 'Of the leads contacted, those whose last answer in the period was interested (or a resume was asked for or received).',
  notInterested: 'Of the leads contacted, those whose last answer in the period was not interested.',
  noResponse: 'Of the leads contacted, those with a wrong number, no answer or not reachable.',
}
const TASK_INFO = {
  done: 'Tasks the person completed in the period.',
  pending: 'Open tasks of the person that are not yet due.',
  overdue: 'Open tasks of the person that are past their due time.',
}

/**
 * The Daily CRM Work Report, exactly as the client listed it and in this order. The summary band above the table uses the
 * full wording; the table's own headings are the short form.
 */
export const DAILY_ORDER = ['forwarded', 'prescreened', 'shortlisted', 'interviewsOnline', 'offers', 'visas', 'travelled'] as const
export type DailyKey = typeof DAILY_ORDER[number]
export function dailyLabel(key: DailyKey, todayOnly: boolean): string {
  const labels: Record<DailyKey, string> = {
    forwarded: 'Resumes forwarded to clients', prescreened: todayOnly ? 'Resumes prescreened today' : 'Resumes prescreened',
    shortlisted: 'Workers shortlisted by clients', interviewsOnline: 'Online interviews conducted',
    offers: 'Offer letters received', visas: 'Visas received', travelled: 'Workers travelled',
  }
  return labels[key]
}
const DAILY_HEAD: Record<DailyKey, string> = { forwarded: 'Forwarded', prescreened: 'Prescreened', shortlisted: 'Shortlisted', interviewsOnline: 'Online interviews', offers: 'Offers', visas: 'Visas', travelled: 'Travelled' }
/** `todayOnly`: the period is the single day today, so the second figure says "today". */
export function dailyCols(metrics: DailyMetric[] = [], todayOnly = false): TableCol[] {
  const info = (key: MetricKey) => metrics.find(item => item.key === key)?.definition ?? ''
  return DAILY_ORDER.map((key): TableCol => key === 'interviewsOnline'
    ? { id: key, label: dailyLabel(key, todayOnly), head: DAILY_HEAD[key], info: 'Interviews conducted whose mode was recorded as online when the interview was scheduled. In person interviews, and any with no mode recorded, are not counted here.', drill: 'interviews' }
    : { id: key, label: dailyLabel(key, todayOnly), head: DAILY_HEAD[key], info: info(key), drill: key })
}
export const DAY_CLOSED_COL: TableCol = { id: 'closed', label: 'Day closed' }
const sum = (...values: (number | undefined)[]) => values.reduce<number>((total, value) => total + (value ?? 0), 0)

/** One person's numbers for the Daily report: the seven figures of the client's list. */
export function dailyCells(row: StaffRow): Cells {
  const cells: Cells = {}
  for (const key of DAILY_ORDER) cells[key] = key === 'interviewsOnline' ? row.interviewsOnline ?? 0 : row.metrics[key] ?? 0
  return cells
}

/** The Total row: the server's own totals for the team (online interviews from extra), else the sum of the rows. */
export function totalCells(report: DailyReport, rows: StaffRow[]): Cells {
  const cells: Cells = {}
  for (const key of DAILY_ORDER) {
    const metric = key === 'interviewsOnline' ? undefined : report.metrics.find(item => item.key === key)
    cells[key] = key === 'interviewsOnline' ? report.extra?.interviewsOnline ?? sum(...rows.map(row => row.interviewsOnline)) : metric ? metric.value : sum(...rows.map(row => row.metrics[key]))
  }
  return cells
}

/**
 * Interviews conducted that are not counted as online (held in person, or with no mode recorded). The server counts
 * online by the mode chosen when the interview was scheduled. Empty when there are none.
 */
export function interviewsNotOnline(report: DailyReport): number {
  const all = report.metrics.find(item => item.key === 'interviews')?.value ?? 0
  return Math.max(0, all - (report.extra?.interviewsOnline ?? 0))
}
export function interviewsNote(report: DailyReport): string {
  const n = interviewsNotOnline(report)
  if (n <= 0) return ''
  return `${n.toLocaleString('en-IN')} ${n === 1 ? 'interview was' : 'interviews were'} recorded without a mode and ${n === 1 ? 'is' : 'are'} not counted as online.`
}

export interface Vs { kind: 'up' | 'down' | 'flat'; text: string }
/** "+3" / "−2" / "0" against the previous period. */
export function vsOf(value: number, previous: number): Vs {
  const diff = Math.round(value - previous)
  return diff > 0 ? { kind: 'up', text: `+${diff}` } : diff < 0 ? { kind: 'down', text: `−${Math.abs(diff)}` } : { kind: 'flat', text: '0' }
}
/** The change against the previous period for the Total row only. Online interviews have no previous value of their own. */
export function totalVs(metrics: DailyMetric[]): Record<string, Vs> {
  const out: Record<string, Vs> = {}
  for (const key of DAILY_ORDER) {
    if (key === 'interviewsOnline') continue
    const metric = metrics.find(item => item.key === key)
    if (metric && metric.previous != null) out[key] = vsOf(metric.value, metric.previous)
  }
  return out
}
/** The muted line under a band total: "−25 vs yesterday", "+1 vs yesterday", "no change". */
export function changeLine(vs: Vs | undefined, label: string): string {
  if (!vs) return ''
  return vs.kind === 'flat' ? 'no change' : `${vs.text} vs ${label}`
}
/** What the Total row compares with: "yesterday" for today, otherwise the matching earlier period. */
export function vsLabel(period: Pick<DailyPeriod, 'from' | 'to'>, today: string): string {
  if (period.from !== period.to) return 'previous period'
  return period.to === today ? 'yesterday' : 'day before'
}
/** The footer of the table: who has not submitted yet (one day, supervisors). Empty when there is nothing to say. */
export function dayCloseFoot(compliance: { expected: number; missing: { name: string }[] } | null, show = 8): string {
  if (!compliance || compliance.expected <= 0) return ''
  if (!compliance.missing.length) return 'everyone has submitted.'
  const names = compliance.missing.slice(0, show).map(person => person.name).join(', ')
  const more = compliance.missing.length - show
  return `not submitted yet — ${names}${more > 0 ? ` and ${more} more` : ''}`
}
/** The hover text of the Day closed tile: how many recruiters submitted and who has not (one day, supervisors). */
export function dayCloseTitle(compliance: { expected: number; submitted: number; missing: { name: string }[] } | null): string {
  if (!compliance || compliance.expected <= 0) return ''
  const head = `${compliance.submitted} of ${compliance.expected} recruiters submitted`
  const foot = dayCloseFoot(compliance)
  return compliance.missing.length ? `${head}. Not submitted yet \u2014 ${foot.replace(/^not submitted yet \u2014 /, '')}` : head
}

/* ---------------------------------------------------------------- Lead sheet tab */

/** Whether the server sends the period-aware lead figures (newInPeriod and friends); older servers get the all-time ones. */
export const hasLeadPeriod = (report: Pick<DailyReport, 'leadSheet'>) => report.leadSheet?.newInPeriod !== undefined
/** Leads nobody owns, whichever shape the server sends (a count, or the count with its never-contacted and new-in-period parts). */
export function unassignedInfo(sheet: LeadSheetSummary | undefined): { total: number; neverContacted: number | null; newInPeriod: number | null } | null {
  if (!sheet) return null
  const u = sheet.unassigned
  if (typeof u === 'number') return { total: u, neverContacted: null, newInPeriod: null }
  return u ? { total: u.total, neverContacted: u.neverContacted, newInPeriod: u.newInPeriod } : null
}
/** The Not contacted figure that ties to the Lead sheet screen: its own rule when the server sends it, else the period's. */
export const neverContactedTotal = (sheet: LeadSheetSummary | undefined) => sheet?.neverContacted ?? sheet?.notContactedInPeriod
/** The Lead sheet columns: Assigned and Not contacted are the period's own figures when the server sends them. */
export function leadCols(report?: Pick<DailyReport, 'leadSheet'>): TableCol[] {
  const period = !!report && hasLeadPeriod(report)
  return [
    col('assigned', 'Assigned', period
      ? { info: 'Leads given to the person in the period.', drill: 'leadsAssignedInPeriod' }
      : { info: 'Open leads on the lead sheet that are assigned to the person right now.', drill: 'leadsAssigned' }),
    col('contacted', 'Contacted', { info: LEAD_INFO.contacted, drill: LEADS_KEY }),
    col('interested', 'Interested', { info: LEAD_INFO.interested }),
    col('callback', 'Call back', { info: 'Of the leads contacted, those that still have a follow-up date.' }),
    col('notInterested', 'Not interested', { info: LEAD_INFO.notInterested }),
    col('wrongNumber', 'Wrong number', { info: 'Of the leads contacted, those with a wrong number.' }),
    col('noResponse', 'No answer / not reachable', { head: 'No response', info: 'Of the leads contacted, those who did not answer or could not be reached.' }),
    col('notContacted', 'Not contacted', { info: period ? 'Leads the person owns that have never been contacted (the same count as the Lead sheet screen).' : 'Leads the person holds that were not contacted in the period.' }),
  ]
}
export const LEAD_PCT_COL: TableCol = { id: 'pct', label: 'Contacted %', info: 'Leads contacted in the period divided by the leads the person holds now, never above 100%.' }

export function leadCells(row: StaffRow): Cells {
  const l = row.leads
  const fb = row.leadFeedback
  // `workload` (the leads held now) is not a column: it is what Contacted % divides by.
  if (l) return { assigned: l.assignedInPeriod ?? l.assigned, contacted: l.contacted, interested: l.interested, callback: l.callback, notInterested: l.notInterested, wrongNumber: l.wrongNumber, noResponse: sum(l.noAnswer, l.notReachable), notContacted: l.neverContacted ?? l.notContactedInPeriod ?? l.notContacted, workload: l.assigned }
  return { assigned: null, contacted: fb?.contacted ?? row.metrics.leadsContacted ?? 0, interested: fb?.interested ?? null, callback: fb?.callback ?? null, notInterested: fb?.notInterested ?? null, wrongNumber: fb?.wrongNumber ?? null, noResponse: fb ? fb.noAnswer : null, notContacted: null }
}
export function sumCells(list: Cells[]): Cells {
  const out: Cells = {}
  for (const id of Object.keys(list[0] ?? {})) {
    const values = list.map(cells => cells[id])
    out[id] = values.some(value => value != null) ? values.reduce<number>((total, value) => total + (value ?? 0), 0) : null
  }
  return out
}
/** Contacted as a share of assigned, held at 100% (a person can contact a lead they no longer hold). null without an assigned count. */
export function contactedPct(assigned: number | null | undefined, contacted: number | null | undefined): number | null {
  if (!assigned || assigned <= 0 || contacted == null) return null
  return Math.min(100, Math.round((contacted / assigned) * 100))
}
export interface StatusRow { key: string; label: string; count: number; pct: number }
export function statusRows(sheet: LeadSheetSummary | undefined): StatusRow[] {
  if (!sheet) return []
  const total = sheet.totalLeads > 0 ? sheet.totalLeads : sheet.byStatus.reduce((all, item) => all + item.count, 0)
  return sheet.byStatus.map(item => ({ key: item.key, label: item.label, count: item.count, pct: total > 0 ? Math.round((item.count / total) * 100) : 0 }))
}

/* ---------------------------------------------------------------- Stages tab */

export interface StageCol extends TableCol { no: number }
/** Columns "No order" then the stage numbers 1 to 12; the names are in the legend above the table and in each heading's title. */
export function stageColumns(report: DailyReport): StageCol[] {
  const totals = report.stages?.totals ?? []
  return [...totals].sort((a, b) => a.no - b.no).map(item => ({
    no: item.no, id: String(item.no), label: item.no === 0 ? 'No order' : String(item.no),
    info: item.no === 0 ? 'Not on an order yet: candidates who are not matched to an order.' : `Stage ${item.no}: ${item.label}. Candidates whose current stage this is.`,
    drill: `stage:${item.no}`, width: item.no === 0 ? 92 : 56,
  }))
}
export const STAGE_TOTAL_COL: TableCol = { id: 'total', label: 'Total', info: 'All the candidates this person holds.', width: 84 }
export const TASK_COLS: TableCol[] = [
  col('done', 'Done', { info: TASK_INFO.done, drill: 'tasksDone', width: 84 }),
  col('pending', 'Pending', { info: TASK_INFO.pending, drill: 'tasksPending', width: 84 }),
  col('overdue', 'Overdue', { info: TASK_INFO.overdue, drill: 'tasksOverdue', strong: true, width: 84 }),
]
/** Tasks, then the candidates at each stage right now (No order, then 1 to 12): one flat set of columns. */
export function taskStageCols(report: DailyReport): TableCol[] {
  return [...TASK_COLS, ...stageColumns(report)]
}
export interface TaskStageRow { id: string; name: string; cells: Cells }
/** One row per person with tasks or candidates: task counts from the person's row, stage counts from the stage breakdown. */
export function taskStageRows(report: DailyReport): TaskStageRow[] {
  const perStaff = new Map((report.stages?.perStaff ?? []).map(item => [item.id, item]))
  const known = new Set(report.staff.map(row => row.id))
  const rows: TaskStageRow[] = report.staff.map(person => {
    const stage = perStaff.get(person.id)
    const cells: Cells = { done: person.tasks?.done ?? null, pending: person.tasks?.pending ?? null, overdue: person.tasks?.overdue ?? null }
    for (const [no, count] of Object.entries(stage?.counts ?? {})) cells[no] = count
    cells.total = stage ? stage.total : null
    return { id: person.id, name: person.name, cells }
  })
  for (const item of report.stages?.perStaff ?? []) {
    if (known.has(item.id)) continue
    rows.push({ id: item.id, name: 'Other', cells: { done: null, pending: null, overdue: null, ...item.counts, total: item.total } })
  }
  return rows.filter(row => Object.values(row.cells).some(value => value != null && value > 0)).sort((a, b) => (b.cells.total ?? 0) - (a.cells.total ?? 0) || a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
}
export function taskStageTotals(report: DailyReport): Cells {
  const cells: Cells = {}
  const t = report.tasks
  const people = report.staff.filter(row => row.tasks)
  cells.done = t ? t.done : people.length ? sum(...people.map(row => row.tasks!.done)) : null
  cells.pending = t ? t.pending : people.length ? sum(...people.map(row => row.tasks!.pending)) : null
  cells.overdue = t ? t.overdue : people.length ? sum(...people.map(row => row.tasks!.overdue)) : null
  for (const item of report.stages?.totals ?? []) cells[String(item.no)] = item.count
  cells.total = report.stages ? report.stages.totals.reduce((all, item) => all + item.count, 0) : null
  return cells
}
/** "N candidates in progress" counts stages 1 to 12; those not on an order yet are named beside it. */
export function stageSummary(report: DailyReport): { inProgress: number; notOnOrder: number } {
  const totals = report.stages?.totals ?? []
  return { inProgress: totals.filter(item => item.no >= 1).reduce((all, item) => all + item.count, 0), notOnOrder: totals.find(item => item.no === 0)?.count ?? 0 }
}

/** The title of a drill-down list when the server has not named it yet. */
export function drillTitle(metric: string, report?: DailyReport | null): string {
  if (metric.startsWith('stage:')) {
    const no = Number(metric.slice(6))
    const found = report?.stages?.totals.find(item => item.no === no)
    return no === 0 ? 'Not on an order yet' : found ? `Stage ${no}: ${found.label}` : `Stage ${no}`
  }
  const fixed: Record<string, string> = { leadsTotal: 'All leads', leadsNotContacted: 'Leads not contacted', leadsAssigned: 'Leads assigned', leadsNewInPeriod: 'New leads', leadsAssignedInPeriod: 'Leads assigned', leadsBacklog: 'Leads still to contact', tasksDone: 'Tasks done', tasksPending: 'Tasks pending', tasksOverdue: 'Tasks overdue' }
  return fixed[metric] ?? METRIC_LABELS[metric as MetricKey] ?? 'Details'
}

/* ---------------------------------------------------------------- people */

export const metricOf = (metrics: DailyMetric[], key: MetricKey) => metrics.find(item => item.key === key)

export interface DayClosed { kind: 'submitted' | 'missing' | 'none'; text: string }
export function dayClosed(row: Pick<StaffRow, 'submitted' | 'submittedAt'>, singleDay: boolean, timeZone: string = AGENCY_TIMEZONE): DayClosed {
  if (!singleDay || row.submitted == null) return { kind: 'none', text: '–' }
  if (!row.submitted) return { kind: 'missing', text: 'Not submitted' }
  const time = clockTime(row.submittedAt, timeZone)
  return { kind: 'submitted', text: time ? `Submitted ${time}` : 'Submitted' }
}
/** A stable colour slot (0 to 7) for an avatar, so a person keeps their colour. */
export function avatarSlot(name: string): number {
  let hash = 0
  for (const char of name.trim().toLowerCase()) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  return hash % 8
}
export const initialsFor = (name: string) => {
  const words = name.trim().split(/\s+/).filter(Boolean)
  return ((words[0]?.[0] ?? '?') + (words.length > 1 ? words[words.length - 1][0] : '')).toUpperCase()
}

/** What GET /mine and POST /submit return, as one shape: /mine sends the caller's staff row (figures under `metrics`), submit sends `figures`. */
export function normaliseMyDay(raw: Record<string, unknown>): MyDay {
  const figures = ((raw.figures ?? raw.metrics) ?? {}) as Figures
  const online = typeof raw.interviewsOnline === 'number' ? raw.interviewsOnline : figures.interviewsOnline
  const submittedAt = typeof raw.submittedAt === 'string' ? raw.submittedAt : null
  return {
    date: String(raw.date ?? ''), submittedAt, note: typeof raw.note === 'string' ? raw.note : null,
    figures: { ...figures, ...(online == null ? {} : { interviewsOnline: online }) },
    submitted: typeof raw.submitted === 'boolean' ? raw.submitted : submittedAt != null,
  }
}

/* ---------------------------------------------------------------- server and queries */

/** A server that does not have the new report yet answers one of these. */
export const isOutdatedServer = (status: number | undefined) => status === 404 || status === 405 || status === 501 || status === 502

/** The query of the report, its lists, history and file: the dates, or period=all (Total) which ignores them. */
export function apiQuery(query: DailyQuery): string {
  const rest = { staff: query.staff || 'all', client: query.client || 'all' }
  return new URLSearchParams(query.all ? { period: 'all', ...rest } : { from: query.from, to: query.to, ...rest }).toString()
}
export function drillQuery(metric: string, query: DailyQuery, page: number, pageSize = 25): string {
  return `metric=${encodeURIComponent(metric)}&${apiQuery(query)}&page=${page}&page_size=${pageSize}`
}
export const pageCount = (total: number, pageSize: number) => Math.max(1, Math.ceil(total / Math.max(1, pageSize)))
export function pageRange(total: number, page: number, pageSize: number): string {
  if (total <= 0) return '0 rows'
  return `${page * pageSize + 1}–${Math.min(total, (page + 1) * pageSize)} of ${total.toLocaleString('en-IN')}`
}
export function drillHref(row: Pick<DrillRow, 'kind' | 'candidateId' | 'leadId'>): string | null {
  if (row.candidateId) return `/candidates/${encodeURIComponent(row.candidateId)}`
  if (row.kind === 'lead' && row.leadId) return `/leads?lead=${encodeURIComponent(row.leadId)}`
  return null
}

/* ---------------------------------------------------------------- CSV of a drill-down list */

/** A cell as CSV: quoted when needed, and text that a spreadsheet would run as a formula is kept as text. */
export function csvCell(value: unknown): string {
  let text = value == null ? '' : String(value)
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}
export const CSV_HEAD = ['Date', 'Time', 'Recruiter', 'Name', 'Candidate ID', 'Type', 'Client', 'Job', 'Detail'] as const
export function buildCsv(rows: DrillRow[], timeZone: string = AGENCY_TIMEZONE): string {
  const lines = [CSV_HEAD.map(csvCell).join(',')]
  for (const row of rows) {
    const date = new Date(row.at)
    const valid = !Number.isNaN(date.getTime())
    lines.push([
      valid ? dayInZone(date, timeZone) : '', valid ? clockTime(date, timeZone) : '', row.staffName, row.name, row.careId ?? '',
      row.kind === 'lead' ? 'Lead' : 'Candidate', row.client ?? '', row.job ?? '', row.detail,
    ].map(csvCell).join(','))
  }
  return lines.join('\r\n')
}
export function csvFileName(label: string, from: string, to: string): string {
  const slug = label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'list'
  return `${slug}-${from === to ? from : `${from}-to-${to}`}.csv`
}

/* ---------------------------------------------------------------- the team: who needs attention, and the two groups of the table */

export type PersonKey = 'pending' | 'idle' | 'reported' | 'active'
export interface PersonStatus { key: PersonKey; label: string; /** The Tasks screen's status styles: behind (amber), none (grey), on-track (green). */ tone: 'behind' | 'none' | 'on-track' }
/**
 * Where one person stands. On a single day: submitted is "Reported" (green); no activity at all is "No activity" (grey);
 * active and not submitted is "Not reported yet" (amber, the one that needs a look). Over several days nothing is
 * submitted, so a person is simply "Active" or "No activity".
 */
export function personStatus(row: Pick<StaffRow, 'idle' | 'submitted'>, single: boolean): PersonStatus {
  if (single && row.submitted === true) return { key: 'reported', label: 'Reported', tone: 'on-track' }
  if (row.idle) return { key: 'idle', label: 'No activity', tone: 'none' }
  return single ? { key: 'pending', label: 'Not reported yet', tone: 'behind' } : { key: 'active', label: 'Active', tone: 'on-track' }
}
const RANK: Record<PersonKey, number> = { pending: 0, active: 0, idle: 1, reported: 2 }
/** Attention first, like Tasks: not reported and active, then no activity, then reported; the busiest first within each. */
export function staffByAttention<T extends StaffRow>(rows: T[], single: boolean): T[] {
  return [...rows].sort((a, b) => RANK[personStatus(a, single).key] - RANK[personStatus(b, single).key] || actionsOf(b) - actionsOf(a) || a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
}
/** The seven figures of the client's list added up for one person: their "actions". */
export function actionsOf(row: Pick<StaffRow, 'metrics' | 'interviewsOnline'>): number {
  return DAILY_ORDER.reduce((all, key) => all + (key === 'interviewsOnline' ? row.interviewsOnline ?? 0 : row.metrics[key] ?? 0), 0)
}
export function actionsLine(n: number, todayOnly: boolean): string {
  return `${n.toLocaleString('en-IN')} ${n === 1 ? 'action' : 'actions'}${todayOnly ? ' today' : ' in this period'}`
}
/** A person's share of the team's actions, 0 to 100. */
export function sharePct(n: number, teamTotal: number): number {
  return teamTotal > 0 ? Math.min(100, Math.round((n / teamTotal) * 100)) : 0
}
export interface TableGroupRows { key: 'active' | 'idle'; title: string; rows: StaffRow[] }
/** The two bands of the table: people with activity first, then people with none (each busiest first). */
export function figureGroups(rows: StaffRow[], single: boolean, todayOnly: boolean): TableGroupRows[] {
  const sorted = [...rows].sort((a, b) => actionsOf(b) - actionsOf(a) || a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
  const active = sorted.filter(row => !row.idle)
  const idle = sorted.filter(row => row.idle)
  return [
    { key: 'active' as const, title: single ? (todayOnly ? 'Active today' : 'Active') : 'Active in this period', rows: active },
    { key: 'idle' as const, title: 'No activity', rows: idle },
  ].filter(group => group.rows.length > 0)
}
/* ---------------------------------------------------------------- by client */

export interface ClientTableRow { id: string; name: string; cells: Cells }
/** Clients with activity, busiest first. Empty when the server sent none (or does not send `byClient` yet). */
export function clientRows(report: Pick<DailyReport, 'byClient'>): ClientTableRow[] {
  return (report.byClient ?? []).map(row => {
    const cells: Cells = {}
    for (const key of DAILY_ORDER) cells[key] = key === 'interviewsOnline' ? row.interviewsOnline ?? 0 : row.metrics[key] ?? 0
    // Always the sum of the columns the table shows, whatever an older server sent as `total` (it added every metric).
    cells.total = DAILY_ORDER.reduce((all, key) => all + (cells[key] ?? 0), 0)
    return { id: row.id, name: row.name, cells }
  }).filter(row => (row.cells.total ?? 0) > 0).sort((a, b) => (b.cells.total ?? 0) - (a.cells.total ?? 0) || a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
}
/** The By client card shows only when there are clients with activity and no client filter is on. */
export const showByClient = (rows: ClientTableRow[], clientFilter: string) => rows.length > 0 && (!clientFilter || clientFilter === 'all')

/* ---------------------------------------------------------------- day-close accountability */

const FIGURE_WORDS: Record<DailyKey, string> = { forwarded: 'forwarded', prescreened: 'prescreened', shortlisted: 'shortlisted', interviewsOnline: 'online interviews', offers: 'offers', visas: 'visas', travelled: 'travelled' }
export interface FigureChange { key: DailyKey; now: number; was: number }
/** The figures that differ between what was submitted and what the numbers say now. Nothing when nothing was submitted. */
export function figureChanges(row: Pick<StaffRow, 'metrics' | 'interviewsOnline' | 'submittedFigures'>): FigureChange[] {
  const sent = row.submittedFigures
  if (!sent) return []
  const out: FigureChange[] = []
  for (const key of DAILY_ORDER) {
    const now = key === 'interviewsOnline' ? row.interviewsOnline ?? 0 : row.metrics[key] ?? 0
    const was = sent[key] ?? 0
    if (now !== was) out.push({ key, now, was })
  }
  return out
}
/** "Numbers changed after this report was submitted (now 5 forwarded, was 4)": empty when nothing changed. */
export function changedSinceText(row: Pick<StaffRow, 'metrics' | 'interviewsOnline' | 'submittedFigures' | 'changedSinceSubmit'>): string {
  const changes = figureChanges(row)
  if (!changes.length) return row.changedSinceSubmit ? 'Numbers changed after this report was submitted' : ''
  const shown = changes.slice(0, 3).map(item => `now ${item.now} ${FIGURE_WORDS[item.key]}, was ${item.was}`).join('; ')
  return `Numbers changed after this report was submitted (${shown}${changes.length > 3 ? `; and ${changes.length - 3} more` : ''})`
}
/** A note cut for a list row. */
export function excerpt(note: string | null | undefined, max = 90): string {
  const text = (note ?? '').replace(/\s+/g, ' ').trim()
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text
}
/** "4 forwarded · 6 prescreened": the non-zero figures of a submitted report, for a list row. */
export function figuresLine(figures: Figures | null | undefined, limit = 4): string {
  const parts = DAILY_ORDER.map(key => ({ key, n: figures?.[key] ?? 0 })).filter(item => item.n > 0)
  if (!parts.length) return 'No figures'
  const shown = parts.slice(0, limit).map(item => `${item.n} ${FIGURE_WORDS[item.key]}`).join(' · ')
  return parts.length > limit ? `${shown} · +${parts.length - limit} more` : shown
}
export function historyQuery(query: DailyQuery, page: number, pageSize = 25): string {
  return `${apiQuery(query)}&page=${page}&page_size=${pageSize}`
}

/* ---------------------------------------------------------------- the end-of-day reminder for recruiters */

/** The hour (0 to 23) in the agency's time zone. */
export function agencyHour(value: Date | number | string, timeZone: string = AGENCY_TIMEZONE): number {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(value))
  return Number(parts.find(part => part.type === 'hour')?.value ?? 0)
}
export const REMINDER_HOUR = 17
export const REMINDER_DISMISS_KEY = 'crm.daily-report.reminder-dismissed'
/** Every figure of the day added up (leads contacted included): "any activity today". */
export function dayActivity(figures: Figures | null | undefined): number {
  return ALL_METRIC_KEYS.reduce((all, key) => all + (figures?.[key] ?? 0), 0)
}
/**
 * The slim "Your daily report is not submitted yet" banner: for a recruiter only, from 17:00 agency time, only while the
 * report for today is not submitted, only if they did anything today, and not after they dismissed it for today.
 */
export function reminderVisible(input: { recruiter: boolean; now: Date | number; timeZone?: string; submitted: boolean; activity: number; dismissedDay: string | null; today: string }): boolean {
  if (!input.recruiter || input.submitted || input.activity <= 0) return false
  if (input.dismissedDay === input.today) return false
  return agencyHour(input.now, input.timeZone ?? AGENCY_TIMEZONE) >= REMINDER_HOUR
}
