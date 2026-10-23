import { useEffect, useState } from 'react'
import { Activity, ArrowDown, Clock3, Coffee, Info, Pencil, RefreshCw, WifiOff } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { agencyDay } from '@/shared/lib/task-time'
import { Badge, EmptyState, Panel, PanelHeader } from '@/shared/ui/display'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { DatePicker } from '@/shared/ui/date-picker'
import { SummaryCards } from '@/shared/ui/workspace'
import { PresenceBadge } from '@/features/attendance/components/attendance-clock'
import { AttendanceWorkspace } from '@/features/attendance/components/attendance-workspace'
import { useAttendance } from '@/features/attendance/attendance-context'
import { durationLabel, type AttendanceResponse, type AttendanceSession, type StaffAttendance } from '@/features/attendance/model'
import '@/features/attendance/attendance-presence.css'

function timeLabel(value: string | null, timezone: string) {
  return value ? new Intl.DateTimeFormat('en-GB', { timeZone: timezone, hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' }).format(new Date(value)) : '—'
}

export default function AttendancePage() {
  const [recordRevision, setRecordRevision] = useState(0)
  return <AttendanceWorkspace recordRevision={recordRevision} presence={(staffId, date) => <PresenceView initialStaffId={staffId} initialDate={date} onUpdated={() => setRecordRevision((value) => value + 1)} />} />
}

function PresenceView({ initialStaffId, initialDate, onUpdated }: { initialStaffId: string; initialDate: string; onUpdated: () => void }) {
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
    const load = async (initial = false) => {
      if (running || !date) return
      running = true
      if (initial) { setResult(null); setLoading(true); setError('') }
      try {
        const params = new URLSearchParams({ date, ...(staffId ? { staffId } : {}) })
        const response = await api.get<AttendanceResponse>('/attendance?' + params, { signal: controller.signal })
        if (!controller.signal.aborted) { setResult(response); setError('') }
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Could not load attendance records.')
      } finally { running = false; if (!controller.signal.aborted) setLoading(false) }
    }
    void load(true)
    const interval = window.setInterval(() => { if (document.visibilityState === 'visible') void load() }, 30_000)
    const resume = () => { if (document.visibilityState === 'visible') void load() }
    window.addEventListener('focus', resume); window.addEventListener('online', resume); document.addEventListener('visibilitychange', resume)
    return () => { controller.abort(); clearInterval(interval); window.removeEventListener('focus', resume); window.removeEventListener('online', resume); document.removeEventListener('visibilitychange', resume) }
  }, [date, staffId, retry, revision, store.me.id])

  const rows = result?.staff ?? []
  const detail = rows.find((row) => row.userId === selected) ?? (rows.length === 1 ? rows[0] : null)
  const timezone = result?.timezone ?? store.agencyTimezone ?? 'Asia/Kolkata'
  const totals = rows.reduce((sum, row) => ({
    attendance: sum.attendance + row.attendance.totalSeconds,
    active: sum.active + row.presence.activeSeconds,
    idle: sum.idle + row.presence.idleSeconds,
    offline: sum.offline + row.presence.offlineSeconds,
  }), { attendance: 0, active: 0, idle: 0, offline: 0 })

  const inspect = (id: string) => { setSelected(id); requestAnimationFrame(() => document.getElementById('attendance-session-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' })) }
  const activitySeconds = totals.active + totals.idle + totals.offline
  const hasTrackedStaff = rows.some((row) => row.presence.tracked)
  return <div className="attendance-presence">
    <Panel className="presence-filter-panel"><div className="presence-heading"><div><h2>Sessions & activity</h2><p>Follow check-ins, check-outs and time observed in the CRM.</p></div><span>Times in {timezone}</span></div>
      <div className="presence-filters">
        <Field label="Attendance date"><DatePicker value={date} onChange={(value) => { if (value) { setDate(value); setSelected(null) } }} max={agencyDay(new Date(), timezone)} required /></Field>
        {team && <Field label="Employee"><Select value={staffId} onChange={(event) => { setStaffId(event.target.value); setSelected(null) }}><option value="">All employees</option>{store.users.filter((user) => user.role !== 'admin' && (store.me.role === 'admin' || user.role === 'staff' || user.id === store.me.id)).map((user) => <option key={user.id} value={user.id}>{user.name}{!user.active ? ' (inactive)' : ''}{user.role !== 'staff' ? ` (${user.role})` : ''}</option>)}</Select></Field>}
        <Button disabled={loading} icon={<RefreshCw size={16} />} onClick={() => setRetry((value) => value + 1)}>Refresh records</Button>
        {result && <p className="presence-updated">Updated {timeLabel(result.generatedAt, timezone)}<span>Refreshes every 30 seconds while visible</span></p>}
      </div>
    </Panel>
    <div className="presence-explanation"><Info size={19} /><p><strong>Attendance and browser activity measure different things.</strong> Check-in/out records your attendance. Online and idle measure a focused CRM tab. Away / no signal includes a hidden tab, closed browser or lost connection; it does not mean the employee was not working. Browser activity is tracked for Staff, not Managers.</p></div>
    {error && <p role="alert" className="attendance-alert">{error} Use Refresh records to try again.{result && ' Displayed records may be out of date.'}</p>}
    {loading ? <p role="status" className="attendance-loading">Loading attendance records…</p> : result && <>
      <SummaryCards items={[
        { label: 'Checked-in time', value: durationLabel(totals.attendance), detail: 'Recorded attendance for selected date', icon: Clock3 },
        { label: 'Online in CRM', value: hasTrackedStaff ? durationLabel(totals.active) : 'Not tracked', detail: 'Focused tab with recent interaction', icon: Activity, tone: 'green' },
        { label: 'Idle in CRM', value: hasTrackedStaff ? durationLabel(totals.idle) : 'Not tracked', detail: 'Focused tab with no recent interaction', icon: Coffee, tone: 'amber' },
        { label: 'Away / no signal', value: hasTrackedStaff ? durationLabel(totals.offline) : 'Not tracked', detail: 'Checked in, without an observed CRM tab', icon: WifiOff, tone: 'violet' },
      ]} />
      {activitySeconds > 0 && <div className="presence-day-breakdown"><div><strong>Staff browser activity during attendance</strong><span>{durationLabel(activitySeconds)} observed or inferred · {date}</span></div><ActivityBar row={{ activeSeconds: totals.active, idleSeconds: totals.idle, offlineSeconds: totals.offline }} /><PresenceLegend /></div>}
      <Panel flush className="presence-roster">
        <PanelHeader title={team ? 'Daily team attendance' : 'Your daily record'} meta={`${date} totals · Clock and presence badges show the current state`} />
        {!rows.length ? <EmptyState title="No employees in this view" description="Choose another employee to see their attendance and sessions." /> : <div className="attendance-table-wrap"><table className="attendance-table presence-table">
          <thead><tr>{['Employee', 'Current state', 'Checked in', 'Online in CRM', 'Idle in CRM', 'Away / no signal', 'Record'].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{rows.map((row) => <tr key={row.userId} className={detail?.userId === row.userId ? 'presence-row-selected' : ''}>
            <td data-label="Employee"><strong>{row.name}{row.userId === store.me.id ? ' (you)' : ''}</strong><small>{row.role === 'manager' ? 'Manager' : row.role === 'admin' ? 'Admin' : 'Staff'}{!row.active ? ' · Inactive account' : ''}</small></td>
            <td data-label="Current state"><div className="presence-state-stack"><Badge tone={row.attendance.status === 'checked_in' ? 'navy' : 'neutral'}>{row.attendance.status === 'checked_in' ? 'Checked in' : 'Checked out'}</Badge>{row.presence.tracked ? <PresenceBadge status={row.presence.status} /> : <span className="presence-muted">Activity not tracked</span>}</div></td>
            <td data-label="Checked-in time"><strong>{durationLabel(row.attendance.totalSeconds)}</strong><small>{row.attendance.sessions.length} {row.attendance.sessions.length === 1 ? 'session' : 'sessions'}</small></td>
            {[['Online in CRM', row.presence.activeSeconds], ['Idle in CRM', row.presence.idleSeconds], ['Away / no signal', row.presence.offlineSeconds]].map(([label, seconds]) => <td key={label} data-label={label}><span className="tnum">{row.presence.tracked ? durationLabel(Number(seconds)) : 'Not tracked'}</span></td>)}
            <td data-label="Record"><Button size="sm" icon={<ArrowDown size={14} />} aria-label={'View sessions for ' + row.name} onClick={() => inspect(row.userId)}>View sessions</Button></td>
          </tr>)}</tbody>
        </table></div>}
      </Panel>
      {detail && <SessionDetail row={detail} timezone={timezone} onUpdated={() => { setRetry((value) => value + 1); onUpdated(); void refresh() }} />}
    </>}
    <details className="presence-rules"><summary>How activity time is measured</summary><ul><li>Online: the CRM is visible and focused, with interaction within the last 5 minutes.</li><li>Idle: the CRM remains visible and focused without a recent interaction, or the page has just opened.</li><li>Away / no signal: there is no observed foreground CRM tab. A lost heartbeat expires after 90 seconds.</li><li>Multiple tabs are merged. Activity counts only inside checked-in sessions; closed shifts do not keep accumulating time.</li><li>These are browser estimates, not payroll or productivity scores. No other apps, screenshots or keystroke contents are monitored.</li></ul></details>
  </div>
}

