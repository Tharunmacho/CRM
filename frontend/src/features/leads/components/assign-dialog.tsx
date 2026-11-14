import { useEffect, useState } from 'react'
import { UserCheck, UserMinus } from 'lucide-react'
import type { User } from '@/shared/model/types'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Select } from '@/shared/ui/primitives'
import { leadApi } from '../lead-api'
import { unassignOneCopy } from '../lead-bulk-model'

/** Hand one or more leads to a recruiter (also one that already belongs to someone else), or spread them evenly over the staff who are available. Admin only. */
export function AssignLeadsDialog({ ids, users, fromName, onClose, onSaved }: {
  ids: string[] | null
  users: User[]
  /** The recruiter who has the lead now (one lead that is already assigned): the dialog then says it is a reassignment. */
  fromName?: string | null
  onClose: () => void
  onSaved: (assigned: number, skipped: number) => void
}) {
  const [owner, setOwner] = useState('auto')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { if (ids) { setOwner('auto'); setError(''); setBusy(false) } }, [ids])
  if (!ids) return null
  // The server only hands leads to active staff accounts (not managers, not people on leave).
  const staff = users.filter((user) => user.active && user.role === 'staff')
  const again = ids.length === 1 && Boolean(fromName)
  const save = async () => {
    setBusy(true); setError('')
    try { const result = await leadApi.assign(ids, owner); onSaved(result.assigned, result.skipped) } catch (e) { setError(e instanceof Error ? e.message : 'We could not assign. Please try again.') } finally { setBusy(false) }
  }
  return (
    <Dialog open onClose={() => { if (!busy) onClose() }} title={ids.length === 1 ? (again ? 'Reassign this lead' : 'Assign this lead') : `Assign ${ids.length} leads`} width="sm"
      description={again ? `Now with ${fromName}. It leaves their list and goes to the person you choose. Converted leads are never moved.` : 'The recruiter can then call them and attach the updated resume. Converted leads are never moved.'}
      footer={<><Button variant="secondary" disabled={busy} onClick={onClose}>Cancel</Button><Button variant="primary" icon={<UserCheck className="h-4 w-4" />} disabled={busy} onClick={() => void save()}>{busy ? (again ? 'Reassigning…' : 'Assigning…') : (again ? 'Reassign' : 'Assign')}</Button></>}>
      <div className="ld-form">
        <label className="ld-opt">
          <span>{again ? 'Reassign to' : 'Assign to'}</span>
          <Select aria-label={again ? 'Reassign to' : 'Assign to'} value={owner} onChange={(event) => setOwner(event.target.value)}>
            <option value="auto">Distribute evenly between available staff</option>
            {staff.map((user) => <option key={user.id} value={user.id} disabled={user.onLeave}>{user.name}{user.onLeave ? ' (on leave)' : ''}</option>)}
          </Select>
        </label>
        {error && <p role="alert" className="ld-alert"><span>{error}</span></p>}
      </div>
    </Dialog>
  )
}

/** Take one lead back from its recruiter: nobody owns it afterwards and it waits for the admin to assign it again. Admin only. */
export function UnassignLeadDialog({ lead, ownerName, onClose, onSaved }: {
  lead: { id: string; name?: string | null } | null
  ownerName: string
  onClose: () => void
  onSaved: (unassigned: number, skipped: number) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { if (lead) { setError(''); setBusy(false) } }, [lead])
  if (!lead) return null
  const copy = unassignOneCopy(lead.name ?? '', ownerName)
  const save = async () => {
    setBusy(true); setError('')
    try { const result = await leadApi.unassign([lead.id]); onSaved(result.unassigned, result.skipped) } catch (e) { setError(e instanceof Error ? e.message : 'We could not remove the allocation. Please try again.'); setBusy(false) }
  }
  return (
    <Dialog open onClose={() => { if (!busy) onClose() }} title={copy.title} width="sm"
      footer={<><Button variant="secondary" disabled={busy} onClick={onClose}>Cancel</Button><Button variant="primary" icon={<UserMinus className="h-4 w-4" />} disabled={busy} onClick={() => void save()}>{busy ? 'Working…' : copy.confirmLabel}</Button></>}>
      <div className="ld-form">
        <div className="ld-confirm-lines" role="group" aria-label="What will happen">
          {copy.lines.map((line) => <p key={line}>{line}</p>)}
        </div>
        {error && <p role="alert" className="ld-alert"><span>{error}</span></p>}
      </div>
    </Dialog>
  )
}
