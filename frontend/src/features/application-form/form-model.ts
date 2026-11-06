/**
 * Pure model of the application form: the field list (in the order of the paper form), missing-field detection,
 * completeness, tracking rows, date and age formatting, validation and repeating-row editing.
 *
 * Nothing here touches React or the network, so it is unit-tested directly (tests/application-form.test.mjs).
 * The API payload (docs/workflows/application-form-contract.md) is read through `normalizeForm`, which tolerates
 * absent parts: an application with no order, a candidate with no passport yet, or an older server.
 */
import type {
  ApplicationFormData,
  ApplicationRef,
  DocumentKind,
  DocumentSlot,
  EducationRow,
  Extraction,
  ExtractionStatusCode,
  ExperienceRow,
  FieldSource,
  FormDocument,
  FormDraft,
  MissingItem,
  SourceDocument,
  TrackingRow,
  TrackingStatus,
  TrackingSummary,
} from './types'

/* ------------------------------------------------------------------ Field list */

export interface FieldOption { value: string; label: string }

export interface FieldDef {
  /** Dotted path into the form, also the key of `fieldSources` and of the missing list. */
  path: string
  label: string
  section: number
  kind: 'text' | 'date' | 'select' | 'tel' | 'languages' | 'textarea' | 'years' | 'email' | 'list'
  /** Highlighted as Missing when empty. */
  required: boolean
  /** Counted in completeness (the server counts exactly these). */
  counted: boolean
  /** Where a value usually comes from, to word the provenance badge when the server cannot say. */
  from?: SourceDocument
  options?: FieldOption[]
  /** A select whose choices are the admin-managed form options of this category (features/form-options), not a built-in list. */
  category?: string
}

export const SECTION_TITLES: Record<number, string> = {
  1: 'Personal details',
  2: 'Passport details',
  3: 'Educational details',
  4: 'Previous experience',
  5: 'Documents',
  6: 'Client & recruitment details',
  7: 'Recruitment process tracking',
  8: 'Assessment & preferences',
  9: 'Follow-up & notes',
}


const field = (def: Omit<FieldDef, 'counted'> & { counted?: boolean }): FieldDef => ({ ...def, counted: def.counted ?? def.required })

/** Section 1, in the paper form's order (WhatsApp and e-mail are extras, placed last so the pairs stay aligned). */
export const PERSONAL_FIELDS: FieldDef[] = [
  field({ path: 'personal.candidateName', label: 'Candidate Name', section: 1, kind: 'text', required: true, from: 'resume' }),
  field({ path: 'personal.fatherName', label: "Father's Name", section: 1, kind: 'text', required: true, from: 'passport' }),
  field({ path: 'personal.dateOfBirth', label: 'Date of Birth', section: 1, kind: 'date', required: true, from: 'passport' }),
  field({ path: 'personal.maritalStatus', label: 'Marital Status', section: 1, kind: 'select', required: true, from: 'resume', category: 'marital_status' }),
  field({ path: 'personal.gender', label: 'Gender', section: 1, kind: 'select', required: true, from: 'passport', category: 'gender' }),
  field({ path: 'personal.nationality', label: 'Nationality', section: 1, kind: 'text', required: true, from: 'passport' }),
  field({ path: 'personal.languagesKnown', label: 'Language Known', section: 1, kind: 'languages', required: true, from: 'resume' }),
  field({ path: 'personal.mobileNumber', label: 'Mobile Number', section: 1, kind: 'tel', required: true, from: 'resume' }),
  field({ path: 'personal.whatsapp', label: 'WhatsApp Number', section: 1, kind: 'tel', required: false, from: 'resume' }),
  field({ path: 'personal.email', label: 'E-Mail', section: 1, kind: 'email', required: false, from: 'resume' }),
  field({ path: 'personal.currentAddress', label: 'Current Address', section: 1, kind: 'textarea', required: true, from: 'resume' }),
]

export const PASSPORT_FIELDS: FieldDef[] = [
  field({ path: 'passport.passportNumber', label: 'Passport Number', section: 2, kind: 'text', required: true, from: 'passport' }),
  field({ path: 'passport.placeOfIssue', label: 'Place of Issue', section: 2, kind: 'text', required: true, from: 'passport' }),
  field({ path: 'passport.issueDate', label: 'Issue Date', section: 2, kind: 'date', required: true, from: 'passport' }),
  field({ path: 'passport.expiryDate', label: 'Expiry Date', section: 2, kind: 'date', required: true, from: 'passport' }),
]

/** Total and the history are counted; India and abroad are on the paper form and highlighted when empty, but not counted. */
export const EXPERIENCE_FIELDS: FieldDef[] = [
  field({ path: 'experience.totalExperience', label: 'Total Experience', section: 4, kind: 'years', required: true, from: 'resume' }),
  field({ path: 'experience.experienceIndia', label: 'Experience in India', section: 4, kind: 'years', required: true, counted: false, from: 'resume' }),
  field({ path: 'experience.experienceAbroad', label: 'Experience Abroad', section: 4, kind: 'years', required: true, counted: false, from: 'resume' }),
]

