import { useCallback, useEffect, useState } from 'react'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Input } from '@/shared/ui/primitives'
import { LEAD_STATUSES } from '../lead-model'
import { NAME_MAX, STATUS_TONES, newStatusCheck, type LeadStatusTemplate } from '../lead-status-model'
import { deleteLeadStatus, listAllLeadStatusTemplates, renameLeadStatus, setLeadStatusActive } from '../lead-status-templates-api'
import '@/styles/lead-status.css'

const STANDARD_NAMES = [...LEAD_STATUSES.flatMap((status) => [status.value, status.label]), 'Resume received']
const toneLabel = (tone: string) => STATUS_TONES.find((item) => item.value === tone)?.hint ?? tone

/**
 * The small list behind "Manage statuses" (admin and MD): every status people added to the Lead sheet's status list, to rename, hide or delete.
 * A built-in status (Ringing, No incoming calls, Not eligible) can be hidden but never renamed or deleted. A lead that holds a status keeps its
 * text when the status is hidden or deleted. The standard statuses (Contacted, Interested ...) are not listed: they cannot be changed.
 */
export function StatusManageDialog({ open, onClose, onChanged }: { open: boolean; onClose: () => void; onChanged?: () => void }) {
  const [items, setItems] = useState<LeadStatusTemplate[] | null>(null)
  const [busyId, setBusyId] = useState('')
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null)
  const [confirmDelete, setConfirmDelete] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try { setItems(await listAllLeadStatusTemplates()); setError('') } catch (failure) { setError(failure instanceof Error ? failure.message : 'The statuses could not be loaded.') }
  }, [])
  useEffect(() => { if (open) { setItems(null); setEditing(null); setConfirmDelete(''); void load() } }, [open, load])
  if (!open) return null

  const run = async (id: string, work: () => Promise<unknown>) => {
    setBusyId(id); setError('')
    try { await work(); await load(); onChanged?.() } catch (failure) { setError(failure instanceof Error ? failure.message : 'That change could not be saved.') } finally { setBusyId('') }
  }
  const saveName = async () => {
    if (!editing || !items) return
    const current = items.find((item) => item.id === editing.id)
    if (!current) return
    const check = newStatusCheck(editing.name, 'neutral', items.filter((item) => item.id !== editing.id), STANDARD_NAMES)
    if (!check.ok) { setError(check.message); return }
    const id = editing.id
    setEditing(null)
    if (check.name !== current.name) await run(id, () => renameLeadStatus(id, check.name))   // a case-only change is a rename too
  }

  return (
    <Dialog open onClose={() => { if (!busyId) onClose() }} title="Manage statuses" width="sm" description="Statuses the team added to the status list. Hidden ones disappear from the list; leads that hold them keep their status."
      footer={<Button variant="secondary" onClick={onClose}>Close</Button>}>
      <div className="ld-form">
        {items === null && !error && <p className="ld-hint" role="status">Loading…</p>}
        {items && items.length === 0 && <p className="ld-hint">No extra statuses yet.</p>}
        {items && items.length > 0 && (
          <ul className="ld-statuslist" aria-label="Statuses">
            {items.map((item) => (
              <li key={item.id} className={item.active ? undefined : 'is-hidden'}>
                <span className="ld-statuslist-name">{item.name}{item.active ? '' : ' (hidden)'}</span>
                <span className="ld-statuslist-meta">{item.builtin ? 'Built in · ' : ''}{toneLabel(item.tone)}</span>
                <span className="ld-statuslist-acts">
                  {!item.builtin && <button type="button" disabled={Boolean(busyId)} onClick={() => setEditing({ id: item.id, name: item.name })}>Rename</button>}
                  <button type="button" disabled={Boolean(busyId)} onClick={() => void run(item.id, () => setLeadStatusActive(item.id, !item.active))}>{item.active ? 'Hide' : 'Show'}</button>
                  {!item.builtin && (confirmDelete === item.id
                    ? <button type="button" className="is-danger" disabled={Boolean(busyId)} onClick={() => { setConfirmDelete(''); void run(item.id, () => deleteLeadStatus(item.id)) }}>Delete for everyone</button>
                    : <button type="button" className="is-danger" disabled={Boolean(busyId)} onClick={() => setConfirmDelete(item.id)}>Delete</button>)}
                </span>
                {editing?.id === item.id && (
                  <div className="ld-statuslist-edit" role="group" aria-label={`Rename ${item.name}`}>
                    <Input autoFocus maxLength={NAME_MAX} value={editing.name} aria-label={`New name for ${item.name}`}
                      onChange={(event) => setEditing({ id: item.id, name: event.target.value })}
                      onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void saveName() } else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setEditing(null) } }} />
                    <Button size="sm" variant="primary" disabled={!editing.name.trim()} onClick={() => void saveName()}>Save</Button>
                    <Button size="sm" variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        {error && <p role="alert" className="ld-alert"><span>{error}</span></p>}
      </div>
    </Dialog>
  )
}
