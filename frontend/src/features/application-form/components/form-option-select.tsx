import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { Check, X } from 'lucide-react'
import { api } from '@/shared/api/client'
import { isAdminLevel } from '@/shared/model/role-labels'
import { useOptionalStore } from '@/shared/state/crm-store'
import { Input, Select } from '@/shared/ui/primitives'
import { ADD_OPTION_LABEL, ADD_OPTION_VALUE, activeOptions, choicesFor, newOptionDraft, type FormOption } from '@/features/form-options/form-options-model'
import { refreshFormOptions, useFormOptions } from '@/features/form-options/use-form-options'
import '@/styles/lead-status.css'

/**
 * One dropdown for every admin-managed list (source, interest, education, job duration, best time, language, marital status,
 * service charges): the active options in the order an admin set, the value already saved even when it was hidden since, an optional
 * first row that clears the value (`allowClear`) and, for an Admin or MD only, a last row "+ Add an option" (`allowAdd`).
 * That row opens a small box under the dropdown; Add (or Enter) saves the option for everybody through POST /form-options and
 * selects it at once, Cancel (or Esc) keeps the old choice. A list with fixed values (the source) cannot get a new one, so it
 * never shows the row. Everybody else sees the plain list.
 */
export function FormOptionSelect({ category, value, onChange, allowClear = false, clearLabel = 'Select an option', allowAdd = false, id, disabled, label, className, ...rest }: {
  category: string
  value: string
  onChange: (value: string) => void
  /** Offer a first row with an empty value that clears the field. */
  allowClear?: boolean
  clearLabel?: string
  /** Offer "+ Add an option" (Admin and MD only, and only for a list whose values are not fixed). */
  allowAdd?: boolean
  id?: string
  disabled?: boolean
  /** The accessible name of the dropdown. */
  label?: string
  className?: string
} & Record<`aria-${string}`, unknown>) {
  const { options, meta } = useFormOptions()
  const me = useOptionalStore()?.me
  // `meta` is empty until the server's answer arrives (or when it failed): no row then, as a fixed list would refuse the new value.
  const canAdd = allowAdd && !disabled && Boolean(me) && isAdminLevel(me!.role) && Boolean(meta[category]) && !meta[category].fixedValues
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const nameId = useId()

  useEffect(() => { if (!canAdd) setAdding(false) }, [canAdd])

  const rows: FormOption[] = choicesFor(options, category, value)
  const close = () => {
    setAdding(false); setName(''); setError('')
    window.setTimeout(() => box.current?.querySelector<HTMLElement>('.custom-select-trigger, select')?.focus(), 0)
  }
  const submit = async () => {
    if (busy) return
    const draft = newOptionDraft(name, meta[category]?.strict === true, activeOptions(options, category))
    if (!draft.ok) { setError(draft.message); return }
    setBusy(true); setError('')
    try {
      const saved = await api.post<{ value?: string }>('/form-options', { category, value: draft.value, label: draft.label })
      await refreshFormOptions()
      onChange(saved?.value || draft.value)
      setAdding(false); setName('')
    } catch (failure) {
      setError(failure instanceof Error && failure.message ? failure.message : 'The option could not be added. Please try again.')
    } finally { setBusy(false) }
  }
  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (!busy) close() }
    else if (event.key === 'Enter' && (event.target as HTMLElement).tagName === 'INPUT') { event.preventDefault(); event.stopPropagation(); void submit() }
  }

  return (
    <div className={className ? `ld-statussel ${className}` : 'ld-statussel'} ref={box}>
      <Select id={id} value={value} disabled={disabled} aria-label={label} {...rest}
        onChange={(event) => { if (event.target.value === ADD_OPTION_VALUE) { setAdding(true); setError('') } else onChange(event.target.value) }}>
        {allowClear && <option value="">{clearLabel}</option>}
        {rows.map((option) => <option key={option.value} value={option.value}>{option.active ? option.label : `${option.label} (no longer offered)`}</option>)}
        {canAdd && <option value={ADD_OPTION_VALUE}>{ADD_OPTION_LABEL}</option>}
      </Select>
      {adding && canAdd && (
        <div className="ld-addstatus" role="group" aria-label="Add an option" onKeyDown={onKey}>
          <label className="ld-addstatus-name" htmlFor={nameId} style={{ gridColumn: '1 / -1' }}>
            <span>New option</span>
            <Input id={nameId} autoFocus autoComplete="off" maxLength={80} value={name} disabled={busy} placeholder="Name, then Enter" aria-invalid={error ? true : undefined}
              onChange={(event) => { setName(event.target.value); if (error) setError('') }} />
          </label>
          <div className="ld-addstatus-actions">
            <button type="button" className="ld-addstatus-ok" disabled={busy || !name.trim()} onClick={() => void submit()}><Check size={15} aria-hidden="true" />{busy ? 'Adding…' : 'Add option'}</button>
            <button type="button" className="ld-addstatus-cancel" disabled={busy} onClick={close}><X size={15} aria-hidden="true" />Cancel</button>
          </div>
          <p className="ld-addstatus-hint">Everybody will see it in the list.</p>
          <p className="ld-addstatus-error" role="alert" aria-live="assertive">{error}</p>
        </div>
      )}
    </div>
  )
}
