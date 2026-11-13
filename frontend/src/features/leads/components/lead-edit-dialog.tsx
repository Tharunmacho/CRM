import { useEffect, useMemo, useState } from 'react'
import { Lock } from 'lucide-react'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { gateFromRefusal, leadApi } from '../lead-api'
import { SETTABLE_STATUSES, experienceAnswer, leadAge, type Lead } from '../lead-model'
import type { LeadGate } from '../lead-gate'

/** The editable facts of one lead, as the form holds them (all text). Keys are the PATCH /leads/{id} names. */
type Form = Record<EditKey, string>
type EditKey = 'name' | 'dob' | 'gender' | 'phone' | 'email' | 'positionApplied' | 'countryInterest' | 'educationLevel'
  | 'experienceIndia' | 'experienceAbroad' | 'experienceTotal' | 'city' | 'state' | 'status' | 'notes'

/** What each experience box starts with: the stored answer as the recruiter would type it. */
const experienceText = (lead: Lead, kind: 'india' | 'abroad' | 'total') => experienceAnswer(lead, kind).value ?? ''

export function formFromLead(lead: Lead): Form {
  return {
    name: lead.name ?? '', dob: lead.dob ?? '', gender: lead.gender ?? '', phone: lead.phone ?? '', email: lead.email ?? '',
    positionApplied: lead.positionApplied ?? '', countryInterest: lead.countryInterest ?? '', educationLevel: lead.educationLevel ?? '',
    experienceIndia: experienceText(lead, 'india'), experienceAbroad: experienceText(lead, 'abroad'), experienceTotal: experienceText(lead, 'total'),
    city: lead.city ?? '', state: lead.state ?? '', status: lead.status, notes: lead.notes ?? '',
  }
}

/** The same limits the server checks (LeadPatch / patch_lead), so a refusal is explained before the request. */
export function validateLeadForm(form: Form, today: Date = new Date()): Partial<Record<EditKey, string>> {
  const errors: Partial<Record<EditKey, string>> = {}
  const digits = form.phone.replace(/\D/g, '')
  if (form.phone.trim() && (digits.length < 7 || digits.length > 15)) errors.phone = 'Enter a valid phone number (7 to 15 digits).'
  if (form.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) errors.email = 'Enter a valid e-mail address.'
  if (form.dob) {
    const age = leadAge(form.dob, today)
    if (age === null) errors.dob = 'Enter a real date of birth, not in the future.'
    else if (age < 14 || age > 80) errors.dob = `That makes the person ${age}. Check the date of birth.`
  }
  return errors
}

/** Only the fields that changed, trimmed; an emptied box clears the value (null). */
export function changedFields(lead: Lead, form: Form): Record<string, unknown> {
  const before = formFromLead(lead)
  const body: Record<string, unknown> = {}
  for (const key of Object.keys(form) as EditKey[]) {
    const value = form[key].trim()
    if (value === before[key].trim()) continue
    body[key] = key === 'status' ? value : key === 'notes' ? value : value || null
  }
  return body
}

