import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Check, ChevronDown, Plus, Search, Trash2, UserRound, Users, X } from 'lucide-react'
import type { User } from '@/shared/model/types'
import { Field, Input, Textarea } from '@/shared/ui/primitives'
import { Avatar } from '@/features/clients/components/parts'
import { emptyStaffRow, firstName, MAX_STAFF_ROWS, type FieldErrors, type PartyFields, type StaffRow } from '@/features/clients/client-form'
import { foldText } from '@/features/clients/client-search'
import '@/styles/clients-simple.css'

/** A titled soft card of a dialog: an icon chip, the title, and the fields below. */
export function Section({ icon, title, count, children }: { icon: ReactNode; title: string; count?: string; children: ReactNode }) {
  return <section className="cs-sec">
    <header><span className="cs-sec-chip" aria-hidden="true">{icon}</span><h3>{title}</h3>{count && <span className="cs-sec-count">{count}</span>}</header>
    <div className="cs-sec-body">{children}</div>
  </section>
}

/** The six details of a client or reference agency: name, agency, contact no, email, then address and state (2:1). */
export function PartyFieldsSection({ values, onChange, errors, title = 'Details' }: {
  values: PartyFields
  onChange: (key: keyof PartyFields, value: string) => void
  errors: FieldErrors
  title?: string
}) {
  return <Section icon={<UserRound size={17} />} title={title}>
    <div className="cs-grid6">
      <Field label="Name (contact person)" required error={errors.name} className="span-3">
        <Input required maxLength={200} value={values.name} onChange={(e) => onChange('name', e.target.value)} aria-invalid={!!errors.name} placeholder="Khalid Rahman" autoComplete="off" />
      </Field>
      <Field label="Name of the agency" required error={errors.agencyName} className="span-3">
        <Input required maxLength={200} value={values.agencyName} onChange={(e) => onChange('agencyName', e.target.value)} aria-invalid={!!errors.agencyName} placeholder="Gulf Build Contracting LLC" autoComplete="off" />
      </Field>
      <Field label="Contact no" error={errors.phone} className="span-3">
        <Input type="tel" maxLength={40} value={values.phone} onChange={(e) => onChange('phone', e.target.value)} aria-invalid={!!errors.phone} placeholder="+91 98765 43210" autoComplete="off" />
      </Field>
      <Field label="Email" error={errors.email} className="span-3">
        <Input type="email" maxLength={255} value={values.email} onChange={(e) => onChange('email', e.target.value)} aria-invalid={!!errors.email} placeholder="name@company.com" autoComplete="off" />
      </Field>
      <Field label="Address" error={errors.address} className="span-4">
        <Textarea rows={3} maxLength={2000} value={values.address} onChange={(e) => onChange('address', e.target.value)} placeholder="Building, street, area" />
      </Field>
      <Field label="State" error={errors.state} className="span-2">
        <Input maxLength={100} value={values.state} onChange={(e) => onChange('state', e.target.value)} placeholder="Kerala" autoComplete="off" />
      </Field>
    </div>
  </Section>
}

/** The agency's own people: a mini-card per person with four boxes and a round remove button, and a dashed "Add staff" pill. */
export function StaffDetailsSection({ rows, onChange, errors }: { rows: StaffRow[]; onChange: (rows: StaffRow[]) => void; errors: FieldErrors }) {
  const change = (index: number, key: keyof StaffRow, value: string) => onChange(rows.map((row, i) => i === index ? { ...row, [key]: value } : row))
  const filled = rows.filter((row) => row.name.trim() || row.designation.trim() || row.phone.trim() || row.email.trim()).length
  return <Section icon={<Users size={17} />} title="Staff details" count={filled ? `${filled} added` : 'Optional'}>
    {rows.map((row, index) => {
      const at = (key: string) => errors[`staff.${index}.${key}`]
      return <fieldset key={index} className="cs-staff-row">
        <legend className="sr-only">Staff member {index + 1}</legend>
        <div className="cs-staff-fields">
          <Field label="Name" error={at('name')}><Input maxLength={160} value={row.name} onChange={(e) => change(index, 'name', e.target.value)} aria-invalid={!!at('name')} autoComplete="off" /></Field>
          <Field label="Designation" error={at('designation')}><Input maxLength={120} value={row.designation} onChange={(e) => change(index, 'designation', e.target.value)} autoComplete="off" /></Field>
          <Field label="Phone" error={at('phone')}><Input type="tel" maxLength={40} value={row.phone} onChange={(e) => change(index, 'phone', e.target.value)} aria-invalid={!!at('phone')} autoComplete="off" /></Field>
          <Field label="Email" error={at('email')}><Input type="email" maxLength={255} value={row.email} onChange={(e) => change(index, 'email', e.target.value)} aria-invalid={!!at('email')} autoComplete="off" /></Field>
        </div>
        <button type="button" className="cs-round-btn" aria-label={`Remove staff member ${index + 1}`} title="Remove" onClick={() => onChange(rows.filter((_, i) => i !== index))}><Trash2 size={15} /></button>
      </fieldset>
    })}
    {errors.staff && <p className="cs-form-error" role="alert">{errors.staff}</p>}
    <button type="button" className="cs-add" disabled={rows.length >= MAX_STAFF_ROWS} onClick={() => onChange([...rows, emptyStaffRow()])}><Plus size={15} />Add staff</button>
  </Section>
}

