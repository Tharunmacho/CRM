import type { CSSProperties } from 'react'
import { CalendarDays, ChevronRight, Clock3, ShieldCheck, WalletCards } from 'lucide-react'
import { initials } from '@/shared/lib/format'
import { cn } from '@/shared/lib/cn'
import { EmptyState, Panel } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { DataTable, type Column } from '@/shared/ui/table'
import { RecordIdentity } from '@/shared/ui/workspace'
import { attendanceLabels, minutesLabel, readableStatus, type AttendanceCode, type AttendancePolicy, type AttendanceRequest, type EmployeeMonth } from '../policy-model'
import { daysInMonth, dayLabel, firstWeekdayIndex, hueClass, monthTitle, weekdayLabel } from '../format'
import { CardHead, DayStatus, StatusLegend } from './attendance-status'

export function AttendancePolicyStrip({ policy }: { policy: AttendancePolicy }) {
  const hours = (minutes: number) => minutes % 60 === 0 ? `${minutes / 60} ${minutes === 60 ? 'hour' : 'hours'}` : minutesLabel(minutes)
  return <ul className="att-policy" aria-label="Attendance policy">
    <li><Clock3 size={16} /><span><strong>{hours(policy.workMinutes)}</strong> work + {hours(policy.breakMinutes)} break</span></li>
    <li><CalendarDays size={16} /><span><strong>{policy.monthlyPaidLeaveDays} day</strong> paid leave a month</span></li>
    <li><ShieldCheck size={16} /><span>Sunday off, or Friday if chosen by Thursday</span></li>
    <li><WalletCards size={16} /><span>Overtime can cover late or short time</span></li>
  </ul>
}

export function AttendanceOverview({ rows, requests, own, month, onRecord, onRequests }: {
  rows: EmployeeMonth[]
  requests: AttendanceRequest[]
  own: boolean
  month: string
  onRecord: (userId: string) => void
  onRequests: (userId: string) => void
}) {
  const title = monthTitle(month)
  const columns: Column<EmployeeMonth>[] = [
    { key: 'name', header: 'Employee', width: '20%', render: (row) => <RecordIdentity name={row.name} detail={readableStatus(row.role) + (row.active ? '' : ' · Inactive')} /> },
    { key: 'rate', header: 'Attendance', width: '14%', render: (row) => {
      const percentage = row.summary.attendancePercentage ?? null
      return percentage === null
        ? <span className="att-muted">No working days yet</span>
        : <div className="att-rate"><strong>{percentage}%</strong><div className="att-meter" role="meter" aria-label={`${row.name} attendance`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percentage}><span style={{ width: `${Math.min(100, Math.max(0, percentage))}%` }} /></div></div>
    } },
    { key: 'days', header: 'Working days', width: '12%', render: (row) => <div className="att-two"><strong>{row.summary.attendedDays ?? 0} of {row.summary.scheduledDays ?? 0}</strong><small>{row.days[0]?.date ? `From ${dayLabel(row.days[0].date)}` : row.trackingStart ? 'No records this month' : 'Not started yet'}</small></div> },
    { key: 'exceptions', header: 'Problem days', width: '31%', render: (row) => {
      const requestCount = requests.filter((request) => request.userId === row.userId).length
      const items = [
        { label: 'absent', value: row.summary.absenceDays ?? row.summary.absentDays, tone: 'A' },
        { label: 'late', value: row.summary.lateDays ?? 0, tone: 'LT' },
        { label: 'incomplete', value: row.summary.incompleteDays ?? 0, tone: 'MP' },
      ]
      return <div className="att-exceptions">
        {items.map(({ label, value, tone }) => <span key={label} className="att-tag" data-st={value ? tone : undefined}>{value} {label}</span>)}
        <button type="button" className="att-tag att-tag-link" aria-label={`View ${requestCount} attendance requests for ${row.name}`} onClick={() => onRequests(row.userId)}>{requestCount} {requestCount === 1 ? 'request' : 'requests'}<ChevronRight size={12} /></button>
      </div>
    } },
    { key: 'unpaid', header: 'Unpaid time', width: '11%', render: (row) => <div className="att-two"><strong className={cn(row.summary.unpaidMinutes > 0 && 'att-warn')}>{minutesLabel(row.summary.unpaidMinutes)}</strong><small>After overtime</small></div> },
    { key: 'open', header: 'Record', width: '7%', actions: true, align: 'right', render: (row) => <Button size="sm" aria-label={`Open attendance record for ${row.name}`} title={`Open attendance record for ${row.name}`} onClick={() => onRecord(row.userId)}>Open<ChevronRight size={14} /></Button> },
  ]
  return <Panel flush className="att-card">
    <CardHead title={own ? 'Your monthly overview' : 'Monthly staff overview'} meta={own ? `${title}: your attendance, exceptions and unpaid time.` : `${title}: open an employee to see their daily record.`} action={<span className="att-count">{own ? title : `${rows.length} ${rows.length === 1 ? 'employee' : 'employees'}`}</span>} />
    {!rows.length ? <EmptyState title="No employees in this view" description="Select another employee or month." /> : <DataTable compact stackBelow={1280} rows={rows} columns={columns} rowKey={(row) => row.userId} pageSize={50} />}
    <p className="att-note">Attendance counts approved paid leave. Weekly offs and holidays are left out. A day that is still checked in counts for now and may change.</p>
  </Panel>
}

