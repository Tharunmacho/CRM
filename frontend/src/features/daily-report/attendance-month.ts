/**
 * The pure part of Reports > Attendance, the monthly view: turns the month's policy record (GET /attendance/month) into the recruiter x day
 * grid (a mark per day, the totals per person and for everyone) and the daily-record rows of one person. The reading of a day is the one of the
 * Excel file (backend app/services/attendance_month_export.py: day_mark, late_minutes, summarize), kept the same on purpose.
 * No React, so it can be tested on its own.
 */
import type { AttendanceCode, EmployeeMonth, MonthlyAttendance, PolicyDay } from '../attendance/policy-model'
import { NO_OWN_ATTENDANCE, arrivalLateness, minutesText, punctualityText, shiftLabel, type PillTone } from './attendance-rows'

/** 100 -> "100%", 83.33 -> "83.3%", 90 -> "90%", null -> "–". */
export function percentText(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '–'
  return `${Math.round(value * 10) / 10}%`
}

const shortDay = (date: string) => new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(date + 'T12:00:00Z'))
const weekday = (date: string) => new Intl.DateTimeFormat('en-GB', { weekday: 'short', timeZone: 'UTC' }).format(new Date(date + 'T12:00:00Z'))

// ------------------------------------------------------------------------------------------------ the month grid

/** P present, LT late, M missed check-out, A absent, L leave, WO weekly off, H holiday, N nothing to say yet (today before the first login). */
export type DayMark = 'P' | 'LT' | 'M' | 'A' | 'L' | 'WO' | 'H' | 'N'
const PRESENT_CODES = new Set(['P', 'LT', 'EE', 'PP', 'OD', 'WFH'])

/** Minutes the first login came after the shift start (whole minutes, in the agency's zone; without a zone only the policy's own late minutes are known); working away on duty has no arrival time. */
export function dayLateMinutes(day: Pick<PolicyDay, 'status' | 'checkIn' | 'shift' | 'lateMinutes'>, zone: string | undefined): number {
  if (!day.checkIn || day.status === 'OD' || day.status === 'WFH') return 0
  return arrivalLateness(day.checkIn, day.shift?.start, zone, day.lateMinutes)
}

export function dayMark(day: Pick<PolicyDay, 'date' | 'status' | 'checkIn' | 'checkOut' | 'shift' | 'lateMinutes'>, today: string, zone: string | undefined): DayMark {
  const status = day.status as string
  const date = day.date.slice(0, 10)
  if (status === 'WO' || status === 'H') return status
  if (status === 'PL' || status === 'UL') return 'L'
  // The policy turns a half-punched day into "absent" once its fix-up window has closed; the person was there, so it stays a missed check-out
  if (status === 'A') return day.checkIn && !day.checkOut ? 'M' : 'A'
  if (status === 'MP') {
    if (!day.checkIn) return date < today ? 'A' : 'N'
    return date >= today && !day.checkOut ? 'P' : 'M'
  }
  if (status === 'LT' || (PRESENT_CODES.has(status) && dayLateMinutes(day, zone) > 0)) return 'LT'
  return PRESENT_CODES.has(status) ? 'P' : 'N'
}

export const MARK_WORDS: Record<DayMark, string> = { P: 'Present', LT: 'Late', M: 'Missed check-out', A: 'Absent', L: 'On leave', WO: 'Weekly off', H: 'Holiday', N: 'Not yet' }
export const MARK_TONE: Record<DayMark, PillTone> = { P: 'green', LT: 'amber', M: 'amber', A: 'red', L: 'blue', WO: 'neutral', H: 'neutral', N: 'neutral' }

export interface GridCell { date: string; mark: DayMark | ''; workedMinutes: number; lateMinutes: number; checkIn: string | null; checkOut: string | null }
export interface GridPerson {
  userId: string
  name: string
  role: string
  active: boolean
  cells: GridCell[]
  present: number
  late: number
  absent: number
  leave: number
  missed: number
  workedDays: number
  totalMinutes: number
  averageMinutes: number
  /** The policy's attendance percentage (days attended over days scheduled), null before any scheduled day. */
  percentage: number | null
}
export interface GridDate { date: string; day: number; weekday: string; weekend: boolean; future: boolean }
export interface GridTotals { present: number; late: number; absent: number; leave: number; missed: number; workedDays: number; totalMinutes: number; averageMinutes: number }
export interface MonthGrid { dates: GridDate[]; people: GridPerson[]; totals: GridTotals }

