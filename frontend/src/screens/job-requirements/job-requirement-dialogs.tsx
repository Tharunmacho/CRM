import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Button, Input, Select, Field } from '@/shared/ui/primitives'
import { Dialog, Sheet } from '@/shared/ui/overlay'
import { jobRequirementsApi } from '@/features/job-requirements/job-requirements-api'
import {
  CLOSED_REASONS, FIELD_LABEL_MAX, FIELD_TYPES, fieldLabelError, historyWhen,
  type ClosedReason, type FieldType, type HistoryEntry, type RequirementField, type RequirementItem,
} from '@/features/job-requirements/job-requirements-model'

/** "+ Add field" and "Rename": a name, and for a new column its type (a type cannot change later: the saved values would not fit). */
export function FieldDialog({ field, fields, onClose, onSave }: {
  field?: RequirementField
  fields: readonly RequirementField[]
  onClose: () => void
  onSave: (label: string, type: FieldType) => Promise<void>
}) {
  const [label, setLabel] = useState(field?.label ?? '')
  const [type, setType] = useState<FieldType>(field?.type ?? 'text')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const submit = async () => {
    const problem = fieldLabelError(label, fields, field?.id)
    if (problem) { setError(problem); return }
    setSaving(true)
    try { await onSave(label.replace(/\s+/g, ' ').trim(), type); onClose() }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Could not save the column'); setSaving(false) }
  }
  return (
    <Dialog open onClose={onClose} width="sm" title={field ? 'Rename column' : 'Add a column'}
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={saving}>Cancel</Button>
        <Button variant="primary" onClick={() => void submit()} disabled={saving}>{saving ? 'Saving...' : field ? 'Rename' : 'Add column'}</Button>
      </>}>
      <form className="jr-form is-single" onSubmit={(event) => { event.preventDefault(); void submit() }}>
        <Field label="Field name" error={error || undefined}>
          <Input autoFocus value={label} maxLength={FIELD_LABEL_MAX} placeholder="Visa type" aria-invalid={error ? true : undefined} onChange={(event) => { setLabel(event.target.value); if (error) setError('') }} />
        </Field>
        {!field && (
          <Field label="Type">
            <Select value={type} onChange={(event) => setType(event.target.value as FieldType)} aria-label="Type">
              {FIELD_TYPES.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
            </Select>
          </Field>
        )}
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </Dialog>
  )
}

/** The confirm step of a delete (a requirement, or a whole column with its values). */
export function ConfirmDialog({ title, body, action, onClose, onConfirm }: { title: string; body: string; action: string; onClose: () => void; onConfirm: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const run = async () => {
    setBusy(true); setError('')
    try { await onConfirm(); onClose() } catch (failure) { setError(failure instanceof Error ? failure.message : 'It could not be deleted.'); setBusy(false) }
  }
  return (
    <Dialog open onClose={onClose} width="sm" title={title}
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="danger" onClick={() => void run()} disabled={busy}>{busy ? 'Deleting...' : action}</Button>
      </>}>
      <p className="jr-confirm">{body}</p>
      {error && <p className="jr-confirm-error" role="alert">{error}</p>}
    </Dialog>
  )
}

/** "Why is it closed?": one press on a reason closes the requirement with it; Skip closes without one. */
export function CloseDialog({ item, onClose, onPick }: { item: RequirementItem; onClose: () => void; onPick: (reason: ClosedReason) => void }) {
  return (
    <Dialog open onClose={onClose} width="sm" title="Why is it closed?" description={item.clientName.trim() ? `${item.clientName}${item.country ? `, ${item.country}` : ''}` : undefined}
      footer={<>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button variant="ghost" onClick={() => onPick('')}>Skip</Button>
      </>}>
      <div className="jr-reasons" role="group" aria-label="Reason for closing">
        {CLOSED_REASONS.map((reason) => <Button key={reason.value} variant="secondary" className="jr-reason" onClick={() => onPick(reason.value)}>{reason.label}</Button>)}
      </div>
    </Dialog>
  )
}

/** The History drawer: who did what and when, newest first, read from the audit entries of this requirement. */
export function HistoryDrawer({ item, onClose }: { item: RequirementItem; onClose: () => void }) {
  const [entries, setEntries] = useState<HistoryEntry[] | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let live = true
    setEntries(null); setError('')
    jobRequirementsApi.history(item.id).then((page) => { if (live) setEntries(page.items) }, (failure) => { if (live) setError(failure instanceof Error ? failure.message : 'The history could not be loaded.') })
    return () => { live = false }
  }, [item.id, attempt])
  return (
    <Sheet open onClose={onClose} title="History" description={item.clientName.trim() || 'Requirement'} width="md"
      footer={<Button variant="primary" onClick={onClose}>Close</Button>}>
      <div className="jr-history">
        {error ? <div role="alert" className="jr-history-state">{error} <button type="button" className="jr-retry" onClick={() => setAttempt((n) => n + 1)}>Try again</button></div>
          : entries === null ? <div className="jr-history-state" role="status"><Loader2 size={15} className="jr-spin" aria-hidden="true" /> Loading</div>
            : entries.length === 0 ? <div className="jr-history-state">Nothing has been recorded for this requirement yet.</div>
              : <ol className="jr-timeline">
                {entries.map((entry, index) => (
                  <li key={`${entry.at}-${index}`}>
                    <span className="jr-timeline-dot" aria-hidden="true" />
                    <p className="jr-timeline-what">{entry.summary || entry.action}</p>
                    {entry.summary && entry.summary !== entry.action && <p className="jr-timeline-sum">{entry.action}</p>}
                    <p className="jr-timeline-who">{entry.actorName || 'Someone'} <span aria-hidden="true">&middot;</span> <time dateTime={entry.at}>{historyWhen(entry.at)}</time></p>
                  </li>
                ))}
              </ol>}
      </div>
    </Sheet>
  )
}
