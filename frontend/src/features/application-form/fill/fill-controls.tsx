import { useCallback, useId, useMemo, type CSSProperties, type ReactElement, type ReactNode, cloneElement, isValidElement } from 'react'
import { Sparkles } from 'lucide-react'
import '@/styles/application-form-fill.css'
import { cn } from '@/shared/lib/cn'
import { DatePicker } from '@/shared/ui/date-picker'
import { FieldLabelContext } from '@/shared/ui/control-context'
import { Input, Select, Textarea } from '@/shared/ui/primitives'
import { PhoneField } from '@/features/candidates/components/entry-fields'
import { TagField } from '@/features/candidates/components/entry-section'
import { FORM_OPTION_CATEGORIES, activeOptions, choicesFor, labelFor } from '@/features/form-options/form-options-model'
import { useFormOptions } from '@/features/form-options/use-form-options'
import { ageFromDob, formatFormDate, isBlank, toDraft, yearsLabel } from '../form-model'
import type { ApplicationFormData, FormDraft } from '../types'
import type { FormContext } from '../components/sections'
import { SOURCE_CHIP_TEXT, listToText, readField, sourceChipFor, textToList, writeField, type FillField, type SourceChip } from './fill-fields'

/**
 * What every field of the Fill details view needs from the form screen: the server's form, the draft being typed, the
 * saved draft (to tell a value a document filled from one a person typed over), the errors, and a writer.
 */
export interface FillEnv {
  form: ApplicationFormData
  draft: FormDraft
  saved: FormDraft
  errors: Record<string, string>
  updated: string[]
  write: (item: Pick<FillField, 'key' | 'extra'>, value: string | string[]) => void
  /** Several fields in one change (the mobile number and WhatsApp while they are the same). */
  writeMany: (changes: Array<[Pick<FillField, 'key' | 'extra'>, string | string[]]>) => void
}

export function useFillEnv(ctx: FormContext): FillEnv {
  const { form, draft, errors, updated, setDraft } = ctx
  // The saved copy only changes when the server's form does, not on every keystroke.
  const saved = useMemo(() => toDraft(form), [form])
  const write = useCallback((item: Pick<FillField, 'key' | 'extra'>, value: string | string[]) => setDraft((current) => writeField(current, item, value)), [setDraft])
  const writeMany = useCallback((changes: Array<[Pick<FillField, 'key' | 'extra'>, string | string[]]>) => setDraft((current) => changes.reduce((next, [item, value]) => writeField(next, item, value), current)), [setDraft])
  return { form, draft, saved, errors, updated, write, writeMany }
}

/** "From passport" / "From resume": a value a document filled in and nobody has changed since. */
export function SourceBadge({ kind }: { kind: SourceChip }) {
  return (
    <span className="aff-from" title={`${SOURCE_CHIP_TEXT[kind]}. Check it before you save.`}>
      <Sparkles aria-hidden="true" />
      {SOURCE_CHIP_TEXT[kind]}
    </span>
  )
}

const todayKey = () => new Date().toISOString().slice(0, 10)

/** What a read-only field shows instead of a box. */
function ReadOnlyValue({ field, value, form, optionLabel }: { field: FillField; value: string | string[]; form: ApplicationFormData; optionLabel: (text: string) => string }) {
  const empty = <span className="aff-empty">Not added</span>
  if (Array.isArray(value)) {
    return value.length ? <ul className="aff-chips">{value.map((item) => <li key={item}>{item}</li>)}</ul> : empty
  }
  const text = String(value)
  if (isBlank(text)) return empty
  if (field.control === 'date') {
    const age = field.key === 'personal.dateOfBirth' ? ageFromDob(text) : null
    return <span className="aff-text">{formatFormDate(text)}{age != null && <span className="aff-muted"> · {age} years old</span>}</span>
  }
  if (field.control === 'years') return <span className="aff-text">{yearsLabel(text)}</span>
  if (field.control === 'select') return <span className="aff-text">{optionLabel(text)}</span>
  if (field.control === 'phone') return <a className="aff-text aff-link" href={`tel:${text.replace(/[^\d+]/g, '')}`}>{text}</a>
  return <span className={cn('aff-text', field.control === 'textarea' && 'is-pre')}>{text}</span>
}

/**
 * One field of the form, in the Add candidate look: label above, required asterisk, a "From passport" chip when a document
 * filled it, a 44px control, then an inline error or (only where it prevents a mistake) one short hint. When the person
 * cannot work on the candidate it shows the value as plain text instead of a box.
 */
