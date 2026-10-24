export const EMPTY_CANDIDATE_FORM = {
  name: '', phone: '', whatsapp: '', email: '', dob: '', gender: '', nationality: 'Indian', currentCity: '', languages: '',
  passportNo: '', passportIssue: '', passportExpiry: '', passportPlace: '', designation: '', totalExperience: '',
  gulfExperience: '', skills: '', expectedSalary: '', source: 'walk_in', sourceDetail: '',
}
export type CandidateEntryForm = typeof EMPTY_CANDIDATE_FORM
export type CandidateEntryField = keyof CandidateEntryForm

/** Match the server's profile constraints before asking it to save. */
export function candidateEntryErrors(form: CandidateEntryForm, today = new Date().toISOString().slice(0, 10)) {
  const errors: Partial<Record<CandidateEntryField, string>> = {}
  if (!form.name.trim()) errors.name = 'Enter the candidate’s full name.'
  let phone = form.phone.normalize('NFKC').replace(/\D/g, '')
  if (phone.startsWith('00')) phone = phone.slice(2)
  if (phone.length === 12 && phone.startsWith('91')) phone = phone.slice(2)
  else if (phone.length === 11 && phone.startsWith('0')) phone = phone.slice(1)
  if (phone.length < 7) errors.phone = 'Enter a phone number with at least seven digits.'
  if (form.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) errors.email = 'Enter a valid email address, or leave it empty.'
  if (form.dob && form.dob > today) errors.dob = 'Date of birth cannot be in the future.'
  if (form.passportIssue && form.passportExpiry && form.passportExpiry < form.passportIssue) {
    errors.passportExpiry = 'Expiry date must be on or after the issue date.'
  }
  for (const key of ['totalExperience', 'gulfExperience'] as const) {
    const value = Number(form[key])
    if (!Number.isInteger(value) || value < 0 || value > 80) errors[key] = 'Enter whole years from 0 to 80.'
  }
  if (!errors.totalExperience && !errors.gulfExperience && Number(form.gulfExperience) > Number(form.totalExperience)) {
    errors.gulfExperience = 'Gulf or overseas experience cannot be more than total experience.'
  }
  if (form.expectedSalary.trim() && (!Number.isFinite(Number(form.expectedSalary)) || Number(form.expectedSalary) < 0 || Number(form.expectedSalary) > 9999999999.99)) {
    errors.expectedSalary = 'Enter an amount from 0 to 9,999,999,999.99.'
  }
  return errors
}

/** Restore one person's saved form without importing another intake's edits. */
export function restoredCandidateDraft(draft?: { form?: Record<string, string>; editedFields?: string[] }) {
  const form = { ...EMPTY_CANDIDATE_FORM }
  for (const key of Object.keys(form) as CandidateEntryField[]) {
    if (typeof draft?.form?.[key] === 'string') form[key] = draft.form[key]
  }
  const edited = new Set((draft?.editedFields ?? []).filter((key): key is CandidateEntryField => Object.hasOwn(form, key)))
  return { form, edited }
}