export const EDUCATION_COLUMNS = [
  { key: 'level', label: 'Educational Level / Course', placeholder: 'e.g. B.Tech Mechanical' },
  { key: 'passedYear', label: 'Passed Year', placeholder: 'YYYY' },
  { key: 'institute', label: 'Institute Name & Address', placeholder: 'Institute, city' },
] as const

export const EXPERIENCE_COLUMNS = [
  { key: 'company', label: 'Company Name', placeholder: 'Company' },
  { key: 'designation', label: 'Designation', placeholder: 'Designation' },
  { key: 'country', label: 'Country', placeholder: 'Country' },
  { key: 'period', label: 'Period / Duration', placeholder: 'e.g. Jan 2018 - Mar 2020' },
] as const

export const RECRUITMENT_FIELDS = [
  { key: 'client', label: 'Client' },
  { key: 'country', label: 'Country' },
  { key: 'position', label: 'Position' },
  { key: 'source', label: 'Source' },
  { key: 'recruiter', label: 'Recruiter' },
] as const

export const ALL_EDITABLE_FIELDS: FieldDef[] = [...PERSONAL_FIELDS, ...PASSPORT_FIELDS, ...EXPERIENCE_FIELDS]

/**
 * The screening details the telemarketer's intake form collects, beyond the paper form (contract section 11).
 * Shown only when the server sends `screening` (`extrasSupported`). Lists (`jobPreference`, `skills`) are kept as
 * comma-separated text in the draft and sent as arrays. `interestLevel` is graded; the call result and the call notes
 * stay what the initial call recorded and are shown read only.
 */
export const EXTRA_FIELDS: FieldDef[] = [
  field({ path: 'screening.nativePlace', label: 'Native place', section: 1, kind: 'text', required: false }),
  field({ path: 'screening.familyMemberNumber', label: 'Family contact no.', section: 1, kind: 'tel', required: false }),
  field({ path: 'screening.jobPreference', label: 'Job preference', section: 8, kind: 'list', required: false }),
  field({ path: 'screening.skills', label: 'Skills', section: 8, kind: 'list', required: false }),
  field({ path: 'screening.countryPreference', label: 'Country looking for', section: 8, kind: 'text', required: false }),
  field({ path: 'screening.jobDurationPreference', label: 'Job duration', section: 8, kind: 'select', required: false, category: 'job_duration_preference' }),
  field({ path: 'screening.bestTimeToContact', label: 'Best time to call', section: 8, kind: 'select', required: false, category: 'best_time_to_contact' }),
  field({ path: 'screening.serviceChargesStatus', label: 'Service charges', section: 8, kind: 'select', required: false, category: 'service_charges_status' }),
  field({ path: 'screening.interestLevel', label: 'Interest level', section: 8, kind: 'text', required: false }),
  field({ path: 'screening.nextFollowUpDate', label: 'Next follow-up date', section: 9, kind: 'date', required: false }),
]

export const DOCUMENT_KINDS: DocumentKind[] = ['resume', 'passport', 'photo']
export const DOCUMENT_LABELS: Record<DocumentKind, string> = { resume: 'Resume', passport: 'Passport', photo: 'Passport-size photo' }

/* ------------------------------------------------------------------ Tracking */

export const TRACKING_STATUS_LABELS: Record<TrackingStatus, string> = {
  not_started: 'Not started',
  in_progress: 'In progress',
  completed: 'Completed',
  failed: 'Failed',
  not_applicable: 'Not tracked',
}

export function trackingStatus(value: unknown): TrackingStatus {
  const text = String(value ?? '').toLowerCase().replace(/[\s-]+/g, '_')
  if (text === 'completed' || text === 'complete' || text === 'done') return 'completed'
  if (text === 'in_progress' || text === 'current') return 'in_progress'
  if (text === 'failed' || text === 'rejected') return 'failed'
  if (text === 'not_applicable' || text === 'na') return 'not_applicable'
  return 'not_started'
}

/** Counts shown above the tracking table. Untracked rows (PCC, Invoice Generated) are not part of the progress. */
export function summariseTracking(rows: TrackingRow[]) {
  const tracked = rows.filter((row) => row.tracked)
  const completed = tracked.filter((row) => row.status === 'completed').length
  return {
    total: tracked.length,
    completed,
    failed: tracked.filter((row) => row.status === 'failed').length,
    inProgress: tracked.filter((row) => row.status === 'in_progress').length,
    percent: tracked.length ? Math.round((completed / tracked.length) * 100) : 0,
    current: rows.find((row) => row.current) ?? null,
  }
}