export function FillFieldView({ env, field, canWork, aux, hint, disabled, onWrite }: {
  env: FillEnv
  field: FillField
  canWork: boolean
  /** Beside the label, left of the source chip ("Same as mobile number"). */
  aux?: ReactNode
  /** Overrides the field's own hint (the live passport expiry sentence). */
  hint?: string
  disabled?: boolean
  /** Replaces the plain write (the mobile number also moves WhatsApp while they are the same). */
  onWrite?: (value: string | string[]) => void
}) {
  const id = useId()
  const { options: formOptions } = useFormOptions()
  const { form, draft, saved, errors, updated } = env
  const value = readField(draft, field)
  const error = errors[field.key]
  const chip = sourceChipFor(form, saved, draft, field)
  const set = (next: string | string[]) => (onWrite ? onWrite(next) : env.write(field, next))
  const optionLabel = (text: string) => (field.category ? labelFor(formOptions, field.category, text) : text)
  const shownHint = hint ?? field.hint
  const style = { ['--span' as string]: field.span, ['--span-md' as string]: field.spanMd } as CSSProperties
  const wrapperClass = cn('aff-field', error && 'has-error', updated.includes(field.key) && 'is-updated', chip && 'is-from', !canWork && 'is-readonly')
  const labelRow = (
    <div className="aff-lab">
      <label htmlFor={id}>{field.label}{field.required && canWork && <span className="aff-req" aria-hidden="true"> *</span>}</label>
      <span className="aff-lab-aux">{aux}{chip && <SourceBadge kind={chip} />}</span>
    </div>
  )

  if (!canWork) {
    return (
      <div className={wrapperClass} data-field={field.key} style={style}>
        <div className="aff-lab"><span className="aff-label-text">{field.label}</span><span className="aff-lab-aux">{chip && <SourceBadge kind={chip} />}</span></div>
        <div className="aff-value"><ReadOnlyValue field={field} value={value} form={form} optionLabel={optionLabel} /></div>
      </div>
    )
  }

  const notes = error ? <p className="aff-note is-error" role="alert">{error}</p> : shownHint ? <p className="aff-note">{shownHint}</p> : null
  const invalid = error ? { 'aria-invalid': true as const } : {}

  // The languages box carries its own label row, so the wrapper only adds the data-field hook and the notes.
  if (field.control === 'languages') {
    const offered = activeOptions(formOptions, FORM_OPTION_CATEGORIES.language).map((option) => option.label)
    return (
      <div className={wrapperClass} data-field={field.key} style={style}>
        <TagField label={field.label} required={field.required} value={listToText(value as string[])} suggestions={offered} placeholder={field.placeholder}
          onChange={(next) => set(textToList(next))} aux={chip && <SourceBadge kind={chip} />} />
        {notes}
      </div>
    )
  }

  let control: ReactElement
  if (field.control === 'date') {
    control = <DatePicker id={id} value={String(value)} onChange={set} max={field.notFuture ? todayKey() : undefined} disabled={disabled} aria-label={field.label} />
  } else if (field.control === 'select') {
    const current = String(value)
    const options = choicesFor(formOptions, field.category ?? '', current)
    control = (
      <Select id={id} value={current} disabled={disabled} onChange={(event) => set(event.target.value)} aria-label={field.label}>
        <option value="">Select an option</option>
        {options.map((option) => <option key={option.value} value={option.value}>{option.active ? option.label : `${option.label} (no longer offered)`}</option>)}
      </Select>
    )
  } else if (field.control === 'phone') {
    control = <PhoneField id={id} value={String(value)} disabled={disabled} invalid={Boolean(error)} showHint={false} onChange={set} />
  } else if (field.control === 'textarea') {
    control = <Textarea id={id} rows={3} value={String(value)} placeholder={field.placeholder} disabled={disabled} onChange={(event) => set(event.target.value)} />
  } else if (field.control === 'years') {
    control = <Input id={id} type="number" inputMode="decimal" min={0} max={80} step={0.5} value={String(value)} placeholder={field.placeholder} disabled={disabled} onChange={(event) => set(event.target.value)} />
  } else {
    control = <Input id={id} type="text" autoComplete="off" value={String(value)} placeholder={field.placeholder} disabled={disabled} className={field.uppercase ? 'uppercase' : undefined} onChange={(event) => set(event.target.value)} />
  }
  const labelled = isValidElement(control) ? cloneElement(control as ReactElement<Record<string, unknown>>, invalid) : control
  return (
    <div className={wrapperClass} data-field={field.key} style={style}>
      {labelRow}
      <FieldLabelContext.Provider value={field.label}>{labelled}</FieldLabelContext.Provider>
      {notes}
    </div>
  )
}
