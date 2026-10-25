import { useState } from 'react'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { canReceiveManualAssignment, manualAssignmentLabel } from './availability-model'
import { useStaffAvailability } from './use-staff-availability'

export function ReassignCandidatesDialog({ candidateIds, onClose, onSaved, description }: {
  candidateIds: string[]
  onClose: () => void
  onSaved?: () => void
  description?: string
}) {
  const store = useStore()
  const availability = useStaffAvailability()
  const [owner, setOwner] = useState('')
  const [reason, setReason] = useState('')
  const [restart, setRestart] = useState(false)
  const [restartReason, setRestartReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const ids = [...new Set(candidateIds)]
  const eligible = (availability.data?.staff ?? []).filter(canReceiveManualAssignment)
    .filter(staff => ids.some(id => store.candidate(id)?.ownerId !== staff.id))
  const validOwner = eligible.some(staff => staff.id === owner)
  const isAdmin = store.me?.role === 'admin'
  const restartMissing = isAdmin && restart && restartReason.trim().length < 3
  const save = async () => {
    if (busy || !validOwner || !reason.trim() || !ids.length || restartMissing) return
    setBusy(true); setError('')
    try {
      await store.reassign(ids, owner, reason.trim(), isAdmin && restart ? restartReason.trim() : undefined)
      onSaved?.()
      onClose()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The candidates could not be reassigned.')
      availability.refresh()
    } finally { setBusy(false) }
  }
  return <Dialog open onClose={() => { if (!busy) onClose() }} title={`Reassign ${ids.length === 1 ? 'candidate' : `${ids.length} candidates`}`}
    description={description || 'Choose who will take responsibility for the selected candidates.'}
    footer={<><Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || availability.loading || !!availability.error || !validOwner || !reason.trim() || !ids.length || restartMissing} onClick={() => void save()}>{busy ? 'Reassigning…' : 'Confirm reassignment'}</Button></>}>
    <div className="space-y-4">
      <p className="rounded-control border border-amber-100 bg-amber-50 p-3 text-sm text-amber-700">All open tasks for these candidates move to the new owner, including tasks from other applications. Original deadlines keep running, and completed work and assignment history are preserved.</p>
      {availability.loading && <p role="status" className="text-sm text-ink-500">Checking current staff availability…</p>}
      {availability.error && <div role="alert" className="rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{availability.error}<Button size="sm" className="mt-2" onClick={availability.refresh}>Retry availability check</Button></div>}
      {availability.data && <>
        <Field label="New owner" required hint={`Availability for ${availability.data.today} · ${availability.data.timezone}. Staff on approved or manual leave cannot receive assignments.`}>
          <Select value={validOwner ? owner : ''} disabled={busy || !eligible.length} onChange={event => setOwner(event.target.value)} aria-label="New owner">
            <option value="">Choose an available staff member</option>
            {eligible.map(staff => <option key={staff.id} value={staff.id}>{manualAssignmentLabel(staff)}</option>)}
          </Select>
        </Field>
        {!eligible.length && <div className="space-y-2"><p role="status" className="text-sm text-amber-700">No other staff member can receive these candidates right now. Review staff availability and try again.</p><Button size="sm" disabled={busy} onClick={availability.refresh}>Refresh availability</Button></div>}
      </>}
      <Field label="Reason for reassignment" required hint="Saved in each candidate’s assignment history.">
        <Textarea value={reason} disabled={busy} maxLength={1000} rows={3} onChange={event => setReason(event.target.value)} placeholder="Explain why responsibility is changing" />
      </Field>
      {isAdmin && <div className="space-y-2">
        <label className="flex items-start gap-3 text-sm"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0" disabled={busy} checked={restart} onChange={event => setRestart(event.target.checked)} /><span><b className="font-semibold text-ink-900">Also restart the 48-hour clock</b><small className="block text-xs text-ink-500">Admin only. Gives every open task of these candidates a fresh 48 hours.</small></span></label>
        {restart && <Field label="Reason for restarting the clock" required hint="Saved in the task history.">
          <Textarea value={restartReason} disabled={busy} maxLength={2000} rows={2} onChange={event => setRestartReason(event.target.value)} placeholder="Explain why the deadline is being restarted" />
        </Field>}
      </div>}
      {error && <p role="alert" className="text-sm text-stamp-700">{error}</p>}
    </div>
  </Dialog>
}
