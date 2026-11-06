import { useEffect, useState } from 'react'
import { UserCheck } from 'lucide-react'
import type { User } from '@/shared/model/types'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Select } from '@/shared/ui/primitives'
import { leadApi } from '../lead-api'

/** Hand one or more leads to a recruiter, or spread them evenly over the staff who are available. Admin and manager only. */
export function AssignLeadsDialog({ ids, users, onClose, onSaved }: { ids: string[] | null; users: User[]; onClose: () => void; onSaved: (assigned: number, skipped: number) => void }) {
  const [owner, setOwner] = useState('auto')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { if (ids) { setOwner('auto'); setError(''); setBusy(false) } }, [ids])
  if (!ids) return null
  const staff = users.filter((user) => user.active && user.role !== 'admin')
  const save = async () => {
    setBusy(true); setError('')
    try { const result = await leadApi.assign(ids, owner); onSaved(result.assigned, result.skipped) } catch (e) { setError(e instanceof Error ? e.message : 'We could not assign. Please try again.') } finally { setBusy(false) }
  }
  return (
    <Dialog open onClose={() => { if (!busy) onClose() }} title={ids.length === 1 ? 'Assign this lead' : `Assign ${ids.length} leads`} width="sm"
      description="The recruiter can then call them and attach the updated resume. Converted leads are never moved."
      footer={<><Button variant="secondary" disabled={busy} onClick={onClose}>Cancel</Button><Button variant="primary" icon={<UserCheck className="h-4 w-4" />} disabled={busy} onClick={() => void save()}>{busy ? 'Assigning…' : 'Assign'}</Button></>}>
      <div className="ld-form">
        <label className="ld-opt">
          <span>Assign to</span>
          <Select aria-label="Assign to" value={owner} onChange={(event) => setOwner(event.target.value)}>
            <option value="auto">Distribute evenly between available staff</option>
            {staff.map((user) => <option key={user.id} value={user.id}>{user.name}{user.onLeave ? ' (on leave)' : ''}</option>)}
          </Select>
        </label>
        {error && <p role="alert" className="ld-alert"><span>{error}</span></p>}
      </div>
    </Dialog>
  )
}
