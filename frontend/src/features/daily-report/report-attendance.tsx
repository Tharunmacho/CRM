import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, CalendarOff, Clock3, ExternalLink, TriangleAlert, UserCheck, UserX } from 'lucide-react'
import { api } from '@/shared/api/client'
import { liveInterval } from '@/shared/state/live'
import { agencyDay } from '@/shared/lib/task-time'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { DatePicker } from '@/shared/ui/date-picker'
import { ScrollEdges } from '@/screens/tasks/daily-table'
import { StatStrip, type StatTileData } from '@/screens/tasks/daily-stat-strip'
import { clockTime, dayLabel, weekdayLabel } from '../attendance/format'
import { durationLabel, type AttendanceResponse } from '../attendance/model'
import type { MonthlyAttendance } from '../attendance/policy-model'
import { attendanceCounts, buildAttendanceRows, punctualityText, type AttendanceRow } from './attendance-rows'
import '@/styles/report-attendance.css'

const hueOf = (name: string) => [...name].reduce((total, character) => total + character.charCodeAt(0), 0) % 6
const DASH = <span className="dw-none" aria-label="not available">{'–'}</span>

/**
 * Reports > Report: who logged in and out on one day, how long they worked and how they did against their shift. Reads the attendance
 * screen's own data (check-ins and CRM use for the day, and the month's policy record for the status, shift and late/early minutes).
 * Without the policy record (the viewer cannot see it, or it failed) the status comes from the check-ins alone.
 */
export function AttendancePanel({ day, staff, team, timeZone, onPickStaff }: {
  day: string
  staff: string
  team: boolean
  timeZone: string
  onPickStaff: (id: string) => void
}) {
  const today = agencyDay(new Date(), timeZone)
  const [date, setDate] = useState(day)
  useEffect(() => { setDate(day) }, [day])
  const [attendance, setAttendance] = useState<AttendanceResponse | null>(null)
  const [month, setMonth] = useState<MonthlyAttendance | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const monthBlocked = useRef('')

  useEffect(() => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return
    const controller = new AbortController()
    let running = false
    let again = false // a change arrived while a read was running: that read may predate it
    const load = async (initial = false) => {
      if (running) return
      running = true
      if (initial) { setAttendance(null); setMonth(null); setLoading(true); setError('') }
      const [year, monthNumber] = date.split('-')
      const monthKey = `${year}-${monthNumber}|${staff}`
      const sessionsQuery = new URLSearchParams({ date, ...(staff ? { staffId: staff } : {}) })
      const monthQuery = new URLSearchParams({ year, month: String(Number(monthNumber)), ...(staff ? { staffId: staff } : {}) })
      try {
        const [sessions, policy] = await Promise.all([
          api.get<AttendanceResponse>('/attendance?' + sessionsQuery, { signal: controller.signal }),
          // The policy record is a bonus: a viewer who may not see it, or a failed read, leaves the sessions part standing
          monthBlocked.current === monthKey ? Promise.resolve(null) : api.get<MonthlyAttendance>('/attendance/month?' + monthQuery, { signal: controller.signal }).catch(() => {
            if (!controller.signal.aborted) monthBlocked.current = monthKey
            return null
          }),
        ])
        if (!controller.signal.aborted) { setAttendance(sessions); setMonth(policy); setError('') }
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'We could not load attendance.')
      } finally {
        running = false
        if (!controller.signal.aborted) setLoading(false)
        if (again && !controller.signal.aborted) { again = false; void load() }
      }
    }
    void load(true)
    const stop = liveInterval(() => { if (document.visibilityState === 'visible') void load() }, 30_000, ['attendance', 'users'], {
      onChange: () => { if (running) again = true; else void load() },
    })
    return () => { controller.abort(); stop() }
  }, [date, staff])

  const rows = useMemo(() => buildAttendanceRows({ day: date, today, staff, attendance, month }), [date, today, staff, attendance, month])
  const counts = useMemo(() => attendanceCounts(rows), [rows])
  const zone = attendance?.timezone || timeZone
  const showPresence = rows.some((row) => row.tracked)
  const subtitle = `${weekdayLabel(date)}, ${dayLabel(date)}${date === today ? ' (today)' : ''}`

  const tiles: StatTileData[] = [
    { key: 'present', label: 'Present', value: counts.present, detail: 'Logged in', icon: UserCheck, tone: 'green' },
    { key: 'late', label: 'Late', value: counts.late, detail: 'After shift start', icon: Clock3, tone: counts.late ? 'amber' : 'blue' },
    { key: 'absent', label: 'Absent', value: counts.absent, detail: 'No check-in', icon: UserX, tone: counts.absent ? 'red' : 'blue' },
    { key: 'leave', label: 'On leave', value: counts.leave, detail: 'Approved leave', icon: CalendarOff, tone: 'blue' },
    { key: 'still', label: 'Still logged in', value: counts.stillIn, detail: 'Not checked out', icon: Activity, tone: 'green' },
  ]

  const head = <div className="dw-panel-head">
    <div><h2><Clock3 size={16} aria-hidden="true" /> Attendance</h2><p>{subtitle}</p></div>
    <div className="dwr-head-tools dwa-tools">
      <DatePicker value={date} onChange={(value) => { if (value) setDate(value) }} max={today} required compact aria-label="Attendance date" />
      <Link to="/attendance" className="dwr-textlink">Open attendance <ExternalLink size={12} aria-hidden="true" /></Link>
    </div>
  </div>

  if (loading) return <section className="dw-panel dwa" aria-label="Attendance" aria-busy="true">{head}<div className="dw-state"><p role="status">Loading attendance…</p></div></section>
  if (error && !attendance) return <section className="dw-panel dwa" aria-label="Attendance">{head}<div className="dw-state"><TriangleAlert size={22} aria-hidden="true" /><p role="alert" className="dwr-empty-title">{error}</p><p>We could not load attendance. It will try again by itself.</p></div></section>

  return <section className="dw-panel dwa" aria-label="Attendance">
    {head}
    {rows.length === 0
      ? <div className="dw-state" role="status"><p className="dwr-empty-title">No attendance to show</p><p>{staff ? 'This person has no attendance record for the day.' : 'Nobody has a record for this day.'}</p></div>
      : <>
        <div className="dwa-strip"><StatStrip tiles={tiles} label="Attendance counts" /></div>
        <AttendanceTable rows={rows} zone={zone} showPresence={showPresence} staff={staff} onPick={team ? onPickStaff : undefined} />
      </>}
  </section>
}

