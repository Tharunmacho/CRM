import { useMemo } from 'react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { clockTime } from '../attendance/format'
import { attendanceLabels, type EmployeeMonth } from '../attendance/policy-model'
import { minutesText } from './attendance-rows'
import { MARK_TONE, MARK_WORDS, cellTitle, dailyRecordRows, dailyRecordTotals, hoursFigure, percentText, type DayMark, type GridPerson, type MonthGrid } from './attendance-month'

const hueOf = (name: string) => [...name].reduce((total, character) => total + character.charCodeAt(0), 0) % 6
const DASH = <span className="rm-dash" aria-label="not available">{'–'}</span>
/** What a grid cell shows: a letter or two (the legend explains them); a day with nothing to say stays empty. */
const LETTER: Record<DayMark, string> = { P: 'P', LT: 'LT', M: 'M', A: 'A', L: 'L', WO: 'W', H: 'H', N: '' }

/**
 * The monthly grid: one row per recruiter, one column per day of the month (P present, LT late, M missed check-out, A absent, L leave,
 * W weekly off, H holiday), then their present days, late days and average and total hours. A recruiter's name opens their day-by-day record.
 */
export function MonthGridTable({ grid, zone, onOpen, today }: { grid: MonthGrid; zone: string; onOpen: (id: string) => void; /** The agency's current day: a login without a logout before it reads "no check-out", not "now". */ today?: string }) {
  const clock = (value: string) => clockTime(value, zone)
  return <div className="rm-scroll am-gridbox" role="region" aria-label="Monthly attendance grid, scroll sideways if the month does not fit" tabIndex={0}>
    <table className="rm-table am-grid" aria-label="Monthly attendance, one row per recruiter and one column per day">
      <colgroup>
        <col className="am-col-name" />
        {grid.dates.map((date) => <col key={date.date} className="am-col-day" />)}
        <col className="am-col-total" /><col className="am-col-total" /><col className="am-col-total" /><col className="am-col-total" />
      </colgroup>
      <thead><tr>
        <th scope="col" className="is-left">Recruiter</th>
        {grid.dates.map((date) => <th key={date.date} scope="col" className={cn('am-dayhead', date.weekend && 'is-weekend')} title={date.date}>
          <span className="tnum">{date.day}</span><small aria-hidden="true">{date.weekday}</small>
        </th>)}
        <th scope="col" title="Days worked: present, late and missed check-out">Present</th>
        <th scope="col" title="Days the first login came after the shift start">Late</th>
        <th scope="col" title="Average hours per day worked">Avg hrs</th>
        <th scope="col" title="Total hours worked in the month">Total hrs</th>
      </tr></thead>
      <tbody>
        {grid.people.map((person, index) => <tr key={person.userId} className={cn(index % 2 === 1 && 'is-alt')}>
          <th scope="row" className="is-left">
            <span className="rm-person">
              <span className={cn('record-avatar', `avatar-hue-${hueOf(person.name)}`)} aria-hidden="true">{initials(person.name)}</span>
              <button type="button" className="rm-who" onClick={() => onOpen(person.userId)} title={`Open ${person.name}'s day-by-day record`}>{person.name}</button>
            </span>
          </th>
          {grid.dates.map((date, i) => {
            const cell = person.cells[i]
            const title = cellTitle(person.name, date, cell, clock, today)
            return <td key={date.date} className={cn('am-day', cell.mark && `m-${cell.mark}`, date.weekend && 'is-weekend', date.future && 'is-future')} title={title}>
              {cell.mark ? <><span aria-hidden="true">{LETTER[cell.mark]}</span><span className="sr-only">{`${date.day}: ${MARK_WORDS[cell.mark]}`}</span></> : null}
            </td>
          })}
          <td className="tnum am-total"><strong>{person.present}</strong></td>
          <td className={cn('tnum am-total', person.late > 0 && 'rm-warn')}>{person.late}</td>
          <td className="tnum am-total" title={person.averageMinutes ? minutesText(person.averageMinutes) : undefined}>{hoursFigure(person.averageMinutes)}</td>
          <td className="tnum am-total" title={person.totalMinutes ? minutesText(person.totalMinutes) : undefined}>{hoursFigure(person.totalMinutes)}</td>
        </tr>)}
      </tbody>
      {grid.people.length > 1 && <tfoot><tr>
        <th scope="row" className="is-left">All recruiters</th>
        <td colSpan={grid.dates.length} aria-hidden="true" />
        <td className="tnum am-total">{grid.totals.present}</td>
        <td className="tnum am-total">{grid.totals.late}</td>
        <td className="tnum am-total">{hoursFigure(grid.totals.averageMinutes)}</td>
        <td className="tnum am-total">{hoursFigure(grid.totals.totalMinutes)}</td>
      </tr></tfoot>}
    </table>
  </div>
}

