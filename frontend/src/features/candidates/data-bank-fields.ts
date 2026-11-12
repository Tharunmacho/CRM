/**
 * Every candidate field the list API returns, in one registry: its heading, the group it belongs to in the Columns menu, a
 * minimum width and how its value is written. The Data Bank's optional columns, its expandable row and the Columns menu all
 * read this list, so a heading cannot differ between them (the Lead sheet does the same with lead-fields.ts).
 *
 * Pure (no React, no store): the few values that need the store (recruiter name, stage, order, option labels) arrive through
 * `BankContext`, so this file is unit-tested on its own. Education and work history are NOT here as values: they are deferred
 * JSONB columns the list never loads, so the expanded row points to the profile for them.
 */
import type { Candidate } from '@/shared/model/types'
import { documentsOnFile } from './data-bank-documents'

export const BANK_GROUPS = ['Person', 'Contact', 'Passport', 'Job & experience', 'Screening', 'Workflow'] as const
export type BankGroup = (typeof BANK_GROUPS)[number]

/** What an empty value looks like everywhere. */
export const EMPTY_VALUE = '–'

/** What the list needs from the store to write the few values that are not on the candidate itself. */
export interface BankContext {
  ownerName: (ownerId: string | null | undefined) => string
  /** "Agency" text (empty when none was recorded). */
  agent: (candidate: Candidate) => string
  statusLabel: (candidate: Candidate) => string
  sourceLabel: (source: string, detail?: string | null) => string
  /** The admin's label for a stored form-option value (the value itself when unknown). */
  optionLabel: (category: string, value: string) => string
  /** The active or latest deployed application's stage and order; null when no placement is recorded. */
  application: (candidateId: string) => { stage: string; order: string } | null
}

export interface BankFieldDef {
  id: string
  label: string
  group: BankGroup
  width: number
  /** Server sort key, when the API can sort on this field. */
  sortKey?: string
  /** False for fields the list does not carry (education, work history): shown in the expanded row as a pointer to the profile. */
  inColumns: boolean
  value: (candidate: Candidate, context: BankContext) => string
}

const text = (value: unknown): string => (value === null || value === undefined ? '' : String(value).trim())
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "05 Jan 1990". A date-only value ("1990-01-05") is read as that calendar day, never shifted by the time zone. */
export function dayLabel(value?: string | null): string {
  const raw = text(value)
  if (!raw) return ''
  const plain = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw)
  if (plain) return `${plain[3]} ${MONTHS[Number(plain[2]) - 1] ?? plain[2]} ${plain[1]}`
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return ''
  return `${String(date.getDate()).padStart(2, '0')} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`
}

/** "3 yrs" / "1 yr"; empty for 0 (a profile that never said). */
export function yearsText(value?: number | null): string {
  const years = Number(value)
  if (!Number.isFinite(years) || years <= 0) return ''
  return `${years} ${years === 1 ? 'yr' : 'yrs'}`
}

/** "male" becomes "Male"; an already capitalised value is left alone. */
export const sentence = (value: string): string => (value ? value.charAt(0).toUpperCase() + value.slice(1) : value)
const list = (values?: readonly unknown[] | null): string => (values ?? []).map(text).filter(Boolean).join(', ')