/** Team view: one row per employee, one cell per day of the month, so patterns show at a glance. */
export function MonthMatrix({ rows, month, today, onRecord }: { rows: EmployeeMonth[]; month: string; today: string; onRecord: (userId: string) => void }) {
  const count = daysInMonth(month)
  const days = Array.from({ length: count }, (_, index) => `${month}-${String(index + 1).padStart(2, '0')}`)
  if (!rows.length) return null
  return <Panel flush className="att-card">
    <CardHead title="Month at a glance" meta={`${monthTitle(month)}: one cell per day. Open an employee for the full record.`} />
    <div className="att-mx" style={{ '--days': count } as CSSProperties}>
      <div className="att-mx-row att-mx-head" aria-hidden="true">
        <span className="att-mx-person-head">Employee</span>
        <div className="att-mx-cells">{days.map((date) => <span key={date} className={cn('att-mx-day', date === today && 'is-today', weekdayLabel(date, 'short') === 'Sun' && 'is-off')}><b>{Number(date.slice(8))}</b><small>{weekdayLabel(date, 'short').slice(0, 1)}</small></span>)}</div>
      </div>
      {rows.map((row) => {
        const byDate = new Map(row.days.map((day) => [day.date, day]))
        const summary = STATUS_SUMMARY.map((code) => ({ code, value: row.days.filter((day) => day.status === code).length })).filter((item) => item.value).map((item) => `${item.value} ${attendanceLabels[item.code].toLowerCase()}`).join(', ')
        return <div className="att-mx-row" key={row.userId}>
          <button type="button" className="att-mx-person" onClick={() => onRecord(row.userId)} title={`Open attendance record for ${row.name}`}>
            <span className={cn("record-avatar", hueClass(row.name))} aria-hidden="true">{initials(row.name)}</span><span className="att-mx-name">{row.name}</span>
          </button>
          <div className="att-mx-cells" role="img" aria-label={`${row.name}, ${monthTitle(month)}: ${summary || 'no records yet'}`}>
            {days.map((date) => {
              const day = byDate.get(date)
              const label = `${row.name}, ${weekdayLabel(date, 'short')} ${dayLabel(date)}${day ? ': ' + attendanceLabels[day.status] + (day.workedMinutes > 0 ? ', ' + minutesLabel(day.workedMinutes) + ' worked' : '') : ': no record'}`
              return <span key={date} className={cn('att-cell', date === today && 'is-today', !day && 'is-empty')} data-st={day?.status} data-d={Number(date.slice(8))} title={label}><b>{Number(date.slice(8))}</b><span>{day?.status ?? ''}</span></span>
            })}
          </div>
        </div>
      })}
    </div>
    <div className="att-mx-legend"><StatusLegend /></div>
  </Panel>
}
const STATUS_SUMMARY: AttendanceCode[] = ['P', 'LT', 'EE', 'PP', 'OD', 'WFH', 'PL', 'UL', 'A', 'MP', 'WO', 'H']

/** Own view: a real month calendar (Monday first). Each day opens the daily records. */
export function MonthCalendar({ employee, month, today, onDay }: { employee: EmployeeMonth; month: string; today: string; onDay: (date: string) => void }) {
  const offset = firstWeekdayIndex(month)
  const count = daysInMonth(month)
  const byDate = new Map(employee.days.map((day) => [day.date, day]))
  const cells: (string | null)[] = [...Array.from({ length: offset }, () => null), ...Array.from({ length: count }, (_, index) => `${month}-${String(index + 1).padStart(2, '0')}`)]
  return <Panel flush className="att-card">
    <CardHead title={`${employee.name}: calendar`} meta={`${monthTitle(month)}. Select a day to open its daily record.`} />
    <div className="att-cal">
      {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((name) => <span key={name} className="att-cal-head">{name}</span>)}
      {cells.map((date, index) => {
        if (!date) return <span key={`blank-${index}`} className="att-cal-blank" aria-hidden="true" />
        const day = byDate.get(date)
        const label = `${weekdayLabel(date)} ${dayLabel(date)}: ${day ? attendanceLabels[day.status] : 'no record'}, open daily record`
        return <button type="button" key={date} className={cn('att-cal-day', date === today && 'is-today', !day && 'is-empty')} data-st={day?.status} disabled={!day} onClick={() => onDay(date)} aria-label={label} title={label}>
          <b>{Number(date.slice(8))}</b>
          {day && <><span className="att-cal-full"><DayStatus status={day.status} /></span><span className="att-cal-code" aria-hidden="true">{day.status}</span><small>{day.workedMinutes > 0 ? minutesLabel(day.workedMinutes) : ''}</small></>}
        </button>
      })}
    </div>
    <div className="att-mx-legend"><StatusLegend /></div>
  </Panel>
}
