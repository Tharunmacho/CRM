/**
 * The pure part of Reports > Attendance (monthly staff overview and the daily record behind it): turns the month's policy record
 * (GET /attendance/month) and the month's requests (GET /attendance/requests) into the overview rows, the exception chips, the
 * summary tiles, the daily-record rows and the CSV. No React, so it can be tested on its own.
 */
import type { AttendanceCode, AttendanceRequest, EmployeeMonth, MonthlyAttendance, PolicyDay } from '../attendance/policy-model'
import { NO_OWN_ATTENDANCE, minutesText, punctualityText, shiftLabel, type PillTone } from './attendance-rows'

export type ChipKey = 'absent' | 'late' | 'incomplete' | 'requests'
export interface ExceptionChip { key: ChipKey; count: number; label: string; tone: PillTone }
export interface OverviewRow {
  userId: string
  name: string
  role: string
  active: boolean
  /** "Tracked from 1 Nov 2026" (or "No records yet"). */
  tracked: string
  /** null when the person has no scheduled working day yet. */
  percentage: number | null
  percentText: string
  /** 0 to 100, for the thin bar. */
  bar: number
  attendedDays: number
  scheduledDays: number
  daysText: string
  chips: ExceptionChip[]
  unpaidMinutes: number
  unpaidText: string
  requests: number
}
export interface MonthTiles { percentage: number | null; presentDays: number; late: number; absent: number; incomplete: number; pendingRequests: number; unpaidMinutes: number; people: number }

/** 100 -> "100%", 83.33 -> "83.3%", 90 -> "90%", null -> "–". */
export function percentText(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '–'
  return `${Math.round(value * 10) / 10}%`
}

const longDay = (date: string) => new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(date + 'T12:00:00Z'))
const shortDay = (date: string) => new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(date + 'T12:00:00Z'))
const weekday = (date: string) => new Intl.DateTimeFormat('en-GB', { weekday: 'short', timeZone: 'UTC' }).format(new Date(date + 'T12:00:00Z'))

/** "Tracked from 1 Nov 2026"; the person's tracking start, else the first day of the record. */
export function trackedFromText(employee: Pick<EmployeeMonth, 'trackingStart' | 'days'>) {
  const first = employee.days[0]?.date || employee.trackingStart
  return first ? `Tracked from ${longDay(first.slice(0, 10))}` : 'No records yet'
}

/** The four chips of a row: "0 absent", "3 late", "1 incomplete", "2 requests". A count above zero carries a tone, zero stays quiet. */
export function exceptionChips(employee: Pick<EmployeeMonth, 'summary'>, requests: number): ExceptionChip[] {
  const s = employee.summary
  const absent = s.absenceDays ?? s.absentDays ?? 0
  return [
    { key: 'absent', count: absent, label: `${absent} absent`, tone: absent > 0 ? 'red' : 'neutral' },
    { key: 'late', count: s.lateDays ?? 0, label: `${s.lateDays ?? 0} late`, tone: (s.lateDays ?? 0) > 0 ? 'amber' : 'neutral' },
    { key: 'incomplete', count: s.incompleteDays ?? 0, label: `${s.incompleteDays ?? 0} incomplete`, tone: (s.incompleteDays ?? 0) > 0 ? 'amber' : 'neutral' },
    { key: 'requests', count: requests, label: `${requests} ${requests === 1 ? 'request' : 'requests'}`, tone: requests > 0 ? 'blue' : 'neutral' },
  ]
}

/** Requests of the month per person (rejected ones are not counted: they changed nothing). */
export function requestCounts(requests: Pick<AttendanceRequest, 'userId' | 'status'>[]) {
  const counts = new Map<string, number>()
  for (const request of requests) if (request.status !== 'rejected') counts.set(request.userId, (counts.get(request.userId) ?? 0) + 1)
  return counts
}
const PENDING = new Set(['pending', 'awaiting_cover'])
export const pendingRequests = (requests: Pick<AttendanceRequest, 'status'>[]) => requests.filter((request) => PENDING.has(request.status)).length

