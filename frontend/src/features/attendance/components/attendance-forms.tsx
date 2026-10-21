import { useRef, useState, type ReactNode } from 'react'
import { api } from '@/shared/api/client'
import { Button, Checkbox, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { useStore } from '@/shared/state/crm-store'
import { Dialog } from '@/shared/ui/overlay'
import { DatePicker } from '@/shared/ui/date-picker'
import { attendanceLabels, requestLabels, approvalLabel, type AttendanceRequest, type AttendanceSettings, type RequestKind } from '../policy-model'

interface FormProps { onClose: () => void; onSaved: () => void }
function SaveDialog({ title, description, onClose, onSaved, children, save, label = 'Save' }: FormProps & { title: string; description?: string; children: ReactNode; save: () => Promise<unknown>; label?: string }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const saving = useRef(false)
  const submit = async () => {
    if (saving.current) return
    saving.current = true; setBusy(true); setError('')
    try { await save(); onSaved() } catch (cause) { setError(cause instanceof Error ? cause.message : 'The change could not be saved. Please retry.') }
    finally { saving.current = false; setBusy(false) }
  }
  return <Dialog open title={title} description={description} onClose={() => { if (!busy) onClose() }} footer={<><Button disabled={busy} onClick={onClose}>Cancel</Button><Button variant="primary" form="attendance-policy-form" type="submit" disabled={busy}>{busy ? 'Saving…' : label}</Button></>}>
    <form id="attendance-policy-form" onSubmit={(event) => { event.preventDefault(); void submit() }}>
      {error && <p role="alert" className="attendance-alert">{error}</p>}
      <fieldset disabled={busy} className="attendance-form-grid">{children}</fieldset>
    </form>
  </Dialog>
}

export function RequestAttendanceDialog({ today, settings, onClose, onSaved }: FormProps & { today: string; settings: AttendanceSettings }) {
  const [date, setDate] = useState(today)
  const [kind, setKind] = useState<RequestKind>('late')
  const [minutes, setMinutes] = useState('15')
  const [reason, setReason] = useState('')
  const [cover, setCover] = useState('')
  const requestKey = useRef(crypto.randomUUID())
  const timed = ['late', 'early_exit', 'early_check_in', 'extra_ot'].includes(kind)
  const leave = ['paid_leave', 'unpaid_leave'].includes(kind)
  return <SaveDialog title="New attendance request" description="Your request is recorded and sent to the required approvers." onClose={onClose} onSaved={onSaved} label="Submit request" save={() => api.post('/attendance/requests', { requestKey: requestKey.current, date, kind, minutes: timed ? Number(minutes) : 0, reason: reason.trim(), ...(leave && cover ? { coverUserId: cover } : {}) })}>
    <Field label="Date" required><DatePicker value={date} onChange={setDate} required /></Field>
    <Field label="Request type" required><Select value={kind} onChange={(event) => setKind(event.target.value as RequestKind)}>{Object.entries(requestLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></Field>
    {timed && <Field label={kind === 'extra_ot' ? 'Overtime minutes' : 'Requested minutes'} required><Input type="number" min={1} max={1440} step={1} value={minutes} onChange={(event) => setMinutes(event.target.value)} required /></Field>}
    {leave && <Field label="Colleague to cover your work" required={settings.coverColleagues.length > 0} hint={settings.coverColleagues.length ? 'Your colleague must accept the cover before this leave reaches approvers.' : 'No eligible colleague is currently available.'}><Select value={cover} onChange={(event) => setCover(event.target.value)} required={settings.coverColleagues.length > 0}><option value="">Choose a colleague</option>{settings.coverColleagues.map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}</Select></Field>}
    <Field label="Reason" required className="attendance-form-wide"><Textarea required minLength={3} maxLength={1000} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></Field>
    <p className="attendance-form-wide attendance-help">Leave and extra overtime require the approval stages shown in request history. The server checks attendance limits and request timing before saving.</p>
  </SaveDialog>
}

export function DecisionDialog({ request, action, settings, onClose, onSaved }: FormProps & { request: AttendanceRequest; action: 'approve' | 'reject' | 'accept_cover' | 'decline_cover' | 'nominate'; settings: AttendanceSettings }) {
  const { me } = useStore()
  const [reason, setReason] = useState('')
  const [cover, setCover] = useState('')
  const [extraOccasion, setExtraOccasion] = useState(false)
  const canAllowExtraOccasion = me.role === 'admin' && action === 'approve' && ['late', 'early_exit'].includes(request.kind)
  const coverAction = action.includes('cover')
  const title = ({ approve: 'Approve request', reject: 'Reject request', accept_cover: 'Accept leave cover', decline_cover: 'Decline leave cover', nominate: 'Choose another cover colleague' })[action]
  return <SaveDialog title={title} description={`${request.name} · ${request.date} · ${requestLabels[request.kind]}`} onClose={onClose} onSaved={onSaved} label={title} save={() => action === 'nominate' ? api.post(`/attendance/requests/${request.id}/cover`, { coverUserId: cover }) : coverAction ? api.post(`/attendance/requests/${request.id}/cover-response`, { accepted: action === 'accept_cover', note: reason.trim() }) : api.post(`/attendance/requests/${request.id}/decision`, { approved: action === 'approve', reason: reason.trim(), emergencyOverride: canAllowExtraOccasion && extraOccasion })}>
    <p className="attendance-form-wide attendance-help">{request.reason}</p>
    {action === 'nominate' ? <Field label="Colleague" required className="attendance-form-wide"><Select required value={cover} onChange={(event) => setCover(event.target.value)}><option value="">Choose a colleague</option>{settings.coverColleagues.filter((person) => person.id !== request.userId && person.id !== request.coverUserId).map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}</Select></Field> : <>
      {!coverAction && <p className="attendance-form-wide attendance-help">Your decision applies to: {request.decisionStages.map(approvalLabel).join(', ')}. Other required approvals remain separate.</p>}
      {canAllowExtraOccasion && <div className="attendance-form-wide"><Checkbox checked={extraOccasion} onChange={setExtraOccasion} label="Allow an extra permission occasion" /><p className="attendance-help mt-2">This still uses the remaining monthly grace. It does not increase the paid-minute allowance. Your reason is recorded with this exception.</p></div>}
      <Field label={coverAction ? 'Note' : 'Decision reason'} required={!coverAction} className="attendance-form-wide"><Textarea value={reason} onChange={(event) => setReason(event.target.value)} required={!coverAction} minLength={coverAction ? undefined : 3} maxLength={coverAction ? 500 : 1000} rows={3} /></Field>
    </>}
  </SaveDialog>
}

export type ScheduleKind = 'duty' | 'timing' | 'calendar' | 'adjustment'
export function AttendanceScheduleDialog({ kind, today, userId, settings, canCorrect, onClose, onSaved }: FormProps & { kind: ScheduleKind; today: string; userId: string; settings: AttendanceSettings; canCorrect: boolean }) {
  const eligibleEmployees = settings.employees.filter((person) => person.active && (canCorrect || person.role === 'staff'))
  const [employee, setEmployee] = useState(eligibleEmployees.some((person) => person.id === userId) ? userId : eligibleEmployees[0]?.id || '')
  const [date, setDate] = useState(today)
  const [start, setStart] = useState('10:00')
  const [end, setEnd] = useState('19:00')
  const [breakMinutes, setBreakMinutes] = useState('60')
  const [reason, setReason] = useState('')
  const [status, setStatus] = useState(kind === 'calendar' ? 'H' : 'P')
  const [adjustment, setAdjustment] = useState(canCorrect ? 'status_override' : 'time_recovery')
  const [minutes, setMinutes] = useState('0')
  const [action, setAction] = useState('check_in')
  const [occurredAt, setOccurredAt] = useState(`${today}T10:00`)
  const title = { duty: 'Plan a duty shift', timing: 'Set work timings', calendar: 'Set a calendar day', adjustment: 'Correct an attendance record' }[kind]
  const save = async () => {
    const common = { userId: employee, date, reason: reason.trim() }
    const shift = { start, end, breakMinutes: Number(breakMinutes) }
    if (kind === 'duty') return api.post('/attendance/duty-plans', { ...common, shift })
    if (kind === 'timing') return api.post('/attendance/work-timings', { userId: employee, effectiveFrom: date, shift, reason: reason.trim() })
    if (kind === 'calendar') return api.post('/attendance/calendar', { ...common, status })
    return api.post('/attendance/adjustments', { ...common, kind: adjustment, ...(adjustment === 'status_override' ? { status } : adjustment === 'time_recovery' ? { minutes: Number(minutes) } : { action, occurredAt: new Date(occurredAt).toISOString() }) })
  }
  return <SaveDialog title={title} description="Changes retain the actor and reason in attendance history." onClose={onClose} onSaved={onSaved} save={save}>
    <Field label="Employee" required><Select required value={employee} onChange={(event) => setEmployee(event.target.value)}><option value="">Choose an employee</option>{eligibleEmployees.map((person) => <option key={person.id} value={person.id}>{person.name}{!person.active ? ' (inactive)' : ''}</option>)}</Select></Field>
    <Field label={kind === 'timing' ? 'Effective from' : 'Date'} required><DatePicker value={date} onChange={setDate} required /></Field>
    {(kind === 'duty' || kind === 'timing') && <><Field label="Shift start" required><Input type="time" value={start} onChange={(event) => setStart(event.target.value)} required /></Field><Field label="Shift end" required><Input type="time" value={end} onChange={(event) => setEnd(event.target.value)} required /></Field><Field label="Break (minutes)" required><Input type="number" min={0} max={480} value={breakMinutes} onChange={(event) => setBreakMinutes(event.target.value)} required /></Field></>}
    {kind === 'adjustment' && <Field label="Correction type" required><Select value={adjustment} onChange={(event) => setAdjustment(event.target.value)}>{canCorrect && <option value="status_override">Override attendance status</option>}<option value="time_recovery">Recover unpaid minutes</option><option value="add_punch">Add a missing punch</option></Select></Field>}
    {(kind === 'calendar' || (kind === 'adjustment' && adjustment === 'status_override')) && <Field label="Attendance status" required><Select value={status} onChange={(event) => setStatus(event.target.value)}>{Object.entries(attendanceLabels).filter(([code]) => kind !== 'calendar' || (canCorrect ? ['H', 'WO', 'PL', 'UL'] : ['H', 'WO']).includes(code)).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</Select></Field>}
    {kind === 'adjustment' && adjustment === 'time_recovery' && <Field label="Minutes to recover" required><Input type="number" min={1} max={1440} value={minutes} onChange={(event) => setMinutes(event.target.value)} required /></Field>}
    {kind === 'adjustment' && adjustment === 'add_punch' && <><Field label="Punch type"><Select value={action} onChange={(event) => setAction(event.target.value)}><option value="check_in">Check in</option><option value="check_out">Check out</option></Select></Field><Field label="Punch time" required hint={`Device timezone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}`}><Input type="datetime-local" value={occurredAt} onChange={(event) => setOccurredAt(event.target.value)} required /></Field></>}
    {kind === 'adjustment' && adjustment === 'status_override' && <p className="attendance-help attendance-form-wide">A status override changes the recorded classification. Use recovered minutes to correct unpaid time.</p>}
    <Field label="Reason" required className="attendance-form-wide"><Textarea required minLength={3} maxLength={1000} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></Field>
  </SaveDialog>
}

export function RemoveDutyDialog({ id, name, date, onClose, onSaved }: FormProps & { id: string; name: string; date: string }) {
  return <SaveDialog title="Remove duty plan" description={`${name} · ${date}`} onClose={onClose} onSaved={onSaved} label="Remove duty plan" save={() => api.del(`/attendance/duty-plans/${id}`)}><p className="attendance-help attendance-form-wide">The date will use the employee’s normal shift and weekly-off rules again.</p></SaveDialog>
}
