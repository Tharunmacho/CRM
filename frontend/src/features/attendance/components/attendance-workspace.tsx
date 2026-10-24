import { useEffect, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle2, ChevronLeft, ChevronRight, Clock3, Plus, RefreshCw, ShieldCheck, Timer, Users, WalletCards } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { agencyDay } from '@/shared/lib/task-time'
import { EmptyState, PageHeader, Panel } from '@/shared/ui/display'
import { Button, Field, IconButton, Input, Select } from '@/shared/ui/primitives'
import { Tabs } from '@/shared/ui/overlay'
import { SummaryCards } from '@/shared/ui/workspace'
import { AttendanceClock } from './attendance-clock'
import { AttendanceOverview, AttendancePolicyStrip, MonthCalendar, MonthMatrix } from './attendance-overview'
import { CorrectionHistory, EmployeeRecord, ScheduleRecords, WeeklyOff } from './attendance-records'
import { RequestsView, type DecisionAction } from './attendance-requests'
import { AttendanceScheduleDialog, DecisionDialog, RemoveDutyDialog, RequestAttendanceDialog, type ScheduleKind } from './attendance-forms'
import { useAttendance } from '../attendance-context'
import { minutesLabel, type AttendanceAdjustment, type AttendanceRequest, type AttendanceSettings, type DutyPlan, type MonthlyAttendance } from '../policy-model'
import { clockTime, shiftMonth } from '../format'
import '../attendance-workspace.css'
import '../attendance-overview.css'

