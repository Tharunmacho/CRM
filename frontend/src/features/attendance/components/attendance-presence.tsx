import { useEffect, useState } from 'react'
import { Activity, ArrowDown, Clock3, Coffee, Info, Pencil, RefreshCw, WifiOff } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { liveInterval } from '@/shared/state/live'
import { agencyDay, browserTimeZone } from '@/shared/lib/task-time'
import { Badge, EmptyState, Panel } from '@/shared/ui/display'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { DatePicker } from '@/shared/ui/date-picker'
import { DataTable, type Column } from '@/shared/ui/table'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { PresenceBadge } from './attendance-clock'
import { CardHead, Callout } from './attendance-status'
import { useAttendance } from '../attendance-context'
import { dayLabel } from '../format'
import { durationLabel, type AttendanceResponse, type AttendanceSession, type StaffAttendance } from '../model'
import '../attendance-presence.css'

function timeLabel(value: string | null, timezone: string) {
  return value ? new Intl.DateTimeFormat('en-GB', { timeZone: timezone, hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' }).format(new Date(value)) : '—'
}

export function PresenceView({ initialStaffId, initialDate, onUpdated }: { initialStaffId: string; initialDate: string; onUpdated: () => void }) {
  const store = useStore()
  const { revision, refresh } = useAttendance()
  const team = store.me.role !== 'staff'
  const [date, setDate] = useState(initialDate)
  const [staffId, setStaffId] = useState(team ? initialStaffId : store.me.id)
  const [result, setResult] = useState<AttendanceResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [retry, setRetry] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  useEffect(() => { setStaffId(team ? initialStaffId : store.me.id); setSelected(null); setDate(initialDate) }, [initialStaffId, initialDate, team, store.me.id])

  useEffect(() => {
    const controller = new AbortController()
    let running = false
    let again = false // a change arrived while a read was running: that read may predate it
    const load = async (initial = false) => {
      if (running || !date) return
      running = true
      if (initial) { setResult(null); setLoading(true); setError('') }
      try {
        const params = new URLSearchParams({ date, ...(staffId ? { staffId } : {}) })
        const response = await api.get<AttendanceResponse>('/attendance?' + params, { signal: controller.signal })
        if (!controller.signal.aborted) { setResult(response); setError('') }
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'We could not load attendance records. Press Refresh records to try again.')
      } finally {
        running = false; if (!controller.signal.aborted) setLoading(false)
        if (again && !controller.signal.aborted) { again = false; void load() }
      }
    }
    void load(true)
    const interval = liveInterval(() => { if (document.visibilityState === 'visible') void load() }, 30_000, ['attendance', 'users'], {
      onChange: () => { if (running) again = true; else void load() },
    })
    const resume = () => { if (document.visibilityState === 'visible') void load() }
    window.addEventListener('focus', resume); window.addEventListener('online', resume); document.addEventListener('visibilitychange', resume)
    return () => { controller.abort(); interval(); window.removeEventListener('focus', resume); window.removeEventListener('online', resume); document.removeEventListener('visibilitychange', resume) }
  }, [date, staffId, retry, revision, store.me.id])

  const rows = result?.staff ?? []
  const detail = rows.find((row) => row.userId === selected) ?? (rows.length === 1 ? rows[0] : null)
  const timezone = result?.timezone ?? store.agencyTimezone ?? browserTimeZone()
  const totals = rows.reduce((sum, row) => ({
    attendance: sum.attendance + row.attendance.totalSeconds,
    active: sum.active + row.presence.activeSeconds,
    idle: sum.idle + row.presence.idleSeconds,
    offline: sum.offline + row.presence.offlineSeconds,
  }), { attendance: 0, active: 0, idle: 0, offline: 0 })

  const inspect = (id: string) => { setSelected(id); requestAnimationFrame(() => document.getElementById('attendance-session-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' })) }
  const activitySeconds = totals.active + totals.idle + totals.offline
  const hasTrackedStaff = rows.some((row) => row.presence.tracked)

  const columns: Column<StaffAttendance>[] = [
    { key: 'name', header: 'Employee', width: '22%', render: (row) => <RecordIdentity name={row.name + (row.userId === store.me.id ? ' (you)' : '')} detail={(row.role === 'manager' ? 'Manager' : row.role === 'admin' ? 'Admin' : 'Staff') + (!row.active ? ' · Inactive account' : '')} /> },
    { key: 'state', header: 'Now', width: '17%', render: (row) => <div className="presence-state-stack"><Badge variant="dot" tone={row.attendance.status === 'checked_in' ? 'green' : 'neutral'}>{row.attendance.status === 'checked_in' ? 'Checked in' : 'Checked out'}</Badge>{row.presence.tracked ? <PresenceBadge status={row.presence.status} /> : <span className="att-muted">CRM use not tracked</span>}</div> },
    { key: 'checked', header: 'Checked in', width: '12%', render: (row) => <div className="att-two"><strong>{durationLabel(row.attendance.totalSeconds)}</strong><small>{row.attendance.sessions.length} {row.attendance.sessions.length === 1 ? 'check-in' : 'check-ins'}</small></div> },
    { key: 'online', header: 'Online in CRM', width: '11%', render: (row) => <span className="tnum">{row.presence.tracked ? durationLabel(row.presence.activeSeconds) : 'Not tracked'}</span> },
    { key: 'idle', header: 'Idle in CRM', width: '10%', render: (row) => <span className="tnum">{row.presence.tracked ? durationLabel(row.presence.idleSeconds) : 'Not tracked'}</span> },
    { key: 'away', header: 'Away or offline', width: '12%', render: (row) => <span className="tnum">{row.presence.tracked ? durationLabel(row.presence.offlineSeconds) : 'Not tracked'}</span> },
    { key: 'record', header: 'Record', width: '9%', actions: true, align: 'right', render: (row) => <Button size="sm" icon={<ArrowDown size={14} />} aria-label={'View check-ins for ' + row.name} title={'View check-ins for ' + row.name} onClick={() => inspect(row.userId)}>Check-ins</Button> },
  ]

  return <div className="attendance-presence att-stack">
    <div className="att-toolbar presence-filters" role="group" aria-label="Check-ins and CRM use filters">
      <Field label="Attendance date"><DatePicker value={date} onChange={(value) => { if (value) { setDate(value); setSelected(null) } }} max={agencyDay(new Date(), timezone)} required /></Field>
      {team && <Field label="Employee"><Select value={staffId} onChange={(event) => { setStaffId(event.target.value); setSelected(null) }}><option value="">All employees</option>{store.users.filter((user) => user.role !== 'admin' && (store.me.role === 'admin' || user.role === 'staff' || user.id === store.me.id)).map((user) => <option key={user.id} value={user.id}>{user.name}{!user.active ? ' (inactive)' : ''}{user.role !== 'staff' ? ` (${user.role})` : ''}</option>)}</Select></Field>}
      <div className="att-tool-end">
        {result && <span className="att-updated">Updated {timeLabel(result.generatedAt, timezone)} · refreshes by itself every 30 seconds</span>}
        <Button disabled={loading} icon={<RefreshCw size={16} className={loading ? 'animate-spin' : ''} />} onClick={() => setRetry((value) => value + 1)}>Refresh records</Button>
      </div>
    </div>
    <div className="presence-explanation"><Info size={18} aria-hidden="true" /><p><strong>Check-in time and CRM use are different things.</strong> Check-in and check-out show attendance. Online and Idle show whether the CRM was open on screen. Away or offline means the CRM tab was hidden, the browser was closed or the internet dropped. It does not mean the person was not working. CRM use is tracked for Staff, not Managers.</p></div>
    {error && <Callout tone="error" role="alert">{error.replace(/[.!?]*$/, '.')} Press Refresh records to try again.{result && ' What you see may be out of date.'}</Callout>}
    {loading ? <p role="status" className="att-loading">Loading attendance records…</p> : result && <>
      <SummaryCards items={[
        { label: 'Checked-in time', value: durationLabel(totals.attendance), detail: 'Checked-in time on this date', icon: Clock3 },
        { label: 'Online in CRM', value: hasTrackedStaff ? durationLabel(totals.active) : 'Not tracked', detail: 'CRM open and in use', icon: Activity, tone: 'green' },
        { label: 'Idle in CRM', value: hasTrackedStaff ? durationLabel(totals.idle) : 'Not tracked', detail: 'CRM open but not in use', icon: Coffee, tone: 'amber' },
        { label: 'Away or offline', value: hasTrackedStaff ? durationLabel(totals.offline) : 'Not tracked', detail: 'Checked in, but CRM not open', icon: WifiOff },
      ]} />
      {activitySeconds > 0 && <Panel className="presence-day-breakdown"><div><strong>How staff used the CRM while checked in</strong><span>{durationLabel(activitySeconds)} counted or estimated · {dayLabel(date)}</span></div><ActivityBar row={{ activeSeconds: totals.active, idleSeconds: totals.idle, offlineSeconds: totals.offline }} /><PresenceLegend /></Panel>}
      <Panel flush className="att-card presence-roster">
        <CardHead title={team ? 'Daily team attendance' : 'Your daily record'} meta={`${dayLabel(date)} totals. The badges show the current state.`} />
        {!rows.length ? <EmptyState title="No employees in this view" description="Choose another employee to see their attendance and sessions." /> : <DataTable compact stackBelow={1280} rows={rows} columns={columns} rowKey={(row) => row.userId} pageSize={50} rowClassName={(row) => detail?.userId === row.userId ? 'presence-row-selected' : undefined} />}
      </Panel>
      {detail && <SessionDetail row={detail} timezone={timezone} onUpdated={() => { setRetry((value) => value + 1); onUpdated(); void refresh() }} />}
    </>}
    <details className="att-ot presence-rules"><summary>How CRM use is counted</summary><ul><li>Online: the CRM is open on screen and the person clicked or typed in the last 5 minutes.</li><li>Idle: the CRM is open on screen but nothing was clicked or typed recently, or the page has just opened.</li><li>Away or offline: the CRM tab is not open on screen. If the signal is lost, this shows after 90 seconds.</li><li>If several tabs are open, they are counted together. Only time while checked in is counted. Time after check-out is not added.</li><li>These are estimates from the browser, not a score of how well someone works. We do not watch other apps, take screenshots or record what is typed.</li></ul></details>
  </div>
}

