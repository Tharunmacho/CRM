export const EMPTY_CANDIDATE_FORM = {
  name: '', phone: '', whatsapp: '', email: '', dob: '', gender: '', nationality: '', currentCity: '', languages: '',
  passportNo: '', passportIssue: '', passportExpiry: '', passportPlace: '', designation: '', totalExperience: '',
  gulfExperience: '', skills: '', expectedSalary: '', source: 'walk_in', sourceDetail: '',
  // Application form (docx) details. `education` and `workHistory` hold the repeating rows as JSON text, which keeps the
  // whole form flat strings, so drafts, recovery and the OCR merge treat them like any other field.
  fatherName: '', maritalStatus: '', currentAddress: '', experienceIndia: '', education: '', workHistory: '',
  // Screening details (reference intake form). The "same as" switches hold 'yes' or ''; the others are plain text.
  nativePlace: '', nativeSame: '', familyNumber: '', countryPreference: '', jobDurationPreference: '', bestTimeToContact: '',
  serviceChargesStatus: 'not_discussed', interestLevel: '', nextFollowUp: '', whatsappSame: 'yes', assignTo: '',
}
/**
 * The keys the server keeps in an intake draft (backend FORM_KEYS). A few screening details are named differently on the
 * server; DRAFT_KEY_ALIAS maps this page's name to the server's, in both directions. "Same as current" and the recruiter
 * choice are not kept in a draft: they are decided again when the person saves.
 */
export const DRAFT_KEY_ALIAS = { familyNumber: 'familyMemberNumber', nextFollowUp: 'nextFollowUpDate', whatsappSame: 'whatsappSameAsPhone' } as const
export const DRAFT_FORM_KEYS = [
  'name', 'phone', 'whatsapp', 'email', 'dob', 'gender', 'nationality', 'currentCity', 'languages', 'passportNo', 'passportIssue',
  'passportExpiry', 'passportPlace', 'designation', 'totalExperience', 'gulfExperience', 'skills', 'expectedSalary', 'source',
  'sourceDetail', 'fatherName', 'maritalStatus', 'currentAddress', 'experienceIndia', 'education', 'workHistory',
  'nativePlace', 'countryPreference', 'jobDurationPreference', 'bestTimeToContact', 'serviceChargesStatus', 'interestLevel',
  ...(Object.keys(DRAFT_KEY_ALIAS) as (keyof typeof DRAFT_KEY_ALIAS)[]),
] as const
const toServerKey = (key: string) => (DRAFT_KEY_ALIAS as Record<string, string>)[key] ?? key
const fromServerKey = (key: string) => Object.entries(DRAFT_KEY_ALIAS).find(([, server]) => server === key)?.[0] ?? key
/** What an autosave sends: only the keys the draft endpoint accepts, under the names it knows. */
export function draftSnapshot(form: Record<string, string>, edited: Iterable<string>) {
  const allowed = new Set<string>(DRAFT_FORM_KEYS)
  return {
    form: Object.fromEntries(Object.entries(form).filter(([key]) => allowed.has(key)).map(([key, value]) => [toServerKey(key), value])),
    editedFields: [...edited].filter((key) => allowed.has(key)).map(toServerKey),
  }
}
export type CandidateEntryForm = typeof EMPTY_CANDIDATE_FORM
export type CandidateEntryField = keyof CandidateEntryForm

/**
 * Match the server's profile constraints before asking it to save.
 * With an uploaded document nothing is required: a blank name or phone is saved as "needs review".
 */
export function candidateEntryErrors(form: CandidateEntryForm, today = new Date().toISOString().slice(0, 10), withDocuments = false) {
  const errors: Partial<Record<CandidateEntryField, string>> = {}
  if (!form.name.trim() && !withDocuments) errors.name = 'Enter the candidate’s full name.'
  // Dropping the agency's calling code or a leading trunk 0 only ever takes a number from 12 or 11 digits to 10,
  // so the server's "at least seven digits" rule can be checked without knowing the calling code.
  const phone = form.phone.normalize('NFKC').replace(/\D/g, '').replace(/^00/, '')
  if (phone.length < 7 && (form.phone.trim() || !withDocuments)) errors.phone = 'Enter a phone number with at least seven digits.'
  if (form.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) errors.email = 'Enter a valid email address, or leave it empty.'
  if (form.dob && form.dob > today) errors.dob = 'Date of birth cannot be in the future.'
  if (form.passportIssue && form.passportExpiry && form.passportExpiry < form.passportIssue) {
    errors.passportExpiry = 'Expiry date must be on or after the issue date.'
  }
  for (const key of ['totalExperience', 'gulfExperience', 'experienceIndia'] as const) {
    if (key === 'experienceIndia' && !form.experienceIndia.trim()) continue
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
export function restoredCandidateDraft(draft?: { form?: Record<string, string>; editedFields?: string[] }, blank: CandidateEntryForm = EMPTY_CANDIDATE_FORM) {
  const form = { ...blank }
  const saved: Record<string, string> = {}
  for (const [key, value] of Object.entries(draft?.form ?? {})) if (typeof value === 'string') saved[fromServerKey(key)] = value
  for (const key of Object.keys(form) as CandidateEntryField[]) {
    if (typeof saved[key] === 'string') form[key] = saved[key]
  }
  const edited = new Set((draft?.editedFields ?? []).map(fromServerKey).filter((key): key is CandidateEntryField => Object.hasOwn(form, key)))
  return { form, edited }
}
