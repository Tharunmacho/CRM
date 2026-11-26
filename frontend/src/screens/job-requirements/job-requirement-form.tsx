import { useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { AlertCircle, Copy, Plus, Trash2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Button, Field, IconButton, Input, Textarea } from '@/shared/ui/primitives'
import { DatePicker } from '@/shared/ui/date-picker'
import { Dialog } from '@/shared/ui/overlay'
import { SuggestInput } from '@/shared/ui/suggest-input'
import {
  GENERAL_DEFAULTS, GENERAL_SUGGESTIONS, LIMITS, REQUIREMENT_FIELD_SUGGESTIONS,
  blankGeneral, blankPosition, blankRequirement, buildPatch, copyFieldsToOthers, formFor, formProblems, isEmptyPatch, mergeSuggestions,
  type GeneralRow, type OptionKind, type Position, type PositionRequirement, type RequirementForm, type RequirementItem, type RequirementOption, type RequirementPatch,
} from '@/features/job-requirements/job-requirements-model'
import '@/styles/job-requirement-form.css'

const BUILT_IN_PARTICULARS = [...GENERAL_DEFAULTS, ...GENERAL_SUGGESTIONS]
const createLabel = (kind: OptionKind) => (typed: string) => `+ Create "${typed}" ${kind === 'requirement' ? 'requirement field' : 'particular'}`

/** A numbered band that starts a section of the form (the same primary-blue band as the table header). */
function Band({ no, id, title }: { no: number; id: string; title: string }) {
  return (
    <header className="jrf-band">
      <span className="jrf-band-no" aria-hidden="true">{no}</span>
      <h3 id={id}>{title}</h3>
    </header>
  )
}

const RowError = ({ message }: { message?: string }) => (message ? <p className="jrf-error" role="alert"><AlertCircle size={13} aria-hidden="true" />{message}</p> : null)

/**
 * The requirement form of one client, laid out like the client's "Job Requirement Form": 1 Job positions (title, vacancies, salary), 2 Job requirement
 * details (rows of field and details under each position), 3 General employment details (particulars, details, remarks), 4 the deadline and the client's
 * confirmation. It is a div, not a form: Enter moves to the next box and never saves or closes. The baseline is frozen when the form opens, so Save sends only
 * what THIS person changed (whole positions / general lists, the deadline, the confirmation), never somebody else's newer value written back from a stale copy.
 */
export function JobRequirementForm({ item, options = [], gone = false, onClose, onSave, onCreateOption }: {
  item: RequirementItem
  /** The field names staff added (suggested after the built-in ones). */
  options?: readonly RequirementOption[]
  /** Creates a field name for everybody right away; throws with the server's message when it cannot (a duplicate, no connection). */
  onCreateOption?: (kind: OptionKind, label: string) => Promise<unknown>
  /** Accepted for the screen's call, which also keeps the page-header manager; the form itself has no button for it. */
  /** The row was deleted by someone else while this form was open: what was typed stays, saving is off. */
  gone?: boolean
  onClose: () => void
  onSave: (patch: RequirementPatch) => Promise<boolean>
}) {
  const [start] = useState(() => formFor(item, options))
  const [form, setForm] = useState<RequirementForm>(start)
  const [saving, setSaving] = useState(false)
  const [tried, setTried] = useState(false)
  const [failed, setFailed] = useState(false)
  const [asking, setAsking] = useState(false)
  /** A field name that could not be created, by the row it was typed in. */
  const [createErrors, setCreateErrors] = useState<Record<string, string>>({})
  const root = useRef<HTMLDivElement>(null)
  const focusKey = useRef<string | null>(null)
  const patch = useMemo(() => buildPatch(start, form), [start, form])
  const dirty = !isEmptyPatch(patch)
  const problems = useMemo(() => formProblems(form), [form])
  const problemOf = (id: string) => (tried ? problems.find((entry) => entry.id === id)?.message : undefined) ?? createErrors[id]
  const requirementNames = useMemo(() => mergeSuggestions(REQUIREMENT_FIELD_SUGGESTIONS, options, 'requirement'), [options])
  const particularNames = useMemo(() => mergeSuggestions(BUILT_IN_PARTICULARS, options, 'particular'), [options])

  // Focus a box that was just added (or the Add button after a removal) once it is on screen.
  useLayoutEffect(() => {
    const key = focusKey.current
    if (!key) return
    focusKey.current = null
    root.current?.querySelector<HTMLElement>(`[data-fk="${CSS.escape(key)}"]`)?.focus()
  })

  const edit = (change: (current: RequirementForm) => RequirementForm) => { setFailed(false); setForm(change) }
  const setPositions = (change: (list: Position[]) => Position[]) => edit((current) => ({ ...current, positions: change(current.positions) }))
  const setGeneral = (change: (list: GeneralRow[]) => GeneralRow[]) => edit((current) => ({ ...current, general: change(current.general) }))
  const clearCreateError = (rowId: string) => { if (createErrors[rowId]) setCreateErrors((current) => { const rest = { ...current }; delete rest[rowId]; return rest }) }
  const createField = async (kind: OptionKind, rowId: string, label: string) => {
    if (!onCreateOption) return
    clearCreateError(rowId)
    try { await onCreateOption(kind, label) }
    catch (failure) { setCreateErrors((current) => ({ ...current, [rowId]: failure instanceof Error && failure.message ? failure.message : 'The field could not be created. Try again.' })) }
  }
  const editPosition = (id: string, next: Partial<Position>) => setPositions((list) => list.map((entry) => (entry.id === id ? { ...entry, ...next } : entry)))
  const editRequirement = (positionId: string, id: string, next: Partial<PositionRequirement>) =>
    setPositions((list) => list.map((entry) => (entry.id === positionId ? { ...entry, requirements: entry.requirements.map((row) => (row.id === id ? { ...row, ...next } : row)) } : entry)))
  const editGeneral = (id: string, next: Partial<GeneralRow>) => setGeneral((list) => list.map((row) => (row.id === id ? { ...row, ...next } : row)))

  const addPosition = () => { const created = blankPosition(); focusKey.current = `${created.id}-title`; setPositions((list) => [...list, created]) }
  const removePosition = (id: string) => { focusKey.current = 'add-position'; setPositions((list) => { const rest = list.filter((entry) => entry.id !== id); return rest.length ? rest : [blankPosition()] }) }
  const addRequirement = (positionId: string) => {
    const created = blankRequirement('')
    focusKey.current = `${created.id}-field`
    setPositions((list) => list.map((entry) => (entry.id === positionId && entry.requirements.length < LIMITS.requirements ? { ...entry, requirements: [...entry.requirements, created] } : entry)))
  }
  const removeRequirement = (positionId: string, id: string) => {
    focusKey.current = `add-requirement-${positionId}`
    setPositions((list) => list.map((entry) => (entry.id === positionId ? { ...entry, requirements: entry.requirements.filter((row) => row.id !== id) } : entry)))
  }
  const addGeneral = () => { const created = blankGeneral(''); focusKey.current = `${created.id}-label`; setGeneral((list) => (list.length < LIMITS.general ? [...list, created] : list)) }
  const removeGeneral = (id: string) => { focusKey.current = 'add-general'; setGeneral((list) => list.filter((row) => row.id !== id)) }

  const requestClose = () => { if (saving) return; if (dirty) setAsking((current) => !current); else onClose() }

  const submit = async () => {
    if (gone || saving) return
    if (problems.length) {
      setTried(true)
      focusKey.current = `${problems[0].id}-${problems[0].kind === 'position' ? 'title' : problems[0].kind === 'requirement' ? 'field' : 'label'}`
      setForm((current) => ({ ...current }))   // a render, so the focus above runs
      return
    }
    if (!dirty) { onClose(); return }
    setSaving(true); setFailed(false)
    const ok = await onSave(patch.positions || patch.general ? { ...patch, rememberNewFields: true } : patch)
    setSaving(false)
    if (ok) onClose(); else setFailed(true)
  }

  // Enter in a box moves to the next box; it never submits, adds or removes anything (only the long confirmation box takes a new line).
  const enterMoves = (event: KeyboardEvent<HTMLDivElement>) => {
    const target = event.target
    if (event.key !== 'Enter' || event.defaultPrevented || event.nativeEvent.isComposing) return
    if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) || target.dataset.multiline) return
    event.preventDefault()
    const boxes = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('input:not([disabled]), textarea:not([disabled])')).filter((box) => box.getClientRects().length > 0)
    boxes[boxes.indexOf(target) + 1]?.focus()
  }

  const named = form.positions.map((position, index) => ({ position, index, name: position.title.trim() || `Position ${index + 1}` }))
  const cards = named.filter(({ position }) => position.title.trim() || position.requirements.length > 0)

  return (
    <Dialog open onClose={requestClose} width="xl" title="Job requirements"
      footer={asking ? <>
        <span className="jrf-foot-note is-warn" role="alert">Discard what you changed?</span>
        <Button variant="secondary" onClick={onClose}>Discard changes</Button>
        <Button variant="primary" onClick={() => setAsking(false)}>Keep editing</Button>
      </> : <>
        {(tried && problems.length > 0)
          ? <span className="jrf-foot-note is-warn" role="alert">{problems.length === 1 ? 'One row needs a name before you can save.' : `${problems.length} rows need a name before you can save.`}</span>
          : failed ? <span className="jrf-foot-note is-warn" role="alert">It could not be saved. Check the connection and press Save again.</span>
            : null}
        <Button variant="secondary" onClick={requestClose} disabled={saving}>Cancel</Button>
        <Button variant="primary" onClick={() => void submit()} disabled={saving || gone}>{saving ? 'Saving...' : 'Save'}</Button>
      </>}>
      <div className="jrf" ref={root} onKeyDown={enterMoves}>
        {gone && <p role="alert" className="jr-gone">This requirement was deleted by someone else. What you typed is still here, but it can no longer be saved.</p>}

        {/* 1 ------------------------------------------------------------ job positions */}
        <section className="jrf-section" aria-labelledby="jrf-h1">
          <Band no={1} id="jrf-h1" title="Job positions" />
          <div className="jrf-box">
            <div className="jrf-head jrf-pos" aria-hidden="true"><span>S.No</span><span>Job title</span><span>No. of vacancies</span><span>Salary</span><span /></div>
            {form.positions.map((position, index) => {
              const error = problemOf(position.id)
              return (
                <div key={position.id} className={cn('jrf-row jrf-pos', error && 'has-error')} role="group" aria-label={`Position ${index + 1}`}>
                  <span className="jrf-no"><span className="jrf-no-word">Position </span>{index + 1}</span>
                  <div className="jrf-cell" data-area="title">
                    <span className="jrf-m" aria-hidden="true">Job title</span>
                    <Input value={position.title} maxLength={LIMITS.title} placeholder="e.g. Captain" data-fk={`${position.id}-title`} aria-label={`Job title of position ${index + 1}`} aria-invalid={error ? true : undefined}
                      onChange={(event) => editPosition(position.id, { title: event.target.value })} />
                  </div>
                  <div className="jrf-cell" data-area="vac">
                    <span className="jrf-m" aria-hidden="true">No. of vacancies</span>
                    <Input value={position.vacancies} maxLength={LIMITS.vacancies} placeholder="e.g. 5" autoComplete="off" aria-label={`No. of vacancies of position ${index + 1}`}
                      onChange={(event) => editPosition(position.id, { vacancies: event.target.value })} />
                  </div>
                  <div className="jrf-cell" data-area="sal">
                    <span className="jrf-m" aria-hidden="true">Salary</span>
                    <Input value={position.salary} maxLength={LIMITS.salary} placeholder="e.g. AED 1,500" autoComplete="off" aria-label={`Salary of position ${index + 1}`}
                      onChange={(event) => editPosition(position.id, { salary: event.target.value })} />
                  </div>
                  <IconButton label={`Remove position ${index + 1}`} className="jrf-rm" onClick={() => removePosition(position.id)}><Trash2 className="h-4 w-4" /></IconButton>
                  <RowError message={error} />
                </div>
              )
            })}
            <div className="jrf-actions">
              <Button size="sm" className="jrf-add" icon={<Plus />} data-fk="add-position" onClick={addPosition} disabled={form.positions.length >= LIMITS.positions}>Add position</Button>
            </div>
          </div>
        </section>

        {/* 2 ------------------------------------------------------------ job requirement details */}
        <section className="jrf-section" aria-labelledby="jrf-h2">
          <Band no={2} id="jrf-h2" title="Job requirement details" />
          <div className="jrf-box is-flush">
            {cards.length === 0 && <p className="jrf-empty">No job positions yet</p>}
            {cards.map(({ position, index, name }) => {
              const full = position.requirements.length >= LIMITS.requirements
              return (
                <div key={position.id} className="jrf-pcard" role="group" aria-label={`Requirements of ${name}`}>
                  <div className="jrf-pcard-head">
                    <span className="jrf-pcard-no" aria-hidden="true">{index + 1}</span>
                    <h4>{name}</h4>
                  </div>
                  {position.requirements.length > 0 && <div className="jrf-head jrf-req" aria-hidden="true"><span>Requirement field</span><span>Details</span><span /></div>}
                  {position.requirements.map((row, at) => {
                    const error = problemOf(row.id)
                    return (
                      <div key={row.id} className={cn('jrf-row jrf-req', error && 'has-error')} role="group" aria-label={`Requirement ${at + 1} of ${name}`}>
                        <div className="jrf-cell" data-area="field">
                          <span className="jrf-m" aria-hidden="true">Requirement field</span>
                          <SuggestInput value={row.field} options={requirementNames} maxLength={LIMITS.field} onCreate={onCreateOption ? (typed) => void createField('requirement', row.id, typed) : undefined} createLabel={createLabel('requirement')} placeholder="e.g. Qualification" data-fk={`${row.id}-field`}
                            aria-label={`Requirement field ${at + 1} of ${name}`} aria-invalid={error ? true : undefined} onChange={(value) => { clearCreateError(row.id); editRequirement(position.id, row.id, { field: value }) }} />
                        </div>
                        <div className="jrf-cell" data-area="details">
                          <span className="jrf-m" aria-hidden="true">Details</span>
                          <Textarea rows={1} minRows={1} maxRows={6} value={row.details} maxLength={LIMITS.details} placeholder="e.g. Hotel management qualification." data-fk={`${row.id}-details`}
                            aria-label={`Details of requirement ${at + 1} of ${name}`} onChange={(event) => editRequirement(position.id, row.id, { details: event.target.value })} />
                        </div>
                        <IconButton label={`Remove requirement ${at + 1} of ${name}`} className="jrf-rm" onClick={() => removeRequirement(position.id, row.id)}><Trash2 className="h-4 w-4" /></IconButton>
                        <RowError message={error} />
                      </div>
                    )
                  })}
                  <div className="jrf-actions">
                    <Button size="sm" className="jrf-add" icon={<Plus />} data-fk={`add-requirement-${position.id}`} disabled={full} onClick={() => addRequirement(position.id)}>Add requirement</Button>
                    {position.requirements.length > 0 && cards.length > 1 && (
                      <Button size="sm" variant="ghost" className="jrf-add" icon={<Copy />} onClick={() => edit((current) => ({ ...current, positions: copyFieldsToOthers(current.positions, position.id) }))}>Use these fields for the other positions</Button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </section>

        {/* 3 ------------------------------------------------------------ general employment details */}
        <section className="jrf-section" aria-labelledby="jrf-h3">
          <Band no={3} id="jrf-h3" title="General employment details" />
          <div className="jrf-box">
            <div className="jrf-head jrf-gen" aria-hidden="true"><span>S.No</span><span>Particulars</span><span>Details</span><span>Remarks</span><span /></div>
            {form.general.map((row, index) => {
              const error = problemOf(row.id)
              return (
                <div key={row.id} className={cn('jrf-row jrf-gen', error && 'has-error')} role="group" aria-label={`Particular ${index + 1}`}>
                  <span className="jrf-no"><span className="jrf-no-word">Particular </span>{index + 1}</span>
                  <div className="jrf-cell" data-area="label">
                    <span className="jrf-m" aria-hidden="true">Particulars</span>
                    <SuggestInput value={row.label} options={particularNames} maxLength={LIMITS.label} onCreate={onCreateOption ? (typed) => void createField('particular', row.id, typed) : undefined} createLabel={createLabel('particular')} placeholder="e.g. Food" data-fk={`${row.id}-label`}
                      aria-label={`Particular ${index + 1}`} aria-invalid={error ? true : undefined} onChange={(value) => { clearCreateError(row.id); editGeneral(row.id, { label: value }) }} />
                  </div>
                  <div className="jrf-cell" data-area="details">
                    <span className="jrf-m" aria-hidden="true">Details</span>
                    <Textarea rows={1} minRows={1} maxRows={6} value={row.details} maxLength={LIMITS.details} data-fk={`${row.id}-details`}
                      aria-label={`Details of ${row.label.trim() || `particular ${index + 1}`}`} onChange={(event) => editGeneral(row.id, { details: event.target.value })} />
                  </div>
                  <div className="jrf-cell" data-area="remarks">
                    <span className="jrf-m" aria-hidden="true">Remarks</span>
                    <Textarea rows={1} minRows={1} maxRows={6} value={row.remarks} maxLength={LIMITS.remarks}
                      aria-label={`Remarks of ${row.label.trim() || `particular ${index + 1}`}`} onChange={(event) => editGeneral(row.id, { remarks: event.target.value })} />
                  </div>
                  <IconButton label={`Remove ${row.label.trim() || `particular ${index + 1}`}`} className="jrf-rm" onClick={() => removeGeneral(row.id)}><Trash2 className="h-4 w-4" /></IconButton>
                  <RowError message={error} />
                </div>
              )
            })}
            <div className="jrf-actions">
              <Button size="sm" className="jrf-add" icon={<Plus />} data-fk="add-general" disabled={form.general.length >= LIMITS.general} onClick={addGeneral}>Add particular</Button>
            </div>
          </div>
        </section>

        {/* 4 ------------------------------------------------------------ deadline and confirmation */}
        <section className="jrf-section" aria-labelledby="jrf-h4">
          <Band no={4} id="jrf-h4" title="Deadline and client confirmation" />
          <div className="jrf-box jrf-last">
            <Field label="Deadline">
              <DatePicker value={/^\d{4}-\d{2}-\d{2}/.test(form.deadline) ? form.deadline.slice(0, 10) : ''} onChange={(iso) => edit((current) => ({ ...current, deadline: iso }))} aria-label="Deadline" placeholder="Select date" />
            </Field>
            <Field label="Client confirmation / special conditions">
              <Textarea data-multiline="true" rows={3} minRows={3} maxRows={10} value={form.remarks} maxLength={LIMITS.special} placeholder="e.g. Vacancies and salary details are pending confirmation."
                onChange={(event) => edit((current) => ({ ...current, remarks: event.target.value }))} />
            </Field>
          </div>
        </section>
      </div>
    </Dialog>
  )
}
