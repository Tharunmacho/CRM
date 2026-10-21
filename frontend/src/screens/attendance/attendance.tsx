import { useEffect, useState } from 'react'
import { Activity, Clock3, Coffee, Pencil, RefreshCw, WifiOff } from 'lucide-react'
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

function timeLabel(value: string | null, timezone: string) {
  return value ? new Intl.DateTimeFormat('en-GB', { timeZone: timezone, hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' }).format(new Date(value)) : '—'
}

export default function AttendancePage() {
  const [recordRevision, setRecordRevision] = useState(0)
  return <AttendanceWorkspace recordRevision={recordRevision} presence={<PresenceView onUpdated={() => setRecordRevision((value) => value + 1)} />} />
}

function PresenceView({ onUpdated }: { onUpdated: () => void }) {
  const store = useStore()
  const { revision, refresh } = useAttendance()
  const team = store.me.role !== 'staff'
  const [date, setDate] = useState(() => agencyDay(new Date(), store.agencyTimezone))
  const [staffId, setStaffId] = useState(team ? '' : store.me.id)
  const [result, setResult] = useState<AttendanceResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [retry, setRetry] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let running = false
    const load = async (initial = false) => {
      if (running || !date) return
      running = true
      if (initial) { setResult(null); setLoading(true) }
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
    return () => { controller.abort(); clearInterval(interval) }
  }, [date, staffId, retry, revision])

  const rows = result?.staff ?? []
  const detail = rows.find((row) => row.userId === selected) ?? (rows.length === 1 ? rows[0] : null)
  const timezone = result?.timezone ?? store.agencyTimezone ?? 'Asia/Kolkata'
  const totals = rows.reduce((sum, row) => ({
    attendance: sum.attendance + row.attendance.totalSeconds,
    active: sum.active + row.presence.activeSeconds,
    idle: sum.idle + row.presence.idleSeconds,
    offline: sum.offline + row.presence.offlineSeconds,
  }), { attendance: 0, active: 0, idle: 0, offline: 0 })

  return <>
    <p className="my-4 text-xs leading-relaxed text-ink-500">Browser presence is tracked for Staff accounts and is visible to staff and supervisors. Online means a foreground CRM tab with recent keyboard, pointer, or scroll activity. After 5 minutes without interaction it becomes idle; a hidden tab or 90 seconds without a heartbeat becomes offline. Presence durations are inferred during checked-in time and are not payroll hours.</p>

    <Panel className="mb-4">
      <div className="flex flex-wrap items-end gap-4">
        <Field label="Attendance date" className="w-full sm:w-52"><DatePicker value={date} onChange={(value) => { if (value) setDate(value) }} max={agencyDay(new Date(), timezone)} required /></Field>
        {team && <Field label="Staff member" className="w-full sm:w-64"><Select value={staffId} onChange={(event) => setStaffId(event.target.value)}><option value="">All staff</option>{store.users.filter((user) => user.role !== 'admin' && (store.me.role === 'admin' || user.role === 'staff' || user.id === store.me.id)).map((user) => <option key={user.id} value={user.id}>{user.name}{!user.active ? ' (inactive)' : ''}{user.role !== 'staff' ? ` (${user.role})` : ''}</option>)}</Select></Field>}
        <Button disabled={loading} icon={<RefreshCw size={15} />} onClick={() => setRetry((value) => value + 1)}>Refresh records</Button>
        <span className="text-xs text-ink-400 sm:ml-auto">Times shown in {timezone}</span>
      </div>
    </Panel>
    {error && <p role="alert" className="mb-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error} Use Refresh records to try again.{result && ' Displayed records may be out of date.'}</p>}
    {loading ? <p role="status" className="py-8 text-center text-sm text-ink-500">Loading attendance records…</p> : <>
      <SummaryCards items={[
        { label: 'Checked-in time', value: durationLabel(totals.attendance), detail: 'Recorded attendance for selected date', icon: Clock3 },
        { label: 'Active in CRM', value: durationLabel(totals.active), detail: 'During checked-in time', icon: Activity, tone: 'green' },
        { label: 'Idle in CRM', value: durationLabel(totals.idle), detail: 'During checked-in time', icon: Coffee, tone: 'amber' },
        { label: 'Offline from CRM', value: durationLabel(totals.offline), detail: 'During checked-in time', icon: WifiOff, tone: 'violet' },
      ]} />
      <Panel flush>
        <PanelHeader title={team ? 'Daily team attendance' : 'Your daily record'} meta={date + ' · Presence badges show current status'} />
        {!rows.length ? <EmptyState title="No attendance records" description="Choose another date or staff member. Sessions appear after a check-in." /> : <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="border-b border-rule-soft bg-field text-xs text-ink-500"><tr>{['Team member', 'Presence now', 'Clock now', 'Checked-in time', 'Active', 'Idle', 'Offline', 'Details'].map((label) => <th key={label} className="px-4 py-3 font-medium">{label}</th>)}</tr></thead>
            <tbody className="divide-y divide-rule-soft">{rows.map((row) => <tr key={row.userId} className={detail?.userId === row.userId ? 'bg-navy-50/40' : ''}>
              <td className="px-4 py-4 font-medium text-ink">{row.name}{row.userId === store.me.id && <span className="ml-1 text-xs font-normal text-ink-400">(you)</span>}</td>
              <td className="px-4 py-4">{store.user(row.userId)?.role === 'staff' ? <PresenceBadge status={row.presence.status} /> : <span className="text-ink-500">Not tracked</span>}</td>
              <td className="px-4 py-4"><Badge tone={row.attendance.status === 'checked_in' ? 'navy' : 'neutral'}>{row.attendance.status === 'checked_in' ? 'Checked in' : 'Checked out'}</Badge></td>
              {[row.attendance.totalSeconds, row.presence.activeSeconds, row.presence.idleSeconds, row.presence.offlineSeconds].map((seconds, index) => <td key={index} className="whitespace-nowrap px-4 py-4 text-ink-600 tnum">{durationLabel(seconds)}</td>)}
              <td className="px-4 py-4"><Button size="sm" variant="ghost" aria-label={'View sessions for ' + row.name} onClick={() => setSelected(row.userId)}>Sessions</Button></td>
            </tr>)}</tbody>
          </table>
        </div>}
      </Panel>
      {detail && <SessionDetail row={detail} timezone={timezone} onUpdated={() => { setRetry((value) => value + 1); onUpdated(); void refresh() }} />}
    </>}
  </>
}

function SessionDetail({ row, timezone, onUpdated }: { row: StaffAttendance; timezone: string; onUpdated: () => void }) {
  const { me, user } = useStore()
  const [editing, setEditing] = useState<AttendanceSession | null>(null)
  return <><div className="mt-4 grid items-start gap-4 xl:grid-cols-2">
    <Panel flush>
      <PanelHeader title={row.name + ' · Attendance sessions'} meta={'Last seen ' + timeLabel(row.presence.lastSeenAt, timezone)} />
      {!row.attendance.sessions.length ? <p className="p-5 text-sm text-ink-500">No check-ins on this date.</p> : <ol className="divide-y divide-rule-soft">{row.attendance.sessions.map((session) => <li key={session.id} className="flex flex-wrap items-center justify-between gap-2 px-5 py-4 text-sm">
        <div><p className="font-medium text-ink">{timeLabel(session.checkedInAt, timezone)} → {session.checkedOutAt ? timeLabel(session.checkedOutAt, timezone) : 'Still checked in'}</p><p className="mt-1 text-xs text-ink-500">{session.checkedOutAt ? 'Check-in and check-out recorded' : 'The staff member can check out to finish this session'}</p></div><div className="flex items-center gap-2"><span className="text-ink-600 tnum">{durationLabel(session.durationSeconds)}</span>{me.role === 'admin' && <Button size="sm" variant="ghost" icon={<Pencil size={13} />} onClick={() => setEditing(session)}>Correct</Button>}</div>
      </li>)}</ol>}
    </Panel>
    <Panel flush>
      <PanelHeader title="Presence timeline" meta="Inferred intervals during checked-in time for Staff accounts" />
      {user(row.userId)?.role !== 'staff' ? <p className="p-5 text-sm text-ink-500">Browser presence is not tracked for this account.</p> : !row.presence.sessions.length ? <p className="p-5 text-sm text-ink-500">No presence intervals for this date.</p> : <ol className="max-h-96 divide-y divide-rule-soft overflow-y-auto">{row.presence.sessions.map((session, index) => <li key={session.startedAt + index} className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 text-xs"><PresenceBadge status={session.status} /><span className="text-ink-500 tnum">{timeLabel(session.startedAt, timezone)} → {session.endedAt ? timeLabel(session.endedAt, timezone) : 'Now'}</span></li>)}</ol>}
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
