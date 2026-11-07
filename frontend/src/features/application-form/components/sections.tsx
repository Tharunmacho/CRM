import type { ReactNode } from 'react'
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Button, IconButton, Input } from '@/shared/ui/primitives'
import {
  EDUCATION_FORM_COLUMNS,
  EXPERIENCE_COLUMNS,
  EXPERIENCE_FIELDS,
  PASSPORT_FIELDS,
  PERSONAL_FIELDS,
  SECTION_TITLES,
  emptyEducationRow,
  emptyExperienceRow,
  isBlank,
  isBlankRow,
  moveRow,
  provenanceOf,
  removeRow,
  updateRow,
} from '../form-model'
import { RECRUITMENT_EMPTY_TEXT, type PaperLine, type ReceivedDocument } from '../paper-model'
import type { ApplicationFormData, FormDraft, MissingItem } from '../types'
import { Cell, FormField, ProvenanceChip } from './fields'

export interface FormContext {
  form: ApplicationFormData
  draft: FormDraft
  editing: boolean
  errors: Record<string, string>
  updated: string[]
  setDraft: (update: (draft: FormDraft) => FormDraft) => void
}

/** A numbered section band of the paper form. */
export function Section({
  no,
  title,
  missing = [],
  aside,
  plain,
  children,
}: {
  no: number
  /** Overrides the standard title of the section (the paper form calls section 5 "Documents received"). */
  title?: string
  /** An extra section the paper form does not number: the badge shows a dot instead of the number. */
  plain?: boolean
  missing?: MissingItem[]
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <section className={cn('af-section', plain && 'is-extra')} id={`af-section-${no}`} aria-labelledby={`af-section-title-${no}`}>
      <header className="af-section-head">
        <span className={cn('af-section-no', plain && 'is-plain')} aria-hidden="true">{plain ? '' : no}</span>
        <h2 id={`af-section-title-${no}`}>{title ?? SECTION_TITLES[no]}</h2>
        {missing.length > 0 && <span className="af-section-missing">{missing.length} missing</span>}
        {aside && <span className="af-section-aside">{aside}</span>}
      </header>
      <div className="af-section-body">{children}</div>
    </section>
  )
}

function fieldProps(ctx: FormContext) {
  return {
    form: ctx.form,
    draft: ctx.draft,
    editing: ctx.editing,
    errors: ctx.errors,
    updated: ctx.updated,
    onChange: (path: string, value: string | string[]) => {
      const [section, key] = path.split('.')
      ctx.setDraft((current) => ({ ...current, [section]: { ...(current as unknown as Record<string, object>)[section], [key]: value } }) as FormDraft)
    },
  }
}

/**
 * Section 1 as the official form has it: pairs of cells (name and father's name, date of birth and marital status, gender and
 * nationality, languages and mobile number) and the address on a row of its own, so no row is left half empty.
 */
export function PersonalSection({ ctx, missing }: { ctx: FormContext; missing: MissingItem[] }) {
  const props = fieldProps(ctx)
  const field = (path: string, wide?: boolean) => {
    const def = PERSONAL_FIELDS.find((item) => item.path === path)!
    return <FormField key={path} def={def} wide={wide} {...props} />
  }
  return (
    <Section no={1} missing={missing}>
      <div className="af-cells">
        {['personal.candidateName', 'personal.fatherName', 'personal.dateOfBirth', 'personal.maritalStatus', 'personal.gender', 'personal.nationality', 'personal.languagesKnown', 'personal.mobileNumber'].map((path) => field(path))}
        {field('personal.currentAddress', true)}
      </div>
    </Section>
  )
}

export function PassportSection({ ctx, missing }: { ctx: FormContext; missing: MissingItem[] }) {
  const props = fieldProps(ctx)
  return (
    <Section no={2} missing={missing}>
      <div className="af-cells">
        {PASSPORT_FIELDS.map((def) => <FormField key={def.path} def={def} {...props} />)}
      </div>
    </Section>
  )
}