/** Does this row open the stage's own screen? Only tracked rows tied to a real stage do. */
export const isOpenableRow = (row: TrackingRow): boolean => row.tracked && row.stage > 0

/* ------------------------------------------------------------------ Values */

export const isBlank = (value: unknown): boolean => {
  if (value == null) return true
  if (Array.isArray(value)) return value.every(isBlank)
  if (typeof value === 'string') return value.trim() === ''
  return false
}

const str = (value: unknown): string => (value == null ? '' : typeof value === 'string' ? value : String(value))
const rec = (value: unknown): Record<string, unknown> => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {})
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
const num = (value: unknown, fallback = 0): number => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

let rowCounter = 0
export const newRowId = (): string => `row-${Date.now().toString(36)}-${(rowCounter++).toString(36)}`

export const emptyEducationRow = (): EducationRow => ({ rowId: newRowId(), level: '', passedYear: '', institute: '' })
export const emptyExperienceRow = (): ExperienceRow => ({ rowId: newRowId(), company: '', designation: '', country: '', period: '' })

const years = (value: unknown): string => {
  if (value == null || value === '') return ''
  const number = Number(value)
  return Number.isFinite(number) ? String(Math.round(number * 10) / 10) : str(value)
}

const languageList = (value: unknown): string[] => {
  const items = Array.isArray(value) ? value : typeof value === 'string' ? value.split(/[,;/]/) : []
  const seen = new Set<string>()
  const out: string[] = []
  for (const item of items) {
    const text = str(item).trim()
    const key = text.toLowerCase()
    if (text && !seen.has(key)) { seen.add(key); out.push(text) }
  }
  return out
}

/** `Male` -> `male`: the server stores whatever the document said, the form offers one canonical spelling. */
const genderValue = (value: unknown): string => {
  const text = str(value).trim()
  return /^(male|female|other)$/i.test(text) ? text.toLowerCase() : text
}

/** `male` -> `Male`; anything else is shown as stored. */
export function optionLabel(options: FieldOption[] | undefined, value: string): string {
  const found = options?.find((option) => option.value.toLowerCase() === value.toLowerCase())
  return found ? found.label : value
}

/* ------------------------------------------------------------------ Normalising the payload */

const EXTRACTION_STATES: ExtractionStatusCode[] = ['processing', 'done', 'failed', 'unreadable', 'needs_review']

function normaliseDocument(raw: unknown): FormDocument | null {
  const value = rec(raw)
  if (!value.url && !value.attachmentId) return null
  const by = value.uploadedBy
  return {
    attachmentId: str(value.attachmentId ?? value.id),
    fileName: str(value.fileName ?? value.name),
    sizeKb: num(value.sizeKb),
    version: num(value.version, 1),
    url: str(value.url),
    uploadedAt: str(value.uploadedAt),
    uploadedBy: typeof by === 'string' ? by : str(rec(by).name),
  }
}

function normaliseExtraction(raw: unknown): Extraction | null {
  if (raw == null) return null
  const value = rec(raw)
  const status = str(value.status).toLowerCase() as ExtractionStatusCode
  if (!EXTRACTION_STATES.includes(status)) return null
  return {
    status,
    message: str(value.message),
    filled: list(value.filled).map(str),
    kept: list(value.kept).map(str),
    kindsFound: list(value.kindsFound).map(str),
    updatedAt: str(value.updatedAt),
  }
}

function normaliseSlot(slot: DocumentKind, raw: unknown): DocumentSlot {
  const value = rec(raw)
  const current = normaliseDocument(value.current)
  return {
    slot,
    label: str(value.label) || DOCUMENT_LABELS[slot],
    uploaded: value.uploaded === true || current !== null,
    current,
    versions: num(value.versions, current ? 1 : 0),
    extraction: normaliseExtraction(value.extraction),
  }
}

const personName = (raw: unknown): string => (typeof raw === 'string' ? raw : str(rec(raw).name))

function normaliseTracking(raw: unknown, application: ApplicationRef | null): { summary: TrackingSummary; rows: TrackingRow[] } {
  const root = rec(raw)
  const summary = rec(root.summary)
  const currentStage = num(summary.currentStage)
  const open = !application || application.status === 'active'
  const rows = list(root.rows).map((item, index): TrackingRow => {
    const value = rec(item)
    const stage = num(value.stage)
    const status = trackingStatus(value.status)
    return {
      key: str(value.key) || `row-${index}`,
      label: str(value.label),
      systemLabel: str(value.systemLabel),
      stage,
      extra: value.extra === true,
      tracked: value.tracked !== false,
      status,
      date: str(value.date),
      completedBy: personName(value.completedBy),
      outcome: str(value.outcome),
      attempt: num(value.attempt, 1),
      note: str(value.note),
      current: open && stage > 0 && stage === currentStage && status === 'in_progress',
    }
  })
  return {
    summary: {
      completed: num(summary.completed),
      tracked: num(summary.tracked, rows.filter((row) => row.tracked).length),
      percent: num(summary.percent),
      currentStage,
      currentStageLabel: str(summary.currentStageLabel),
      applicationStatus: str(summary.applicationStatus) || 'none',
      closureLabel: str(summary.closureLabel),
    },
    rows,
  }
}