/** Edit every fact shown in a lead's row (and the remarks) in one form; saves through PATCH /leads/{id}. */
export function LeadEditDialog({ lead, blockedReason, onClose, onSaved, onGateRefusal }: {
  lead: Lead | null
  blockedReason: string | null
  onClose: () => void
  onSaved: (lead: Lead) => void
  onGateRefusal: (gate: LeadGate) => void
}) {
  const [form, setForm] = useState<Form | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [tried, setTried] = useState(false)
  useEffect(() => { setForm(lead ? formFromLead(lead) : null); setError(''); setTried(false) }, [lead])
  const errors = useMemo(() => (form ? validateLeadForm(form) : {}), [form])
  const body = useMemo(() => (lead && form ? changedFields(lead, form) : {}), [lead, form])
  if (!lead || !form) return null
  const converted = Boolean(lead.candidateId) || lead.status === 'converted'
  const set = (key: EditKey) => (event: { target: { value: string } }) => setForm((current) => (current ? { ...current, [key]: event.target.value } : current))
  const show = (key: EditKey) => (tried ? errors[key] : undefined)
  const age = leadAge(form.dob)
  const dirty = Object.keys(body).length > 0
  const disabled = Boolean(blockedReason) || saving

  const save = async () => {
    setTried(true)
    if (Object.keys(errors).length || !dirty || disabled) return
    setSaving(true); setError('')
    try { onSaved(await leadApi.update(lead.id, body)) } catch (e) {
      const refused = gateFromRefusal(e)
      if (refused) onGateRefusal(refused)
      setError(e instanceof Error ? e.message : 'Your changes were not saved. Please try again.')
    } finally { setSaving(false) }
  }

  return (
    <Dialog open onClose={onClose} title={`Edit ${lead.name || 'lead'}`} width="lg"
      footer={<>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!dirty || disabled} title={blockedReason ?? undefined} onClick={() => void save()}>{saving ? 'Saving…' : 'Save changes'}</Button>
      </>}>
      <form className="ld-editform" onSubmit={(event) => { event.preventDefault(); void save() }}>
        {blockedReason && <p className="ld-locked" role="alert"><Lock size={15} aria-hidden="true" />{blockedReason}</p>}
        <fieldset disabled={disabled} className="ld-editgrid">
          <Field label="Name" error={show('name')}><Input value={form.name} maxLength={200} onChange={set('name')} autoFocus /></Field>
          <Field label="Date of birth" hint={age === null ? 'Age is worked out from this' : `Age ${age}`} error={show('dob')}><Input type="date" value={form.dob} onChange={set('dob')} /></Field>
          <Field label="Phone" error={show('phone')}><Input value={form.phone} maxLength={40} inputMode="tel" onChange={set('phone')} /></Field>
          <Field label="E-mail" error={show('email')}><Input type="email" value={form.email} maxLength={255} onChange={set('email')} /></Field>
          <Field label="Gender">
            <Select value={form.gender} onChange={set('gender')}>
              <option value="">Not given</option>
              {['Male', 'Female', 'Other'].map((item) => <option key={item} value={item}>{item}</option>)}
              {form.gender && !['Male', 'Female', 'Other'].includes(form.gender) && <option value={form.gender}>{form.gender}</option>}
            </Select>
          </Field>
          <Field label="Job applied"><Input value={form.positionApplied} maxLength={200} onChange={set('positionApplied')} /></Field>
          <Field label="Country interest"><Input value={form.countryInterest} maxLength={120} onChange={set('countryInterest')} /></Field>
          <Field label="Education"><Input value={form.educationLevel} maxLength={200} onChange={set('educationLevel')} /></Field>
          <Field label="India experience" hint='e.g. "3", "2.5 yrs", "6 months"'><Input value={form.experienceIndia} maxLength={200} onChange={set('experienceIndia')} /></Field>
          <Field label="Abroad experience" hint='e.g. "2 years" or "No"'><Input value={form.experienceAbroad} maxLength={200} onChange={set('experienceAbroad')} /></Field>
          <Field label="Total experience"><Input value={form.experienceTotal} maxLength={200} onChange={set('experienceTotal')} /></Field>
          <Field label="City"><Input value={form.city} maxLength={120} onChange={set('city')} /></Field>
          <Field label="State"><Input value={form.state} maxLength={120} onChange={set('state')} /></Field>
          <Field label="Status" hint={converted ? 'A converted lead keeps its status.' : undefined}>
            <Select value={form.status} disabled={converted} onChange={set('status')}>
              {SETTABLE_STATUSES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              {converted && <option value="converted">Converted</option>}
            </Select>
          </Field>
          <Field label="Remarks" className="ld-editwide"><Textarea rows={3} value={form.notes} maxLength={5000} placeholder="Anything worth remembering about this person" onChange={set('notes')} /></Field>
        </fieldset>
        {error && <p role="alert" className="ld-alert"><span>{error}</span></p>}
        <button type="submit" hidden />
      </form>
    </Dialog>
  )
}