/** Shared repeating-row table: header, rows, add button. `editing` swaps text for inputs and shows row controls. */
export function RowTable<T extends { rowId: string }>({
  label,
  columns,
  rows,
  editing,
  cols,
  errors,
  errorPrefix,
  onChange,
  onMove,
  onRemove,
  onAdd,
  addLabel,
  emptyText,
  printBlankRows,
}: {
  label: string
  columns: readonly { key: string; label: string; placeholder: string }[]
  rows: T[]
  editing: boolean
  cols: string
  errors?: Record<string, string>
  errorPrefix?: string
  onChange: (rowId: string, key: string, value: string) => void
  onMove: (rowId: string, direction: -1 | 1) => void
  onRemove: (rowId: string) => void
  onAdd: () => void
  addLabel: string
  emptyText: string
  printBlankRows: number
}) {
  const shown = editing ? rows : rows.filter((row) => !isBlankRow(row as unknown as Record<string, unknown>))
  const blanks = editing ? 0 : Math.max(0, printBlankRows - shown.length)
  return (
    <div className={cn('af-rowtable', editing && 'is-editing')} style={{ ['--af-cols' as string]: cols }}>
      <div className="af-rowtable-head" role="row">
        {columns.map((column) => <span key={column.key} role="columnheader">{column.label}</span>)}
        {editing && <span className="af-rowtable-ctl" aria-hidden="true" />}
      </div>
      {shown.length === 0 && !editing && (
        <div className="af-rowtable-empty">
          <span className="af-empty" aria-hidden="true">–</span>
          <span>{emptyText}</span>
        </div>
      )}
      {shown.map((row, index) => (
        <div className="af-rowtable-row" role="row" key={row.rowId}>
          {columns.map((column) => {
            const value = String((row as unknown as Record<string, unknown>)[column.key] ?? '')
            const error = errors?.[`${errorPrefix}.${index}.${column.key}`]
            return (
              <div className={cn('af-rowtable-cell', error && 'has-error')} role="cell" data-label={column.label} key={column.key}>
                {editing ? (
                  <>
                    <Input value={value} placeholder={column.placeholder} aria-label={`${column.label}, row ${index + 1}`} inputMode={column.key === 'passedYear' ? 'numeric' : undefined} onChange={(event) => onChange(row.rowId, column.key, event.target.value)} />
                    {error && <p className="af-note is-error" role="alert">{error}</p>}
                  </>
                ) : isBlank(value) ? <span className="af-empty">–</span> : <span className="af-text">{value}</span>}
              </div>
            )
          })}
          {editing && (
            <div className="af-rowtable-ctl">
              <IconButton label={`Move row ${index + 1} up`} disabled={index === 0} onClick={() => onMove(row.rowId, -1)}><ArrowUp className="h-4 w-4" /></IconButton>
              <IconButton label={`Move row ${index + 1} down`} disabled={index === rows.length - 1} onClick={() => onMove(row.rowId, 1)}><ArrowDown className="h-4 w-4" /></IconButton>
              <IconButton label={`Remove row ${index + 1}`} tone="danger" onClick={() => onRemove(row.rowId)}><Trash2 className="h-4 w-4" /></IconButton>
            </div>
          )}
        </div>
      ))}
      {Array.from({ length: blanks }, (_, index) => <div className="af-rowtable-row is-print-blank" aria-hidden="true" key={`blank-${index}`}>{columns.map((column) => <div className="af-rowtable-cell" key={column.key} />)}</div>)}
      {editing && (
        <div className="af-rowtable-add">
          <Button size="sm" icon={<Plus />} onClick={onAdd}>{addLabel}</Button>
          <span>{label}</span>
        </div>
      )}
    </div>
  )
}

/** The education table as the official form words it: one column for the name and address of the institution. */
const PAPER_EDUCATION_COLUMNS = [
  { key: 'level', label: 'Educational level or course', placeholder: '' },
  { key: 'passedYear', label: 'Year passed', placeholder: '' },
  { key: 'institute', label: 'Name and address of institution', placeholder: '' },
] as const

