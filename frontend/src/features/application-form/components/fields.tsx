import { useState, type KeyboardEvent, type ReactNode } from 'react'
import { AlertTriangle, FileText, IdCard, Pencil, Sparkles, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { DatePicker } from '@/shared/ui/date-picker'
import { Input, Select, Textarea } from '@/shared/ui/primitives'
import { passportExpiryNotice } from '@/features/candidates/passport-expiry'
import { choicesFor, labelFor } from '@/features/form-options/form-options-model'
import { useFormOptions } from '@/features/form-options/use-form-options'
import {
  PROVENANCE_LABELS,
  ageFromDob,
  formatFormDate,
  isBlank,
  provenanceOf,
  yearsLabel,
  type FieldDef,
} from '../form-model'
import type { ApplicationFormData, FormDraft } from '../types'

/** Where a value came from: a tiny icon beside the label, with the words as its tooltip. Hidden in print. */
export function ProvenanceChip({ kind }: { kind: ReturnType<typeof provenanceOf> }) {
  if (kind === 'none') return null
  const Icon = kind === 'manual' ? Pencil : kind === 'passport' ? IdCard : kind === 'resume' ? FileText : Sparkles
  return (
    <span className={cn('af-prov', kind === 'manual' && 'is-manual')} title={PROVENANCE_LABELS[kind]} role="img" aria-label={PROVENANCE_LABELS[kind]}>
      <Icon aria-hidden="true" />
    </span>
  )
}

export function MissingChip() {
  return (
    <span className="af-chip is-missing">
      <AlertTriangle aria-hidden="true" />
      Missing
    </span>
  )
}

/** A bordered cell of the paper form: label, optional chips, then the value or its input. */
export function Cell({
  label,
  path,
  wide,
  missing,
  provenance,
  updated,
  error,
  hint,
  children,
}: {
  label: string
  path?: string
  wide?: boolean
  missing?: boolean
  provenance?: ReturnType<typeof provenanceOf>
  updated?: boolean
  error?: string
  hint?: ReactNode
  children: ReactNode
}) {
  return (
    <div className={cn('af-cell', wide && 'is-wide', missing && 'is-missing', updated && 'is-updated', error && 'has-error')} data-field={path}>
      <div className="af-cell-head">
        <span className="af-label">{label}</span>
        {provenance && provenance !== 'none' && <span className="af-cell-chips"><ProvenanceChip kind={provenance} /></span>}
      </div>
      <div className="af-value-cell">
        <div className="af-value">{children}</div>
        {error ? <p className="af-note is-error" role="alert">{error}</p> : hint ? <p className="af-note">{hint}</p> : null}
      </div>
    </div>
  )
}

/** Chips for a list of words (languages, job titles, skills): Enter or comma adds, Backspace removes the last. */
export function ListChips({ value, onChange, placeholder }: { value: string[]; onChange: (next: string[]) => void; placeholder?: string }) {
  const [text, setText] = useState('')
  const add = () => {
    const parts = text.split(/[,;]/).map((part) => part.trim()).filter(Boolean)
    if (!parts.length) return
    const next = [...value]
    for (const part of parts) if (!next.some((item) => item.toLowerCase() === part.toLowerCase())) next.push(part)
    onChange(next)
    setText('')
  }
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); add() }
    else if (event.key === 'Backspace' && !text && value.length) onChange(value.slice(0, -1))
  }
  return (
    <div className="af-langs is-editing">
      {value.map((language) => (
        <span key={language} className="af-lang">
          {language}
          <button type="button" aria-label={`Remove ${language}`} onClick={() => onChange(value.filter((item) => item !== language))}><X aria-hidden="true" /></button>
        </span>
      ))}
      <input
        className="af-lang-input"
        value={text}
        placeholder={value.length ? 'Add another' : (placeholder ?? 'For example English, Hindi')}
        aria-label="Add a language"
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKey}
        onBlur={add}
      />
    </div>
  )
}

const display = (text: string) => (isBlank(text) ? <span className="af-empty" aria-label="Not filled">–</span> : <span className="af-text">{text}</span>)

function ExpiryHint({ value }: { value: string }) {
  const notice = passportExpiryNotice(value)
  if (!notice) return null
  return <span className={cn('af-expiry', notice.tone === 'expired' ? 'is-expired' : 'is-soon')}><AlertTriangle aria-hidden="true" />{notice.text}</span>
}