function normaliseApplication(raw: unknown): ApplicationRef | null {
  if (!raw || typeof raw !== 'object') return null
  const value = rec(raw)
  return {
    id: str(value.id),
    orderId: str(value.orderId),
    orderRef: str(value.orderRef),
    clientName: str(value.clientName),
    position: str(value.position),
    country: str(value.country),
    status: str(value.status),
    closureReason: str(value.closureReason),
    currentStage: num(value.currentStage),
    selected: value.selected === true,
  }
}

/**
 * "XXXX XXXX 1234" from whatever the server sent. Only the last four digits are ever kept, so even a server that sent the whole
 * number could not make the form show it. Empty when there are fewer than four digits.
 */
export function maskedAadhaar(value: unknown): string {
  const digits = String(value ?? '').replace(/\D/g, '')
  return digits.length >= 4 ? `XXXX XXXX ${digits.slice(-4)}` : ''
}

/** Turn whatever the server sent (possibly little of it) into a complete, well-typed form. */
export function normalizeForm(raw: unknown): ApplicationFormData {
  const root = rec(raw)
  const personal = rec(root.personal)
  const passport = rec(root.passport)
  const expiry = rec(passport.expiry)
  const experience = rec(root.experience)
  const recruitment = rec(root.recruitment)
  const documents = rec(root.documents)
  const candidate = rec(root.candidate)
  const header = rec(root.header)
  const application = normaliseApplication(root.application)

  const fieldSources: Record<string, FieldSource> = {}
  for (const [key, value] of Object.entries(rec(root.fieldSources))) {
    if (value === 'extracted' || value === 'manual') fieldSources[key] = value
  }

  const form: ApplicationFormData = {
    header: {
      company: str(header.company) || 'Careindia Overseas Pvt Ltd',
      title: str(header.title) || 'JOB APPLICATION FORM',
      address: str(header.address) || '743/Dhanalakshmi Apartments, 1st Avenue, near reliance digital, Ashok Nagar, Chennai - 600083.',
      phone: str(header.phone) || '044 - 24711234',
      email: str(header.email) || 'info@careindiaoverseas.com',
    },
    candidate: {
      id: str(candidate.id), ref: str(candidate.ref), name: str(candidate.name), status: str(candidate.status),
      needsReview: candidate.needsReview === true, ownerId: str(candidate.ownerId), ownerName: str(candidate.ownerName),
      updatedAt: str(candidate.updatedAt), canEdit: candidate.canEdit === true,
    },
    application,
    applications: list(root.applications).map(normaliseApplication).filter((item): item is ApplicationRef => item !== null),
    personal: {
      candidateName: str(personal.candidateName), fatherName: str(personal.fatherName),
      dateOfBirth: str(personal.dateOfBirth).slice(0, 10), maritalStatus: str(personal.maritalStatus),
      gender: genderValue(personal.gender), nationality: str(personal.nationality),
      languagesKnown: languageList(personal.languagesKnown), mobileNumber: str(personal.mobileNumber),
      currentAddress: str(personal.currentAddress), whatsapp: str(personal.whatsapp), email: str(personal.email),
    },
    passport: {
      passportNumber: str(passport.passportNumber), placeOfIssue: str(passport.placeOfIssue),
      issueDate: str(passport.issueDate).slice(0, 10), expiryDate: str(passport.expiryDate).slice(0, 10),
      expiry: { status: (str(expiry.status) || 'unknown') as ApplicationFormData['passport']['expiry']['status'], daysLeft: expiry.daysLeft == null ? null : num(expiry.daysLeft) },
    },
    education: list(root.education).map((item) => {
      const row = rec(item)
      return { rowId: newRowId(), level: str(row.level), passedYear: str(row.passedYear), institute: str(row.institute) }
    }),
    experience: {
      totalExperience: years(experience.totalExperience), experienceIndia: years(experience.experienceIndia), experienceAbroad: years(experience.experienceAbroad),
      history: list(experience.history).map((item) => {
        const row = rec(item)
        return { rowId: newRowId(), company: str(row.company), designation: str(row.designation), country: str(row.country), period: str(row.period) }
      }),
    },
    documents: {
      resume: normaliseSlot('resume', documents.resume),
      passport: normaliseSlot('passport', documents.passport),
      photo: normaliseSlot('photo', documents.photo ?? (root.photo ? { uploaded: true, current: root.photo } : null)),
    },
    recruitment: {
      client: str(recruitment.client), clientContact: str(recruitment.clientContact), country: str(recruitment.country),
      position: str(recruitment.position), orderRef: str(recruitment.orderRef), source: str(recruitment.source),
      sourceDetail: str(recruitment.sourceDetail), recruiter: personName(recruitment.recruiter),
    },
    tracking: normaliseTracking(root.tracking, application),
    extras: readExtras(root),
    extrasSupported: 'screening' in root,
    call: readCall(root),
    completeness: { percent: 0, filled: 0, total: 0, missing: [] },
    fieldSources,
    warnings: list(root.warnings).map(str).filter(Boolean),
    processing: root.processing === true,
    aadhaarMasked: maskedAadhaar(rec(root.aadhaar).maskedNumber),
  }

  const serverCompleteness = rec(root.completeness)
  form.completeness = {
    percent: Math.max(0, Math.min(100, Math.round(num(serverCompleteness.percent)))),
    filled: num(serverCompleteness.filled),
    total: num(serverCompleteness.total),
    missing: list(serverCompleteness.missing).map((item): MissingItem | null => {
      const value = rec(item)
      const key = str(value.key)
      if (!key) return null
      const sectionName = str(value.section)
      return {
        key,
        label: str(value.label) || labelForPath(key),
        section: SECTION_BY_NAME[sectionName] ?? sectionForPath(key),
        kind: value.kind === 'document' || key.startsWith('documents.') ? 'document' : 'field',
        message: str(value.message) || str(value.label) || labelForPath(key),
      }
    }).filter((item): item is MissingItem => item !== null),
  }
  if (!('completeness' in root)) {
    const computed = completenessOf(toDraft(form), form.documents)
    form.completeness = { percent: computed.percent, filled: computed.filled, total: computed.total, missing: computed.missing }
  }
  return form
}