function PresenceLegend() {
  return <div className="presence-legend"><span><i className="presence-online" />Online in CRM</span><span><i className="presence-idle" />Idle in CRM</span><span><i className="presence-offline" />Away / no signal</span></div>
}

function ActivityBar({ row }: { row: Pick<StaffAttendance['presence'], 'activeSeconds' | 'idleSeconds' | 'offlineSeconds'> }) {
  const total = row.activeSeconds + row.idleSeconds + row.offlineSeconds
  return <div className="presence-duration-bar" aria-label={`Online ${durationLabel(row.activeSeconds)}, idle ${durationLabel(row.idleSeconds)}, away or no signal ${durationLabel(row.offlineSeconds)}`}>{[[row.activeSeconds, 'online'], [row.idleSeconds, 'idle'], [row.offlineSeconds, 'offline']].map(([seconds, state]) => Number(seconds) > 0 && <span key={state} className={`presence-${state}`} style={{ width: `${Number(seconds) / total * 100}%` }} />)}</div>
}

function SessionDetail({ row, timezone, onUpdated }: { row: StaffAttendance; timezone: string; onUpdated: () => void }) {
  const { me, user } = useStore()
  const [editing, setEditing] = useState<AttendanceSession | null>(null)
  return <><div id="attendance-session-detail" className="presence-session-detail">
    <Panel flush>
      <PanelHeader title={row.name + ' · Check-in / check-out'} meta="Recorded clock times. Durations include only the selected date." />
      {!row.attendance.sessions.length ? <p className="p-5 text-sm text-ink-500">No check-ins on this date.</p> : <ol className="divide-y divide-rule-soft">{row.attendance.sessions.map((session) => <li key={session.id} className="flex flex-wrap items-center justify-between gap-2 px-5 py-4 text-sm">
        <div><p className="font-medium text-ink">{timeLabel(session.checkedInAt, timezone)} → {session.checkedOutAt ? timeLabel(session.checkedOutAt, timezone) : 'Still checked in'}</p><p className="mt-1 text-xs text-ink-500">{session.checkedOutAt ? 'Check-in and check-out recorded' : 'Check out explicitly to finish this session'}</p>{session.correctionReason && <p className="presence-correction">Corrected by {user(session.correctedBy ?? '')?.name || 'Admin'}: {session.correctionReason}</p>}</div><div className="flex items-center gap-2"><span className="text-ink-600 tnum">{durationLabel(session.durationSeconds)}</span>{me.role === 'admin' && <Button size="sm" variant="ghost" icon={<Pencil size={13} />} onClick={() => setEditing(session)}>Correct</Button>}</div>
      </li>)}</ol>}
    </Panel>
    <Panel flush>
      <PanelHeader title="CRM activity timeline" meta="Observed and inferred intervals during checked-in time" />
      {!row.presence.tracked ? <p className="p-5 text-sm text-ink-500">Browser activity is not tracked for this account. Its attendance time is not counted as offline.</p> : !row.presence.sessions.length ? <p className="p-5 text-sm text-ink-500">Activity durations appear after a check-in on this date.</p> : <><div className="presence-person-breakdown"><ActivityBar row={row.presence} /><PresenceLegend /></div><ol className="presence-timeline">{row.presence.sessions.map((session, index) => <li key={session.startedAt + index}><div><PresenceBadge status={session.status} /><span>{timeLabel(session.startedAt, timezone)} → {timeLabel(session.endedAt, timezone)}</span></div><strong>{session.durationSeconds < 60 ? `${session.durationSeconds}s` : durationLabel(session.durationSeconds)}</strong></li>)}</ol></>}
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
    if (reason.trim().length < 3) { setError('Explain why this attendance session needs a correction.'); return }
    if (!start || !Number.isFinite(new Date(start).getTime()) || (end && !Number.isFinite(new Date(end).getTime()))) { setError('Enter valid attendance times.'); return }
    if (end && new Date(end) < new Date(start)) { setError('Check-out must be after check-in.'); return }
    setBusy(true); setError('')
    try {
      await api.patch('/attendance/sessions/' + session.id, { checkedInAt: new Date(start).toISOString(), checkedOutAt: end ? new Date(end).toISOString() : null, reason: reason.trim() })
      onSaved()
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not save the correction.') }
    finally { setBusy(false) }
  }
  return <Dialog open title="Correct attendance" description={name + ' · Changes and your reason are recorded in the audit history.'} onClose={() => { if (!busy) onClose() }}
    footer={<><Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button><Button type="submit" form="attendance-correction" variant="primary" disabled={busy}>{busy ? 'Saving…' : 'Save correction'}</Button></>}>
    <form id="attendance-correction" onSubmit={(event) => { event.preventDefault(); void save() }}>
      {error && <p role="alert" className="mb-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
      <p className="mb-4 text-xs text-ink-500">Correction times use your device timezone: {Intl.DateTimeFormat().resolvedOptions().timeZone}.</p>
      <fieldset disabled={busy} className="space-y-4">
        <Field label="Check-in time" required><Input type="datetime-local" step={1} required value={start} onChange={(event) => setStart(event.target.value)} /></Field>
        <Field label="Check-out time" hint="Leave blank only if this session should remain checked in."><Input type="datetime-local" step={1} value={end} onChange={(event) => setEnd(event.target.value)} /></Field>
        <Field label="Reason for correction" required><Textarea required minLength={3} maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} rows={3} /></Field>
      </fieldset>
    </form>
  </Dialog>
}