function PresenceLegend() {
  return <div className="presence-legend"><span><i className="presence-online" />Online in CRM</span><span><i className="presence-idle" />Idle in CRM</span><span><i className="presence-offline" />Away or offline</span></div>
}

function ActivityBar({ row }: { row: Pick<StaffAttendance['presence'], 'activeSeconds' | 'idleSeconds' | 'offlineSeconds'> }) {
  const total = row.activeSeconds + row.idleSeconds + row.offlineSeconds
  return <div className="presence-duration-bar" role="img" aria-label={`Online ${durationLabel(row.activeSeconds)}, idle ${durationLabel(row.idleSeconds)}, away or no signal ${durationLabel(row.offlineSeconds)}`}>{[[row.activeSeconds, 'online'], [row.idleSeconds, 'idle'], [row.offlineSeconds, 'offline']].map(([seconds, state]) => Number(seconds) > 0 && <span key={state} className={`presence-${state}`} style={{ width: `${Number(seconds) / total * 100}%` }} />)}</div>
}

function SessionDetail({ row, timezone, onUpdated }: { row: StaffAttendance; timezone: string; onUpdated: () => void }) {
  const { me, user } = useStore()
  const [editing, setEditing] = useState<AttendanceSession | null>(null)
  return <><div id="attendance-session-detail" className="presence-session-detail">
    <Panel flush className="att-card">
      <CardHead title={row.name + ': check-in and check-out'} meta="Times recorded. Only time on the chosen date is counted." />
      {!row.attendance.sessions.length ? <p className="presence-empty">No check-ins on this date.</p> : <ol className="presence-list">{row.attendance.sessions.map((session) => <li key={session.id}>
        <div><p className="presence-when">{timeLabel(session.checkedInAt, timezone)} to {session.checkedOutAt ? timeLabel(session.checkedOutAt, timezone) : 'still checked in'}</p><p className="att-help">{session.checkedOutAt ? 'Check-in and check-out recorded' : 'Check out to end this session'}</p>{session.correctionReason && <p className="presence-correction">Fixed by {user(session.correctedBy ?? '')?.name || 'Admin'}: {session.correctionReason}</p>}</div>
        <div className="presence-row-end"><strong className="tnum">{durationLabel(session.durationSeconds)}</strong>{me.role === 'admin' && <Button size="sm" variant="ghost" icon={<Pencil size={13} />} onClick={() => setEditing(session)}>Fix</Button>}</div>
      </li>)}</ol>}
    </Panel>
    <Panel flush className="att-card">
      <CardHead title="CRM use during the day" meta="When the CRM was in use while checked in." />
      {!row.presence.tracked ? <p className="presence-empty">CRM use is not tracked for this account. Its checked-in time is not counted as away.</p> : !row.presence.sessions.length ? <p className="presence-empty">This will show after a check-in on this date.</p> : <><div className="presence-person-breakdown"><ActivityBar row={row.presence} /><PresenceLegend /></div><ol className="presence-list presence-timeline">{row.presence.sessions.map((session, index) => <li key={session.startedAt + index}><div><PresenceBadge status={session.status} /><span className="att-help">{timeLabel(session.startedAt, timezone)} to {timeLabel(session.endedAt, timezone)}</span></div><strong className="tnum">{session.durationSeconds < 60 ? `${session.durationSeconds}s` : durationLabel(session.durationSeconds)}</strong></li>)}</ol></>}
    </Panel>
  </div>{editing && <CorrectionDialog session={editing} name={row.name} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); onUpdated() }} />}</>
}