const daysIn = (year: number, month: number) => new Date(Date.UTC(year, month, 0)).getUTCDate()

/** Every date of the month, with the day number and weekday letter for the grid's heading. Days after `today` are marked future. */
export function monthDates(year: number, month: number, today: string): GridDate[] {
  return Array.from({ length: daysIn(year, month) }, (_, index) => {
    const date = `${year}-${String(month).padStart(2, '0')}-${String(index + 1).padStart(2, '0')}`
    const weekdayIndex = new Date(date + 'T12:00:00Z').getUTCDay()
    return { date, day: index + 1, weekday: ['S', 'M', 'T', 'W', 'T', 'F', 'S'][weekdayIndex], weekend: weekdayIndex === 0 || weekdayIndex === 6, future: date > today }
  })
}

const average = (total: number, days: number) => (days ? Math.round(total / days) : 0)

/** The grid of a month: one row per person (the Administrator account is never a row; ordered by name), one cell per date, totals per person and overall. */
export function buildMonthGrid(month: MonthlyAttendance | null | undefined, today: string, zone: string, only = ''): MonthGrid {
  const dates = month ? monthDates(month.year, month.month, today) : []
  const totals: GridTotals = { present: 0, late: 0, absent: 0, leave: 0, missed: 0, workedDays: 0, totalMinutes: 0, averageMinutes: 0 }
  const people = (month?.staff ?? []).filter((person) => !NO_OWN_ATTENDANCE.has(person.role) && (!only || person.userId === only)).map((person): GridPerson => {
    const byDate = new Map(person.days.map((day) => [day.date.slice(0, 10), day]))
    const row: GridPerson = { userId: person.userId, name: person.name, role: person.role, active: person.active, cells: [], present: 0, late: 0, absent: 0, leave: 0, missed: 0, workedDays: 0, totalMinutes: 0, averageMinutes: 0, percentage: person.summary.attendancePercentage ?? null }
    for (const date of dates) {
      const day = byDate.get(date.date)
      if (!day) { row.cells.push({ date: date.date, mark: '', workedMinutes: 0, lateMinutes: 0, checkIn: null, checkOut: null }); continue }
      const mark = dayMark(day, today, zone)
      const worked = Math.max(0, Math.round(day.workedMinutes || 0))
      row.cells.push({ date: date.date, mark, workedMinutes: worked, lateMinutes: dayLateMinutes(day, zone), checkIn: day.checkIn, checkOut: day.checkOut })
      if (mark === 'P' || mark === 'LT' || mark === 'M') row.present += 1
      if (mark === 'LT') row.late += 1
      if (mark === 'A') row.absent += 1
      if (mark === 'L') row.leave += 1
      if (mark === 'M') row.missed += 1
      if (worked > 0) { row.workedDays += 1; row.totalMinutes += worked }
    }
    row.averageMinutes = average(row.totalMinutes, row.workedDays)
    for (const key of ['present', 'late', 'absent', 'leave', 'missed', 'workedDays', 'totalMinutes'] as const) totals[key] += row[key]
    return row
  }).sort((a, b) => a.name.localeCompare(b.name))
  totals.averageMinutes = average(totals.totalMinutes, totals.workedDays)
  return { dates, people, totals }
}

/** 450 minutes as "7.5": hours with at most one decimal, for the narrow Avg and Total columns (the cards and the drawer say "7h 30m"). */
export function hoursFigure(minutes: number) {
  if (!minutes) return '–'
  return String(Math.round((minutes / 60) * 10) / 10)
}

/** "Asha, Mon 3 Nov: Late, late 25m, 10:25 to 19:05, 8h 00m" for the hover text and the screen-reader name of a grid cell. */
export function cellTitle(name: string, date: GridDate, cell: GridCell, clock: (stamp: string) => string, today?: string): string {
  const label = `${name}, ${new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(date.date + 'T12:00:00Z'))}`
  if (!cell.mark) return label
  const parts = [MARK_WORDS[cell.mark]]
  if (cell.lateMinutes > 0 && cell.mark !== 'A') parts.push(`late ${minutesText(cell.lateMinutes)}`)
  // "now" is only true for a day that is still running; on a finished day a login without a logout is a missing check-out
  if (cell.checkIn) parts.push(`${clock(cell.checkIn)} to ${cell.checkOut ? clock(cell.checkOut) : today && date.date < today ? 'no check-out' : 'now'}`)
  if (cell.workedMinutes > 0) parts.push(minutesText(cell.workedMinutes))
  return `${label}: ${parts.join(', ')}`
}