function AttendanceTable({ rows, zone, showPresence, staff, onPick }: { rows: AttendanceRow[]; zone: string; showPresence: boolean; staff: string; onPick?: (id: string) => void }) {
  const widths = [132, 108, 84, 118, 92, 112, ...(showPresence ? [84, 84] : []), 96]
  return <ScrollEdges label="Attendance for the day, scroll sideways for more columns">
    <table className="dw-table dw-main dwr-table dwa-table" aria-label="Attendance for the day">
      <colgroup><col style={{ width: 'auto' }} />{widths.map((width, i) => <col key={i} style={{ width: width + 'px' }} />)}</colgroup>
      <thead><tr>
        <th scope="col" className="is-left dwr-name-head">Recruiter</th>
        <th scope="col" className="is-num">Status</th>
        <th scope="col" className="is-num">Shift</th>
        <th scope="col" className="is-num">First login</th>
        <th scope="col" className="is-num">Last logout</th>
        <th scope="col" className="is-num">Hours worked</th>
        <th scope="col" className="is-num" title="Minutes after the shift start (late) or before the shift end (early)">Late / early by</th>
        {showPresence && <th scope="col" className="is-num" title="CRM open and in use while checked in">Online time</th>}
        {showPresence && <th scope="col" className="is-num" title="CRM open but not in use while checked in">Idle time</th>}
        <th scope="col" className="is-num">Sessions</th>
      </tr></thead>
      <tbody>
        {rows.map((row, index) => <tr key={row.id} className={cn('dw-staff-row', index % 2 === 1 && 'is-alt', row.id === staff && 'is-active')}>
          <th scope="row" className="dwr-name">
            <span className="dwr-person">
              <span className={cn('record-avatar', `avatar-hue-${hueOf(row.name)}`)} aria-hidden="true">{initials(row.name)}</span>
              {onPick
                ? <button type="button" className="dwr-who" onClick={() => onPick(row.id === staff ? '' : row.id)} aria-pressed={row.id === staff} title={row.id === staff ? 'Show all recruiters' : `Show only ${row.name}`}>{row.name}</button>
                : <span className="dwr-who">{row.name}</span>}
            </span>
          </th>
          <td className="is-num" data-label="Status"><span className={cn('dwa-pill', `tone-${row.tone}`)}>{row.label}</span></td>
          <td className="is-num" data-label="Shift">{row.shift ? <span className="dwr-plain tnum">{row.shift}</span> : DASH}</td>
          <td className="is-num" data-label="First login">{row.firstIn ? <span className="dwr-plain tnum">{clockTime(row.firstIn, zone)}</span> : DASH}</td>
          <td className="is-num" data-label="Last logout">{row.stillIn
            ? <span className="dwa-live"><i aria-hidden="true" />Still logged in</span>
            : row.lastOut ? <span className="dwr-plain tnum">{clockTime(row.lastOut, zone)}</span> : DASH}</td>
          <td className="is-num" data-label="Hours worked">{row.sessions.length || row.workedSeconds > 0 ? <span className="dwr-plain tnum">{durationLabel(row.workedSeconds)}</span> : DASH}</td>
          <td className="is-num" data-label="Late / early by">{punctualityText(row) ? <span className="dwr-plain dwa-warn tnum">{punctualityText(row)}</span> : DASH}</td>
          {showPresence && <td className="is-num" data-label="Online time">{row.tracked ? <span className="dwr-plain tnum">{durationLabel(row.activeSeconds)}</span> : DASH}</td>}
          {showPresence && <td className="is-num" data-label="Idle time">{row.tracked ? <span className="dwr-plain tnum">{durationLabel(row.idleSeconds)}</span> : DASH}</td>}
          <td className="is-num" data-label="Sessions"><Sessions row={row} zone={zone} /></td>
        </tr>)}
      </tbody>
    </table>
  </ScrollEdges>
}

function Sessions({ row, zone }: { row: AttendanceRow; zone: string }) {
  if (!row.sessions.length) return DASH
  return <details className="dwa-sessions">
    <summary title="Show each login and logout"><span className="tnum">{row.sessions.length}</span></summary>
    <ol>{row.sessions.map((session, index) => <li key={session.checkedInAt + index} className="tnum">
      {clockTime(session.checkedInAt, zone)} {'–'} {session.checkedOutAt ? clockTime(session.checkedOutAt, zone) : 'now'}
      <small>{durationLabel(session.seconds)}</small>
    </li>)}</ol>
  </details>
}
