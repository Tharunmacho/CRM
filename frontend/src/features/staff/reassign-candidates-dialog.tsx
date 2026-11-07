import { useState } from 'react'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { canReceiveManualAssignment, manualAssignmentLabel } from './availability-model'
import { useStaffAvailability } from './use-staff-availability'

/** Hands candidates to a recruiter. Candidates nobody owns yet are simply assigned (no reason needed); owned ones are reassigned with a reason. */
export function ReassignCandidatesDialog({ candidateIds, onClose, onSaved }: {
  candidateIds: string[]
  onClose: () => void
  onSaved?: () => void
  /** Kept so existing callers still compile; the dialog no longer shows an explanation line. */
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
  const firstAssignment = ids.length > 0 && ids.every(id => !store.candidate(id)?.ownerId)
  const eligible = (availability.data?.staff ?? []).filter(canReceiveManualAssignment)
    .filter(staff => ids.some(id => store.candidate(id)?.ownerId !== staff.id))
  const validOwner = eligible.some(staff => staff.id === owner)
  // Why each staff member is not in the list, so an empty list is never a dead end.
  const staffAccounts = (availability.data?.staff ?? []).filter(staff => staff.role === 'staff')
  const notListed = staffAccounts.filter(staff => !eligible.some(option => option.id === staff.id)).map(staff => ({
    id: staff.id,
    name: staff.name,
    reason: canReceiveManualAssignment(staff) ? 'Already responsible' : staff.availability.label,
  }))
  const isAdmin = store.me?.role === 'admin'
  const reasonMissing = !firstAssignment && !reason.trim()
  const restartMissing = isAdmin && restart && restartReason.trim().length < 3
  const blocked = busy || availability.loading || !!availability.error || !validOwner || reasonMissing || !ids.length || restartMissing
  const verb = firstAssignment ? 'Assign' : 'Reassign'
  const save = async () => {
    if (blocked) return
    setBusy(true); setError('')
    try {
      await store.reassign(ids, owner, firstAssignment ? 'Assigned manually' : reason.trim(), isAdmin && restart ? restartReason.trim() : undefined)
      onSaved?.()
      onClose()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The candidates could not be assigned.')
      availability.refresh()
    } finally { setBusy(false) }
  }
  return <Dialog open onClose={() => { if (!busy) onClose() }} title={`${verb} ${ids.length === 1 ? 'candidate' : `${ids.length} candidates`}`}
    footer={<><Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button><Button variant="primary" disabled={blocked} onClick={() => void save()}>{busy ? 'Saving…' : verb}</Button></>}>
    <div className="ui-form-stack">
      {availability.loading && <p role="status" className="text-sm text-ink-500">Checking who is available…</p>}
      {availability.error && <div role="alert" className="rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{availability.error}<Button size="sm" className="mt-2" onClick={availability.refresh}>Try again</Button></div>}
      {availability.data && <>
        <Field label="New owner" required>
          <Select value={validOwner ? owner : ''} disabled={busy || !eligible.length} onChange={event => setOwner(event.target.value)} aria-label="New owner">
            <option value="">{eligible.length ? 'Choose a staff member' : 'No one is available'}</option>
            {eligible.map(staff => <option key={staff.id} value={staff.id}>{manualAssignmentLabel(staff)}</option>)}
          </Select>
        </Field>
        {!eligible.length && <div className="space-y-2">
          <p role="status" className="text-sm text-amber-700">{staffAccounts.length === 0 ? 'There are no staff accounts yet. Add one in User Management.' : 'No one else can take these candidates right now.'}</p>
          {notListed.length > 0 && <ul className="space-y-1 text-sm text-ink-600" aria-label="Staff who cannot take candidates right now">
            {notListed.map(item => <li key={item.id}><b className="font-semibold text-ink-900">{item.name}</b> · {item.reason}</li>)}
          </ul>}
          <Button size="sm" disabled={busy} onClick={availability.refresh}>Check again</Button>
        </div>}
      </>}
      {!firstAssignment && <Field label="Reason" required>
        <Textarea value={reason} disabled={busy} maxLength={1000} rows={2} onChange={event => setReason(event.target.value)} placeholder="Why is the owner changing?" />
      </Field>}
      {isAdmin && !firstAssignment && <div className="space-y-2">
        <label className="flex items-center gap-3 text-sm"><input type="checkbox" className="h-4 w-4 shrink-0" disabled={busy} checked={restart} onChange={event => setRestart(event.target.checked)} /><b className="font-semibold text-ink-900">Restart the 48-hour clock</b></label>
        {restart && <Field label="Reason for restarting" required>
          <Textarea value={restartReason} disabled={busy} maxLength={2000} rows={2} onChange={event => setRestartReason(event.target.value)} placeholder="Why is the deadline restarting?" />
        </Field>}
      </div>}
      {error && <p role="alert" className="text-sm text-stamp-700">{error}</p>}
    </div>
  </Dialog>
}
