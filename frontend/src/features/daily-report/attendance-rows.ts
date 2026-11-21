/**
 * The pure part of the Reports attendance monitor: turns the day's check-ins (GET /attendance) and, when the viewer can see it, the month's
 * policy record (GET /attendance/month) into one row per person. No React and no runtime imports, so it can be tested on its own.
 */
import type { AttendanceResponse, StaffAttendance } from '../attendance/model'
import type { AttendanceCode, MonthlyAttendance, PolicyDay } from '../attendance/policy-model'

export type AttendanceKind = 'present' | 'late' | 'early' | 'absent' | 'leave' | 'off' | 'holiday' | 'pending' | 'missed' | 'duty'
export type PillTone = 'green' | 'amber' | 'red' | 'blue' | 'neutral'

export interface AttendancePair { checkedInAt: string; checkedOutAt: string | null; seconds: number }
export interface AttendanceRow {
  id: string
  name: string
  role: string
  kind: AttendanceKind
  label: string
  tone: PillTone
  /** "09:30 – 18:30", or '' when the policy record is not available (or the day is a weekly off or a holiday). */
  shift: string
  firstIn: string | null
  /** The last check-out of the day; null while the person is still logged in or never logged in. */
  lastOut: string | null
  stillIn: boolean
  workedSeconds: number
  lateMinutes: number
  earlyMinutes: number
  tracked: boolean
  activeSeconds: number
  idleSeconds: number
  sessions: AttendancePair[]
  /** The status came from the policy record (otherwise from the sessions alone). */
  fromPolicy: boolean
}
export interface AttendanceCounts { present: number; late: number; absent: number; leave: number; stillIn: number; total: number }

const PILLS: Record<AttendanceKind, { label: string; tone: PillTone }> = {
  present: { label: 'Present', tone: 'green' },
  late: { label: 'Late', tone: 'amber' },
  early: { label: 'Early exit', tone: 'amber' },
  absent: { label: 'Absent', tone: 'red' },
  leave: { label: 'On leave', tone: 'blue' },
  off: { label: 'Weekly off', tone: 'neutral' },
  holiday: { label: 'Holiday', tone: 'neutral' },
  pending: { label: 'Not clocked in yet', tone: 'neutral' },
  missed: { label: 'Missed check-out', tone: 'amber' },
  duty: { label: 'Present', tone: 'green' },
}
export function kindLabel(kind: AttendanceKind) { return PILLS[kind].label }
export function kindTone(kind: AttendanceKind) { return PILLS[kind].tone }

/** The finer policy codes keep their own words (Work from home, Official duty, Paid permission) on the pill. */
const CODE_LABEL: Partial<Record<AttendanceCode, string>> = { WFH: 'Work from home', OD: 'Official duty', PP: 'Paid permission', PL: 'Paid leave', UL: 'Unpaid leave' }

/** The pill kind for a policy code; null when the code says nothing useful (the sessions decide). */
export function kindOfCode(code: string): AttendanceKind | null {
  switch (code) {
    case 'P': return 'present'
    case 'LT': return 'late'
    case 'EE': return 'early'
    case 'A': return 'absent'
    case 'PL': case 'UL': return 'leave'
    case 'WO': return 'off'
    case 'H': return 'holiday'
    case 'MP': return 'missed'
    case 'PP': case 'OD': case 'WFH': return 'duty'
    default: return null
  }
}

/** "09:30:00" and "09:30" both become "09:30". */
function hhmm(value: string | null | undefined) {
  const match = /^(\d{1,2}):(\d{2})/.exec(value ?? '')
  return match ? match[1].padStart(2, '0') + ':' + match[2] : ''
}
export function shiftLabel(shift: { start?: string; end?: string } | null | undefined) {
  const start = hhmm(shift?.start), end = hhmm(shift?.end)
  return start && end ? `${start} – ${end}` : ''
}

const stamp = (value: string | null | undefined) => { const t = value ? new Date(value).getTime() : NaN; return Number.isFinite(t) ? t : 0 }

function policyDayOf(month: MonthlyAttendance | null | undefined, userId: string, day: string): PolicyDay | null {
  const person = month?.staff.find((entry) => entry.userId === userId)
  return person?.days.find((entry) => entry.date === day) ?? null
}

/**
 * One row for one person. `today` is the agency's current day: on it (or later) a person with no check-in is "Not clocked in yet", on a
 * past day "Absent". The policy status wins when there is one, except that an open session always means the person is here.
 */
