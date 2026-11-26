import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Button, CheckboxInput, IconButton, Input } from '@/shared/ui/primitives'
import { Dialog, Tabs } from '@/shared/ui/overlay'
import {
  OPTION_KINDS, OPTION_LABEL_MAX, optionNameError, optionsOf,
  type OptionKind, type RequirementOption,
} from '@/features/job-requirements/job-requirements-model'
import { ConfirmDialog } from './job-requirement-dialogs'
import '@/styles/job-requirement-fields.css'

const messageOf = (failure: unknown, fallback: string) => (failure instanceof Error && failure.message ? failure.message : fallback)

/**
 * "Manage fields": the names offered in the requirement form, so staff add a new one themselves. Two lists (Requirement fields of a position, and the general
 * Particulars); each shows the built-in names (fixed), then the ones the team added: rename them, delete them (admin level only; rows already saved keep their
 * text) and, for particulars, choose "Show on every form" (a standard particular is a row of every new form after the built-in eight). Changes are saved at once.
 */
export function FieldManagerDialog({ options, canDelete, onClose, onCreate, onUpdate, onDelete }: {
  options: readonly RequirementOption[]
  canDelete: boolean
  onClose: () => void
  onCreate: (kind: OptionKind, label: string, standard: boolean) => Promise<unknown>
  onUpdate: (id: string, body: { label?: string; standard?: boolean }) => Promise<unknown>
  onDelete: (id: string) => Promise<void>
}) {
  const [kind, setKind] = useState<OptionKind>('requirement')
  const [name, setName] = useState('')
  const [standard, setStandard] = useState(false)
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState('')
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [deleting, setDeleting] = useState<RequirementOption | null>(null)
  const meta = OPTION_KINDS.find((entry) => entry.kind === kind) ?? OPTION_KINDS[0]
  // Saving a form remembers every name it uses; the built-in ones are already listed above, so they are not repeated as "added by your team" (unless set to show on every form).
  const builtLower = new Set(meta.builtIn.map((label) => label.trim().toLowerCase()))
  const mine = optionsOf(options, kind).filter((option) => option.standard || !builtLower.has(option.label.trim().toLowerCase()))
  const singular = kind === 'requirement' ? 'requirement field' : 'particular'

  const mark = (id: string, patch: { busy?: boolean; error?: string }) => {
    if (patch.busy !== undefined) setBusy((current) => ({ ...current, [id]: patch.busy as boolean }))
    if (patch.error !== undefined) setErrors((current) => { const rest = { ...current }; if (patch.error) rest[id] = patch.error; else delete rest[id]; return rest })
  }
  const forget = (id: string) => setDrafts((current) => { const rest = { ...current }; delete rest[id]; return rest })

  const add = async () => {
    const problem = optionNameError(name, kind, options)
    if (problem) { setAddError(problem); return }
    setAdding(true); setAddError('')
    try { await onCreate(kind, name.replace(/\s+/g, ' ').trim(), kind === 'particular' && standard); setName(''); setStandard(false) }
    catch (failure) { setAddError(messageOf(failure, 'The field could not be added. Try again.')) }
    finally { setAdding(false) }
  }

  const rename = async (option: RequirementOption) => {
    const typed = (drafts[option.id] ?? option.label).replace(/\s+/g, ' ').trim()
    if (typed === option.label) { forget(option.id); mark(option.id, { error: '' }); return }
    const problem = optionNameError(typed, option.kind, options, option.id)
    if (problem) { mark(option.id, { error: problem }); return }
    mark(option.id, { busy: true, error: '' })
    try { await onUpdate(option.id, { label: typed }); forget(option.id) }
    catch (failure) { mark(option.id, { error: messageOf(failure, 'It could not be renamed. Try again.') }) }
    finally { mark(option.id, { busy: false }) }
  }

  const setStandardOf = async (option: RequirementOption, on: boolean) => {
    mark(option.id, { busy: true, error: '' })
    try { await onUpdate(option.id, { standard: on }) }
    catch (failure) { mark(option.id, { error: messageOf(failure, 'It could not be changed. Try again.') }) }
    finally { mark(option.id, { busy: false }) }
  }

  return (
    <>
      <Dialog open onClose={onClose} width="md" title="Manage fields"
        footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
        <div className="jrx">
          <Tabs variant="pill" active={kind} onChange={(key) => { setKind(key as OptionKind); setAddError('') }} tabs={OPTION_KINDS.map((entry) => ({ key: entry.kind, label: entry.label }))} />

          <section aria-label={`Built-in ${meta.label.toLowerCase()}`}>
            <h4 className="jrx-title">Built in</h4>
            <ul className="jrx-chips">{meta.builtIn.map((label, index) => <li key={label} className={cn('jrx-chip', kind === 'particular' && index < 8 && 'is-standard')}>{label}</li>)}</ul>
          </section>

          <section aria-label={`Added by your team: ${meta.label.toLowerCase()}`}>
            <h4 className="jrx-title">Added by your team</h4>
            {mine.length === 0 && <p className="jrx-note">None yet</p>}
            <ul className="jrx-list">
              {mine.map((option) => (
                <li key={option.id} className={cn('jrx-row', errors[option.id] && 'has-error')}>
                  <Input value={drafts[option.id] ?? option.label} maxLength={OPTION_LABEL_MAX} disabled={busy[option.id]} aria-label={`Name of ${option.label}`} aria-invalid={errors[option.id] ? true : undefined}
                    onChange={(event) => { setDrafts((current) => ({ ...current, [option.id]: event.target.value })); if (errors[option.id]) mark(option.id, { error: '' }) }}
                    onBlur={() => void rename(option)}
                    onKeyDown={(event) => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.blur() } }} />
                  {kind === 'particular' && (
                    <label className="jrx-toggle"><CheckboxInput checked={option.standard} disabled={busy[option.id]} aria-label={`Show ${option.label} on every form`} onChange={(on) => void setStandardOf(option, on)} />Show on every form</label>
                  )}
                  {canDelete && <IconButton label={`Delete ${option.label}`} className="jrx-rm" disabled={busy[option.id]} onClick={() => setDeleting(option)}><Trash2 className="h-4 w-4" /></IconButton>}
                  {errors[option.id] && <p className="jrx-error" role="alert">{errors[option.id]}</p>}
                </li>
              ))}
            </ul>
          </section>

          <section className="jrx-add" aria-label={`Add a ${singular}`}>
            <h4 className="jrx-title">Add a {singular}</h4>
            <div className="jrx-add-row">
              <Input value={name} maxLength={OPTION_LABEL_MAX} placeholder={kind === 'requirement' ? 'e.g. Visa status' : 'e.g. Joining date'} aria-label={`Name of the new ${singular}`} aria-invalid={addError ? true : undefined}
                onChange={(event) => { setName(event.target.value); if (addError) setAddError('') }}
                onKeyDown={(event) => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); void add() } }} />
              <Button variant="primary" icon={<Plus />} onClick={() => void add()} disabled={adding}>{adding ? 'Adding...' : 'Add'}</Button>
            </div>
            {kind === 'particular' && <label className="jrx-toggle"><CheckboxInput checked={standard} onChange={setStandard} aria-label="Show the new particular on every form" />Show on every form</label>}
            {addError && <p className="jrx-error" role="alert">{addError}</p>}
          </section>
        </div>
      </Dialog>
      {deleting && (
        <ConfirmDialog title={`Delete "${deleting.label}"?`} action="Delete" onClose={() => setDeleting(null)}
          body="It is no longer suggested. Forms that already use it keep the text they have."
          onConfirm={async () => { await onDelete(deleting.id) }} />
      )}
    </>
  )
}
