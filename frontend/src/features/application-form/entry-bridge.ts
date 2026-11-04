/**
 * The Add candidate page keeps its form as flat strings (entry-model.ts). This file bridges that form to the
 * application form: the repeating rows (education, work history) live in it as JSON text, and a read-only copy of the
 * paper form can be built from whatever has been entered so far, before the candidate exists.
 *
 * Pure: no React, no network. Tested in tests/application-form.test.mjs.
 */
import { normalizeForm } from './form-model'
import type { ApplicationFormData } from './types'

export interface EntryRow { [key: string]: string }

export const EDUCATION_KEYS = ['level', 'passedYear', 'institute'] as const
export const HISTORY_KEYS = ['company', 'designation', 'country', 'period'] as const

const text = (value: unknown): string => (value == null ? '' : String(value))

/** Read the JSON the form stores for a repeating list. Anything unreadable is an empty list, never an exception. */
export function parseRows(json: string | undefined, keys: readonly string[]): EntryRow[] {
  if (!json || !json.trim()) return []
  let parsed: unknown
  try { parsed = JSON.parse(json) } catch { return [] }
  if (!Array.isArray(parsed)) return []
  return parsed
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => Object.fromEntries(keys.map((key) => [key, text(item[key])])))
}

const blank = (row: EntryRow) => Object.values(row).every((value) => !value.trim())

/** The text kept in the form for a list; empty when every row is blank, so an untouched list stays untouched. */
export function serializeRows(rows: EntryRow[]): string {
  const kept = rows.filter((row) => !blank(row))
  return kept.length ? JSON.stringify(rows) : ''
}

export const emptyEntryRow = (keys: readonly string[]): EntryRow => Object.fromEntries(keys.map((key) => [key, '']))

/** The rows as the candidate-create endpoint wants them: no blank rows, the passed year as a number. */
export function educationPayload(json: string | undefined) {
  return parseRows(json, EDUCATION_KEYS)
    .filter((row) => !blank(row))
    .map((row) => {
      const year = Number(row.passedYear)
      return { level: row.level.trim() || null, passedYear: /^\d{4}$/.test(row.passedYear.trim()) && Number.isFinite(year) ? year : null, institute: row.institute.trim() || null }
    })
}

export function historyPayload(json: string | undefined) {
  return parseRows(json, HISTORY_KEYS)
    .filter((row) => !blank(row))
    .map((row) => ({ company: row.company.trim() || null, designation: row.designation.trim() || null, country: row.country.trim() || null, period: row.period.trim() || null }))
}

/** Add-candidate field -> the application form's dotted path. */
export const ENTRY_TO_FORM_PATH: Record<string, string> = {
  name: 'personal.candidateName', fatherName: 'personal.fatherName', dob: 'personal.dateOfBirth', maritalStatus: 'personal.maritalStatus',
  gender: 'personal.gender', nationality: 'personal.nationality', languages: 'personal.languagesKnown', phone: 'personal.mobileNumber',
  whatsapp: 'personal.whatsapp', email: 'personal.email', currentAddress: 'personal.currentAddress',
  passportNo: 'passport.passportNumber', passportPlace: 'passport.placeOfIssue', passportIssue: 'passport.issueDate', passportExpiry: 'passport.expiryDate',
  totalExperience: 'experience.totalExperience', experienceIndia: 'experience.experienceIndia', gulfExperience: 'experience.experienceAbroad',
  education: 'education', workHistory: 'experience.history',
}

export interface EntryDocument { kind: string; filename: string; url: string; sizeKb: number }

/**
 * A read-only application form built from what has been entered so far.
 * `sources` says which document filled which add-candidate field; `edited` holds the fields a person typed.
 */
export function previewFormFromEntry(
  entry: Record<string, string>,
  options: {
    documents: EntryDocument[]
    sources: Partial<Record<string, 'resume' | 'passport' | 'aadhaar'>>
    edited: ReadonlySet<string>
    source?: string
  },
): ApplicationFormData {
  const fieldSources: Record<string, string> = {}
  const filledBy: Record<string, string[]> = { resume: [], passport: [] }
  for (const [key, path] of Object.entries(ENTRY_TO_FORM_PATH)) {
    if (options.edited.has(key)) fieldSources[path] = 'manual'
    else if (options.sources[key]) {
      fieldSources[path] = 'extracted'
      const kind = options.sources[key] === 'passport' ? 'passport' : 'resume'
      filledBy[kind].push(path)
    }
  }
  const slot = (kind: 'resume' | 'passport' | 'photo') => {
    const file = options.documents.find((item) => item.kind === kind)
    return {
      uploaded: Boolean(file),
      current: file ? { attachmentId: `entry-${kind}`, fileName: file.filename, sizeKb: file.sizeKb, version: 1, url: file.url, uploadedAt: '' } : null,
      extraction: file && kind !== 'photo' && filledBy[kind].length ? { status: 'done', filled: filledBy[kind] } : null,
    }
  }
  return normalizeForm({
    candidate: { name: entry.name, ref: '', canEdit: false },
    personal: {
      candidateName: entry.name, fatherName: entry.fatherName, dateOfBirth: entry.dob, maritalStatus: entry.maritalStatus, gender: entry.gender,
      nationality: entry.nationality, languagesKnown: (entry.languages || '').split(',').map((item) => item.trim()).filter(Boolean),
      mobileNumber: entry.phone, currentAddress: entry.currentAddress, whatsapp: entry.whatsapp, email: entry.email,
    },
    passport: { passportNumber: entry.passportNo, placeOfIssue: entry.passportPlace, issueDate: entry.passportIssue, expiryDate: entry.passportExpiry },
    education: educationPayload(entry.education),
    experience: { totalExperience: entry.totalExperience, experienceIndia: entry.experienceIndia, experienceAbroad: entry.gulfExperience, history: historyPayload(entry.workHistory) },
    documents: { resume: slot('resume'), passport: slot('passport'), photo: slot('photo') },
    recruitment: { source: options.source ?? '' },
    fieldSources,
  })
}
