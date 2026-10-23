import { ChevronRight, Clock3, CalendarDays, ShieldCheck, WalletCards } from 'lucide-react'
import { EmptyState, Panel } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { minutesLabel, readableStatus, type AttendancePolicy, type AttendanceRequest, type EmployeeMonth } from '../policy-model'

export function AttendancePolicyStrip({ policy }: { policy: AttendancePolicy }) {
  const hours = (minutes: number) => minutes % 60 === 0 ? `${minutes / 60} ${minutes === 60 ? 'hour' : 'hours'}` : minutesLabel(minutes)
  return <div className="attendance-policy" aria-label="Attendance policy">
    <span><Clock3 size={17} /><span><strong>{hours(policy.workMinutes)}</strong> work + {hours(policy.breakMinutes)} break</span></span>
    <span><CalendarDays size={17} /><span><strong>{policy.monthlyPaidLeaveDays} day</strong> paid leave</span></span>
    <span><ShieldCheck size={17} /><span>Sunday off, or Friday if chosen by Thursday</span></span>
    <span><WalletCards size={17} /><span>OT offsets late / short time</span></span>
  </div>
}

export function AttendanceOverview({ rows, requests, own, month, onRecord, onRequests }: {
  rows: EmployeeMonth[]
  requests: AttendanceRequest[]
  own: boolean
  month: string
  onRecord: (userId: string) => void
  onRequests: (userId: string) => void
}) {
  const monthLabel = new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${month}-01T12:00:00Z`))
  return <Panel flush className="attendance-overview">
    <header className="attendance-overview-heading">
      <div><h2>{own ? 'Your monthly overview' : 'Monthly staff overview'}</h2><p>{own ? `${monthLabel} · Your attendance, exceptions and unpaid time.` : 'Open an employee to see their daily record.'}</p></div>
      <span className="attendance-staff-count">{own ? monthLabel : `${rows.length} ${rows.length === 1 ? 'employee' : 'employees'}`}</span>
    </header>
    {!rows.length ? <EmptyState title="No employees in this view" description="Select another employee or month." /> : <>
      <div className="attendance-overview-table-wrap"><table className="attendance-overview-table">
        <caption className="sr-only">{monthLabel} attendance overview. Attendance includes paid leave and excludes weekly offs and holidays.</caption>
        <thead><tr><th scope="col">Employee</th><th scope="col">Attendance</th><th scope="col">Working days</th><th scope="col">Exceptions</th><th scope="col">Unpaid time</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{rows.map((row) => {
          const percentage = row.summary.attendancePercentage ?? null
          const requestCount = requests.filter((request) => request.userId === row.userId).length
          const trackedFrom = row.days[0]?.date
          const incomplete = row.summary.incompleteDays ?? 0
          const exceptions = [
            { label: 'absent', value: row.summary.absenceDays ?? row.summary.absentDays, tone: 'absence' },
            { label: 'late', value: row.summary.lateDays ?? 0, tone: 'late' },
            { label: 'incomplete', value: incomplete, tone: 'incomplete' },
          ]
          return <tr key={row.userId}>
            <td className="attendance-person" data-label="Employee"><strong>{row.name}</strong><small>{readableStatus(row.role)}{!row.active ? ' · Inactive' : ''}</small></td>
            <td data-label="Attendance"><div className="attendance-rate">
              <strong>{percentage === null ? '—' : `${percentage}%`}</strong>
              {percentage !== null ? <div className="attendance-rate-track" role="meter" aria-label={`${row.name} attendance`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percentage}><span style={{ width: `${Math.min(100, Math.max(0, percentage))}%` }} /></div> : <small>No working days in this month</small>}
            </div></td>
            <td data-label="Working days"><strong>{row.summary.attendedDays ?? 0} of {row.summary.scheduledDays ?? 0}</strong><small>{trackedFrom ? `Tracked from ${trackedFrom}` : row.trackingStart ? 'No records in this month' : 'Tracking has not started'}</small></td>
            <td className="attendance-exceptions-cell" data-label="Exceptions"><div className="attendance-exceptions">
              {exceptions.map(({ label, value, tone }) => <span key={label} className={value ? `has-value is-${tone}` : ''}>{value} {label}</span>)}
              <button type="button" aria-label={`View ${requestCount} attendance requests for ${row.name}`} onClick={() => onRequests(row.userId)}>{requestCount} requests<ChevronRight size={12} /></button>
            </div></td>
            <td className="attendance-unpaid-cell" data-label="Unpaid time"><strong className={row.summary.unpaidMinutes > 0 ? 'attendance-unpaid' : ''}>{minutesLabel(row.summary.unpaidMinutes)}</strong><small>After allowance &amp; approved OT</small></td>
            <td className="attendance-record-action"><Button aria-label={`Open attendance record for ${row.name}`} onClick={() => onRecord(row.userId)}>View record<ChevronRight size={16} /></Button></td>
          </tr>
        })}</tbody>
      </table></div>
      <p className="attendance-overview-note">Attendance includes approved paid leave. Weekly offs and holidays are excluded. Checked-in incomplete sessions count provisionally.</p>
    </>}
  </Panel>
}
