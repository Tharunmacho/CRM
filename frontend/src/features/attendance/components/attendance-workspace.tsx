import { useEffect, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CalendarDays, CheckCircle2, ChevronDown, ClipboardList, Clock3, History, Plus, RefreshCw, ShieldCheck, Timer, Trash2, Users, WalletCards, type LucideIcon } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { agencyDay } from '@/shared/lib/task-time'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { Button, Field, Input, Select } from '@/shared/ui/primitives'
import { SummaryCards } from '@/shared/ui/workspace'
import { Dropdown, MenuItem } from '@/shared/ui/overlay'
import { AttendanceClock } from './attendance-clock'
import { AttendanceOverview, AttendancePolicyStrip } from './attendance-overview'
import { useAttendance } from '../attendance-context'
import { AttendanceScheduleDialog, DecisionDialog, RemoveDutyDialog, RequestAttendanceDialog, type ScheduleKind } from './attendance-forms'
import { approvalLabel, attendanceLabels, minutesLabel, readableStatus, requestLabels, type AttendanceAdjustment, type AttendanceRequest, type AttendanceSettings, type DutyPlan, type EmployeeMonth, type MonthlyAttendance } from '../policy-model'
import '../attendance-workspace.css'
import '../attendance-overview.css'

type Tab = 'overview' | 'employee' | 'requests' | 'weekly' | 'schedule' | 'history' | 'presence'
type Decision = { request: AttendanceRequest; action: 'approve' | 'reject' | 'accept_cover' | 'decline_cover' | 'nominate' }
interface Bundle { month: MonthlyAttendance; requests: AttendanceRequest[]; settings: AttendanceSettings; adjustments: AttendanceAdjustment[] }
function stamp(value: string | null | undefined, timezone: string) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat('en-GB', { timeZone: timezone, month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(parsed)
}
function Status({ status }: { status: string }) {
  return <Badge tone={['approved', 'P', 'PP', 'PL'].includes(status) ? 'navy' : ['rejected', 'A', 'UL'].includes(status) ? 'stamp' : ['pending', 'awaiting_cover', 'MP', 'LT', 'EE'].includes(status) ? 'amber' : 'neutral'}>{attendanceLabels[status as keyof typeof attendanceLabels] ?? readableStatus(status)}</Badge>
}

export function AttendanceWorkspace({ presence, recordRevision = 0 }: { presence: (staffId: string, date: string) => ReactNode; recordRevision?: number }) {
  const store = useStore()
  const [searchParams] = useSearchParams()
  const { revision } = useAttendance()
  const canManage = store.me.role !== 'staff'
  const today = agencyDay(new Date(), store.agencyTimezone)
  const [month, setMonth] = useState(today.slice(0, 7))
  const [scope, setScope] = useState(canManage ? 'team' : 'mine')
  const [staffId, setStaffId] = useState('')
  const [selected, setSelected] = useState('')
  const [tab, setTab] = useState<Tab>('overview')
  const [bundle, setBundle] = useState<Bundle | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [retry, setRetry] = useState(0)
  const [requesting, setRequesting] = useState(false)
  const [decision, setDecision] = useState<Decision | null>(null)
  const [schedule, setSchedule] = useState<ScheduleKind | null>(null)
  const [removing, setRemoving] = useState<DutyPlan | null>(null)
  const [weeklyBusy, setWeeklyBusy] = useState(false)
  const [requestStaff, setRequestStaff] = useState('')
  const [activityDate, setActivityDate] = useState(today)
  const own = scope === 'mine' || !canManage
  const employeeId = own ? store.me.id : staffId

  useEffect(() => {
    const linkedMonth = searchParams.get('month')
    const linkedStaff = canManage ? searchParams.get('staff') || '' : ''
    const linkedView = searchParams.get('view')
    const linkedDate = searchParams.get('date')
    if (linkedDate && /^\d{4}-\d{2}-\d{2}$/.test(linkedDate) && linkedDate <= today && Number.isFinite(Date.parse(linkedDate)) && new Date(linkedDate).toISOString().slice(0, 10) === linkedDate) setActivityDate(linkedDate)
    if (linkedView === 'presence') setTab('presence')
    if (linkedMonth && /^(20\d{2}|2100)-(0[1-9]|1[0-2])$/.test(linkedMonth)) setMonth(linkedMonth)
    if (linkedStaff) {
      setScope('team')
      setStaffId(linkedStaff)
      setSelected(linkedStaff)
      setTab(linkedView === 'presence' ? 'presence' : linkedView === 'requests' ? 'requests' : linkedView === 'schedule' ? 'schedule' : 'employee')
    }
  }, [searchParams, canManage])

  useEffect(() => {
    const controller = new AbortController()
    let running = false
    setRequesting(false); setDecision(null); setSchedule(null); setRemoving(null)
    const load = async (initial = false) => {
      if (running || !/^\d{4}-\d{2}$/.test(month)) return
      running = true
      if (initial) { setBundle(null); setLoading(true); setError('') }
      const [year, monthNumber] = month.split('-')
      const params = new URLSearchParams({ year, month: String(Number(monthNumber)), ...(employeeId ? { staffId: employeeId } : {}) })
      try {
        const [monthly, requests, settings, adjustments] = await Promise.all([
          api.get<MonthlyAttendance>('/attendance/month?' + params, { signal: controller.signal }),
          api.get<{ requests: AttendanceRequest[] }>('/attendance/requests?' + params, { signal: controller.signal }),
          api.get<AttendanceSettings>('/attendance/settings?' + params, { signal: controller.signal }),
          api.get<{ adjustments: AttendanceAdjustment[] }>('/attendance/adjustments?' + params, { signal: controller.signal }),
        ])
        if (!controller.signal.aborted) { setBundle({ month: monthly, requests: requests.requests, settings, adjustments: adjustments.adjustments }); setError('') }
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not load attendance. Refresh to try again.')
      } finally { running = false; if (!controller.signal.aborted) setLoading(false) }
    }
    void load(true)
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void load() }, 30_000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [month, employeeId, retry, revision, recordRevision, store.me.id])

  useEffect(() => { setRequestStaff('') }, [month, employeeId])

  const saved = () => {
    if (!store.isCurrentSession()) return
    setRequesting(false); setDecision(null); setSchedule(null); setRemoving(null)
    setNotice('Saved.'); setRetry((value) => value + 1)
  }
  const rows = bundle?.month.staff ?? []
  const detail = rows.find((row) => row.userId === selected) ?? rows[0]
  const timezone = bundle?.month.timezone ?? store.agencyTimezone ?? 'Asia/Kolkata'
  const pending = (bundle?.requests ?? []).filter((request) => request.canDecide).length
  const incomingCover = (bundle?.requests ?? []).filter((request) => request.canRespondCover)
  const visibleRequests = (bundle?.requests ?? []).filter((request) => !own || request.userId === store.me.id)
  const filteredRequests = visibleRequests.filter((request) => !requestStaff || request.userId === requestStaff)
  const management = Boolean(bundle?.month.canManage && !own && bundle.settings.employees.some((person) => person.active && person.id !== store.me.id && (store.me.role === 'admin' || person.role === 'staff')))
  const tabs: { id: Tab; label: string; icon: LucideIcon; count?: number }[] = [
    { id: 'overview', label: own ? 'My month' : 'Team overview', icon: Users },
    { id: 'requests', label: own ? 'My requests' : 'Approvals', icon: ShieldCheck, count: own ? incomingCover.length : pending },
    { id: 'employee', label: own ? 'Daily records' : 'Employee record', icon: CalendarDays },
    { id: 'schedule', label: management ? 'Duty roster' : 'My schedule', icon: ClipboardList, count: bundle?.settings.dutyPlans.length },
    ...(own && store.me.role !== 'admin' ? [{ id: 'weekly' as const, label: 'Weekly off', icon: CalendarDays }] : []),
    { id: 'history', label: 'Correction history', icon: History },
    { id: 'presence', label: 'Sessions & activity', icon: Clock3 },
  ]
  const activeTab = tabs.some((item) => item.id === tab) ? tab : 'overview'
  const totals = rows.reduce((sum, row) => ({ present: sum.present + row.summary.presentDays, worked: sum.worked + row.summary.workedMinutes, unpaid: sum.unpaid + row.summary.unpaidMinutes, overtime: sum.overtime + row.summary.extraOtMinutes, beforeOt: sum.beforeOt + (row.summary.unpaidBeforeOtMinutes ?? row.summary.unpaidMinutes), offset: sum.offset + (row.summary.otOffsetMinutes ?? 0), unusedOt: sum.unusedOt + (row.summary.unusedOtMinutes ?? 0) }), { present: 0, worked: 0, unpaid: 0, overtime: 0, beforeOt: 0, offset: 0, unusedOt: 0 })
  const changeWeekly = async (weekStart: string, day: string) => {
    if (weeklyBusy) return
    setWeeklyBusy(true); setError('')
    try { await api.put('/attendance/weekly-off', { weekStart, day }); saved() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not change your weekly off.') }
    finally { setWeeklyBusy(false) }
  }

  return <div className="attendance-workspace">
    <PageHeader title={own ? 'My attendance' : 'Staff attendance'} eyebrow="Workforce operations" description={activeTab === 'presence' ? `Daily attendance sessions and browser presence · ${timezone}` : own ? 'Your attendance, leave and work schedule in one place.' : 'Monthly attendance, exceptions and approvals for your team.'} actions={<div className="attendance-header-tools">{activeTab !== 'presence' && <div className="attendance-filters"><Field label="Month"><Input type="month" value={month} min="2000-01" max="2100-12" required onChange={(event) => { if (event.target.value) { setMonth(event.target.value); setNotice('') } }} /></Field>{!own && <Field label="Employee"><Select value={staffId} onChange={(event) => { setStaffId(event.target.value); setSelected(event.target.value); setNotice('') }}><option value="">All employees</option>{(bundle?.settings.employees ?? store.users.filter((user) => user.role !== 'admin' && (store.me.role === 'admin' || user.role === 'staff' || user.id === store.me.id))).map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}</Select></Field>}<Button disabled={loading} icon={<RefreshCw size={16} />} onClick={() => setRetry((value) => value + 1)}>Refresh</Button></div>}<div className="attendance-header-primary">
      {activeTab !== 'presence' && canManage && store.me.role !== 'admin' && <div className="attendance-view-switch" aria-label="Attendance view"><Button variant={own ? 'ghost' : 'primary'} onClick={() => { setScope('team'); setNotice('') }} aria-pressed={!own}>Team</Button><Button variant={own ? 'primary' : 'ghost'} onClick={() => { setScope('mine'); setNotice('') }} aria-pressed={own}>My attendance</Button></div>}
      {store.me.role !== 'admin' && <Button variant="primary" disabled={!bundle || !!error} icon={<Plus size={16} />} onClick={() => setRequesting(true)}>New request</Button>}
    </div></div>} />
    <AttendanceClock />

    {activeTab !== 'presence' && error && <p role="alert" className="attendance-alert">{error}{bundle ? ' Displayed records may be out of date.' : ''} Use Refresh to retry.</p>}
    {notice && <p role="status" className="attendance-notice">{notice}</p>}
    {activeTab !== 'presence' && !loading && bundle && <SummaryCards items={own ? [
          { label: own ? 'Present days' : 'Employee present days', value: totals.present, detail: 'Recorded for selected month', icon: CheckCircle2 },
          { label: 'Worked time', value: minutesLabel(totals.worked), detail: 'Attendance policy calculation', icon: Clock3, tone: 'green' },
          { label: 'Unpaid time', value: minutesLabel(totals.unpaid), detail: 'Balance after approved overtime credit', icon: Timer, tone: 'amber' },
          { label: 'Extra overtime', value: minutesLabel(totals.overtime), detail: 'Approved overtime credit', icon: ShieldCheck, tone: 'violet' },
        ] : [
          { label: 'Active staff', value: rows.filter((row) => row.active).length, detail: 'Included in this month', icon: Users },
          { label: 'Pending permissions', value: visibleRequests.filter((request) => request.kind !== 'extra_ot' && request.status === 'pending').length, detail: 'Awaiting a decision', icon: ShieldCheck },
          { label: 'Pending extra OT', value: visibleRequests.filter((request) => request.kind === 'extra_ot' && request.status === 'pending').length, detail: 'Offsets late / short time', icon: Timer },
          { label: 'Unpaid time', value: minutesLabel(totals.unpaid), detail: 'After allowance and approved OT', icon: WalletCards },
        ]} />}
    <nav className="attendance-navigation" aria-label="Attendance sections">
      <div className="attendance-tabs">{tabs.filter((item) => item.id !== 'history').map(({ icon: Icon, ...item }) => <button type="button" key={item.id} aria-current={activeTab === item.id ? 'page' : undefined} onClick={() => { setTab(item.id); if (item.id === 'requests') setRequestStaff('') }}><Icon size={18} />{item.label}{Boolean(item.count) && <span>{item.count}</span>}</button>)}</div>
      <Dropdown width="w-60" trigger={({ open, toggle }) => <button type="button" className="attendance-more" aria-expanded={open} aria-current={activeTab === 'history' ? 'page' : undefined} onClick={toggle}>{activeTab === 'history' ? 'Correction history' : 'More'}<ChevronDown size={16} /></button>}>
        {(close) => <>{tabs.filter((item) => item.id === 'history').map(({ icon: Icon, ...item }) => <MenuItem key={item.id} icon={<Icon size={17} />} onClick={() => { setTab(item.id); close() }}>{item.label}</MenuItem>)}</>}
      </Dropdown>
    </nav>
    {activeTab === 'presence' ? presence(own ? store.me.id : staffId || selected, activityDate) : loading ? <p role="status" className="attendance-loading">Loading attendance records…</p> : bundle ? <>
      {own && detail && detail.summary.graceUsedMinutes >= bundle.month.policy.warningMinutes && <p role="status" className="attendance-notice">Monthly grace: {detail.summary.graceUsedMinutes} of {bundle.month.policy.monthlyGraceMinutes} minutes used; {detail.summary.graceRemainingMinutes} minutes remain. Paid permissions used: {detail.summary.permissionOccasionsUsed} of {bundle.month.policy.monthlyPermissionOccasions} occasions.</p>}
      {activeTab === 'overview' && <>
        <AttendancePolicyStrip policy={bundle.month.policy} />
        <AttendanceOverview rows={rows} requests={visibleRequests} own={own} month={month} onRecord={(id) => { setSelected(id); setTab('employee') }} onRequests={(id) => { setRequestStaff(id); setTab('requests') }} />
        <details className="attendance-ot-details"><summary>Overtime and unpaid time breakdown</summary><div className="attendance-ot-summary"><span>Unpaid before overtime <strong>{minutesLabel(totals.beforeOt)}</strong></span><span>Overtime applied <strong>{minutesLabel(totals.offset)}</strong></span><span>Unpaid balance <strong>{minutesLabel(totals.unpaid)}</strong></span><span>Unused overtime <strong>{minutesLabel(totals.unusedOt)}</strong></span><p className="attendance-help">Approved overtime offsets eligible late or short-work minutes. Full-day absence and unpaid leave remain separate.</p></div></details>
        {own && detail && <EmployeeCalendar employee={detail} onDay={() => setTab('employee')} />}
      </>}
      {activeTab === 'employee' && <>{rows.length > 1 && <Field label="Employee record" className="attendance-record-picker"><Select value={detail?.userId ?? ''} onChange={(event) => setSelected(event.target.value)}>{rows.map((row) => <option key={row.userId} value={row.userId}>{row.name}</option>)}</Select></Field>}{detail ? <EmployeeRecord employee={detail} timezone={timezone} canAdjust={management && !error && detail.userId !== store.me.id} onAdjust={() => setSchedule('adjustment')} onSessions={(date) => { setSelected(detail.userId); setActivityDate(date); setTab('presence') }} /> : <EmptyState title="No employee selected" description="Choose an employee from the month view." />}</>}
      {activeTab === 'requests' && <>
        {incomingCover.length > 0 && <Panel flush className="attendance-section"><PanelHeader title="Cover requests for you" meta="Accepting cover confirms that you can take responsibility for the colleague’s work." /><div className="attendance-request-list">{incomingCover.map((request) => <RequestCard key={request.id} request={request} timezone={timezone} disabled={!!error} onDecision={(action) => setDecision({ request, action })} />)}</div></Panel>}
        <Panel flush className="attendance-section"><PanelHeader title={own ? 'Your request history' : 'Requests and approvals'} meta={`${filteredRequests.length} requests in ${month}${requestStaff ? ` · ${rows.find((row) => row.userId === requestStaff)?.name ?? 'Selected employee'}` : ''}`} action={requestStaff && <Button onClick={() => setRequestStaff('')}>Show all requests</Button>} />{!filteredRequests.length ? <EmptyState title="No requests in this view" description={own ? 'Your permission, leave and overtime requests appear here after submission.' : 'Employee requests and their approval history will appear here.'} /> : <div className="attendance-request-list">{filteredRequests.map((request) => <RequestCard key={request.id} request={request} timezone={timezone} disabled={!!error} onDecision={(action) => setDecision({ request, action })} />)}</div>}</Panel>
      </>}
      {activeTab === 'weekly' && <Panel flush><PanelHeader title="Choose your weekly off" meta="Current planning window: this week and the next three weeks. Choose Friday before the Thursday deadline; Sunday then becomes a working day." /><div className="attendance-week-list">{bundle.settings.weeklyOff.length ? bundle.settings.weeklyOff.map((week) => <div key={week.weekStart} className="attendance-week"><div><strong>Week of {week.weekStart}</strong><p className="attendance-help">{week.approvedRequestId ? 'Approved rotational weekly off' : week.canChange ? `Change by ${stamp(week.deadline, timezone)}` : 'This week is locked'}</p></div>{['friday', 'sunday'].includes(week.day) ? <Select aria-label={`Weekly off for week ${week.weekStart}`} value={week.day} disabled={!week.canChange || weeklyBusy || !!error} onChange={(event) => void changeWeekly(week.weekStart, event.target.value)}><option value="sunday">Sunday off</option><option value="friday">Friday off</option></Select> : <Badge>{readableStatus(week.day)}</Badge>}</div>) : <EmptyState title="No selectable weeks" description="Weekly-off choices will appear when the planning window opens." />}</div></Panel>}
      {activeTab === 'schedule' && <ScheduleRecords settings={bundle.settings} canManage={management && !error} onNew={setSchedule} onRemove={setRemoving} />}
      {activeTab === 'history' && <Panel flush><PanelHeader title="Attendance correction history" meta="Original reason, actor and time are retained for every recorded adjustment." />{!bundle.adjustments.length ? <EmptyState title="No corrections in this month" description="Approved attendance corrections will appear here." /> : <div className="attendance-table-wrap"><table className="attendance-table"><thead><tr>{['Date', 'Employee', 'Correction', 'Detail', 'Reason', 'Recorded by'].map((title) => <th key={title}>{title}</th>)}</tr></thead><tbody>{bundle.adjustments.map((row) => <tr key={row.id}><td>{row.date}</td><td>{row.name}</td><td>{readableStatus(row.kind)}</td><td>{row.status ? attendanceLabels[row.status] : row.occurredAt ? `${readableStatus(row.action ?? '')} · ${stamp(row.occurredAt, timezone)}` : `${row.minutes ?? 0} minutes`}</td><td className="attendance-table-prose">{row.reason}</td><td>{row.actorName}<small>{stamp(row.createdAt, timezone)}</small></td></tr>)}</tbody></table></div>}</Panel>}
    </> : !error && <EmptyState title="Attendance unavailable" description="Refresh to load your records." />}
    {bundle && requesting && <RequestAttendanceDialog today={today} settings={bundle.settings} onClose={() => setRequesting(false)} onSaved={saved} />}
    {bundle && decision && <DecisionDialog {...decision} settings={bundle.settings} onClose={() => setDecision(null)} onSaved={saved} />}
    {bundle && schedule && <AttendanceScheduleDialog kind={schedule} today={today} userId={detail?.userId ?? ''} settings={bundle.settings} canCorrect={bundle.month.canCorrect} onClose={() => setSchedule(null)} onSaved={saved} />}
    {removing && <RemoveDutyDialog id={removing.id} name={removing.name} date={removing.date} onClose={() => setRemoving(null)} onSaved={saved} />}
  </div>
}

function EmployeeCalendar({ employee, onDay }: { employee: EmployeeMonth; onDay: () => void }) {
  return <Panel flush className="attendance-section"><PanelHeader title={employee.name + ' · Calendar'} /><div className="attendance-calendar">{employee.days.map((day) => <button key={day.date} type="button" onClick={onDay} aria-label={`${day.date}: ${attendanceLabels[day.status]}, open daily records`}><span>{new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(day.date + 'T12:00:00Z'))}</span><Status status={day.status} /><small>{day.workedMinutes > 0 ? minutesLabel(day.workedMinutes) : '—'}</small></button>)}</div></Panel>
}
function EmployeeRecord({ employee, timezone, canAdjust, onAdjust, onSessions }: { employee: EmployeeMonth; timezone: string; canAdjust: boolean; onAdjust: () => void; onSessions: (date: string) => void }) {
  return <Panel flush className="attendance-employee-record">
    <PanelHeader title={employee.name + ' · Daily record'} meta={`Times in ${timezone}. Daily unpaid time is shown before the monthly overtime offset.`} action={canAdjust && <Button icon={<Plus size={15} />} onClick={onAdjust}>Correct record</Button>} />
    <dl className="attendance-record-summary">
      <div><dt>Attendance</dt><dd>{employee.summary.attendancePercentage == null ? '—' : `${employee.summary.attendancePercentage}%`}</dd></div>
      <div><dt>Working days</dt><dd>{employee.summary.attendedDays ?? 0} of {employee.summary.scheduledDays ?? 0}</dd></div>
      <div><dt>Grace remaining</dt><dd>{employee.summary.graceRemainingMinutes} min</dd></div>
      <div><dt>Unpaid after approved OT</dt><dd>{minutesLabel(employee.summary.unpaidMinutes)}</dd></div>
    </dl>
    {!employee.days.length ? <EmptyState title="No attendance records in this month" description="Tracking begins with the first check-in, leave, permission or recorded adjustment." /> : <div className="attendance-table-wrap"><table className="attendance-table attendance-daily-table">
      <thead><tr>{['Date', 'Result', 'Work session', 'Required', 'Late / early', 'Paid allowance', 'Unpaid time'].map((title) => <th scope="col" key={title}>{title}</th>)}</tr></thead>
      <tbody>{employee.days.map((day) => <tr key={day.date}>
        <td data-label="Date"><strong>{new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(day.date + 'T12:00:00Z'))}</strong><small>{new Intl.DateTimeFormat('en-GB', { weekday: 'long', timeZone: 'UTC' }).format(new Date(day.date + 'T12:00:00Z'))}</small></td>
        <td data-label="Result"><Status status={day.status} />{day.provisional && <small className="attendance-provisional">Provisional{day.regularisationDeadline && <span>Correct by {stamp(day.regularisationDeadline, timezone)}</span>}</small>}</td>
        <td className="attendance-work-session" data-label="Work session"><strong>{minutesLabel(day.workedMinutes)} worked</strong><small>In: {stamp(day.checkIn, timezone)}</small><small>Out: {stamp(day.checkOut, timezone)}</small><Button className="mt-2" size="sm" variant="ghost" onClick={() => onSessions(day.date)}>View sessions & activity</Button></td>
        <td data-label="Required"><strong>{minutesLabel(day.requiredMinutes)}</strong><small>{day.shift.start}–{day.shift.end}</small><small>{day.shift.breakMinutes} min break</small></td>
        <td data-label="Late / early"><span>{day.lateMinutes} min late</span><small>{day.earlyMinutes} min early</small></td>
        <td data-label="Paid allowance"><span>{day.graceMinutes} min grace</span><small>{day.paidPermissionMinutes} min permission</small></td>
        <td data-label="Unpaid time"><strong className={day.unpaidMinutes > 0 ? 'attendance-unpaid' : ''}>{minutesLabel(day.unpaidMinutes)}</strong><small>{minutesLabel(day.extraOtMinutes)} approved OT</small></td>
      </tr>)}</tbody>
    </table></div>}
  </Panel>
}
function RequestCard({ request, timezone, disabled, onDecision }: { request: AttendanceRequest; timezone: string; disabled: boolean; onDecision: (action: Decision['action']) => void }) {
  return <article className="attendance-request"><div className="attendance-request-heading"><div><strong>{request.name}</strong><p>{request.date} · {requestLabels[request.kind]}{request.minutes > 0 ? ` · ${request.minutes} minutes` : ''}</p></div><Status status={request.status} /></div><p className="attendance-request-reason">{request.reason}</p>{request.emergencyOverride && <p className="attendance-help">Administrator allowed an extra permission occasion; remaining monthly grace still applies.</p>}{request.convertedFromPaidLeave && <p className="attendance-alert">Recorded as unpaid leave because the monthly paid-leave allowance is already used.</p>}{request.coverNote && <p className="attendance-help">Cover response: {request.coverNote}</p>}{request.status === 'approved' && request.coverStatus === 'accepted' && !request.coverStartedAt && <p className="attendance-help">Cover handover is scheduled for the leave date.</p>}{request.coverStartedAt && <p className="attendance-help">Work handover started {stamp(request.coverStartedAt, timezone)}{request.coverSettledAt ? ` · Settled ${stamp(request.coverSettledAt, timezone)}` : ' · In progress'}</p>}{request.coverName && <p className="attendance-help">Cover: {request.coverName} · {readableStatus(request.coverStatus ?? 'requested')}</p>}<div className="attendance-approvals">{request.requiredStages.map((stage) => { const result = request.approvals[stage]; return <div key={stage}><strong>{approvalLabel(stage)}</strong><span>{result ? `${result.approved ? 'Approved' : 'Rejected'} by ${result.name}` : 'Awaiting decision'}</span>{result && <><small>{stamp(result.at, timezone)}</small>{result.reason && <p>{result.reason}</p>}</>}</div> })}</div><div className="attendance-request-footer"><small>Requested {stamp(request.createdAt, timezone)}</small><div className="attendance-actions">{request.canNominateCover && <Button size="sm" disabled={disabled} onClick={() => onDecision('nominate')}>Choose cover</Button>}{request.canRespondCover && <><Button size="sm" disabled={disabled} onClick={() => onDecision('decline_cover')}>Decline cover</Button><Button size="sm" variant="primary" disabled={disabled} onClick={() => onDecision('accept_cover')}>Accept cover</Button></>}{request.canDecide && <><Button size="sm" variant="dangerGhost" disabled={disabled} onClick={() => onDecision('reject')}>Reject</Button><Button size="sm" variant="primary" disabled={disabled} onClick={() => onDecision('approve')}>Approve</Button></>}</div></div></article>
}
function ScheduleRecords({ settings, canManage, onNew, onRemove }: { settings: AttendanceSettings; canManage: boolean; onNew: (kind: ScheduleKind) => void; onRemove: (plan: DutyPlan) => void }) {
  const { me } = useStore()
  return <div className="attendance-section-stack">
    <Panel flush><PanelHeader title="Duty roster" meta="Date-specific shifts take precedence over normal work timings." action={canManage && <Button icon={<Plus size={15} />} onClick={() => onNew('duty')}>Plan duty</Button>} />{!settings.dutyPlans.length ? <EmptyState title="No duty plans" description="Normal work timings and weekly-off choices apply." /> : <div className="attendance-table-wrap"><table className="attendance-table"><thead><tr><th>Date</th><th>Employee</th><th>Shift</th><th>Break</th><th>Reason</th>{canManage && <th>Action</th>}</tr></thead><tbody>{settings.dutyPlans.map((plan) => <tr key={plan.id}><td>{plan.date}</td><td>{plan.name}</td><td>{plan.shift.start}–{plan.shift.end}</td><td>{plan.shift.breakMinutes} min</td><td className="attendance-table-prose">{plan.reason}</td>{canManage && <td><Button size="sm" variant="dangerGhost" icon={<Trash2 size={14} />} disabled={plan.userId === me.id} aria-label={`Remove duty plan for ${plan.name} on ${plan.date}`} onClick={() => onRemove(plan)}>Remove</Button></td>}</tr>)}</tbody></table></div>}</Panel>
    <Panel flush><PanelHeader title="Work timings" meta="Effective dates retain the schedule used for historical attendance." action={canManage && <Button icon={<Plus size={15} />} onClick={() => onNew('timing')}>Set timings</Button>} />{!settings.workTimings.length ? <EmptyState title="Default timings apply" description="No individual timing assignment is recorded for this view." /> : <div className="attendance-table-wrap"><table className="attendance-table"><thead><tr><th>Employee</th><th>Effective from</th><th>Shift</th><th>Break</th><th>Reason</th></tr></thead><tbody>{settings.workTimings.map((row) => <tr key={row.id}><td>{row.name}</td><td>{row.effectiveFrom}</td><td>{row.shift.start}–{row.shift.end}</td><td>{row.shift.breakMinutes} min</td><td className="attendance-table-prose">{row.reason}</td></tr>)}</tbody></table></div>}</Panel>
    <Panel flush><PanelHeader title="Calendar exceptions" meta="Employee holidays, weekly offs and recorded leave." action={canManage && <Button icon={<Plus size={15} />} onClick={() => onNew('calendar')}>Set calendar day</Button>} />{!settings.calendarDays.length ? <EmptyState title="No calendar exceptions" description="Approved requests and weekly-off rules continue to apply." /> : <div className="attendance-table-wrap"><table className="attendance-table"><thead><tr><th>Date</th><th>Employee</th><th>Status</th><th>Reason</th></tr></thead><tbody>{settings.calendarDays.map((row) => <tr key={row.id}><td>{row.date}</td><td>{row.name}</td><td><Status status={row.status} /></td><td className="attendance-table-prose">{row.reason}</td></tr>)}</tbody></table></div>}</Panel>
  </div>
}