function localDateTime(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}T${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}`
}

function CorrectionDialog({ session, name, onClose, onSaved }: { session: AttendanceSession; name: string; onClose: () => void; onSaved: () => void }) {
  const [start, setStart] = useState(localDateTime(session.checkedInAt))
  const [end, setEnd] = useState(localDateTime(session.checkedOutAt))
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = async () => {
    if (busy) return
    if (reason.trim().length < 3) { setError('Say why this needs to be fixed.'); return }
    if (!start || !Number.isFinite(new Date(start).getTime()) || (end && !Number.isFinite(new Date(end).getTime()))) { setError('Enter valid times.'); return }
    if (end && new Date(end) < new Date(start)) { setError('Check-out must be after check-in.'); return }
    setBusy(true); setError('')
    try {
      await api.patch('/attendance/sessions/' + session.id, { checkedInAt: new Date(start).toISOString(), checkedOutAt: end ? new Date(end).toISOString() : null, reason: reason.trim() })
      onSaved()
    } catch (err) { setError(err instanceof Error ? err.message : 'We could not save the fix. Try again.') }
    finally { setBusy(false) }
  }
  return <Dialog open title="Fix attendance" description={name + ' · Your change and reason are saved in the history.'} onClose={() => { if (!busy) onClose() }}
    footer={<><Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button><Button type="submit" form="attendance-correction" variant="primary" disabled={busy}>{busy ? 'Saving…' : 'Save fix'}</Button></>}>
    <form id="attendance-correction" onSubmit={(event) => { event.preventDefault(); void save() }}>
      {error && <p role="alert" className="attendance-alert">{error}</p>}
      <p className="attendance-help mb-4">Times use your device time zone: {Intl.DateTimeFormat().resolvedOptions().timeZone}.</p>
      <fieldset disabled={busy} className="space-y-4">
        <Field label="Check-in time" required><Input type="datetime-local" step={1} required value={start} onChange={(event) => setStart(event.target.value)} /></Field>
        <Field label="Check-out time" hint="Leave empty only if the person is still checked in."><Input type="datetime-local" step={1} value={end} onChange={(event) => setEnd(event.target.value)} /></Field>
        <Field label="Why are you fixing this?" required><Textarea required minLength={3} maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} rows={3} /></Field>
      </fieldset>
    </form>
  </Dialog>
}