/** One row per person: administrators do not clock in and are left out; ordered by name. */
export function buildOverviewRows(month: MonthlyAttendance | null | undefined, requests: AttendanceRequest[] = []): OverviewRow[] {
  const counts = requestCounts(requests)
  return (month?.staff ?? [])
    .filter((person) => !NO_OWN_ATTENDANCE.has(person.role))
    .map((person) => {
      const s = person.summary
      const percentage = s.attendancePercentage ?? null
      const asked = counts.get(person.userId) ?? 0
      return {
        userId: person.userId,
        name: person.name,
        role: person.role,
        active: person.active,
        tracked: trackedFromText(person),
        percentage,
        percentText: percentText(percentage),
        bar: percentage == null ? 0 : Math.min(100, Math.max(0, percentage)),
        attendedDays: s.attendedDays ?? 0,
        scheduledDays: s.scheduledDays ?? 0,
        daysText: `${s.attendedDays ?? 0} of ${s.scheduledDays ?? 0}`,
        chips: exceptionChips(person, asked),
        unpaidMinutes: s.unpaidMinutes ?? 0,
        unpaidText: minutesText(s.unpaidMinutes ?? 0),
        requests: asked,
      }
    })
    .sort((a, b) => a.name.localeCompare(b.name))
}

/** The tiles above the table. The percentage is the days attended over the days scheduled across everyone shown (not an average of averages). */
export function monthTiles(month: MonthlyAttendance | null | undefined, requests: AttendanceRequest[] = [], only = ''): MonthTiles {
  const people = (month?.staff ?? []).filter((person) => !NO_OWN_ATTENDANCE.has(person.role) && (!only || person.userId === only))
  let attended = 0, scheduled = 0, present = 0, late = 0, absent = 0, incomplete = 0, unpaid = 0
  for (const person of people) {
    const s = person.summary
    attended += s.attendedDays ?? 0; scheduled += s.scheduledDays ?? 0; present += s.presentDays ?? 0
    late += s.lateDays ?? 0; absent += s.absenceDays ?? s.absentDays ?? 0; incomplete += s.incompleteDays ?? 0; unpaid += s.unpaidMinutes ?? 0
  }
  const ids = new Set(people.map((person) => person.userId))
  return {
    percentage: scheduled > 0 ? Math.round((attended / scheduled) * 1000) / 10 : null,
    presentDays: present, late, absent, incomplete,
    pendingRequests: pendingRequests(requests.filter((request) => ids.has(request.userId))),
    unpaidMinutes: unpaid,
    people: people.length,
  }
}

export type DayTone = PillTone
const DAY_TONE: Record<AttendanceCode, PillTone> = { P: 'green', LT: 'amber', EE: 'amber', MP: 'amber', A: 'red', PL: 'blue', UL: 'blue', PP: 'blue', OD: 'blue', WFH: 'blue', WO: 'neutral', H: 'neutral' }
export const dayTone = (code: string): PillTone => DAY_TONE[code as AttendanceCode] ?? 'neutral'

export interface DailyRecordRow {
  date: string
  dayLabel: string
  weekday: string
  status: string
  tone: PillTone
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

export function dailyRecordRows(employee: Pick<EmployeeMonth, 'days'>): DailyRecordRow[] {
  return [...employee.days].sort((a, b) => a.date.localeCompare(b.date)).map((day: PolicyDay) => {
    const off = day.status === 'WO' || day.status === 'H'
    const worked = Math.max(0, Math.round(day.workedMinutes || 0))
    return {
      date: day.date,
      dayLabel: shortDay(day.date),
      weekday: weekday(day.date),
      status: day.status,
      tone: dayTone(day.status),
      off,
      leave: day.status === 'PL' || day.status === 'UL',
      shift: off ? '' : shiftLabel(day.shift),
      checkIn: day.checkIn,
      checkOut: day.checkOut,
      stillIn: !!day.checkIn && !day.checkOut,
      workedMinutes: worked,
      workedText: day.checkIn || worked > 0 ? minutesText(worked) : '',
      punctuality: punctualityText({ lateMinutes: Math.max(0, Math.round(day.lateMinutes || 0)), earlyMinutes: Math.max(0, Math.round(day.earlyMinutes || 0)) }),
      lateMinutes: Math.max(0, Math.round(day.lateMinutes || 0)),
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

const quote = (value: string | number) => { const text = String(value); return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text }
/** The month overview as CSV (opens in Excel): one line per person. */
export function overviewCsv(rows: OverviewRow[]) {
  const head = ['Employee', 'Tracked from', 'Attendance %', 'Days attended', 'Working days', 'Absent', 'Late', 'Incomplete', 'Requests', 'Unpaid time']
  const lines = rows.map((row) => [row.name, row.tracked.replace('Tracked from ', ''), row.percentage ?? '', row.attendedDays, row.scheduledDays, ...row.chips.map((chip) => chip.count), row.unpaidText])
  return [head, ...lines].map((line) => line.map(quote).join(',')).join('\r\n')
}
export const overviewCsvName = (month: string) => `attendance-overview-${month}.csv`