/** One editable field of the form: shows the value, or its input while the form is being edited. */
export function FormField({
  def,
  form,
  draft,
  editing,
  onChange,
  errors,
  updated,
  wide: wideOverride,
}: {
  wide?: boolean
  def: FieldDef
  form: ApplicationFormData
  draft: FormDraft
  editing: boolean
  onChange: (path: string, value: string | string[]) => void
  errors: Record<string, string>
  updated: string[]
}) {
  const { options: formOptions } = useFormOptions()
  const [section, key] = def.path.split('.')
  const value = (draft as unknown as Record<string, Record<string, unknown>>)[section][key] as string | string[]
  const blank = isBlank(value)
  const provenance = provenanceOf(form, def.path, !blank, def.from)
  const set = (next: string | string[]) => onChange(def.path, next)
  const id = `af-${def.path.replace('.', '-')}`
  const wide = wideOverride ?? def.kind === 'textarea'

  let body: ReactNode
  let hint: ReactNode
  if (def.kind === 'languages') {
    const list = value as string[]
    body = editing ? <ListChips value={list} onChange={set} /> : list.length
      ? <ul className="af-langs">{list.map((item) => <li key={item} className="af-lang">{item}</li>)}</ul>
      : display('')
  } else if (!editing) {
    const text = String(value ?? '')
    if (def.kind === 'date') {
      body = display(formatFormDate(text))
      if (def.path === 'personal.dateOfBirth') {
        const age = ageFromDob(text)
        if (age != null) body = <span className="af-text">{formatFormDate(text)}<span className="af-age"> · {age} years old</span></span>
      }
      if (def.path === 'passport.expiryDate') hint = <ExpiryHint value={text} />
    } else if (def.kind === 'select') body = display(def.category ? labelFor(formOptions, def.category, text) : text)
    else if (def.kind === 'years') body = display(yearsLabel(text))
    else if (def.kind === 'tel') body = text ? <a className="af-text af-link" href={`tel:${text.replace(/[^\d+]/g, '')}`}>{text}</a> : display('')
    else if (def.kind === 'email' && text) body = <a className="af-text af-link" href={`mailto:${text}`}>{text}</a>
    else body = display(text)
  } else if (def.kind === 'date') {
    body = <DatePicker id={id} value={String(value)} onChange={set} aria-label={def.label} max={def.path === 'personal.dateOfBirth' ? new Date().toISOString().slice(0, 10) : undefined} />
    if (def.path === 'passport.expiryDate') hint = <ExpiryHint value={String(value)} />
  } else if (def.kind === 'select') {
    const current = String(value)
    const options = def.category ? choicesFor(formOptions, def.category, current).map((option) => ({ value: option.value, label: option.active ? option.label : `${option.label} (no longer offered)` })) : (def.options ?? [])
    body = (
      <Select id={id} value={current} onChange={(event) => set(event.target.value)} aria-label={def.label}>
        <option value="">Select an option</option>
        {current && !options.some((option) => option.value === current) && <option value={current}>{current}</option>}
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </Select>
    )
  } else if (def.kind === 'textarea') {
    body = <Textarea id={id} rows={2} value={String(value)} onChange={(event) => set(event.target.value)} aria-label={def.label} />
  } else if (def.kind === 'years') {
    body = <Input id={id} type="number" inputMode="decimal" min={0} max={80} step={1} value={String(value)} onChange={(event) => set(event.target.value)} aria-label={def.label} placeholder="Years" />
  } else if (def.kind === 'tel') {
    body = <Input id={id} type="tel" inputMode="tel" value={String(value)} onChange={(event) => set(event.target.value)} aria-label={def.label} placeholder="+91 98765 43210" />
  } else {
    body = <Input id={id} type={def.kind === 'email' ? 'email' : 'text'} value={String(value)} onChange={(event) => set(event.target.value)} aria-label={def.label} className={def.path === 'passport.passportNumber' ? 'uppercase' : undefined} />
  }

  return (
    <Cell label={def.label} path={def.path} wide={wide} missing={def.required && blank} provenance={provenance} updated={updated.includes(def.path)} error={errors[def.path]} hint={hint}>
      {body}
    </Cell>
  )
}
