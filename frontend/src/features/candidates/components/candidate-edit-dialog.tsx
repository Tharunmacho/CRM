import { useState } from 'react'
import type { Candidate } from '@/shared/model/types'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { Button, CheckboxInput, Field, Input, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { normalizedSkills } from '../candidate-skills'
import { candidateEntryErrors, EMPTY_CANDIDATE_FORM } from '../entry-model'

export function CandidateEditDialog({ candidate, onClose }: { candidate: Candidate; onClose: () => void }) {
  const store = useStore()
  const [form, setForm] = useState({
    name: candidate.name, phone: candidate.phone, whatsapp: candidate.whatsapp || '', email: candidate.email || '',
    dob: candidate.dob?.slice(0, 10) || '', gender: candidate.gender || '', nationality: candidate.nationality || '',
    current_city: candidate.currentCity || '', languages: candidate.languages.join(', '),
    passport_no: candidate.passportNo || '', passport_issue: candidate.passportIssue?.slice(0, 10) || '',
    passport_expiry: candidate.passportExpiry?.slice(0, 10) || '', passport_place: candidate.passportPlace || '',
    designations: candidate.designations.join(', '), skills: candidate.skills.join(', '),
    total_experience: String(candidate.totalExperience), gulf_experience: String(candidate.gulfExperience),
    expected_salary: candidate.expectedSalary == null ? '' : String(candidate.expectedSalary),
  })
  const [reviewed, setReviewed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const set = (key: keyof typeof form, value: string) => {
    setForm(previous => ({ ...previous, [key]: value }))
    setErrors(previous => ({ ...previous, [key]: '' }))
  }
  const fields: [keyof typeof form, string, string?][] = [
    ['name', 'Full name'], ['phone', 'Phone'], ['whatsapp', 'WhatsApp'], ['email', 'Email', 'email'],
    ['dob', 'Date of birth', 'date'], ['gender', 'Gender'], ['nationality', 'Nationality'], ['current_city', 'Current city'],
    ['languages', 'Languages, separated by commas'], ['passport_no', 'Passport number'], ['passport_issue', 'Passport issue', 'date'],
    ['passport_expiry', 'Passport expiry', 'date'], ['passport_place', 'Place of issue'], ['designations', 'Designations, separated by commas'],
    ['total_experience', 'Total experience (years)', 'number'], ['gulf_experience', 'Gulf experience (years)', 'number'], ['expected_salary', 'Expected salary', 'number'],
  ]
  const save = async () => {
    if (saving) return
    if (!saved) {
      const problems = candidateEntryErrors({ ...EMPTY_CANDIDATE_FORM, ...form, passportIssue: form.passport_issue,
        passportExpiry: form.passport_expiry, totalExperience: form.total_experience, gulfExperience: form.gulf_experience,
        expectedSalary: form.expected_salary })
      const names: Record<string, string> = { passportExpiry: 'passport_expiry', totalExperience: 'total_experience', gulfExperience: 'gulf_experience', expectedSalary: 'expected_salary' }
      setErrors(Object.fromEntries(Object.entries(problems).map(([key, message]) => [names[key] || key, message])))
      if (Object.keys(problems).length) { setError('Review the highlighted fields before saving your corrections.'); return }
    }
    setSaving(true); setError('')
    let committed = saved
    try {
      if (!committed) await api.patch(`/candidates/${candidate.id}`, {
        ...form, dob: form.dob || null, passport_issue: form.passport_issue || null, passport_expiry: form.passport_expiry || null,
        total_experience: Number(form.total_experience), gulf_experience: Number(form.gulf_experience),
        expected_salary: form.expected_salary ? Number(form.expected_salary) : null,
        skills: normalizedSkills(form.skills.split(/[,;\n]/)), designations: normalizedSkills(form.designations.split(',')),
        languages: normalizedSkills(form.languages.split(',')), mark_reviewed: reviewed,
      })
      if (!store.isCurrentSession()) return
      committed = true
      setSaved(true)
      await store.refresh({ keepCurrent: true })
      if (store.isCurrentSession()) onClose()
    } catch (error) {
      if (store.isCurrentSession()) setError(committed
        ? 'Your corrections are saved. Refresh the profile to see them; retrying will not save them again.'
        : error instanceof Error ? error.message : 'Your corrections could not be saved.')
    } finally { setSaving(false) }
  }
  return <Dialog open onClose={() => { if (!saving) onClose() }} title="Correct candidate details" description="Update the reviewed profile. Original documents and OCR evidence stay available." width="lg" footer={<><Button disabled={saving} onClick={onClose}>{saved ? 'Close' : 'Cancel'}</Button><Button disabled={saving || !form.name.trim() || !form.phone.trim()} variant="primary" onClick={() => void save()}>{saving ? saved ? 'Refreshing…' : 'Saving…' : saved ? 'Refresh saved profile' : 'Save corrections'}</Button></>}>
    <fieldset disabled={saving || saved} className="min-w-0">
    <div className="grid gap-4 sm:grid-cols-2">{fields.map(([key, label, type]) => <Field key={key} label={label} error={errors[key]} required={key === 'name' || key === 'phone'}><Input type={type || 'text'} value={form[key]} min={type === 'number' ? 0 : undefined} step={key === 'expected_salary' ? '0.01' : undefined} onChange={event => set(key, event.target.value)} /></Field>)}
      <Field className="sm:col-span-2" label="Skills, separated by commas"><Textarea value={form.skills} onChange={event => set('skills', event.target.value)} /></Field>
    </div>
    {candidate.needsReview && (candidate.ownerId
      ? <label className="mt-5 flex items-center gap-2 text-sm"><CheckboxInput checked={reviewed} onChange={setReviewed} />I checked the profile and documents; mark review complete.</label>
      : <p className="mt-5 text-sm text-amber-700">You can save corrections now. Assign a recruiter before marking the candidate review complete.</p>)}
    </fieldset>
    {error && <p role="alert" className="mt-4 text-sm text-stamp-600">{error}</p>}
  </Dialog>
}