export const BANK_FIELD_DEFS: readonly BankFieldDef[] = [
  { id: 'name', label: 'Full name', group: 'Person', width: 140, sortKey: 'name', inColumns: true, value: (c) => text(c.name) },
  { id: 'ref', label: 'Reference', group: 'Person', width: 110, inColumns: true, value: (c) => text(c.ref) },
  { id: 'dob', label: 'Date of birth', group: 'Person', width: 110, inColumns: true, value: (c) => dayLabel(c.dob) },
  { id: 'gender', label: 'Gender', group: 'Person', width: 90, inColumns: true, value: (c) => sentence(text(c.gender)) },
  { id: 'nationality', label: 'Nationality', group: 'Person', width: 110, inColumns: true, value: (c) => text(c.nationality) },
  { id: 'maritalStatus', label: 'Marital status', group: 'Person', width: 110, inColumns: true, value: (c, x) => (c.maritalStatus ? x.optionLabel('marital_status', c.maritalStatus) : '') },
  { id: 'fatherName', label: "Father's name", group: 'Person', width: 140, inColumns: true, value: (c) => text(c.fatherName) },
  { id: 'languages', label: 'Languages', group: 'Person', width: 130, inColumns: true, value: (c) => list(c.languages) },

  { id: 'phone', label: 'Phone number', group: 'Contact', width: 150, sortKey: 'phone', inColumns: true, value: (c) => text(c.phone) },
  { id: 'whatsapp', label: 'WhatsApp number', group: 'Contact', width: 150, inColumns: true, value: (c) => text(c.whatsapp) },
  { id: 'email', label: 'Email', group: 'Contact', width: 180, inColumns: true, value: (c) => text(c.email) },
  { id: 'currentCity', label: 'Current city / location', group: 'Contact', width: 130, inColumns: true, value: (c) => text(c.currentCity) },
  { id: 'nativePlace', label: 'Native place', group: 'Contact', width: 120, inColumns: true, value: (c) => text(c.nativePlace) },
  { id: 'currentAddress', label: 'Current address', group: 'Contact', width: 200, inColumns: true, value: (c) => text(c.currentAddress) },
  { id: 'familyNumber', label: 'Family member number', group: 'Contact', width: 140, inColumns: true, value: (c) => text(c.familyMemberNumber) },

  { id: 'passportNo', label: 'Passport number', group: 'Passport', width: 120, sortKey: 'passport', inColumns: true, value: (c) => text(c.passportNo) },
  { id: 'passportIssue', label: 'Passport issue date', group: 'Passport', width: 110, inColumns: true, value: (c) => dayLabel(c.passportIssue) },
  { id: 'passportExpiry', label: 'Passport expiry date', group: 'Passport', width: 110, inColumns: true, value: (c) => dayLabel(c.passportExpiry) },
  { id: 'passportPlace', label: 'Passport place of issue', group: 'Passport', width: 130, inColumns: true, value: (c) => text(c.passportPlace) },

  { id: 'designations', label: 'Job preferred', group: 'Job & experience', width: 150, sortKey: 'designation', inColumns: true, value: (c) => list(c.designations) },
  { id: 'skills', label: 'Skills', group: 'Job & experience', width: 170, inColumns: true, value: (c) => list(c.skills) },
  { id: 'totalExperience', label: 'Total experience', group: 'Job & experience', width: 100, sortKey: 'experience', inColumns: true, value: (c) => yearsText(c.totalExperience) },
  { id: 'abroadExperience', label: 'Experience abroad', group: 'Job & experience', width: 110, inColumns: true, value: (c) => yearsText(c.gulfExperience) },
  { id: 'indiaExperience', label: 'Experience in India', group: 'Job & experience', width: 110, inColumns: true, value: (c) => yearsText(c.experienceIndia) },
  { id: 'expectedSalary', label: 'Expected salary', group: 'Job & experience', width: 110, inColumns: true, value: (c) => (c.expectedSalary ? Number(c.expectedSalary).toLocaleString('en-IN') : '') },
  { id: 'education', label: 'Education', group: 'Job & experience', width: 0, inColumns: false, value: () => '' },
  { id: 'workHistory', label: 'Work history', group: 'Job & experience', width: 0, inColumns: false, value: () => '' },

  { id: 'countryPreference', label: 'Preferred country', group: 'Screening', width: 130, sortKey: 'country', inColumns: true, value: (c) => text(c.countryPreference) },
  { id: 'serviceCharges', label: 'Service charges', group: 'Screening', width: 110, sortKey: 'service', inColumns: true, value: (c, x) => (c.serviceChargesStatus ? x.optionLabel('service_charges_status', c.serviceChargesStatus) : '') },
  { id: 'interestLevel', label: 'Interest level', group: 'Screening', width: 110, sortKey: 'interest', inColumns: true, value: (c, x) => (c.interestLevel ? x.optionLabel('interest_level', c.interestLevel) : '') },
  { id: 'bestTime', label: 'Best time to contact', group: 'Screening', width: 130, inColumns: true, value: (c, x) => (c.bestTimeToContact ? x.optionLabel('best_time_to_contact', c.bestTimeToContact) : '') },
  { id: 'jobDuration', label: 'Job duration preference', group: 'Screening', width: 130, inColumns: true, value: (c, x) => (c.jobDurationPreference ? x.optionLabel('job_duration_preference', c.jobDurationPreference) : '') },
  { id: 'followUp', label: 'Next follow-up', group: 'Screening', width: 110, inColumns: true, value: (c) => dayLabel(c.nextFollowUpDate) },
  { id: 'callOutcome', label: 'Call outcome', group: 'Screening', width: 140, inColumns: true, value: (c) => callOutcomeText(c) },

  { id: 'added', label: 'Added on', group: 'Workflow', width: 110, sortKey: 'added', inColumns: true, value: (c) => dayLabel(c.addedAt) },
  { id: 'owner', label: 'Recruiter', group: 'Workflow', width: 110, sortKey: 'owner', inColumns: true, value: (c, x) => (c.ownerId ? x.ownerName(c.ownerId) : '') },
  { id: 'agent', label: 'Agency', group: 'Workflow', width: 130, inColumns: true, value: (c, x) => x.agent(c) },
  { id: 'source', label: 'Source', group: 'Workflow', width: 90, sortKey: 'source', inColumns: true, value: (c, x) => x.sourceLabel(c.source, c.sourceDetail) },
  { id: 'stage', label: 'Stage', group: 'Workflow', width: 120, sortKey: 'stage', inColumns: true, value: (c, x) => x.application(c.id)?.stage ?? '' },
  { id: 'order', label: 'Order', group: 'Workflow', width: 110, sortKey: 'order', inColumns: true, value: (c, x) => x.application(c.id)?.order ?? '' },
  { id: 'status', label: 'Status', group: 'Workflow', width: 110, sortKey: 'status', inColumns: true, value: (c, x) => x.statusLabel(c) },
  { id: 'documents', label: 'Documents on file', group: 'Workflow', width: 160, inColumns: true, value: (c) => documentsOnFile(c.documents).join(', ') },
]