/** The server-built Excel file of the month (API path without /api) and its file name. */
export function monthExportPath(month: string, staff: string): string {
  const [year, monthNumber] = month.split('-')
  const query = new URLSearchParams({ year, month: String(Number(monthNumber)), ...(staff ? { staffId: staff } : {}) })
  return `/attendance/month/export?${query}`
}
export const monthExportName = (month: string) => `attendance-${month}.xlsx`

// ------------------------------------------------------------------------------------------------ the daily record of one person

export type DayTone = PillTone
const DAY_TONE: Record<AttendanceCode, PillTone> = { P: 'green', LT: 'amber', EE: 'amber', MP: 'amber', A: 'red', PL: 'blue', UL: 'blue', PP: 'blue', OD: 'blue', WFH: 'blue', WO: 'neutral', H: 'neutral' }
export const dayTone = (code: string): PillTone => DAY_TONE[code as AttendanceCode] ?? 'neutral'

export interface DailyRecordRow {
  date: string
  dayLabel: string
  weekday: string
  status: string
  tone: PillTone
  /** The grid's reading of the day (late arrival, missed check-out...), the words the pill shows. */
  mark: DayMark
  /** A weekly off or a holiday: shown quietly. */
  off: boolean
  leave: boolean
  /** "09:30 – 18:30", '' on a day off. */
  shift: string
  checkIn: string | null
  checkOut: string | null
  /** Checked in and not yet out (the day is still open). */
  stillIn: boolean
  workedMinutes: number
  workedText: string
  punctuality: string
  lateMinutes: number
  earlyMinutes: number
  unpaidMinutes: number
  /** The day is not final yet (it can still be fixed). */
  provisional: boolean
}
export interface DailyRecordTotals { daysWorked: number; workedMinutes: number; lateMinutes: number; earlyMinutes: number; unpaidMinutes: number; weeklyOffs: number; holidays: number; leaves: number }

/** The days of one person, oldest first. `today` and `zone` (the agency's) give each day its mark and the late minutes of an arrival after the shift start. */
export function dailyRecordRows(employee: Pick<EmployeeMonth, 'days'>, today = '9999-12-31', zone?: string): DailyRecordRow[] {
  return [...employee.days].sort((a, b) => a.date.localeCompare(b.date)).map((day: PolicyDay) => {
    const off = day.status === 'WO' || day.status === 'H'
    const worked = Math.max(0, Math.round(day.workedMinutes || 0))
    const lateMinutes = dayLateMinutes(day, zone)
    return {
      date: day.date,
      dayLabel: shortDay(day.date),
      weekday: weekday(day.date),
      status: day.status,
      tone: dayTone(day.status),
      mark: dayMark(day, today, zone),
      off,
      leave: day.status === 'PL' || day.status === 'UL',
      shift: off ? '' : shiftLabel(day.shift),
      checkIn: day.checkIn,
      checkOut: day.checkOut,
      stillIn: !!day.checkIn && !day.checkOut,
      workedMinutes: worked,
      workedText: day.checkIn || worked > 0 ? minutesText(worked) : '',
      punctuality: punctualityText({ lateMinutes, earlyMinutes: Math.max(0, Math.round(day.earlyMinutes || 0)) }),
      lateMinutes,
      earlyMinutes: Math.max(0, Math.round(day.earlyMinutes || 0)),
      unpaidMinutes: Math.max(0, Math.round(day.unpaidMinutes || 0)),
      provisional: !!day.provisional,
    }
  })
}

export function dailyRecordTotals(rows: DailyRecordRow[]): DailyRecordTotals {
  const totals: DailyRecordTotals = { daysWorked: 0, workedMinutes: 0, lateMinutes: 0, earlyMinutes: 0, unpaidMinutes: 0, weeklyOffs: 0, holidays: 0, leaves: 0 }
  for (const row of rows) {
    if (row.checkIn) totals.daysWorked += 1
    totals.workedMinutes += row.workedMinutes; totals.lateMinutes += row.lateMinutes; totals.earlyMinutes += row.earlyMinutes; totals.unpaidMinutes += row.unpaidMinutes
    if (row.status === 'WO') totals.weeklyOffs += 1
    if (row.status === 'H') totals.holidays += 1
    if (row.leave) totals.leaves += 1
  }
  return totals
}
