import type { OcrScan } from '@/shared/api/client'
import { hasOcrExtraction, type DocumentScans, type ScanKind } from './ocr-form'
import type { CandidateEntryField, CandidateEntryForm } from './entry-model'

/** Plain names for the fields a document can fill in, so the reviewer sees what to double-check. */
export const ENTRY_FIELD_LABELS: Partial<Record<CandidateEntryField, string>> = {
  name: 'Full name', phone: 'Phone', whatsapp: 'WhatsApp', email: 'Email', dob: 'Date of birth', gender: 'Gender', nationality: 'Nationality',
  currentCity: 'Current city', languages: 'Languages', passportNo: 'Passport number', passportIssue: 'Issue date', passportExpiry: 'Expiry date',
  passportPlace: 'Place of issue', designation: 'Job titles', totalExperience: 'Total experience', gulfExperience: 'Gulf experience', skills: 'Skills',
}

export const SCAN_KIND_LABEL: Record<ScanKind, string> = { resume: 'resume', passport: 'passport', aadhaar: 'Aadhaar' }

const scanText = (value: unknown) => Array.isArray(value) ? value.join('') : value == null ? '' : String(value)

/** Names of the fields one document filled in that the recruiter has not changed. */
export function appliedFieldLabels(scan: OcrScan, edited: ReadonlySet<CandidateEntryField>): string[] {
  if (!hasOcrExtraction(scan)) return []
  return Object.entries(scan.fields).flatMap(([key, value]) => {
    const label = ENTRY_FIELD_LABELS[key as CandidateEntryField]
    return label && scanText(value).trim() && !edited.has(key as CandidateEntryField) ? [label] : []
  })
}

/**
 * Which document supplied each untouched field. The order matches mergeOcrForm (passport wins over Aadhaar over resume),
 * so the marker beside a field names the file whose value is really in the box.
 */
export function fieldSources(scans: DocumentScans, edited: ReadonlySet<CandidateEntryField>): Partial<Record<CandidateEntryField, ScanKind>> {
  const sources: Partial<Record<CandidateEntryField, ScanKind>> = {}
  for (const kind of ['resume', 'aadhaar', 'passport'] as const) {
    const scan = scans[kind]
    if (!hasOcrExtraction(scan)) continue
    for (const [key, value] of Object.entries(scan.fields)) {
      if (!ENTRY_FIELD_LABELS[key as CandidateEntryField] || edited.has(key as CandidateEntryField) || !scanText(value).trim()) continue
      sources[key as CandidateEntryField] = kind
    }
  }
  return sources
}

/**
 * What the live duplicate check sends, or null while there is nothing worth checking yet.
 * The server matches on phone, email and passport number only; name and phone are filler the schema insists on.
 */
export function liveDuplicateQuery(form: CandidateEntryForm) {
  const phoneDigits = form.phone.normalize('NFKC').replace(/\D/g, '')
  const email = form.email.trim()
  const passport = form.passportNo.replace(/\s/g, '')
  const usablePhone = phoneDigits.length >= 7
  const usableEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
  const usablePassport = passport.length >= 6
  if (!usablePhone && !usableEmail && !usablePassport) return null
  return {
    name: form.name.trim() || 'New candidate',
    phone: usablePhone ? form.phone.trim() : '0',
    email: usableEmail ? email : undefined,
    passport_no: usablePassport ? form.passportNo.trim() : undefined,
    dob: form.dob || undefined,
  }
}

/** The existing candidate the server found for the typed phone, email or passport number. */
export type DuplicateMatch = { id: string; ref: string; name: string; phone: string; ownerId: string | null; ownerName: string | null; matchedOn: string[] }
