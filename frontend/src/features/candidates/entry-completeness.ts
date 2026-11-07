import { parseRows, EDUCATION_KEYS, HISTORY_KEYS } from '@/features/application-form/entry-bridge'
import type { CandidateEntryForm } from './entry-model'
import { effectiveNativePlace } from './entry-extras'
import { documentLabel } from '@/shared/model/document-slots'

export type CompletenessSection = 'documents' | 'personal' | 'passport' | 'education' | 'experience'
export interface CompletenessItem { key: string; label: string; section: CompletenessSection; anchor: string }
export interface EntryDocuments { resume: boolean; passport: boolean; photo: boolean }

/** The same 19 counted items as the saved application form (docs/workflows/application-form-contract.md section 2). */
const FIELD_ITEMS: { key: keyof CandidateEntryForm; label: string; section: CompletenessSection; anchor: string }[] = [
  { key: 'name', label: 'Candidate name', section: 'personal', anchor: 'candidate-personal' },
  { key: 'fatherName', label: 'Father’s name', section: 'personal', anchor: 'candidate-personal' },
  { key: 'dob', label: 'Date of birth', section: 'personal', anchor: 'candidate-personal' },
  { key: 'maritalStatus', label: 'Marital status', section: 'personal', anchor: 'candidate-personal' },
  { key: 'gender', label: 'Gender', section: 'personal', anchor: 'candidate-personal' },
  { key: 'nationality', label: 'Nationality', section: 'personal', anchor: 'candidate-personal' },
  { key: 'languages', label: 'Language known', section: 'personal', anchor: 'candidate-personal' },
  { key: 'phone', label: 'Mobile number', section: 'personal', anchor: 'candidate-personal' },
  { key: 'currentAddress', label: 'Current address', section: 'personal', anchor: 'candidate-personal' },
  { key: 'passportNo', label: 'Passport number', section: 'passport', anchor: 'candidate-passport' },
  { key: 'passportPlace', label: 'Place of issue', section: 'passport', anchor: 'candidate-passport' },
  { key: 'passportIssue', label: 'Issue date', section: 'passport', anchor: 'candidate-passport' },
  { key: 'passportExpiry', label: 'Expiry date', section: 'passport', anchor: 'candidate-passport' },
  { key: 'totalExperience', label: 'Total experience', section: 'experience', anchor: 'candidate-experience' },
]

export const SECTION_LABEL: Record<CompletenessSection, string> = {
  documents: 'Documents', personal: 'Personal', passport: 'Passport', education: 'Education', experience: 'Experience',
}

const hasRow = (json: string, keys: readonly string[]) => parseRows(json, keys).some((row) => Object.values(row).some((value) => value.trim()))

/** Which of the 19 items are filled, which are missing, and the percentage (round(100 * filled / total)). */
export function entryCompleteness(form: CandidateEntryForm, documents: EntryDocuments) {
  const items: (CompletenessItem & { filled: boolean })[] = [
    ...FIELD_ITEMS.map((item) => ({ key: item.key as string, label: item.label, section: item.section, anchor: item.anchor, filled: Boolean(form[item.key].trim()) })),
    { key: 'education', label: 'Education', section: 'education', anchor: 'candidate-education', filled: hasRow(form.education, EDUCATION_KEYS) },
    { key: 'workHistory', label: 'Previous employer', section: 'experience', anchor: 'candidate-experience', filled: hasRow(form.workHistory, HISTORY_KEYS) },
    { key: 'documents.resume', label: documentLabel('resume'), section: 'documents', anchor: 'candidate-documents', filled: documents.resume },
    { key: 'documents.passport', label: documentLabel('passport'), section: 'documents', anchor: 'candidate-documents', filled: documents.passport },
    { key: 'documents.photo', label: documentLabel('photo'), section: 'documents', anchor: 'candidate-documents', filled: documents.photo },
  ]
  const filled = items.filter((item) => item.filled).length
  const missing = items.filter((item) => !item.filled)
  const groups = (Object.keys(SECTION_LABEL) as CompletenessSection[]).map((section) => {
    const own = items.filter((item) => item.section === section)
    return { section, label: SECTION_LABEL[section], filled: own.filter((item) => item.filled).length, total: own.length }
  })
  return { percent: Math.round((100 * filled) / items.length), filled, total: items.length, missing, groups }
}

/** Screening details not recorded yet (listed, never counted in the percentage). */
export function screeningMissing(form: CandidateEntryForm): string[] {
  return [
    !effectiveNativePlace(form) && 'Native place',
    !form.familyNumber.trim() && 'Family member number',
    !form.designation.trim() && 'Job preference',
    !form.countryPreference.trim() && 'Country looking for',
    !form.jobDurationPreference.trim() && 'Job duration',
    !form.bestTimeToContact.trim() && 'Best time to contact',
    !form.interestLevel && 'Interest level',
    form.serviceChargesStatus === 'not_discussed' && 'Service charges',
  ].filter(Boolean) as string[]
}

/** What to add next: the passport when only the resume exists, and the other way round. */
export function nextDocument(documents: EntryDocuments): 'passport' | 'resume' | 'photo' | null {
  if (documents.resume && !documents.passport) return 'passport'
  if (!documents.resume && documents.passport) return 'resume'
  if (documents.resume && documents.passport && !documents.photo) return 'photo'
  return null
}