function callOutcomeText(candidate: Candidate): string {
  const outcome = candidate.callOutcome
  if (!outcome) return ''
  const answer = outcome.interested === 'yes' ? 'Interested' : outcome.interested === 'no' ? 'Not interested' : 'Unreachable'
  return outcome.called ? answer : `Not called yet (${answer.toLowerCase()})`
}

export const FIELD_PREFIX = 'f:'
export const bankFieldDef = (id: string) => BANK_FIELD_DEFS.find((field) => field.id === id)

/**
 * Fields that already have a column of their own in the default table (the Columns menu lists them once, under that column;
 * several fields share one cell: name over date of birth, phone with WhatsApp over e-mail).
 */
export const DEFAULT_COLUMN_FIELDS: ReadonlySet<string> = new Set([
  'name', 'dob', 'phone', 'whatsapp', 'email', 'passportNo', 'designations', 'totalExperience', 'countryPreference', 'serviceCharges',
  'interestLevel', 'added', 'owner', 'agent', 'source', 'status',
])

export interface BankFieldRow { key: string; label: string; value: string; large: boolean }

/** Every field of one candidate with its heading, for the expandable row. Fields with no answer stay listed so two rows compare line by line. */
export function bankFieldRows(candidate: Candidate, context: BankContext): BankFieldRow[] {
  return BANK_FIELD_DEFS.map((field) => ({ key: field.id, label: field.label, value: field.inColumns ? field.value(candidate, context) : '', large: !field.inColumns }))
}