type Tab = 'overview' | 'employee' | 'requests' | 'weekly' | 'schedule' | 'history' | 'presence'
type Decision = { request: AttendanceRequest; action: DecisionAction }
interface Bundle { month: MonthlyAttendance; requests: AttendanceRequest[]; settings: AttendanceSettings; adjustments: AttendanceAdjustment[] }

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
  const [loadedAt, setLoadedAt] = useState<string | null>(null)
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
        if (!controller.signal.aborted) { setBundle({ month: monthly, requests: requests.requests, settings, adjustments: adjustments.adjustments }); setLoadedAt(new Date().toISOString()); setError('') }
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'We could not load attendance. Check your internet and press Refresh.')
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
  const incomingCover = (bundle?.requests ?? []).filter((request) => request.canRespondCover)
  const visibleRequests = (bundle?.requests ?? []).filter((request) => !own || request.userId === store.me.id)
  const filteredRequests = visibleRequests.filter((request) => !requestStaff || request.userId === requestStaff)
  const management = Boolean(bundle?.month.canManage && !own && bundle.settings.employees.some((person) => person.active && person.id !== store.me.id && (store.me.role === 'admin' || person.role === 'staff')))
  const tabs: { key: Tab; label: string }[] = [
    { key: 'overview', label: own ? 'My month' : 'Team overview' },
    { key: 'requests', label: own ? 'My requests' : 'Approvals' },
    { key: 'employee', label: own ? 'Daily records' : 'Employee days' },
    { key: 'schedule', label: management ? 'Shift plan' : 'My schedule' },
    ...(own && store.me.role !== 'admin' ? [{ key: 'weekly' as const, label: 'Weekly off' }] : []),
    { key: 'history', label: 'Changes made' },
    { key: 'presence', label: 'Check-ins and CRM use' },
  ]
  const activeTab = tabs.some((item) => item.key === tab) ? tab : 'overview'
  const totals = rows.reduce((sum, row) => ({ present: sum.present + row.summary.presentDays, worked: sum.worked + row.summary.workedMinutes, unpaid: sum.unpaid + row.summary.unpaidMinutes, overtime: sum.overtime + row.summary.extraOtMinutes, beforeOt: sum.beforeOt + (row.summary.unpaidBeforeOtMinutes ?? row.summary.unpaidMinutes), offset: sum.offset + (row.summary.otOffsetMinutes ?? 0), unusedOt: sum.unusedOt + (row.summary.unusedOtMinutes ?? 0) }), { present: 0, worked: 0, unpaid: 0, overtime: 0, beforeOt: 0, offset: 0, unusedOt: 0 })
  const changeWeekly = async (weekStart: string, day: string) => {
    if (weeklyBusy) return
    setWeeklyBusy(true); setError('')
    try { await api.put('/attendance/weekly-off', { weekStart, day }); saved() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'We could not change your weekly off. Try again.') }
    finally { setWeeklyBusy(false) }
  }
  const people = bundle?.settings.employees ?? store.users.filter((user) => user.role !== 'admin' && (store.me.role === 'admin' || user.role === 'staff' || user.id === store.me.id))
  const changeMonth = (value: string) => { if (/^\d{4}-\d{2}$/.test(value) && value >= '2000-01' && value <= '2100-12') { setMonth(value); setNotice('') } }

  return <div className="attendance-workspace">
    <PageHeader title={own ? 'My attendance' : 'Staff attendance'} description={activeTab === 'presence' ? `Check-ins and check-outs for each day, and when people were using the CRM. Times are in ${timezone}.` : own ? 'Your attendance, leave and work schedule in one place.' : 'Attendance for the month, problem days and approvals for your team.'} actions={<>
      {canManage && store.me.role !== 'admin' && <Tabs variant="pill" className="att-scope" tabs={[{ key: 'team', label: 'Team' }, { key: 'mine', label: 'My attendance' }]} active={own ? 'mine' : 'team'} onChange={(key) => { setScope(key); setNotice('') }} />}
      {store.me.role !== 'admin' && <Button variant="primary" disabled={!bundle || !!error} icon={<Plus size={16} />} onClick={() => setRequesting(true)}>New request</Button>}
    </>} />
    <AttendanceClock targetMinutes={bundle?.month.policy.workMinutes} />

    {activeTab !== 'presence' && error && <p role="alert" className="att-callout att-callout-error">{error}{bundle ? ' What you see may be out of date.' : ''} Press Refresh to try again.</p>}
    {notice && <p role="status" className="att-callout att-callout-ok">{notice}</p>}
    {activeTab !== 'presence' && !loading && bundle && <SummaryCards items={own ? [
      { label: 'Present days', value: totals.present, detail: 'In the month shown', icon: CheckCircle2 },
      { label: 'Worked time', value: minutesLabel(totals.worked), detail: 'Counted by company rules', icon: Clock3, tone: 'green' },
      { label: 'Unpaid time', value: minutesLabel(totals.unpaid), detail: 'After approved overtime is taken off', icon: Timer, tone: 'amber' },
      { label: 'Extra overtime', value: minutesLabel(totals.overtime), detail: 'Approved extra hours', icon: ShieldCheck },
    ] : [
      { label: 'Active staff', value: rows.filter((row) => row.active).length, detail: 'Counted this month', icon: Users },
      { label: 'Permissions waiting', value: visibleRequests.filter((request) => request.kind !== 'extra_ot' && request.status === 'pending').length, detail: 'Waiting for a decision', icon: ShieldCheck, tone: 'amber' },
      { label: 'Extra overtime waiting', value: visibleRequests.filter((request) => request.kind === 'extra_ot' && request.status === 'pending').length, detail: 'Can cover late or short time', icon: Timer, tone: 'amber' },
      { label: 'Unpaid time', value: minutesLabel(totals.unpaid), detail: 'After free minutes and approved overtime', icon: WalletCards, tone: 'red' },
    ]} />}

    <div className="att-tabbar"><Tabs tabs={tabs} active={activeTab} onChange={(key) => { setTab(key as Tab); if (key === 'requests') setRequestStaff('') }} /></div>

    {activeTab !== 'presence' && <div className="att-toolbar" role="group" aria-label="Attendance filters">
      <div className="att-tool att-tool-month">
        <span className="att-tool-label" id="att-month-label">Month</span>
        <div className="att-stepper">
          <IconButton label="Previous month" className="att-step" onClick={() => changeMonth(shiftMonth(month, -1))}><ChevronLeft size={16} /></IconButton>
          <Input type="month" aria-labelledby="att-month-label" value={month} min="2000-01" max="2100-12" required onChange={(event) => changeMonth(event.target.value)} />
          <IconButton label="Next month" className="att-step" onClick={() => changeMonth(shiftMonth(month, 1))}><ChevronRight size={16} /></IconButton>
        </div>
      </div>
      {!own && <Field label="Employee" className="att-tool-select"><Select value={staffId} onChange={(event) => { setStaffId(event.target.value); setSelected(event.target.value); setNotice('') }}><option value="">All employees</option>{people.map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}</Select></Field>}
      {activeTab === 'employee' && rows.length > 1 && <Field label="Employee shown" className="att-tool-select"><Select value={detail?.userId ?? ''} onChange={(event) => setSelected(event.target.value)}>{rows.map((row) => <option key={row.userId} value={row.userId}>{row.name}</option>)}</Select></Field>}
      <div className="att-tool-end">
        {loadedAt && <span className="att-updated">Updated {clockTime(loadedAt, timezone)}</span>}
        <Button disabled={loading} icon={<RefreshCw size={16} className={loading ? 'animate-spin' : ''} />} onClick={() => setRetry((value) => value + 1)}>Refresh</Button>
      </div>
    </div>}

    {activeTab === 'presence' ? presence(own ? store.me.id : staffId || selected, activityDate) : loading ? <p role="status" className="att-loading">Loading attendance records…</p> : bundle ? <>
      {own && detail && detail.summary.graceUsedMinutes >= bundle.month.policy.warningMinutes && <p role="status" className="att-callout att-callout-warn">Free late minutes this month: {detail.summary.graceUsedMinutes} of {bundle.month.policy.monthlyGraceMinutes} used, {detail.summary.graceRemainingMinutes} left. Paid permissions used: {detail.summary.permissionOccasionsUsed} of {bundle.month.policy.monthlyPermissionOccasions}.</p>}
      {activeTab === 'overview' && <div className="att-stack">
        <AttendancePolicyStrip policy={bundle.month.policy} />
        <AttendanceOverview rows={rows} requests={visibleRequests} own={own} month={month} onRecord={(id) => { setSelected(id); setTab('employee') }} onRequests={(id) => { setRequestStaff(id); setTab('requests') }} />
        {own ? (detail && <MonthCalendar employee={detail} month={month} today={today} onDay={() => setTab('employee')} />) : <MonthMatrix rows={rows} month={month} today={today} onRecord={(id) => { setSelected(id); setTab('employee') }} />}
        <details className="att-ot">
          <summary>How overtime and unpaid time add up</summary>
          <dl className="att-ot-grid">
            <div><dt>Unpaid before overtime</dt><dd>{minutesLabel(totals.beforeOt)}</dd></div>
            <div><dt>Overtime used</dt><dd>{minutesLabel(totals.offset)}</dd></div>
            <div><dt>Unpaid time left</dt><dd>{minutesLabel(totals.unpaid)}</dd></div>
            <div><dt>Overtime not used</dt><dd>{minutesLabel(totals.unusedOt)}</dd></div>
          </dl>
          <p className="att-help">Approved overtime covers late or short minutes. Full days absent and unpaid leave are counted separately.</p>
        </details>
      </div>}
      {activeTab === 'employee' && (detail ? <EmployeeRecord employee={detail} timezone={timezone} canAdjust={management && !error && detail.userId !== store.me.id} onAdjust={() => setSchedule('adjustment')} onSessions={(date) => { setSelected(detail.userId); setActivityDate(date); setTab('presence') }} /> : <Panel flush><EmptyState title="No employee selected" description="Choose an employee from the month view." /></Panel>)}
      {activeTab === 'requests' && <RequestsView requests={filteredRequests} own={own} timezone={timezone} disabled={!!error} incomingCover={incomingCover}
        heading={own ? 'Your request history' : 'Requests and approvals'}
        headingMeta={`${filteredRequests.length} ${filteredRequests.length === 1 ? 'request' : 'requests'} in ${month}${requestStaff ? ` · ${rows.find((row) => row.userId === requestStaff)?.name ?? 'Selected employee'}` : ''}`}
        clearAction={requestStaff ? <Button onClick={() => setRequestStaff('')}>Show all requests</Button> : undefined}
        onDecision={(request, action) => setDecision({ request, action })} />}
      {activeTab === 'weekly' && <WeeklyOff settings={bundle.settings} timezone={timezone} busy={weeklyBusy} disabled={!!error} onChange={(weekStart, day) => void changeWeekly(weekStart, day)} />}
      {activeTab === 'schedule' && <ScheduleRecords settings={bundle.settings} canManage={management && !error} onNew={setSchedule} onRemove={setRemoving} />}
      {activeTab === 'history' && <CorrectionHistory adjustments={bundle.adjustments} timezone={timezone} />}
    </> : !error && <Panel flush><EmptyState title="Attendance unavailable" description="Refresh to load your records." /></Panel>}
    {bundle && requesting && <RequestAttendanceDialog today={today} settings={bundle.settings} onClose={() => setRequesting(false)} onSaved={saved} />}
    {bundle && decision && <DecisionDialog {...decision} settings={bundle.settings} onClose={() => setDecision(null)} onSaved={saved} />}
    {bundle && schedule && <AttendanceScheduleDialog kind={schedule} today={today} userId={detail?.userId ?? ''} settings={bundle.settings} canCorrect={bundle.month.canCorrect} onClose={() => setSchedule(null)} onSaved={saved} />}
    {removing && <RemoveDutyDialog id={removing.id} name={removing.name} date={removing.date} onClose={() => setRemoving(null)} onSaved={saved} />}
  </div>
}