/** The screening values the server sent, flattened to dotted paths. Lists become comma-separated text. */
function readExtras(root: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const def of EXTRA_FIELDS) {
    const [section, key] = def.path.split('.')
    const value = rec(root[section])[key]
    out[def.path] = def.kind === 'list' ? languageList(value).join(', ') : def.kind === 'date' ? str(value).slice(0, 10) : str(value)
  }
  return out
}

/** The initial-call result (read only here): written by the call action, not by this form. */
function readCall(root: Record<string, unknown>): ApplicationFormData['call'] {
  const call = rec(rec(root.screening).callOutcome)
  if (!Object.keys(call).length) return null
  return { called: call.called === true, interested: str(call.interested), remarks: str(call.remarks), recordedAt: str(call.recordedAt) }
}

const SECTION_BY_NAME: Record<string, number> = { personal: 1, passport: 2, education: 3, experience: 4, documents: 5 }

export function toDraft(form: (FormDraft | Pick<ApplicationFormData, 'personal' | 'passport' | 'education' | 'experience'>) & { extras?: Record<string, string> }): FormDraft {
  const { expiry: _expiry, ...passport } = form.passport as FormDraft['passport'] & { expiry?: unknown }
  return {
    personal: { ...form.personal, languagesKnown: [...form.personal.languagesKnown] },
    passport,
    education: form.education.map((row) => ({ ...row })),
    experience: { ...form.experience, history: form.experience.history.map((row) => ({ ...row })) },
    extras: { ...(form.extras ?? {}) },
  }
}

/* ------------------------------------------------------------------ Paths */

export function labelForPath(path: string): string {
  const found = ALL_EDITABLE_FIELDS.find((item) => item.path === path)
  if (found) return found.label
  if (path.startsWith('documents.')) return DOCUMENT_LABELS[path.slice(10) as DocumentKind] ?? path
  if (path === 'education') return 'Educational details'
  if (path === 'experience.history') return 'Previous experience'
  return path
}

export function sectionForPath(path: string): number {
  const found = ALL_EDITABLE_FIELDS.find((item) => item.path === path)
  if (found) return found.section
  if (path.startsWith('documents.')) return 5
  if (path.startsWith('education')) return 3
  if (path.startsWith('experience')) return 4
  if (path.startsWith('recruitment')) return 6
  if (path.startsWith('tracking')) return 7
  return 1
}

export function valueAt(source: FormDraft, path: string): unknown {
  if (source.extras && Object.hasOwn(source.extras, path)) return source.extras[path]
  const [section, key] = path.split('.')
  return rec((source as unknown as Record<string, unknown>)[section])[key]
}

/* ------------------------------------------------------------------ Missing / completeness */

export interface Completeness { percent: number; filled: number; total: number; missing: MissingItem[] }

/**
 * Completeness of the form as it stands (the draft, while editing), by the server's own rule: 19 counted items, the
 * nine personal fields, four passport fields, education, total experience, work history and the three documents.
 */
