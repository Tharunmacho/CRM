import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { Check, X } from 'lucide-react'
import { Input, Select } from '@/shared/ui/primitives'
import { LEAD_STATUSES, statusOptions, type Lead } from '../lead-model'
import { ADD_STATUS_LABEL, ADD_STATUS_VALUE, DEFAULT_TONE, NAME_MAX, STATUS_TONES, isAddStatus, newStatusCheck, type StatusTone } from '../lead-status-model'
import { addLeadStatus, useLeadStatusTemplates } from '../lead-status-templates-api'
import '@/styles/lead-status.css'

/** Every name a new status may not take: the standard statuses' codes and both wordings. */
const STANDARD_NAMES = [...LEAD_STATUSES.flatMap((status) => [status.value, status.label]), 'Resume received']

/**
 * The status dropdown of the Lead sheet: the built-in statuses, then the shared status templates (built-in ones first, the rest A to Z),
 * and last "+ Add a status". That row opens a small box under the dropdown (name up to 40 characters, tone); Add (or Enter) saves the
 * status for everybody and selects it at once, Cancel (or Esc) closes the box and keeps the old choice. If the server refuses, the
 * choice goes back to what it was and the reason is shown. The value is the built-in code, or the template's name.
 * `lead` keeps a lead's own status listed even when its template was hidden since. `allowAdd={false}` is the plain list (filters).
 */
export function StatusSelect({ value, onChange, lead, disabled, label = 'Status', allowAdd = true, extra }: {
  value: string
  onChange: (value: string) => void
  lead?: Pick<Lead, 'status' | 'statusLabel'> | null
  disabled?: boolean
  label?: string
  allowAdd?: boolean
  /** Extra entries (for example "Converted") kept after the list and before the add row. */
  extra?: { value: string; label: string }[]
}) {
  const templates = useLeadStatusTemplates()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [tone, setTone] = useState<StatusTone>(DEFAULT_TONE)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const nameId = useId()
  const options = statusOptions(templates.items, lead)

  useEffect(() => { if (!allowAdd) setAdding(false) }, [allowAdd])

  const close = () => {
    setAdding(false); setName(''); setTone(DEFAULT_TONE); setError('')
    window.setTimeout(() => box.current?.querySelector<HTMLElement>('.custom-select-trigger, select')?.focus(), 0)
  }
  const submit = async () => {
    if (busy) return
    const check = newStatusCheck(name, tone, templates.items, STANDARD_NAMES)
    if (!check.ok) { setError(check.message); return }
    const before = value
    setBusy(true); setError('')
    onChange(check.name)   // selected at once; taken back below if the server refuses
    try {
      const saved = await addLeadStatus(check.name, check.tone)
      if (saved.name !== check.name) onChange(saved.name)   // the status already existed under another spelling: use that one
      setAdding(false); setName(''); setTone(DEFAULT_TONE)
    } catch (failure) {
      onChange(before)
      setError(failure instanceof Error && failure.message ? failure.message : 'The status could not be added. Please try again.')
    } finally { setBusy(false) }
  }
  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (!busy) close() }
    else if (event.key === 'Enter' && (event.target as HTMLElement).tagName === 'INPUT') { event.preventDefault(); void submit() }   // not while the tone list is open
  }

  return (
    <div className="ld-statussel" ref={box}>
      <Select aria-label={label} value={value} disabled={disabled}
        onChange={(event) => { if (isAddStatus(event.target.value)) { setAdding(true); setError('') } else onChange(event.target.value) }}>
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        {extra?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        {allowAdd && <option value={ADD_STATUS_VALUE}>{ADD_STATUS_LABEL}</option>}
      </Select>
      {adding && !disabled && (
        <div className="ld-addstatus" role="group" aria-label="Add a status" onKeyDown={onKey}>
          <label className="ld-addstatus-name" htmlFor={nameId}>
            <span>New status</span>
            <Input id={nameId} autoFocus autoComplete="off" maxLength={NAME_MAX} value={name} disabled={busy} placeholder="Name, then Enter" aria-invalid={error ? true : undefined}
              onChange={(event) => { setName(event.target.value); if (error) setError('') }} />
          </label>
          <label className="ld-addstatus-tone">
            <span>Counts as</span>
            <Select aria-label="Tone of the new status" value={tone} disabled={busy} onChange={(event) => setTone(event.target.value as StatusTone)}>
              {STATUS_TONES.map((item) => <option key={item.value} value={item.value}>{`${item.label}: ${item.hint}`}</option>)}
            </Select>
          </label>
          <div className="ld-addstatus-actions">
            <button type="button" className="ld-addstatus-ok" disabled={busy || !name.trim()} onClick={() => void submit()}><Check size={15} aria-hidden="true" />{busy ? 'Adding…' : 'Add status'}</button>
            <button type="button" className="ld-addstatus-cancel" disabled={busy} onClick={close}><X size={15} aria-hidden="true" />Cancel</button>
          </div>
          <p className="ld-addstatus-hint">Everybody will see it in the list.</p>
          <p className="ld-addstatus-error" role="alert" aria-live="assertive">{error}</p>
        </div>
      )}
    </div>
  )
}