export function buildRow(person: StaffAttendance, policy: PolicyDay | null, day: string, today: string): AttendanceRow {
  const sessions: AttendancePair[] = [...person.attendance.sessions]
    .sort((a, b) => stamp(a.checkedInAt) - stamp(b.checkedInAt))
    .map((session) => ({ checkedInAt: session.checkedInAt, checkedOutAt: session.checkedOutAt, seconds: session.durationSeconds }))
  const open = person.attendance.status === 'checked_in' || sessions.some((session) => !session.checkedOutAt)
  const firstIn = sessions[0]?.checkedInAt ?? policy?.checkIn ?? null
  const closed = sessions.map((session) => session.checkedOutAt).filter((value): value is string => !!value)
  const lastOut = open ? null : closed.sort((a, b) => stamp(a) - stamp(b)).pop() ?? person.attendance.lastCheckedOutAt ?? (sessions.length ? null : policy?.checkOut ?? null)
  const attended = sessions.length > 0
  const workedSeconds = person.attendance.totalSeconds > 0 || attended ? person.attendance.totalSeconds : Math.max(0, (policy?.workedMinutes ?? 0) * 60)

  let kind: AttendanceKind | null = policy ? kindOfCode(policy.status) : null
  // The policy calls a day "absent" before it has finished; someone logged in, or a day still open, is not absent
  if (kind === 'absent' && (open || (!attended && day >= today) || policy?.provisional)) kind = attended ? 'present' : 'pending'
  // "Missed check-out" is for someone who DID log in and never logged out on a finished day. Nobody who has not clocked in is ever
  // "missed" (a past day without any login is absent, today without one is "not clocked in yet"), and someone still logged in today has missed nothing yet.
  if (kind === 'missed') {
    if (!attended) kind = day < today ? 'absent' : 'pending'
    else if (open && day >= today) kind = 'present'
  }
  if (!kind) kind = attended ? 'present' : day >= today ? 'pending' : 'absent'
  // A day off with no check-ins stays a day off; a check-in on it is just attendance
  if ((kind === 'off' || kind === 'holiday' || kind === 'leave') && attended && !policy) kind = 'present'

  const pill = PILLS[kind]
  const specific = policy && kind !== 'late' && kind !== 'early' ? CODE_LABEL[policy.status] : undefined
  const idleDay = kind === 'off' || kind === 'holiday'
  return {
    id: person.userId,
    name: person.name,
    role: person.role,
    kind,
    label: specific && (kind === 'duty' || kind === 'leave') ? specific : pill.label,
    tone: pill.tone,
    shift: policy && !idleDay ? shiftLabel(policy.shift) : '',
    firstIn,
    lastOut,
    stillIn: open,
    workedSeconds,
    lateMinutes: Math.max(0, Math.round(policy?.lateMinutes ?? 0)),
    earlyMinutes: Math.max(0, Math.round(policy?.earlyMinutes ?? 0)),
    tracked: !!person.presence?.tracked,
    activeSeconds: person.presence?.activeSeconds ?? 0,
    idleSeconds: person.presence?.idleSeconds ?? 0,
    sessions,
    fromPolicy: !!policy,
  }
}

/** Roles that have no attendance of their own (an Admin does not clock in; an MD does, like a recruiter). */
export const NO_OWN_ATTENDANCE = new Set(['admin'])

/**
 * The rows of the table: everyone the check-in response lists (or only `staff`), the policy record folded in where it is there, ordered by
 * name. Administrators are left out of an all-recruiters view (they do not clock in); a narrowed or own view is never emptied by that rule.
 */
export function buildAttendanceRows({ day, today, staff = '', attendance, month }: { day: string; today: string; staff?: string; attendance: AttendanceResponse | null | undefined; month?: MonthlyAttendance | null }): AttendanceRow[] {
  const people = (attendance?.staff ?? []).filter((person) => !staff || person.userId === staff)
  const shown = people.filter((person) => !!staff || people.length === 1 || !NO_OWN_ATTENDANCE.has(person.role))
    .filter((person) => person.active || person.attendance.sessions.length > 0)
  return shown.map((person) => buildRow(person, policyDayOf(month, person.userId, day), day, today))
    .sort((a, b) => a.name.localeCompare(b.name))
}

export function attendanceCounts(rows: AttendanceRow[]): AttendanceCounts {
  const counts: AttendanceCounts = { present: 0, late: 0, absent: 0, leave: 0, stillIn: 0, total: rows.length }
  for (const row of rows) {
    if (row.kind === 'present' || row.kind === 'late' || row.kind === 'early' || row.kind === 'duty' || (row.kind === 'missed' && row.sessions.length > 0)) counts.present += 1
    if (row.kind === 'late') counts.late += 1
    if (row.kind === 'absent') counts.absent += 1
    if (row.kind === 'leave') counts.leave += 1
    if (row.stillIn) counts.stillIn += 1
  }
  return counts
}

/** "Late 12m" / "Early 5m" / both, '' when on time. */
export function punctualityText(row: Pick<AttendanceRow, 'lateMinutes' | 'earlyMinutes'>) {
  const parts: string[] = []
  if (row.lateMinutes > 0) parts.push(`Late ${minutesText(row.lateMinutes)}`)
  if (row.earlyMinutes > 0) parts.push(`Early ${minutesText(row.earlyMinutes)}`)
  return parts.join(' · ')
}
/** 45 -> "45m", 75 -> "1h 15m". */
export function minutesText(minutes: number) {
  const value = Math.max(0, Math.round(minutes || 0))
  return value < 60 ? `${value}m` : `${Math.floor(value / 60)}h ${String(value % 60).padStart(2, '0')}m`
}

/** The live line under the status: where the person is right now ("Logged in now", "Logged out", "Not clocked in"). */
export function presenceLabel(row: Pick<AttendanceRow, 'stillIn' | 'sessions' | 'kind'>): { label: string; live: boolean } {
  if (row.stillIn) return { label: 'Logged in now', live: true }
  if (row.sessions.length) return { label: 'Logged out', live: false }
  if (row.kind === 'leave' || row.kind === 'off' || row.kind === 'holiday') return { label: '', live: false }
  return { label: 'Not clocked in', live: false }
}