export function completenessOf(draft: FormDraft, documents?: Record<DocumentKind, Pick<DocumentSlot, 'uploaded'>>): Completeness {
  const missing: MissingItem[] = []
  let total = 0
  if (documents) {
    for (const kind of DOCUMENT_KINDS) {
      total += 1
      if (!documents[kind]?.uploaded) {
        missing.push({ key: `documents.${kind}`, label: DOCUMENT_LABELS[kind], section: 5, kind: 'document', message: `${DOCUMENT_LABELS[kind]} not uploaded` })
      }
    }
  }
  for (const def of ALL_EDITABLE_FIELDS) {
    if (!def.counted) continue
    total += 1
    if (isBlank(valueAt(draft, def.path))) missing.push({ key: def.path, label: def.label, section: def.section, kind: 'field', message: `${def.label} is missing` })
  }
  total += 2
  if (!draft.education.some((row) => !isBlankRow(row))) missing.push({ key: 'education', label: 'Educational details', section: 3, kind: 'field', message: 'No educational details' })
  if (!draft.experience.history.some((row) => !isBlankRow(row))) missing.push({ key: 'experience.history', label: 'Previous experience', section: 4, kind: 'field', message: 'No previous experience' })
  missing.sort((a, b) => a.section - b.section)
  const filled = total - missing.length
  return { percent: total ? Math.round((filled / total) * 100) : 0, filled, total, missing }
}

/** Whether a field is empty for display purposes (the amber "Missing" highlight). */
export function fieldIsMissing(draft: FormDraft, path: string): boolean {
  return isBlank(valueAt(draft, path))
}

export function missingBySection(items: MissingItem[]): Map<number, MissingItem[]> {
  const out = new Map<number, MissingItem[]>()
  for (const item of items) out.set(item.section, [...(out.get(item.section) ?? []), item])
  return out
}

/* ------------------------------------------------------------------ Provenance */

export type ProvenanceKind = 'resume' | 'passport' | 'manual' | 'extracted' | 'none'

export const PROVENANCE_LABELS: Record<ProvenanceKind, string> = {
  resume: 'Read from resume',
  passport: 'Read from passport',
  extracted: 'Read from document',
  manual: 'Entered manually',
  none: '',
}

/**
 * What to tell the viewer about where a value came from. The server says only 'extracted' or 'manual'; which document
 * an extracted value came from is taken from what each document's last reading reported filling, then from where that
 * field normally comes from. Empty values have no provenance.
 */
export function provenanceOf(
  form: Pick<ApplicationFormData, 'fieldSources' | 'documents'>,
  path: string,
  hasValue: boolean,
  fallback?: SourceDocument,
): ProvenanceKind {
  if (!hasValue) return 'none'
  const source = form.fieldSources[path]
  if (!source) return 'none'
  if (source === 'manual') return 'manual'
  if (path.startsWith('passport.')) return 'passport'
  const reported = (kind: SourceDocument) => form.documents[kind]?.extraction?.filled.includes(path) === true
  if (reported('passport') && !reported('resume')) return 'passport'
  if (reported('resume') && !reported('passport')) return 'resume'
  return fallback ?? 'extracted'
}

/* ------------------------------------------------------------------ Extraction */

export interface ExtractionNote {
  tone: 'reading' | 'done' | 'review' | 'failed' | 'none'
  text: string
}

/** What a document's last reading did, in the words a recruiter needs. */
export function describeExtraction(kind: DocumentKind, slot: Pick<DocumentSlot, 'uploaded' | 'extraction'>): ExtractionNote {
  const extraction = slot.extraction
  if (kind === 'photo' || !slot.uploaded || !extraction) return { tone: 'none', text: '' }
  const filled = extraction.filled.length
  const fields = `${filled} ${filled === 1 ? 'field' : 'fields'}`
  switch (extraction.status) {
    case 'processing':
      return { tone: 'reading', text: extraction.message || `Reading the ${kind}…` }
    case 'done':
      return { tone: 'done', text: filled > 0 ? `Filled ${fields} on the form` : 'Read. Nothing new to fill in' }
    case 'needs_review':
      return { tone: 'review', text: `${filled > 0 ? `Filled ${fields}. ` : ''}${extraction.message || 'Some pages could not be read. Please check the details.'}` }
    default:
      return { tone: 'failed', text: `${extraction.message || 'Could not read this document.'} Enter the details manually.` }
  }
}

export const isReading = (form: Pick<ApplicationFormData, 'documents' | 'processing'>): boolean =>
  form.processing || DOCUMENT_KINDS.some((kind) => form.documents[kind]?.extraction?.status === 'processing')

/* ------------------------------------------------------------------ Dates, age, experience */