/** CRM users who can be given a client: active staff and managers (admins are not allocated to clients). */
export function allocatableUsers(users: User[]): User[] {
  return users.filter((user) => user.active && (user.role === 'staff' || user.role === 'manager')).sort((a, b) => a.name.localeCompare(b.name))
}

const ROLE_LABEL: Record<string, string> = { staff: 'Staff', manager: 'Manager', admin: 'Admin' }

/**
 * A multi-select dropdown of CRM users. Selected people appear as removable chips. The list opens in the page flow (not
 * floating), so the dialog's own scrolling never clips it. Type to search; arrow keys move, Enter or Space toggles, Escape closes.
 */
export function StaffPicker({ users, value, onChange, error, disabled }: { users: User[]; value: string[]; onChange: (ids: string[]) => void; error?: string; disabled?: boolean }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState(0)
  const listId = useId()
  const box = useRef<HTMLDivElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const selected = useMemo(() => new Set(value), [value])
  const shown = useMemo(() => {
    const needle = foldText(query).trim()
    return needle ? users.filter((user) => foldText(user.name).includes(needle) || foldText(ROLE_LABEL[user.role]).includes(needle)) : users
  }, [users, query])
  const toggle = (id: string) => onChange(selected.has(id) ? value.filter((item) => item !== id) : [...value, id])
  useEffect(() => { setCursor(0) }, [query, open])
  useEffect(() => {
    if (!open) return undefined
    const away = (event: PointerEvent) => { if (box.current && !box.current.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [open])

  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape' && open) {
      // Closes only the list; the dialog's own Escape handler listens on the document and must not also fire.
      event.preventDefault(); event.nativeEvent.stopPropagation(); setOpen(false); return
    }
    if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true); setCursor((n) => Math.min(shown.length - 1, n + 1)) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setCursor((n) => Math.max(0, n - 1)) }
    else if (event.key === 'Enter' && open) { event.preventDefault(); const user = shown[cursor]; if (user) toggle(user.id) }
  }

  return <div className="cs-picker" ref={box}>
    {value.length > 0 && <ul className="cs-chips" aria-label="Allocated staff">{value.map((id) => {
      const user = users.find((item) => item.id === id)
      const name = user?.name ?? 'Unknown user'
      return <li key={id} className="cs-chip">
        <Avatar name={name} size="sm" /><span>{name}</span>
        <button type="button" aria-label={`Remove ${name}`} disabled={disabled} onClick={() => toggle(id)}><X size={13} /></button>
      </li>
    })}</ul>}
    <button type="button" className="cs-picker-trigger" aria-haspopup="listbox" aria-expanded={open} aria-controls={listId} disabled={disabled}
      onClick={() => { setOpen((v) => !v); if (!open) window.setTimeout(() => search.current?.focus(), 0) }}
      aria-invalid={!!error}>
      <span>{value.length ? `${value.length} selected. Add or remove staff` : 'Choose CRM staff'}</span><ChevronDown size={16} aria-hidden="true" />
    </button>
    {open && <div className="cs-picker-panel" onKeyDown={onKey}>
      <label className="cs-picker-search"><Search size={14} aria-hidden="true" />
        <input ref={search} type="text" role="combobox" aria-expanded="true" aria-controls={listId} aria-autocomplete="list" aria-activedescendant={shown[cursor] ? `${listId}-${shown[cursor].id}` : undefined}
          value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by name" aria-label="Search CRM staff" autoComplete="off" />
      </label>
      <ul id={listId} role="listbox" aria-multiselectable="true" aria-label="CRM staff">
        {shown.length === 0 && <li className="cs-picker-none" role="presentation">{users.length === 0 ? 'No staff or managers are set up yet. Add them in User Management.' : 'No one matches that name.'}</li>}
        {shown.map((user, index) => {
          const on = selected.has(user.id)
          return <li key={user.id} id={`${listId}-${user.id}`} role="option" aria-selected={on} className={`${on ? 'is-on' : ''}${index === cursor ? ' is-cursor' : ''}`}
            onMouseEnter={() => setCursor(index)} onClick={() => toggle(user.id)}>
            <Avatar name={user.name} size="sm" /><span className="cs-opt-name">{user.name}</span><span className="cs-opt-role">{ROLE_LABEL[user.role]}</span>
            <Check size={15} className="cs-opt-check" aria-hidden="true" />
          </li>
        })}
      </ul>
      <div className="cs-picker-foot"><span>{value.length ? `${value.length} selected` : 'None selected'}{value.length > 0 && <> · {value.map((id) => firstName(users.find((u) => u.id === id)?.name)).filter(Boolean).join(', ')}</>}</span><button type="button" onClick={() => setOpen(false)}>Done</button></div>
    </div>}
    {error && <span className="ui-field-error" role="alert">{error}</span>}
  </div>
}
