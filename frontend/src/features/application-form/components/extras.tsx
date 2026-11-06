import type { ReactNode } from 'react'
import { DatePicker } from '@/shared/ui/date-picker'
import { Input, Select } from '@/shared/ui/primitives'
import { InterestCards } from '../parts/interest-cards'
import { PhoneField } from '../parts/phone-field'
import { EXTRA_FIELDS, formatFormDate, isBlank, type FieldDef } from '../form-model'
import { FORM_OPTION_CATEGORIES, activeOptions, choicesFor, labelFor, toneStyle } from '@/features/form-options/form-options-model'
import { useFormOptions } from '@/features/form-options/use-form-options'
import type { FormDraft } from '../types'
import { Cell, ListChips } from './fields'
import { Section, type FormContext } from './sections'

const byPath = (path: string) => EXTRA_FIELDS.find((item) => item.path === path) as FieldDef
const dash = <span className="af-empty" aria-label="Not filled">–</span>

export function ExtraCell({ ctx, path, wide }: { ctx: FormContext; path: string; wide?: boolean }) {
  const def = byPath(path)
  const { options: formOptions } = useFormOptions()
  const value = ctx.draft.extras[path] ?? ''
  const set = (next: string) => ctx.setDraft((current: FormDraft) => ({ ...current, extras: { ...current.extras, [path]: next } }))
  const id = `af-${path.replace('.', '-')}`
  const list = value.split(',').map((item) => item.trim()).filter(Boolean)
  let body: ReactNode
  if (path === 'screening.interestLevel') {
    const known = (formOptions[FORM_OPTION_CATEGORIES.interestLevel] ?? []).find((item) => item.value === value)
    const tone = toneStyle(known?.tone)
    body = ctx.editing
      ? <InterestCards value={value} onChange={set} levels={activeOptions(formOptions, FORM_OPTION_CATEGORIES.interestLevel)} />
      : value ? <span className={`afp-interest-chip is-${tone.name ?? 'gray'}`} style={tone.color ? ({ ['--tone' as string]: tone.color } as React.CSSProperties) : undefined}><i aria-hidden="true" />{known?.label ?? value}{known && !known.active ? ' (no longer offered)' : ''}</span> : dash
  } else if (def.kind === 'list') {
    body = ctx.editing
      ? <ListChips value={list} onChange={(next) => set(next.join(', '))} placeholder="Add and press Enter" />
      : list.length ? <ul className="af-langs">{list.map((item) => <li key={item} className="af-lang">{item}</li>)}</ul> : dash
  } else if (!ctx.editing) {
    const text = def.kind === 'date' ? formatFormDate(value) : def.kind === 'select' ? (def.category ? labelFor(formOptions, def.category, value) : value) : value
    body = isBlank(text) ? dash : <span className="af-text">{text}</span>
  } else if (def.kind === 'date') body = <DatePicker id={id} value={value} onChange={set} aria-label={def.label} />
  else if (def.kind === 'select') {
    body = (
      <Select id={id} value={value} onChange={(event) => set(event.target.value)} aria-label={def.label}>
        <option value="">Select…</option>
        {(def.category ? choicesFor(formOptions, def.category, value) : []).map((option) => <option key={option.value} value={option.value}>{option.active ? option.label : `${option.label} (no longer offered)`}</option>)}
      </Select>
    )
  } else if (def.kind === 'tel') body = <PhoneField id={id} value={value} onChange={set} label={def.label} error={ctx.errors[path]} compact />
  else body = <Input id={id} value={value} onChange={(event) => set(event.target.value)} aria-label={def.label} />
  const manual = ctx.form.fieldSources[path] === 'manual' && !isBlank(value)
  const empty = isBlank(value)
  return (
    <Cell label={def.label} path={path} wide={wide} missing={empty && !ctx.editing && false} provenance={manual ? 'manual' : undefined}
      error={def.kind === 'tel' ? undefined : ctx.errors[path]} updated={ctx.updated.includes(path)}>{body}</Cell>
  )
}

/** Native place and family number sit with the personal details (section 1) when the server has them. */
export function PersonalExtras({ ctx }: { ctx: FormContext }) {
  if (!ctx.form.extrasSupported) return null
  return (
    <div className="af-cells af-cells-extra">
      <ExtraCell ctx={ctx} path="screening.nativePlace" />
      <ExtraCell ctx={ctx} path="screening.familyMemberNumber" />
    </div>
  )
}

export function AssessmentSection({ ctx }: { ctx: FormContext }) {
  if (!ctx.form.extrasSupported) return null
  return (
    <Section no={8} plain>
      <div className="af-cells">
        <ExtraCell ctx={ctx} path="screening.jobPreference" wide />
        <ExtraCell ctx={ctx} path="screening.skills" wide />
        <ExtraCell ctx={ctx} path="screening.countryPreference" />
        <ExtraCell ctx={ctx} path="screening.jobDurationPreference" />
        <ExtraCell ctx={ctx} path="screening.bestTimeToContact" />
        <ExtraCell ctx={ctx} path="screening.serviceChargesStatus" />
        <ExtraCell ctx={ctx} path="screening.interestLevel" wide />
      </div>
    </Section>
  )
}

const CALL_RESULT: Record<string, string> = { yes: 'Interested', no: 'Not interested', unreachable: 'Not reachable' }

export function FollowUpSection({ ctx }: { ctx: FormContext }) {
  if (!ctx.form.extrasSupported) return null
  const call = ctx.form.call
  return (
    <Section no={9} plain>
      <div className="af-cells">
        <ExtraCell ctx={ctx} path="screening.nextFollowUpDate" />
        <Cell label="Assigned to"><span className="af-text">{ctx.form.candidate.ownerName || dash}</span></Cell>
        <Cell label="Initial call" wide>{call ? <span className="af-text">{call.called ? 'Called' : 'Not called'} · {CALL_RESULT[call.interested] ?? call.interested}{call.recordedAt ? ` · ${formatFormDate(call.recordedAt)}` : ''}</span> : dash}</Cell>
        <Cell label="Call notes" wide>{call?.remarks ? <span className="af-text af-pre">{call.remarks}</span> : dash}</Cell>
      </div>
    </Section>
  )
}