/** `2026-03-09` -> `09/03/2026`, the order the paper form and the team use. Unreadable input is returned as typed. */
export function formatFormDate(value: string | null | undefined): string {
  if (!value) return ''
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
  if (!match) return value
  return `${match[3]}/${match[2]}/${match[1]}`
}

const isRealDate = (value: string): boolean => {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return false
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const date = new Date(Date.UTC(year, month - 1, day))
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
}

export function ageFromDob(dob: string | null | undefined, now: Date = new Date()): number | null {
  if (!dob || !isRealDate(dob.slice(0, 10))) return null
  const [year, month, day] = dob.slice(0, 10).split('-').map(Number)
  let age = now.getFullYear() - year
  if (now.getMonth() + 1 < month || (now.getMonth() + 1 === month && now.getDate() < day)) age -= 1
  return age >= 0 && age < 130 ? age : null
}

/** 4.5 -> "4.5 years", 1 -> "1 year", "" -> "". */
export function yearsLabel(value: string | number | null | undefined): string {
  if (value == null || value === '') return ''
  const number = Number(value)
  if (!Number.isFinite(number)) return String(value)
  return `${number} ${number === 1 ? 'year' : 'years'}`
}

/** Total experience should cover India plus abroad; a mismatch is shown as a hint, not blocked. */
export function experienceSplitHint(experience: { totalExperience: string; experienceIndia: string; experienceAbroad: string }): string {
  const total = Number(experience.totalExperience)
  const india = Number(experience.experienceIndia || 0)
  const abroad = Number(experience.experienceAbroad || 0)
  if (![total, india, abroad].every(Number.isFinite) || experience.totalExperience === '') return ''
  if (india + abroad !== total && experience.experienceIndia !== '' && experience.experienceAbroad !== '') return `India (${india}) and abroad (${abroad}) do not add up to the total (${total}).`
  return ''
}

/* ------------------------------------------------------------------ Validation */

export type FormErrors = Record<string, string>

const todayKey = (now: Date) => `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`

export function validateDraft(draft: FormDraft, now: Date = new Date()): FormErrors {
  const errors: FormErrors = {}
  const { personal, passport } = draft

  if (personal.dateOfBirth) {
    if (!isRealDate(personal.dateOfBirth)) errors['personal.dateOfBirth'] = 'Enter a real date.'
    else if (personal.dateOfBirth > todayKey(now)) errors['personal.dateOfBirth'] = 'Date of birth cannot be in the future.'
    else if ((ageFromDob(personal.dateOfBirth, now) ?? 99) < 16) errors['personal.dateOfBirth'] = 'The candidate must be at least 16.'
  }
  for (const key of ['mobileNumber', 'whatsapp'] as const) {
    if (personal[key] && personal[key].replace(/\D/g, '').length < 7) errors[`personal.${key}`] = 'Enter a phone number with at least 7 digits.'
  }
  if (personal.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(personal.email)) errors['personal.email'] = 'Enter a valid e-mail address.'

  if (passport.issueDate && !isRealDate(passport.issueDate)) errors['passport.issueDate'] = 'Enter a real date.'
  if (passport.expiryDate && !isRealDate(passport.expiryDate)) errors['passport.expiryDate'] = 'Enter a real date.'
  if (!errors['passport.issueDate'] && !errors['passport.expiryDate'] && passport.issueDate && passport.expiryDate && passport.expiryDate <= passport.issueDate) {
    errors['passport.expiryDate'] = 'Expiry must be after the issue date.'
  }

  for (const def of EXPERIENCE_FIELDS) {
    const value = valueAt(draft, def.path) as string
    if (!value) continue
    const number = Number(value)
    if (!Number.isFinite(number) || number < 0 || number > 80) errors[def.path] = 'Enter years between 0 and 80.'
  }

  const followUp = draft.extras['screening.nextFollowUpDate']
  if (followUp && !isRealDate(followUp)) errors['screening.nextFollowUpDate'] = 'Enter a real date.'
  for (const key of ['screening.familyMemberNumber']) {
    const value = draft.extras[key]
    if (value && value.replace(/\D/g, '').length < 7) errors[key] = 'Enter a phone number with at least 7 digits.'
  }

  draft.education.forEach((row, index) => {
    const year = row.passedYear.trim()
    if (!year) return
    if (!/^\d{4}$/.test(year)) errors[`education.${index}.passedYear`] = 'Use a 4-digit year.'
    else if (Number(year) < 1950 || Number(year) > now.getFullYear() + 6) errors[`education.${index}.passedYear`] = `Use a year from 1950 to ${now.getFullYear() + 6}.`
  })
  return errors
}

/* ------------------------------------------------------------------ Repeating rows */

export function updateRow<T extends { rowId: string }>(rows: T[], rowId: string, patch: Partial<T>): T[] {
  return rows.map((row) => (row.rowId === rowId ? { ...row, ...patch, rowId: row.rowId } : row))
}

