import type { ReactNode } from 'react'
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Button, IconButton, Input } from '@/shared/ui/primitives'
import {
  EDUCATION_COLUMNS,
  EXPERIENCE_COLUMNS,
  EXPERIENCE_FIELDS,
  PASSPORT_FIELDS,
  PERSONAL_FIELDS,
  RECRUITMENT_FIELDS,
  SECTION_TITLES,
  emptyEducationRow,
  emptyExperienceRow,
  experienceSplitHint,
  isBlank,
  isBlankRow,
  moveRow,
  provenanceOf,
  removeRow,
  updateRow,
} from '../form-model'
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
  missing = [],
  aside,
  plain,
  children,
}: {
  no: number
  /** An extra section the paper form does not number: the badge shows a dot instead of the number. */
  plain?: boolean
  missing?: MissingItem[]
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <section className={cn('af-section', plain && 'is-extra', no === 5 && 'is-documents')} id={`af-section-${no}`} aria-labelledby={`af-section-title-${no}`}>
      <header className="af-section-head">
        <span className={cn('af-section-no', plain && 'is-plain')} aria-hidden="true">{plain ? '' : no}</span>
        <h2 id={`af-section-title-${no}`}>{SECTION_TITLES[no]}</h2>
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

export function PersonalSection({ ctx, missing, extras }: { ctx: FormContext; missing: MissingItem[]; extras?: ReactNode }) {
  const props = fieldProps(ctx)
  return (
    <Section no={1} missing={missing}>
      <div className="af-cells">
        {PERSONAL_FIELDS.map((def) => <FormField key={def.path} def={def} {...props} />)}
      </div>
      {extras}
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
                    <Input value={value} placeholder={column.placeholder} aria-label={`${column.label}, row ${index + 1}`} inputMode={column.key === 'year' ? 'numeric' : undefined} onChange={(event) => onChange(row.rowId, column.key, event.target.value)} />
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

export function EducationSection({ ctx, missing }: { ctx: FormContext; missing: MissingItem[] }) {
  const { draft, setDraft, editing, form } = ctx
  const set = (update: (rows: FormDraft['education']) => FormDraft['education']) => setDraft((current) => ({ ...current, education: update(current.education) }))
  const hasRows = draft.education.some((row) => !isBlankRow(row as unknown as Record<string, unknown>))
  return (
    <Section no={3} missing={missing} aside={hasRows ? <ProvenanceChip kind={provenanceOf(form, 'education', true, 'resume')} /> : undefined}>
      <RowTable
        label="Add every course, newest first."
        columns={EDUCATION_COLUMNS}
        rows={draft.education}
        editing={editing}
        cols="minmax(0,1.4fr) minmax(0,.55fr) minmax(0,1.8fr)"
        errors={ctx.errors}
        errorPrefix="education"
        onChange={(rowId, key, value) => set((rows) => updateRow(rows, rowId, { [key]: value }))}
        onMove={(rowId, direction) => set((rows) => moveRow(rows, rowId, direction))}
        onRemove={(rowId) => set((rows) => removeRow(rows, rowId))}
        onAdd={() => set((rows) => [...rows, emptyEducationRow()])}
        addLabel="Add education"
        emptyText="No educational details yet. Upload a resume or add a row."
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
  const split = experienceSplitHint(draft.experience)
  return (
    <Section no={4} missing={missing} aside={hasRows ? <ProvenanceChip kind={provenanceOf(form, 'experience.history', true, 'resume')} /> : undefined}>
      <div className="af-cells af-cells-3">
        {EXPERIENCE_FIELDS.map((def) => <FormField key={def.path} def={def} {...props} />)}
      </div>
      {split && <p className="af-split-hint" role="status">{split}</p>}
      <RowTable
        label="Add every employer, newest first."
        columns={EXPERIENCE_COLUMNS}
        rows={draft.experience.history}
        editing={editing}
        cols="minmax(0,1.4fr) minmax(0,1.2fr) minmax(0,.9fr) minmax(0,1fr)"
        onChange={(rowId, key, value) => set((rows) => updateRow(rows, rowId, { [key]: value }))}
        onMove={(rowId, direction) => set((rows) => moveRow(rows, rowId, direction))}
        onRemove={(rowId) => set((rows) => removeRow(rows, rowId))}
        onAdd={() => set((rows) => [...rows, emptyExperienceRow()])}
        addLabel="Add employer"
        emptyText="No previous experience yet. Upload a resume or add a row."
        printBlankRows={2}
      />
    </Section>
  )
}

/** Client, country, position, source and recruiter come from the order and the candidate; they are shown, not typed here. */
export function RecruitmentSection({ form, hasOrder, fallbackCountry }: { form: ApplicationFormData; hasOrder: boolean; fallbackCountry?: string }) {
  void fallbackCountry
  return (
    <Section no={6} aside={hasOrder ? undefined : <span className="af-aside-note">Not matched to an order yet</span>}>
      <div className="af-cells">
        {RECRUITMENT_FIELDS.map((def) => {
          const value = form.recruitment[def.key]
          const blank = isBlank(value)
          return (
            <Cell key={def.key} label={def.label} path={`recruitment.${def.key}`} missing={blank && hasOrder && def.key !== 'source'}>
              {blank ? <span className="af-empty" aria-label="Not filled">–</span> : <span className="af-text">{value}</span>}
            </Cell>
          )
        })}
        {!isBlank(form.recruitment.sourceDetail) && (
          <Cell label="Source detail" path="recruitment.sourceDetail"><span className="af-text">{form.recruitment.sourceDetail}</span></Cell>
        )}
        {(RECRUITMENT_FIELDS.length + (isBlank(form.recruitment.sourceDetail) ? 0 : 1)) % 2 === 1 && <div className="af-cell is-filler" aria-hidden="true" />}
      </div>
    </Section>
  )
}