/**
 * One person's month, day by day: login, logout, hours and how late, with the facts of the month above and the totals below. Times are in
 * the agency's time zone.
 */
export function DailyRecord({ employee, person, zone, today }: { employee: EmployeeMonth; person: GridPerson | null; zone: string; today: string }) {
  const rows = useMemo(() => dailyRecordRows(employee, today, zone), [employee, today, zone])
  const totals = useMemo(() => dailyRecordTotals(rows), [rows])
  const s = employee.summary
  const facts: [string, string][] = [
    ['Attendance', percentText(s.attendancePercentage)],
    ['Present days', String(person?.present ?? 0)],
    ['Late days', String(person?.late ?? 0)],
    ['Absent days', String(person?.absent ?? 0)],
    ['Missed check-outs', String(person?.missed ?? 0)],
    ['Average hours', person?.averageMinutes ? minutesText(person.averageMinutes) : '–'],
    ['Total hours', person?.totalMinutes ? minutesText(person.totalMinutes) : '–'],
    ['Unpaid time', minutesText(s.unpaidMinutes ?? 0)],
  ]
  return <div className="am-rec">
    <dl className="am-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd className="tnum">{value}</dd></div>)}</dl>
    {!rows.length
      ? <div className="rm-state" role="status"><p className="rm-state-title">No records in this month</p></div>
      : <div className="rm-scroll am-rec-scroll" role="region" aria-label={`Daily record of ${employee.name}`} tabIndex={0}><table className="rm-table am-rec-table" aria-label={`Daily record of ${employee.name}`}>
        <thead><tr>
          <th scope="col" className="is-left">Date</th><th scope="col">Day</th><th scope="col">Status</th><th scope="col">Shift</th>
          <th scope="col">Login</th><th scope="col">Logout</th><th scope="col">Hours worked</th><th scope="col">Late / early by</th>
        </tr></thead>
        <tbody>
          {rows.map((row) => <tr key={row.date} className={cn(row.off && 'is-off', row.leave && 'is-leave')}>
            <th scope="row" className="is-left tnum">{row.dayLabel}</th>
            <td>{row.weekday}</td>
            <td><span className={cn('rm-pill', `tone-${row.mark === 'LT' || row.mark === 'M' ? MARK_TONE[row.mark] : row.tone}`)}>{recordStatus(row.status, row.mark)}</span>{row.provisional && <small className="am-draft">Not final</small>}</td>
            <td className="tnum">{row.shift || DASH}</td>
            <td className="tnum">{row.checkIn ? clockTime(row.checkIn, zone) : DASH}</td>
            <td className="tnum">{row.stillIn && row.date >= today ? <span className="am-live"><i aria-hidden="true" />Still in</span> : row.checkOut ? clockTime(row.checkOut, zone) : row.checkIn ? <span className="rm-warn">Missing</span> : DASH}</td>
            <td className="tnum">{row.workedText || DASH}</td>
            <td className={cn('tnum', row.punctuality && 'rm-warn')}>{row.punctuality || DASH}</td>
          </tr>)}
        </tbody>
        <tfoot><tr>
          <th scope="row" className="is-left" colSpan={4}>Month total</th>
          <td colSpan={2}>{totals.daysWorked} {totals.daysWorked === 1 ? 'day' : 'days'} worked</td>
          <td className="tnum">{minutesText(totals.workedMinutes)}</td>
          <td className="tnum">{totals.lateMinutes || totals.earlyMinutes ? [totals.lateMinutes ? `Late ${minutesText(totals.lateMinutes)}` : '', totals.earlyMinutes ? `Early ${minutesText(totals.earlyMinutes)}` : ''].filter(Boolean).join(' · ') : DASH}</td>
        </tr></tfoot>
      </table></div>}
  </div>
}

/** The words of a day's pill: the policy's own (Paid leave, Work from home...), except a late arrival or a missed check-out, which say so. */
function recordStatus(status: string, mark: DayMark): string {
  if (mark === 'LT') return 'Late'
  if (mark === 'M') return 'Missed check-out'
  return attendanceLabels[status as keyof typeof attendanceLabels] ?? status
}