export function removeRow<T extends { rowId: string }>(rows: T[], rowId: string): T[] {
  return rows.filter((row) => row.rowId !== rowId)
}

export function moveRow<T extends { rowId: string }>(rows: T[], rowId: string, direction: -1 | 1): T[] {
  const from = rows.findIndex((row) => row.rowId === rowId)
  const to = from + direction
  if (from < 0 || to < 0 || to >= rows.length) return rows
  const next = [...rows]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}

export const isBlankRow = (row: object): boolean => Object.entries(row).every(([key, value]) => key === 'rowId' || isBlank(value))

/* ------------------------------------------------------------------ Saving */

/** Compare two drafts by content, ignoring the row ids that only exist for React. */
const contentOf = (value: unknown): string => JSON.stringify(value, (key, item) => (key === 'rowId' ? undefined : item))

export function isDirty(base: FormDraft, draft: FormDraft): boolean {
  return contentOf(base) !== contentOf(draft)
}

/** Dotted paths whose value differs between two drafts. */
export function changedPaths(base: FormDraft, draft: FormDraft): string[] {
  const paths: string[] = []
  for (const def of [...ALL_EDITABLE_FIELDS, ...EXTRA_FIELDS]) {
    if (contentOf(valueAt(base, def.path)) !== contentOf(valueAt(draft, def.path))) paths.push(def.path)
  }
  if (contentOf(base.education) !== contentOf(draft.education)) paths.push('education')
  if (contentOf(base.experience.history) !== contentOf(draft.experience.history)) paths.push('experience.history')
  return paths
}

const toInt = (value: string): number | null => {
  const trimmed = value.trim()
  if (!trimmed) return null
  const number = Number(trimmed)
  return Number.isFinite(number) ? Math.round(number) : null
}

/** The body for a partial save, in the server's shape: only what changed, blank repeating rows left out. */
export function buildSavePayload(base: FormDraft, draft: FormDraft): Record<string, unknown> {
  const body: Record<string, Record<string, unknown> | unknown[]> = {}
  for (const path of changedPaths(base, draft)) {
    if (path === 'education') {
      body.education = draft.education.filter((row) => !isBlankRow(row)).map((row) => ({ level: row.level.trim(), passedYear: toInt(row.passedYear), institute: row.institute.trim() }))
      continue
    }
    if (path === 'experience.history') {
      const experience = (body.experience ?? {}) as Record<string, unknown>
      experience.history = draft.experience.history.filter((row) => !isBlankRow(row)).map((row) => ({ company: row.company.trim(), designation: row.designation.trim(), country: row.country.trim(), period: row.period.trim() }))
      body.experience = experience
      continue
    }
    const [section, key] = path.split('.')
    const target = (body[section] ?? {}) as Record<string, unknown>
    const value = valueAt(draft, path)
    const def = [...ALL_EDITABLE_FIELDS, ...EXTRA_FIELDS].find((item) => item.path === path)
    target[key] = def?.kind === 'years' ? toInt(value as string) : def?.kind === 'date' ? (value || null) : def?.kind === 'list' ? languageList(value) : value
    body[section] = target
  }
  return body
}

/**
 * A fresh copy of the form arrived (a document was read, a stage finished elsewhere) while somebody is editing.
 * Fields they have not touched take the new server value; fields they changed keep what they typed.
 */
export function rebaseDraft(oldBase: FormDraft, draft: FormDraft, newBase: FormDraft): FormDraft {
  const next = toDraft(newBase)
  for (const def of [...ALL_EDITABLE_FIELDS, ...EXTRA_FIELDS]) {
    if (contentOf(valueAt(oldBase, def.path)) === contentOf(valueAt(draft, def.path))) continue
    if (Object.hasOwn(draft.extras ?? {}, def.path)) { next.extras[def.path] = String(valueAt(draft, def.path) ?? ''); continue }
    const [section, key] = def.path.split('.')
    ;(next as unknown as Record<string, Record<string, unknown>>)[section][key] = valueAt(draft, def.path)
  }
  if (contentOf(oldBase.education) !== contentOf(draft.education)) next.education = draft.education.map((row) => ({ ...row }))
  if (contentOf(oldBase.experience.history) !== contentOf(draft.experience.history)) next.experience.history = draft.experience.history.map((row) => ({ ...row }))
  return next
}

/* ------------------------------------------------------------------ Links */

/** The linkable address of a candidate's form, for one order (or application) when it is known. */
export function applicationFormHref(candidateId: string, orderId?: string | null, applicationId?: string | null): string {
  const query = applicationId ? `?application=${encodeURIComponent(applicationId)}` : orderId ? `?order=${encodeURIComponent(orderId)}` : ''
  return `/candidates/${encodeURIComponent(candidateId)}/application${query}`
}
