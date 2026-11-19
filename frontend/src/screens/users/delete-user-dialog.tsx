import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import type { User } from '@/shared/model/types'
import { Button, Field, Select } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { useStaffAvailability } from '@/features/staff/use-staff-availability'
import { canReceiveManualAssignment, manualAssignmentLabel } from '@/features/staff/availability-model'
import { LEAVE_UNASSIGNED, deleteQuery, deleteReady, hasWorkToMove, successText, workSummaryText, type DeleteSummary } from '@/features/staff/delete-user-model'
import { RolePill, UserAvatar, failureText } from './user-ui'

/** Delete a Recruiter's account: the login goes, history stays, and the person's candidates, open tasks and leads go where the admin says. */
export function DeleteUserDialog({ user, onClose, onDeleted, onDeactivateInstead }: {
  user: User; onClose: () => void; onDeleted: (message: string) => Promise<void>; onDeactivateInstead: () => void
}) {
  const store = useStore()
  const availability = useStaffAvailability({ enabled: true })
  const [summary, setSummary] = useState<DeleteSummary | null>(null)
  const [choice, setChoice] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [summaryError, setSummaryError] = useState('')

  useEffect(() => {
    let live = true
    api.get<DeleteSummary>('/users/' + user.id + '/delete-summary')
      .then((value) => { if (live) setSummary(value) })
      .catch((err) => { if (live) setSummaryError(failureText(err)) })
    return () => { live = false }
  }, [user.id])

  const options = (availability.data?.staff ?? []).filter((person) => person.id !== user.id && canReceiveManualAssignment(person))
  const moves = hasWorkToMove(summary)
  const chosen = options.find((person) => person.id === choice)
  const validChoice = choice === LEAVE_UNASSIGNED || !!chosen ? choice : ''
  const ready = deleteReady(summary, validChoice, busy)
  const close = () => { if (!busy) onClose() }

  const submit = async () => {
    if (!ready || !summary) return
    setBusy(true); setError('')
    try {
      await api.del('/users/' + user.id + deleteQuery(validChoice))
      await onDeleted(successText(user.name, summary, chosen ? chosen.name : null))
    } catch (err) { setError(failureText(err)); availability.refresh() }
    finally { setBusy(false) }
  }

  return <Dialog open onClose={close} width="md" title={`Delete ${user.name}?`}
    description="The sign-in is removed for good. Everything they did stays in the history."
    footer={<>
      <Button disabled={busy} variant="ghost" onClick={close}>Cancel</Button>
      {error && <Button disabled={busy} variant="secondary" onClick={onDeactivateInstead}>Deactivate instead</Button>}
      <Button variant="danger" disabled={!ready} onClick={() => void submit()}>{busy ? 'Deleting…' : 'Delete user'}</Button>
    </>}>
    <div className="form-stack uv-delete">
      <div className="uv-target"><UserAvatar name={user.name} /><div><p>{user.name}</p><span>{user.email}</span></div><RolePill role={user.role} /></div>
      <p className="uv-note">They will no longer be able to sign in, and they disappear from the team list and every picker. Their notes, calls, remarks, uploads and
        audit entries are kept and keep their name, marked as a former user. This cannot be undone.</p>
      {summaryError && <p role="alert" className="uv-error">{summaryError}</p>}
      {!summary && !summaryError && <p role="status" className="uv-note">Checking what this person still holds…</p>}
      {summary && <div className="uv-delete-summary" role="status"><AlertTriangle size={16} aria-hidden="true" /><span>{moves ? <>This person holds <strong>{workSummaryText(summary)}</strong>.</> : <strong>Nothing to move.</strong>}</span></div>}
      {moves && <>
        {availability.loading && <p role="status" className="uv-note">Checking who is free to take over…</p>}
        {availability.error && <p role="alert" className="uv-error">{availability.error}</p>}
        <Field label="Move their candidates and tasks to" required hint="Open tasks follow their candidates. Finished work stays as it was.">
          <Select value={validChoice} disabled={busy || availability.loading || !!availability.error} onChange={(event) => setChoice(event.target.value)}>
            <option value="">Choose what happens…</option>
            {options.map((person) => <option key={person.id} value={person.id}>{manualAssignmentLabel(person)}</option>)}
            <option value={LEAVE_UNASSIGNED}>Leave unassigned (goes to the Data Bank for the next assignment)</option>
          </Select>
        </Field>
      </>}
      {error && <p role="alert" className="uv-error">{error}</p>}
      {store.me.id === user.id && <p className="uv-warn">You cannot delete your own account.</p>}
    </div>
  </Dialog>
}
