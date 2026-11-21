import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Button, Input, Select, Textarea, Field } from '@/shared/ui/primitives'
import { DatePicker } from '@/shared/ui/date-picker'
import { Dialog, Sheet } from '@/shared/ui/overlay'
import { jobRequirementsApi } from '@/features/job-requirements/job-requirements-api'
import {
  CLOSED_REASONS, DETAIL_INPUTS, FIELD_LABEL_MAX, FIELD_TYPES, detailsChanges, detailsFor, fieldLabelError, historyWhen,
  type ClosedReason, type DetailKey, type FieldType, type HistoryEntry, type RequirementField, type RequirementItem, type RequirementPatch,
} from '@/features/job-requirements/job-requirements-model'

/**
 * "Requirements: <client>": the form behind the Requirements details cell, laid out like the owner's sheet: a header band (Sl. No. | Job Requirements Details |
 * Client Information) and 16 numbered rows, the number and label on the left and a box on the right for what the client wrote; the 17th row is the Deadline
 * (a date picker, used for the due-date reminders). Saves only the keys that changed (the server merges them by key); the form scrolls inside the dialog.
 */
export function DetailsDialog({ item, onClose, onSave }: { item: RequirementItem; onClose: () => void; onSave: (patch: RequirementPatch) => Promise<boolean> }) {
  const start = detailsFor(item)
  const [form, setForm] = useState<Record<DetailKey, string>>(start)
  const [saving, setSaving] = useState(false)
  const changes = detailsChanges(start, form)
  const dirty = Object.keys(changes).length > 0
  const set = (key: DetailKey, value: string) => setForm((current) => ({ ...current, [key]: value }))
  const submit = async () => {
    if (!dirty) { onClose(); return }
    setSaving(true)
    const ok = await onSave({ details: changes })
    setSaving(false)
    if (ok) onClose()
  }
  return (
    <Dialog open onClose={onClose} width="lg" title={`Requirements: ${item.clientName.trim() || 'new requirement'}`}
      description={item.country ? `Country: ${item.country}` : undefined}
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={saving}>Cancel</Button>
        <Button variant="primary" onClick={() => void submit()} disabled={saving}>{saving ? 'Saving...' : 'Save'}</Button>
      </>}>
      <form className="jr-sheet" onSubmit={(event) => { event.preventDefault(); void submit() }}>
        <div className="jr-sheet-head" aria-hidden="true">
          <span className="jr-sheet-no">Sl. No.</span><span>Job Requirements Details</span><span>Client Information</span>
        </div>
        {DETAIL_INPUTS.map((input) => {
          const id = `jr-detail-${input.key}`
          const value = form[input.key]
          return (
            <div key={input.key} className={cn('jr-sheet-row', input.kind === 'area' && 'is-tall')}>
              <span className="jr-sheet-no">{input.no}</span>
              <label className="jr-sheet-label" htmlFor={id}>{input.label}{input.hint && <small>{input.hint}</small>}</label>
              <div className="jr-sheet-input">
                {input.kind === 'area'
                  ? <Textarea id={id} value={value} rows={4} minRows={4} maxRows={10} maxLength={10000} onChange={(event) => set(input.key, event.target.value)} />
                  : input.kind === 'date'
                    ? <DatePicker id={id} value={/^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : ''} onChange={(iso) => set(input.key, iso)} aria-label={input.label} placeholder="Select date" />
                    : <Input id={id} value={value} maxLength={300} onChange={(event) => set(input.key, event.target.value)} />}
              </div>
            </div>
          )
        })}
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </Dialog>
  )
}

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
      description={field ? undefined : 'A new column appears at the right of Status for everyone.'}
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
