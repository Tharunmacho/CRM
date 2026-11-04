import { cloneElement, isValidElement, useId, useState, type CSSProperties, type ReactElement, type ReactNode } from 'react'
import { Sparkles, type LucideIcon } from 'lucide-react'
import { CheckboxInput, Input } from '@/shared/ui/primitives'
import { FieldLabelContext } from '@/shared/ui/control-context'
import { COUNTRY_CODES, DEFAULT_COUNTRY_CODE, joinPhone, phoneHint, splitPhone } from '../entry-phone'
import { toneStyle, type FormOption } from '@/features/form-options/form-options-model'
import type { ScanKind } from '../ocr-form'

/**
 * Building blocks of the one-card Add candidate form: a section header with a divider, a field with its label row
 * (required star, "Read from" badge, an optional inline checkbox), a phone box with a country code and
 * the four coloured interest cards. Layout lives in styles/add-candidate-v2.css under `.ac-sec` and `.ac-f`.
 */

const SOURCE_NAME: Record<ScanKind, string> = { resume: 'resume', passport: 'passport', aadhaar: 'Aadhaar' }
const SOURCE_SHORT: Record<ScanKind, string> = { resume: 'Resume', passport: 'Passport', aadhaar: 'Aadhaar' }

/** One section of the form card: icon, bold title, one-line subtitle, a thin divider, then its content. */
export function FormSection({ id, icon: Icon, title, subtitle, aside, children }: {
  id?: string; icon: LucideIcon; title: string; subtitle?: ReactNode; aside?: ReactNode; children: ReactNode
}) {
  return (
    <section id={id} className="ac-sec">
      <header className="ac-sec-head">
        <span className="ac-tile" aria-hidden><Icon size={17} /></span>
        <div className="ac-sec-title">
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {aside && <div className="ac-sec-aside">{aside}</div>}
      </header>
      <div className="ac-sec-body">{children}</div>
    </section>
  )
}

/** "Read from the resume" marker beside a label. */
export function ReadBadge({ kind }: { kind: ScanKind }) {
  // Short on the page (the room beside a label is small); the full wording is the tooltip and the screen-reader text.
  return <span className="ac-from" title={`Read from the ${SOURCE_NAME[kind]}. Check it before you save.`}><Sparkles size={11} aria-hidden /><span aria-hidden>{SOURCE_SHORT[kind]}</span><span className="sr-only">Read from the {SOURCE_NAME[kind]}</span></span>
}

/**
 * A labelled field. The label sits above the control; the right end of the label row holds the badge for a value a
 * document filled in, or an inline checkbox such as "Same as primary". Empty fields stay quiet: what is still missing
 * is counted in the section headers and the completeness bar instead of a pill on every field.
 */
export function EntryField({ label, required, error, hint, source, aux, className = '', children }: {
  label: string; required?: boolean; error?: string; hint?: ReactNode; source?: ScanKind
  aux?: ReactNode; className?: string; children: ReactElement
}) {
  const id = useId()
  const control = isValidElement(children) ? cloneElement(children as ReactElement<{ id?: string; 'aria-invalid'?: boolean }>, { id, ...(error ? { 'aria-invalid': true } : {}) }) : children
  return (
    <div className={`ac-f ${className}`} data-from={source ? '' : undefined} data-invalid={error ? '' : undefined}>
      <div className="ac-lab">
        <label htmlFor={id}>{label}{required && <span className="ac-req" aria-hidden> *</span>}</label>
        <span className="ac-lab-aux">
          {aux}
          {source && <ReadBadge kind={source} />}
        </span>
      </div>
      <FieldLabelContext.Provider value={label}>{control}</FieldLabelContext.Provider>
      {error ? <span className="ac-err" role="alert">{error}</span> : hint ? <span className="ac-hint">{hint}</span> : null}
    </div>
  )
}

/** The inline checkbox of a label row ("Same as current", "Same as primary"). */
export function SameAs({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean }) {
  return (
    <label className="ac-same">
      <CheckboxInput checked={checked} disabled={disabled} onChange={onChange} />
      <span>{label}</span>
    </label>
  )
}

/** A phone number: country-code select and number box, stored as one string ("+91 98400 00000"). */
export function PhoneField({ id, value, onChange, placeholder = '98400 00000', disabled, invalid, showHint = true }: {
  id?: string; value: string; onChange: (value: string) => void; placeholder?: string; disabled?: boolean; invalid?: boolean; showHint?: boolean
}) {
  const parsed = splitPhone(value)
  const [picked, setPicked] = useState(DEFAULT_COUNTRY_CODE)
  // A number that is already there decides the code; with an empty box the person's pick (or +91) shows.
  const code = value.trim() ? parsed.code : picked
  const hint = phoneHint(code, parsed.national)
  return (
    <div className="ac-phone-wrap">
      <div className="ac-phone">
        <select className="ui-input ac-cc" aria-label="Country code" value={code} disabled={disabled}
          onChange={(event) => { setPicked(event.target.value); onChange(joinPhone(event.target.value, parsed.national)) }}>
          {!COUNTRY_CODES.some((entry) => entry.code === code) && <option value={code}>{code || 'Other'}</option>}
          {COUNTRY_CODES.map((entry) => <option key={entry.code} value={entry.code}>{entry.country} ({entry.code})</option>)}
        </select>
        <Input ident id={id} type="tel" inputMode="tel" autoComplete="off" value={parsed.national} disabled={disabled} aria-invalid={invalid || undefined}
          placeholder={placeholder} onChange={(event) => onChange(joinPhone(code, event.target.value))} />
      </div>
      {showHint && <span className={`ac-hint ac-phone-hint is-${hint.tone}`}>{hint.text}</span>}
    </div>
  )
}

/**
 * The coloured, selectable interest cards. They come from the managed "interest level" list (label, hint, tone), so an
 * admin can rename or add levels; picking the chosen card again clears it.
 */
export function InterestCards({ value, options, onChange, disabled }: { value: string; options: FormOption[]; onChange: (value: string) => void; disabled?: boolean }) {
  return (
    <div className="ac-interest" role="radiogroup" aria-label="Interest level">
      {options.map((level) => {
        const on = value === level.value
        const tone = toneStyle(level.tone)
        return (
          <button key={level.value} type="button" role="radio" aria-checked={on} disabled={disabled} data-tone={tone.name}
            style={tone.color ? ({ '--tone': tone.color } as CSSProperties) : undefined}
            className={`ac-interest-card ${on ? 'is-on' : ''}`} onClick={() => onChange(on ? '' : level.value)}>
            <i aria-hidden />
            <span><strong>{level.label}</strong>{level.hint && <small>{level.hint}</small>}</span>
          </button>
        )
      })}
    </div>
  )
}
