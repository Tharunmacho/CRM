import { useState } from 'react'
import { BriefcaseBusiness, Contact, IdCard, UserRound } from 'lucide-react'
import type { Candidate } from '@/shared/model/types'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { Button, CheckboxInput, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { DatePicker } from '@/shared/ui/date-picker'
import { Dialog } from '@/shared/ui/overlay'
import { FormSection } from '@/shared/ui/form-section'
import { useFormOptions } from '@/features/form-options/use-form-options'
import { choicesFor, FORM_OPTION_CATEGORIES } from '@/features/form-options/form-options-model'
import { normalizedSkills } from '../candidate-skills'
import { candidateReviewIssues } from '../candidate-review'
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
  const { options: formOptions } = useFormOptions()
  const text = (key: keyof typeof form, label: string, extra: { type?: string; placeholder?: string; className?: string } = {}) => (
    <Field key={key} label={label} error={errors[key]} required={key === 'name' || key === 'phone'} className={extra.className}>
      <Input type={extra.type || 'text'} value={form[key]} min={extra.type === 'number' ? 0 : undefined} step={key === 'expected_salary' ? '0.01' : undefined}
        placeholder={extra.placeholder} onChange={event => set(key, event.target.value)} />
    </Field>
  )
  const date = (key: keyof typeof form, label: string) => (
    <Field key={key} label={label} error={errors[key]}><DatePicker value={form[key]} onChange={value => set(key, value)} /></Field>
  )
  const save = async () => {
    if (saving) return
    if (!saved) {
      const problems = candidateEntryErrors({ ...EMPTY_CANDIDATE_FORM, ...form, passportIssue: form.passport_issue,
        passportExpiry: form.passport_expiry, totalExperience: form.total_experience, gulfExperience: form.gulf_experience,
        expectedSalary: form.expected_salary })
      const names: Record<string, string> = { passportExpiry: 'passport_expiry', totalExperience: 'total_experience', gulfExperience: 'gulf_experience', expectedSalary: 'expected_salary' }
      setErrors(Object.fromEntries(Object.entries(problems).map(([key, message]) => [names[key] || key, message])))
      if (Object.keys(problems).length) { setError('Please fix the fields marked in red, then save again.'); return }
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
        ? 'Your changes are saved. Refresh the profile to see them. You do not need to save again.'
        : error instanceof Error ? error.message : 'We could not save your changes. Please try again.')
    } finally { setSaving(false) }
  }
  return <Dialog open onClose={() => { if (!saving) onClose() }} title="Edit candidate details" description="Change the details on this profile. The original documents stay saved." width="lg" footer={<><Button variant="ghost" disabled={saving} onClick={onClose}>{saved ? 'Close' : 'Cancel'}</Button><Button disabled={saving || !form.name.trim() || !form.phone.trim()} variant="primary" onClick={() => void save()}>{saving ? saved ? 'Refreshing…' : 'Saving…' : saved ? 'Refresh profile' : 'Save changes'}</Button></>}>
    <fieldset disabled={saving || saved} className="ui-fs-stack">
      {candidateReviewIssues(candidate).length > 0 && <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900" role="note">
        <strong>Profile details to check</strong>
        <ul className="mt-2 space-y-1">{candidateReviewIssues(candidate).map(issue => <li key={issue.key}><b>{issue.label}.</b> {issue.action}</li>)}</ul>
      </div>}
      <FormSection icon={<UserRound size={18} />} title="Name and contact" hint="Who the candidate is and how to reach them">
        {text('name', 'Full name')}
        {text('phone', 'Phone', { placeholder: '+91 98400 00000' })}
        {text('whatsapp', 'WhatsApp', { placeholder: '+91 98400 00000' })}
        {text('email', 'Email', { type: 'email', placeholder: 'name@example.com' })}
      </FormSection>
      <FormSection icon={<Contact size={18} />} title="Personal details" hint="Taken from the passport or the resume">
        {date('dob', 'Date of birth')}
        <Field label="Gender" error={errors.gender}>
          <Select value={form.gender} onChange={event => set('gender', event.target.value)}>
            <option value="">Not recorded</option>
            {choicesFor(formOptions, FORM_OPTION_CATEGORIES.gender, form.gender).map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
          </Select>
        </Field>
        {text('nationality', 'Nationality', { placeholder: 'Indian' })}
        {text('current_city', 'Current city')}
        {text('languages', 'Languages', { className: 'sm:col-span-2', placeholder: 'English, Hindi, Arabic' })}
      </FormSection>
      <FormSection icon={<IdCard size={18} />} title="Passport" hint="As printed in the passport">
        {text('passport_no', 'Passport number')}
        {text('passport_place', 'Place of issue')}
        {date('passport_issue', 'Issue date')}
        {date('passport_expiry', 'Expiry date')}
      </FormSection>
      <FormSection icon={<BriefcaseBusiness size={18} />} title="Work" hint="Job titles, skills and experience" cols={3}>
        {text('designations', 'Job titles', { className: 'sm:col-span-3', placeholder: 'Welder, Pipe fitter' })}
        <Field className="sm:col-span-3" label="Skills" hint="Separate with commas"><Textarea rows={2} value={form.skills} placeholder="TIG welding, blueprint reading" onChange={event => set('skills', event.target.value)} /></Field>
        {text('total_experience', 'Total experience (years)', { type: 'number' })}
        {text('gulf_experience', 'Gulf experience (years)', { type: 'number' })}
        {text('expected_salary', 'Expected salary', { type: 'number' })}
      </FormSection>
    {(candidate.needsReview || candidate.status === 'needs_review') && (candidate.ownerId
      ? <label className="mt-6 flex items-center gap-2 text-sm"><CheckboxInput checked={reviewed} onChange={setReviewed} />I checked the profile and documents. Mark the review as done.</label>
      : <p className="mt-6 text-sm text-amber-700">You can save your changes now. Give this candidate a recruiter before you mark the review as done.</p>)}
    </fieldset>
    {error && <p role="alert" className="mt-4 text-sm text-stamp-600">{error}</p>}
  </Dialog>
}
