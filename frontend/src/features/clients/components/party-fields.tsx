import { useId, useMemo, useState, type ReactNode } from 'react'
import { Globe, Plus, Search, Tags, Trash2, UserRound, Users } from 'lucide-react'
import type { User } from '@/shared/model/types'
import { Field, Input, Textarea } from '@/shared/ui/primitives'
import { Avatar } from '@/features/clients/components/parts'
import { emptyStaffRow, MAX_STAFF_ROWS, PREFERRED_COUNTRIES, SPECIALISATIONS, toggleSpecialisation, togglePreferredCountry, type AgencyFocus, type FieldErrors, type PartyFields, type StaffRow } from '@/features/clients/client-form'
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

/** One group of toggle buttons: any number can be on, each is a pill that reports its state with aria-pressed. The card's title names the group. */
function PillGroup({ label, options, value, onToggle, error }: { label: string; options: readonly string[]; value: readonly string[]; onToggle: (name: string) => void; error?: string }) {
  return <div className="cs-pillgroup">
    <div className="cs-pills" role="group" aria-label={label}>
      {options.map((name) => <button key={name} type="button" className="cs-pill" aria-pressed={value.includes(name)} onClick={() => onToggle(name)}>{name}</button>)}
    </div>
    {error && <span className="ui-field-error" role="alert">{error}</span>}
  </div>
}

const selectedCount = (n: number) => (n ? `${n} selected` : undefined)

/** The agency's specialisation and its preferred countries: two separate cards, each a group of toggle buttons. */
export function AgencyFocusSection({ value, onChange, errors }: { value: AgencyFocus; onChange: (next: AgencyFocus) => void; errors: FieldErrors }) {
  return <>
    <Section icon={<Tags size={17} />} title="Specialisation" count={selectedCount(value.specialisations.length)}>
      <PillGroup label="Specialisation" options={SPECIALISATIONS} value={value.specialisations} error={errors.specialisations}
        onToggle={(name) => onChange({ ...value, specialisations: toggleSpecialisation(value.specialisations, name) })} />
    </Section>
    <Section icon={<Globe size={17} />} title="Preferred country" count={selectedCount(value.preferredCountries.length)}>
      <PillGroup label="Preferred country" options={PREFERRED_COUNTRIES} value={value.preferredCountries} error={errors.preferredCountries}
        onToggle={(name) => onChange({ ...value, preferredCountries: togglePreferredCountry(value.preferredCountries, name) })} />
    </Section>
  </>
}

/** The agency's own people: a mini-card per person with four boxes and a round remove button, and a dashed "Add staff" pill. */
export function StaffDetailsSection({ rows, onChange, errors }: { rows: StaffRow[]; onChange: (rows: StaffRow[]) => void; errors: FieldErrors }) {
  const change = (index: number, key: keyof StaffRow, value: string) => onChange(rows.map((row, i) => i === index ? { ...row, [key]: value } : row))
  const filled = rows.filter((row) => row.name.trim() || row.designation.trim() || row.phone.trim() || row.email.trim()).length
  return <Section icon={<Users size={17} />} title="Staff details" count={filled ? `${filled} added` : undefined}>
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

/** CRM users who can be given a client: active recruiters and managers (admins are not allocated to clients). */
export function allocatableUsers(users: User[]): User[] {
  return users.filter((user) => user.active && (user.role === 'staff' || user.role === 'manager')).sort((a, b) => a.name.localeCompare(b.name))
}

const ROLE_LABEL: Record<string, string> = { staff: 'Recruiter', manager: 'Manager', admin: 'Admin' }

/**
 * The CRM recruiters who can look after a client, shown as a plain checklist: no dropdown to open and nothing to scroll to.
 * Tick or untick a name. A search box only appears when the list is long.
 */
export function RecruiterPicker({ users, value, onChange, error, disabled }: { users: User[]; value: string[]; onChange: (ids: string[]) => void; error?: string; disabled?: boolean }) {
  const [query, setQuery] = useState('')
  const selected = useMemo(() => new Set(value), [value])
  const searchable = users.length > 6
  const shown = useMemo(() => {
    const needle = foldText(query).trim()
    return needle ? users.filter((user) => foldText(user.name).includes(needle)) : users
  }, [users, query])
  const toggle = (id: string) => onChange(selected.has(id) ? value.filter((item) => item !== id) : [...value, id])

  return <div className="cs-picker">
    {searchable && <label className="cs-picker-search"><Search size={14} aria-hidden="true" />
      <input type="text" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search recruiters" aria-label="Search recruiters" autoComplete="off" disabled={disabled} />
    </label>}
    <ul className="cs-rec-list" aria-label="Recruiters" aria-invalid={!!error}>
      {shown.length === 0 && <li className="cs-picker-none">{users.length === 0 ? 'No recruiters are set up yet. Add them in User Management.' : 'No one matches that name.'}</li>}
      {shown.map((user) => {
        const on = selected.has(user.id)
        return <li key={user.id}>
          <label className={on ? 'cs-rec is-on' : 'cs-rec'}>
            <input type="checkbox" checked={on} disabled={disabled} onChange={() => toggle(user.id)} />
            <Avatar name={user.name} size="sm" /><span className="cs-opt-name">{user.name}</span><span className="cs-opt-role">{ROLE_LABEL[user.role]}</span>
          </label>
        </li>
      })}
    </ul>
    {users.length > 0 && <p className="cs-rec-foot">{value.length ? `${value.length} selected` : 'Tick one or more recruiters'}</p>}
    {error && <span className="ui-field-error" role="alert">{error}</span>}
  </div>
}
