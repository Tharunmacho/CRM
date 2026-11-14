import { useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'
import type { User } from '@/shared/model/types'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Input, Select } from '@/shared/ui/primitives'
import { bulkErrorMessage } from '../lead-bulk-api'
import { UNASSIGNED, confirmCopy, deleteOneCopy, typedConfirmOk, type BulkAction, type BulkOptions } from '../lead-bulk-model'
import { SETTABLE_STATUSES, leadStatus, type Lead } from '../lead-model'
import '@/styles/lead-bulk.css'

type Written = Exclude<BulkAction, 'export'>

/**
 * The one dialog before any bulk write: it states the exact number of leads and the action, asks for the missing choice
 * (who, or which status) and, for a big delete, the typed word DELETE. Nothing happens until the button is pressed.
 */
export function BulkDialog({ action, count, allMatching, users, onClose, onConfirm }: {
  action: Written | null
  count: number
  allMatching: boolean
  users: User[]
  onClose: () => void
  onConfirm: (options: BulkOptions) => Promise<void>
}) {
  const [owner, setOwner] = useState('auto')
  const [status, setStatus] = useState('contacted')
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { if (action) { setOwner('auto'); setStatus('contacted'); setTyped(''); setError(''); setBusy(false) } }, [action])
  if (!action) return null
  const staff = users.filter((user) => user.active && user.role === 'staff')
  const copy = confirmCopy({
    action, count, allMatching,
    ownerName: owner === 'auto' || owner === UNASSIGNED ? owner : users.find((user) => user.id === owner)?.name,
    statusLabel: leadStatus(status).label,
  })
  const ready = !busy && count > 0 && (!copy.typed || typedConfirmOk(typed))
  const go = async () => {
    setBusy(true); setError('')
    try {
      await onConfirm({ ownerId: action === 'assign' ? owner : undefined, status: action === 'status' ? status : undefined, confirm: copy.typed ? typed.trim() : undefined })
    } catch (e) { setError(bulkErrorMessage(e)); setBusy(false) }
  }
  return (
    <Dialog open onClose={() => { if (!busy) onClose() }} title={copy.title} width="sm"
      footer={<>
        <Button variant="secondary" disabled={busy} onClick={onClose}>Cancel</Button>
        <Button variant={copy.danger ? 'danger' : 'primary'} icon={copy.danger ? <Trash2 className="h-4 w-4" /> : undefined} disabled={!ready} onClick={() => void go()}>{busy ? 'Working…' : copy.confirmLabel}</Button>
      </>}>
      <div className="ld-form">
        {action === 'assign' && (
          <label className="ld-opt">
            <span>Assign to</span>
            <Select aria-label="Assign to" value={owner} onChange={(event) => setOwner(event.target.value)}>
              <option value="auto">Distribute evenly between available recruiters</option>
              {staff.map((user) => <option key={user.id} value={user.id} disabled={user.onLeave}>{user.name}{user.onLeave ? ' (on leave)' : ''}</option>)}
              <option value={UNASSIGNED}>Nobody: make unassigned</option>
            </Select>
          </label>
        )}
        {action === 'status' && (
          <label className="ld-opt">
            <span>New status</span>
            <Select aria-label="New status" value={status} onChange={(event) => setStatus(event.target.value)}>
              {SETTABLE_STATUSES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </Select>
          </label>
        )}
        <div className="ld-confirm-lines" role="group" aria-label="What will happen">
          {copy.lines.map((line) => <p key={line} className={line.startsWith('Type ') ? 'is-strong' : undefined}>{line}</p>)}
        </div>
        {copy.typed && (
          <label className="ld-opt">
            <span>Type DELETE to confirm</span>
            <Input aria-label="Type DELETE to confirm" value={typed} autoComplete="off" spellCheck={false} onChange={(event) => setTyped(event.target.value)} />
          </label>
        )}
        {error && <p role="alert" className="ld-alert"><span>{error}</span></p>}
      </div>
    </Dialog>
  )
}

/** Change one lead's status from the row menu: the same status list the drawer uses (lead-model.ts). */
export function StatusDialog({ lead, onClose, onConfirm }: { lead: Lead | null; onClose: () => void; onConfirm: (lead: Lead, status: string) => Promise<void> }) {
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { if (lead) { setStatus(lead.status); setError(''); setBusy(false) } }, [lead])
  if (!lead) return null
  const go = async () => {
    setBusy(true); setError('')
    try { await onConfirm(lead, status) } catch (e) { setError(bulkErrorMessage(e)); setBusy(false) }
  }
  return (
    <Dialog open onClose={() => { if (!busy) onClose() }} title="Change status" width="sm" description={lead.name || undefined}
      footer={<><Button variant="secondary" disabled={busy} onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || status === lead.status} onClick={() => void go()}>{busy ? 'Saving…' : 'Change status'}</Button></>}>
      <div className="ld-form">
        <label className="ld-opt">
          <span>Status</span>
          <Select aria-label="Status" value={status} onChange={(event) => setStatus(event.target.value)}>
            {SETTABLE_STATUSES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </Select>
        </label>
        <p className="ld-hint">You can undo the change right after.</p>
        {error && <p role="alert" className="ld-alert"><span>{error}</span></p>}
      </div>
    </Dialog>
  )
}

/** Delete one lead (admin). A converted lead is refused by the server with a clear message, shown here. */
export function DeleteLeadDialog({ lead, onClose, onConfirm }: { lead: Lead | null; onClose: () => void; onConfirm: (lead: Lead) => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { if (lead) { setError(''); setBusy(false) } }, [lead])
  if (!lead) return null
  const copy = deleteOneCopy(lead.name)
  const blocked = lead.candidateId ? 'This lead was converted to a candidate, so it cannot be deleted.' : ''
  const go = async () => {
    setBusy(true); setError('')
    try { await onConfirm(lead) } catch (e) { setError(bulkErrorMessage(e)); setBusy(false) }
  }
  return (
    <Dialog open onClose={() => { if (!busy) onClose() }} title={copy.title} width="sm"
      footer={<><Button variant="secondary" disabled={busy} onClick={onClose}>Cancel</Button><Button variant="danger" icon={<Trash2 className="h-4 w-4" />} disabled={busy || Boolean(blocked)} onClick={() => void go()}>{busy ? 'Deleting…' : copy.confirmLabel}</Button></>}>
      <div className="ld-form">
        <div className="ld-confirm-lines" role="group" aria-label="What will happen">
          {copy.lines.map((line) => <p key={line}>{line}</p>)}
        </div>
        {blocked && <p role="alert" className="ld-alert"><span>{blocked}</span></p>}
        {error && <p role="alert" className="ld-alert"><span>{error}</span></p>}
      </div>
    </Dialog>
  )
}
