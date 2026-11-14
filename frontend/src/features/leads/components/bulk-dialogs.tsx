import { useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'
import type { User } from '@/shared/model/types'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Input, Select } from '@/shared/ui/primitives'
import { bulkErrorMessage } from '../lead-bulk-api'
import { UNASSIGNED, assignChoice, confirmCopy, deleteOneCopy, typedConfirmOk, type BulkAction, type BulkOptions } from '../lead-bulk-model'
import { SETTABLE_STATUSES, leadStatus, type Lead } from '../lead-model'
import { RecruiterPicker } from './recruiter-picker'
import '@/styles/lead-bulk.css'

type Written = Exclude<BulkAction, 'export'>

/** The BulkOptions for the recruiters ticked (see lead-bulk-model assignChoice). */
const assignChoiceOptions = (ids: string[]): Pick<BulkOptions, 'ownerId' | 'assigneeIds'> => {
  const choice = assignChoice(ids)
  return 'assigneeIds' in choice ? { assigneeIds: choice.assigneeIds } : { ownerId: choice.ownerId }
}

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
  const [chosen, setChosen] = useState<string[]>([])  // recruiters ticked; none = spread evenly between everyone available
  const [nobody, setNobody] = useState(false)          // "Nobody: make unassigned"
  const [status, setStatus] = useState('contacted')
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { if (action) { setChosen([]); setNobody(false); setStatus('contacted'); setTyped(''); setError(''); setBusy(false) } }, [action])
  if (!action) return null
  const names = chosen.map((id) => users.find((user) => user.id === id)?.name).filter((name): name is string => Boolean(name))
  const copy = confirmCopy({
    action, count, allMatching,
    ownerName: nobody ? UNASSIGNED : names.length ? names[0] : 'auto', recruiterNames: nobody ? undefined : names,
    statusLabel: leadStatus(status).label,
  })
  const ready = !busy && count > 0 && (!copy.typed || typedConfirmOk(typed))
  const go = async () => {
    setBusy(true); setError('')
    try {
      await onConfirm({ ...(action === 'assign' ? (nobody ? { ownerId: UNASSIGNED } : assignChoiceOptions(chosen)) : {}), status: action === 'status' ? status : undefined, confirm: copy.typed ? typed.trim() : undefined })
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
          <RecruiterPicker users={users} selected={chosen} onChange={setChosen} total={count} disabled={busy} label="Assign to"
            exclusive={{ label: 'Nobody: make unassigned', summary: 'The leads are taken back from their recruiters and nobody owns them.', active: nobody, onChange: setNobody }} />
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