export function EducationSection({ ctx, missing }: { ctx: FormContext; missing: MissingItem[] }) {
  const { draft, setDraft, editing, form } = ctx
  const set = (update: (rows: FormDraft['education']) => FormDraft['education']) => setDraft((current) => ({ ...current, education: update(current.education) }))
  const hasRows = draft.education.some((row) => !isBlankRow(row as unknown as Record<string, unknown>))
  return (
    <Section no={3} missing={missing} aside={hasRows ? <ProvenanceChip kind={provenanceOf(form, 'education', true, 'resume')} /> : undefined}>
      <RowTable
        label="List every qualification, newest first."
        columns={PAPER_EDUCATION_COLUMNS}
        rows={draft.education}
        editing={editing}
        cols="minmax(0,1.3fr) minmax(0,.6fr) minmax(0,2.2fr)"
        errors={ctx.errors}
        errorPrefix="education"
        onChange={(rowId, key, value) => set((rows) => updateRow(rows, rowId, { [key]: value }))}
        onMove={(rowId, direction) => set((rows) => moveRow(rows, rowId, direction))}
        onRemove={(rowId) => set((rows) => removeRow(rows, rowId))}
        onAdd={() => set((rows) => [...rows, emptyEducationRow()])}
        addLabel="Add qualification"
        emptyText="No qualifications yet. Upload a resume or add one."
        printBlankRows={2}
      />
    </Section>
  )
}

export function ExperienceSection({ ctx, missing }: { ctx: FormContext; missing: MissingItem[] }) {
  const { draft, setDraft, editing, form } = ctx
  const props = fieldProps(ctx)
  const set = (update: (rows: FormDraft['experience']['history']) => FormDraft['experience']['history']) =>
    setDraft((current) => ({ ...current, experience: { ...current.experience, history: update(current.experience.history) } }))
  const hasRows = draft.experience.history.some((row) => !isBlankRow(row as unknown as Record<string, unknown>))
  return (
    <Section no={4} missing={missing} aside={hasRows ? <ProvenanceChip kind={provenanceOf(form, 'experience.history', true, 'resume')} /> : undefined}>
      <div className="af-cells af-cells-3">
        {EXPERIENCE_FIELDS.map((def) => <FormField key={def.path} def={def} {...props} />)}
      </div>
      <RowTable
        label="List every employer, newest first."
        columns={EXPERIENCE_COLUMNS}
        rows={draft.experience.history}
        editing={editing}
        cols="minmax(0,1.4fr) minmax(0,1.2fr) minmax(0,.9fr) minmax(0,1fr)"
        onChange={(rowId, key, value) => set((rows) => updateRow(rows, rowId, { [key]: value }))}
        onMove={(rowId, direction) => set((rows) => moveRow(rows, rowId, direction))}
        onRemove={(rowId) => set((rows) => removeRow(rows, rowId))}
        onAdd={() => set((rows) => [...rows, emptyExperienceRow()])}
        addLabel="Add previous employer"
        emptyText="No previous employers yet. Upload a resume or add one."
        printBlankRows={2}
      />
    </Section>
  )
}

/**
 * Section 5 of the paper form: the documents that have arrived, with their file names. Documents that have not arrived are not
 * listed (the checklist card says what is missing); this part only records what the paper form can show as received.
 */
export function ReceivedDocumentsSection({ received, missing }: { received: ReceivedDocument[]; missing: MissingItem[] }) {
  return (
    <Section no={5} title="Documents received" missing={missing}>
      {received.length === 0 ? (
        <p className="af-sheet-empty">No documents received yet.</p>
      ) : (
        <ul className="af-received">
          {received.map((item) => (
            <li key={item.kind}>
              <span className="af-received-label">{item.label}</span>
              <span className="af-received-files">
                {item.fileNames.length ? item.fileNames.map((name, index) => <span key={`${name}-${index}`}>{name}</span>) : <span>Received</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

/**
 * Section 6: Client, Job, Country, Source, Agency and Recruiter, from the live assignment the pickers above the form edit.
 * Only what is known is shown; when nothing is, one muted line says so. They are shown here, not typed here.
 */
export function RecruitmentSection({ rows }: { rows: PaperLine[] }) {
  return (
    <Section no={6}>
      {rows.length === 0 ? (
        <p className="af-sheet-empty">{RECRUITMENT_EMPTY_TEXT}</p>
      ) : (
        <div className="af-cells">
          {/* An odd count would leave a gap in the last row, so the last cell spans it. */}
          {rows.map((row, index) => (
            <Cell key={row.key} label={row.label} path={`recruitment.${row.key}`} wide={rows.length % 2 === 1 && index === rows.length - 1}>
              <span className="af-text">{row.value}</span>
            </Cell>
          ))}
        </div>
      )}
    </Section>
  )
}
