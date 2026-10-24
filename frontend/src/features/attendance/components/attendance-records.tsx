import { Activity, Lock, Plus, Trash2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { useStore } from '@/shared/state/crm-store'
import { Badge, EmptyState, Panel } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { DataTable, type Column } from '@/shared/ui/table'
import { RecordIdentity } from '@/shared/ui/workspace'
import { attendanceLabels, minutesLabel, readableStatus, type AttendanceAdjustment, type AttendanceSettings, type DutyPlan, type EmployeeMonth, type PolicyDay } from '../policy-model'
import { clockTime, dayLabel, stamp, weekdayLabel } from '../format'
import { CardHead, DayStatus } from './attendance-status'
import type { ScheduleKind } from './attendance-forms'

const Prose = ({ text }: { text: string }) => <span className="att-prose" title={text}>{text}</span>
const Shift = ({ shift }: { shift: { start: string; end: string } }) => <span className="att-nowrap">{shift.start} to {shift.end}</span>

/* ------------------------------------------------------------ employee record */

export function EmployeeRecord({ employee, timezone, canAdjust, onAdjust, onSessions }: { employee: EmployeeMonth; timezone: string; canAdjust: boolean; onAdjust: () => void; onSessions: (date: string) => void }) {
  const s = employee.summary
  const columns: Column<PolicyDay>[] = [
    { key: 'date', header: 'Date', width: '9%', render: (day) => <div className="att-two"><strong>{dayLabel(day.date)}</strong><small>{weekdayLabel(day.date)}</small></div> },
    { key: 'result', header: 'Result', width: '15%', render: (day) => <div className="att-two"><DayStatus status={day.status} />{day.provisional && <small className="att-warn">Not final{day.regularisationDeadline ? ` · fix by ${stamp(day.regularisationDeadline, timezone)}` : ''}</small>}</div> },
    { key: 'session', header: 'Work session', width: '18%', render: (day) => <div className="att-two"><strong>{day.checkIn ? minutesLabel(day.workedMinutes) + ' worked' : 'No session'}</strong><small>{day.checkIn ? `${clockTime(day.checkIn, timezone)} to ${day.checkOut ? clockTime(day.checkOut, timezone) : 'still in'}` : 'No check-ins recorded'}</small></div> },
    { key: 'required', header: 'Hours needed', width: '13%', render: (day) => <div className="att-two"><strong>{day.requiredMinutes ? minutesLabel(day.requiredMinutes) : '—'}</strong><small>{day.requiredMinutes ? `${day.shift.start} to ${day.shift.end}` : 'Day off'}</small></div> },
    { key: 'late', header: 'Late / early', width: '11%', render: (day) => <div className="att-two"><span>{day.lateMinutes} min late</span><small>{day.earlyMinutes} min early</small></div> },
    { key: 'allowance', header: 'Free time', width: '12%', render: (day) => <div className="att-two"><span>{day.graceMinutes} min free</span><small>{day.paidPermissionMinutes} min paid permission</small></div> },
    { key: 'unpaid', header: 'Unpaid time', width: '10%', render: (day) => <div className="att-two"><strong className={cn(day.unpaidMinutes > 0 && 'att-warn')}>{minutesLabel(day.unpaidMinutes)}</strong><small>{minutesLabel(day.extraOtMinutes)} OT</small></div> },
    { key: 'sessions', header: 'Check-ins', width: '5%', actions: true, align: 'right', render: (day) => <Button size="sm" aria-label={`View check-ins and CRM use for ${dayLabel(day.date)}`} title={`View check-ins and CRM use for ${dayLabel(day.date)}`} onClick={() => onSessions(day.date)}><Activity size={14} />Check-ins</Button> },
  ]
  return <Panel flush className="att-card">
    <CardHead title={`${employee.name}: daily record`} meta={`Times are in ${timezone}. Unpaid time for each day is shown before overtime is taken off for the month.`} action={canAdjust && <Button icon={<Plus size={15} />} onClick={onAdjust}>Fix a record</Button>} />
    <dl className="att-facts">
      <div><dt>Attendance</dt><dd>{s.attendancePercentage == null ? '—' : `${s.attendancePercentage}%`}</dd></div>
      <div><dt>Working days</dt><dd>{s.attendedDays ?? 0} <span>of {s.scheduledDays ?? 0}</span></dd></div>
      <div><dt>Free minutes left</dt><dd>{s.graceRemainingMinutes} <span>min</span></dd></div>
      <div><dt>Unpaid after overtime</dt><dd>{minutesLabel(s.unpaidMinutes)}</dd></div>
    </dl>
    {!employee.days.length
      ? <EmptyState title="No attendance records in this month" description="Records start with the first check-in, leave, permission or fix." />
      : <DataTable compact stackBelow={1280} pageSize={40} rows={employee.days} columns={columns} rowKey={(day) => day.date} rowClassName={(day) => (day.status === 'WO' || day.status === 'H') ? 'att-row-off' : undefined} />}
  </Panel>
}

/* ------------------------------------------------------------------- schedule */

export function ScheduleRecords({ settings, canManage, onNew, onRemove }: { settings: AttendanceSettings; canManage: boolean; onNew: (kind: ScheduleKind) => void; onRemove: (plan: DutyPlan) => void }) {
  const { me } = useStore()
  const duty: Column<DutyPlan>[] = [
    { key: 'date', header: 'Date', width: canManage ? '13%' : '14%', render: (plan) => <div className="att-two"><strong>{dayLabel(plan.date)}</strong><small>{weekdayLabel(plan.date)}</small></div> },
    { key: 'name', header: 'Employee', width: canManage ? '22%' : '26%', render: (plan) => <RecordIdentity name={plan.name} /> },
    { key: 'shift', header: 'Shift', width: '16%', render: (plan) => <Shift shift={plan.shift} /> },
    { key: 'break', header: 'Break', width: '10%', render: (plan) => `${plan.shift.breakMinutes} min` },
    { key: 'reason', header: 'Reason', width: canManage ? '27%' : '34%', render: (plan) => <Prose text={plan.reason} /> },
    ...(canManage ? [{ key: 'action', header: 'Action', width: '12%', actions: true, align: 'right' as const, render: (plan: DutyPlan) => <Button size="sm" variant="dangerGhost" icon={plan.userId === me.id ? <Lock size={14} /> : <Trash2 size={14} />} disabled={plan.userId === me.id} aria-label={`Remove duty plan for ${plan.name} on ${plan.date}`} onClick={() => onRemove(plan)}>Remove</Button> }] : []),
  ]
  return <div className="att-stack">
    <Panel flush className="att-card">
      <CardHead title="Shift plan" meta="A shift set for one date is used instead of the normal work timings." action={canManage && <Button icon={<Plus size={15} />} onClick={() => onNew('duty')}>Plan a shift</Button>} />
      {!settings.dutyPlans.length ? <EmptyState title="No planned shifts" description="Normal work timings and weekly offs are used." /> : <DataTable compact stackBelow={1280} rows={settings.dutyPlans} columns={duty} rowKey={(plan) => plan.id} pageSize={50} />}
    </Panel>
    <Panel flush className="att-card">
      <CardHead title="Work timings" meta="Each change keeps the date it started, so old attendance uses the right shift." action={canManage && <Button icon={<Plus size={15} />} onClick={() => onNew('timing')}>Set timings</Button>} />
      {!settings.workTimings.length ? <EmptyState title="Normal timings are used" description="No personal timings are set here." /> : <DataTable compact stackBelow={1280} pageSize={50} rows={settings.workTimings} rowKey={(row) => row.id} columns={[
        { key: 'name', header: 'Employee', width: '26%', render: (row) => <RecordIdentity name={row.name} /> },
        { key: 'from', header: 'Starts on', width: '16%', render: (row) => dayLabel(row.effectiveFrom) },
        { key: 'shift', header: 'Shift', width: '16%', render: (row) => <Shift shift={row.shift} /> },
        { key: 'break', header: 'Break', width: '10%', render: (row) => `${row.shift.breakMinutes} min` },
        { key: 'reason', header: 'Reason', width: '32%', render: (row) => <Prose text={row.reason} /> },
      ]} />}
    </Panel>
    <Panel flush className="att-card">
      <CardHead title="Special days" meta="Holidays, weekly offs and leave set for employees." action={canManage && <Button icon={<Plus size={15} />} onClick={() => onNew('calendar')}>Set a special day</Button>} />
      {!settings.calendarDays.length ? <EmptyState title="No special days" description="Approved requests and weekly-off rules still apply." /> : <DataTable compact stackBelow={1280} pageSize={50} rows={settings.calendarDays} rowKey={(row) => row.id} columns={[
        { key: 'date', header: 'Date', width: '14%', render: (row) => <div className="att-two"><strong>{dayLabel(row.date)}</strong><small>{weekdayLabel(row.date)}</small></div> },
        { key: 'name', header: 'Employee', width: '26%', render: (row) => <RecordIdentity name={row.name} /> },
        { key: 'status', header: 'Status', width: '18%', render: (row) => <DayStatus status={row.status} /> },
        { key: 'reason', header: 'Reason', width: '42%', render: (row) => <Prose text={row.reason} /> },
      ]} />}
    </Panel>
  </div>
}

/* ----------------------------------------------------------------- weekly off */

export function WeeklyOff({ settings, timezone, busy, disabled, onChange }: { settings: AttendanceSettings; timezone: string; busy: boolean; disabled: boolean; onChange: (weekStart: string, day: string) => void }) {
  const range = (weekStart: string) => {
    const end = new Date(`${weekStart}T12:00:00Z`)
    end.setUTCDate(end.getUTCDate() + 6)
    return `${dayLabel(weekStart)} to ${dayLabel(end.toISOString().slice(0, 10))}`
  }
  return <Panel flush className="att-card">
    <CardHead title="Choose your weekly off" meta="You can plan this week and the next three. To take Friday off, choose it before the Thursday deadline. Sunday then becomes a working day." />
    {!settings.weeklyOff.length ? <EmptyState title="No weeks to choose" description="Weeks you can choose will show here when planning opens." /> : <ul className="att-weeks">{settings.weeklyOff.map((week) => {
      const choosable = ['friday', 'sunday'].includes(week.day)
      return <li key={week.weekStart}>
        <div><strong>Week of {range(week.weekStart)}</strong><p className="att-help">{week.approvedRequestId ? 'Approved weekly off swap' : week.canChange ? `Change by ${stamp(week.deadline, timezone)}` : 'This week can no longer be changed'}</p></div>
        {choosable
          ? <div className="att-segment" role="radiogroup" aria-label={`Weekly off for week ${week.weekStart}`}>{[['sunday', 'Sunday off'], ['friday', 'Friday off']].map(([value, label]) => <button key={value} type="button" role="radio" aria-checked={week.day === value} disabled={!week.canChange || busy || disabled} onClick={() => { if (week.day !== value) onChange(week.weekStart, value) }}>{label}</button>)}</div>
          : <Badge>{readableStatus(week.day)}</Badge>}
      </li>
    })}</ul>}
  </Panel>
}

/* -------------------------------------------------------------------- history */

export function CorrectionHistory({ adjustments, timezone }: { adjustments: AttendanceAdjustment[]; timezone: string }) {
  const columns: Column<AttendanceAdjustment>[] = [
    { key: 'date', header: 'Date', width: '10%', render: (row) => <div className="att-two"><strong>{dayLabel(row.date)}</strong><small>{weekdayLabel(row.date, 'short')}</small></div> },
    { key: 'name', header: 'Employee', width: '20%', render: (row) => <RecordIdentity name={row.name} /> },
    { key: 'kind', header: 'Change', width: '13%', render: (row) => <Badge variant="chip">{readableStatus(row.kind)}</Badge> },
    { key: 'detail', header: 'Detail', width: '17%', render: (row) => row.status ? attendanceLabels[row.status] : row.occurredAt ? `${readableStatus(row.action ?? '')} · ${stamp(row.occurredAt, timezone)}` : `${row.minutes ?? 0} minutes` },
    { key: 'reason', header: 'Reason', width: '24%', render: (row) => <Prose text={row.reason} /> },
    { key: 'by', header: 'Changed by', width: '16%', render: (row) => <div className="att-two"><strong>{row.actorName}</strong><small>{stamp(row.createdAt, timezone)}</small></div> },
  ]
  return <Panel flush className="att-card">
    <CardHead title="Changes made to attendance" meta="Every change is saved with who made it, when, and why." />
    {!adjustments.length ? <EmptyState title="No changes this month" description="Changes to attendance will show here." /> : <DataTable compact stackBelow={1280} rows={adjustments} columns={columns} rowKey={(row) => row.id} pageSize={50} />}
  </Panel>
}
