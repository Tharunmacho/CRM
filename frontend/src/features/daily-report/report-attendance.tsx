import { useMemo, useState } from 'react'
import { Clock3, TriangleAlert } from 'lucide-react'
import { useAgencyToday } from './use-agency-today'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { DatePicker } from '@/shared/ui/date-picker'
import { clockTime } from '../attendance/format'
import { durationLabel } from '../attendance/model'
import { buildAttendanceRows, presenceLabel, punctualityText, type AttendanceRow } from './attendance-rows'
import { useDayAttendance } from './use-attendance-data'
import '@/styles/report-mgmt-tabs.css'
import '@/styles/report-attendance.css'

const hueOf = (name: string) => [...name].reduce((total, character) => total + character.charCodeAt(0), 0) % 6
const DASH = <span className="rm-dash" aria-label="not available">{'–'}</span>

/**
 * The live board: who logged in and out on one day, how long they have worked and how they did against their shift. A recruiter's name is a
 * button that opens that person's month (the page's drawer); the board itself reads nothing: the page gives it the rows.
 */
export function AttendanceBoard({ rows, zone, staff, onOpen, empty }: { rows: AttendanceRow[]; zone: string; staff: string; onOpen?: (id: string) => void; empty?: string }) {
  const showPresence = rows.some((row) => row.tracked)
  if (!rows.length) return <div className="rm-state" role="status"><p className="rm-state-title">No attendance to show</p><p>{empty ?? 'Nobody matches this view for the day.'}</p></div>
  const widths = [118, 112, 84, 118, 96, 104, ...(showPresence ? [124] : []), 76]
  return <div className="rm-scroll is-cards" role="region" aria-label="Login and logout for the day, scroll sideways for more columns" tabIndex={0}>
    <table className="rm-table is-cards am-board" aria-label="Login and logout for the day">
      <colgroup><col />{widths.map((width, i) => <col key={i} style={{ width: width + 'px' }} />)}</colgroup>
      <thead><tr>
        <th scope="col" className="is-left">Recruiter</th>
        <th scope="col">Status</th>
        <th scope="col">Shift</th>
        <th scope="col">First login</th>
        <th scope="col">Last logout</th>
        <th scope="col">Hours worked</th>
        <th scope="col" title="Minutes after the shift start (late) or before the shift end (early)">Late / early by</th>
        {showPresence && <th scope="col" title="CRM open and in use (online) and open but not in use (idle) while checked in">Online / idle</th>}
        <th scope="col">Sessions</th>
      </tr></thead>
      <tbody>
        {rows.map((row, index) => <tr key={row.id} className={cn(index % 2 === 1 && 'is-alt', row.id === staff && 'is-picked')}>
          <th scope="row" className="is-left">
            <span className="rm-person">
              <span className={cn('record-avatar', `avatar-hue-${hueOf(row.name)}`)} aria-hidden="true">{initials(row.name)}</span>
              {onOpen
                ? <button type="button" className="rm-who" onClick={() => onOpen(row.id)} title={`Open ${row.name}'s month, day by day`}>{row.name}</button>
                : <span className="rm-who">{row.name}</span>}
            </span>
          </th>
          <td data-label="Status"><StatusCell row={row} /></td>
          <td data-label="Shift" className="tnum">{row.shift || DASH}</td>
          <td data-label="First login" className="tnum">{row.firstIn ? clockTime(row.firstIn, zone) : DASH}</td>
          <td data-label="Last logout" className="tnum">{row.stillIn
            ? <span className="am-live"><i aria-hidden="true" />Still logged in</span>
            : row.lastOut ? clockTime(row.lastOut, zone) : DASH}</td>
          <td data-label="Hours worked" className="tnum">{row.sessions.length || row.workedSeconds > 0 ? durationLabel(row.workedSeconds) : DASH}</td>
          <td data-label="Late / early by" className={cn('tnum', punctualityText(row) && 'rm-warn')}>{punctualityText(row) || DASH}</td>
          {showPresence && <td data-label="Online / idle" className="tnum">{row.tracked ? `${durationLabel(row.activeSeconds)} / ${durationLabel(row.idleSeconds)}` : DASH}</td>}
          <td data-label="Sessions"><Sessions row={row} zone={zone} /></td>
        </tr>)}
      </tbody>
    </table>
  </div>
}

function StatusCell({ row }: { row: AttendanceRow }) {
  const now = presenceLabel(row)
  return <span className="am-status">
    <span className={cn('rm-pill', `tone-${row.tone}`)}>{row.label}</span>
    {now.label && <small className={cn('am-now', now.live && 'is-live')}>{now.live && <i aria-hidden="true" />}{now.label}</small>}
  </span>
}

function Sessions({ row, zone }: { row: AttendanceRow; zone: string }) {
  if (!row.sessions.length) return DASH
  return <details className="am-sessions">
    <summary title="Show each login and logout"><span className="tnum">{row.sessions.length}</span></summary>
    <ol>{row.sessions.map((session, index) => <li key={session.checkedInAt + index} className="tnum">
      {clockTime(session.checkedInAt, zone)} {'–'} {session.checkedOutAt ? clockTime(session.checkedOutAt, zone) : 'now'}
      <small>{durationLabel(session.seconds)}</small>
    </li>)}</ol>
  </details>
}

/**
 * The board on its own, with its own reading of the day (a date picker and the check-ins). The Reports Attendance tab does not use it (it
 * reads the day once, for its summary cards, and draws an AttendanceBoard); it stays for screens that want the board alone.
 */
export function AttendancePanel({ day, staff, team, timeZone, onPickStaff, reload = 0, title = 'Attendance' }: {
  day: string
  staff: string
  team: boolean
  timeZone: string
  onPickStaff: (id: string) => void
  /** Changes when the page's Refresh was pressed: the day is read again. */
  reload?: number
  title?: string
}) {
  const today = useAgencyToday(timeZone)   // the same hook as the page around it, so the board and the page agree on 'today' (and both roll over at midnight)
  const [picked, setPicked] = useState<{ from: string; date: string }>({ from: day, date: day })
  const date = picked.from === day ? picked.date : day
  const state = useDayAttendance(date, staff, reload, '', null)
  const zone = state.attendance?.timezone || timeZone
  const rows = useMemo(() => buildAttendanceRows({ day: date, today, staff, attendance: state.attendance, month: state.month, zone }), [date, today, staff, state.attendance, state.month, zone])
  const head = <div className="rm-head">
    <div><h2><Clock3 size={16} aria-hidden="true" /> {title}</h2></div>
    <div className="rm-tools">
      <DatePicker value={date} onChange={(value) => { if (value) setPicked({ from: day, date: value }) }} max={today} required compact aria-label="Attendance date" />
    </div>
  </div>
  return <section className="rm-panel" aria-label="Attendance" aria-busy={state.loading || undefined}>
    {head}
    {state.loading ? <div className="rm-state"><p role="status">Loading attendance…</p></div>
      : state.error && !state.attendance ? <div className="rm-state"><TriangleAlert size={22} aria-hidden="true" /><p role="alert" className="rm-state-title">{state.error}</p><p>It will try again by itself.</p></div>
        : <AttendanceBoard rows={rows} zone={zone} staff={staff} onOpen={team ? onPickStaff : undefined} empty={staff ? 'This person has no attendance record for the day.' : 'Nobody has a record for this day.'} />}
  </section>
}
