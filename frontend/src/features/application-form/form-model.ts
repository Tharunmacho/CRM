/**
 * Pure model of the application form: the field list (in the order of the paper form), missing-field detection,
 * completeness, tracking rows, date and age formatting, validation and repeating-row editing.
 *
 * Nothing here touches React or the network, so it is unit-tested directly (tests/application-form.test.mjs).
 * The API payload (docs/workflows/application-form-contract.md) is read through `normalizeForm`, which tolerates
 * absent parts: an application with no order, a candidate with no passport yet, or an older server.
 */
import { DOCUMENT_SLOTS, documentExpected, documentLabel, documentSlot } from '@/shared/model/document-slots'
import type {
  ApplicationFormData,
  ApplicationRef,
  AssignmentClientOption,
  AssignmentData,
  FeedbackData,
  FeedbackItem,
  FeedbackKind,
  FeedbackReason,
  AssignmentChange,
  OptionalDocument,
  StageClock,
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
  6: 'Client and recruitment details',
  7: 'Recruitment process tracking',
  8: 'Assessment and preferences',
  9: 'Follow-up and notes',
}


const field = (def: Omit<FieldDef, 'counted'> & { counted?: boolean }): FieldDef => ({ ...def, counted: def.counted ?? def.required })

/** Section 1, in the paper form's order (WhatsApp and e-mail are extras, placed last so the pairs stay aligned). */
export const PERSONAL_FIELDS: FieldDef[] = [
  field({ path: 'personal.candidateName', label: 'Candidate name', section: 1, kind: 'text', required: true, from: 'resume' }),
  field({ path: 'personal.fatherName', label: "Father's name", section: 1, kind: 'text', required: true, from: 'passport' }),
  field({ path: 'personal.dateOfBirth', label: 'Date of birth', section: 1, kind: 'date', required: true, from: 'passport' }),
  field({ path: 'personal.maritalStatus', label: 'Marital status', section: 1, kind: 'select', required: true, from: 'resume', category: 'marital_status' }),
  field({ path: 'personal.gender', label: 'Gender', section: 1, kind: 'select', required: true, from: 'passport', category: 'gender' }),
  field({ path: 'personal.nationality', label: 'Nationality', section: 1, kind: 'text', required: true, from: 'passport' }),
  field({ path: 'personal.languagesKnown', label: 'Languages known', section: 1, kind: 'languages', required: true, from: 'resume' }),
  field({ path: 'personal.mobileNumber', label: 'Mobile number', section: 1, kind: 'tel', required: true, from: 'resume' }),
  field({ path: 'personal.whatsapp', label: 'WhatsApp number', section: 1, kind: 'tel', required: false, from: 'resume' }),
  field({ path: 'personal.email', label: 'Email address', section: 1, kind: 'email', required: false, from: 'resume' }),
  field({ path: 'personal.currentAddress', label: 'Current address', section: 1, kind: 'textarea', required: true, from: 'resume' }),
]

export const PASSPORT_FIELDS: FieldDef[] = [
  field({ path: 'passport.passportNumber', label: 'Passport number', section: 2, kind: 'text', required: true, from: 'passport' }),
  field({ path: 'passport.placeOfIssue', label: 'Place of issue', section: 2, kind: 'text', required: true, from: 'passport' }),
  field({ path: 'passport.issueDate', label: 'Date of issue', section: 2, kind: 'date', required: true, from: 'passport' }),
  field({ path: 'passport.expiryDate', label: 'Date of expiry', section: 2, kind: 'date', required: true, from: 'passport' }),
]

/** Total and the history are counted; India and abroad are on the paper form and highlighted when empty, but not counted. */
export const EXPERIENCE_FIELDS: FieldDef[] = [
  field({ path: 'experience.totalExperience', label: 'Total experience', section: 4, kind: 'years', required: true, from: 'resume' }),
  field({ path: 'experience.experienceIndia', label: 'Experience in India', section: 4, kind: 'years', required: true, counted: false, from: 'resume' }),
  field({ path: 'experience.experienceAbroad', label: 'Experience abroad', section: 4, kind: 'years', required: true, counted: false, from: 'resume' }),
]

export const EDUCATION_COLUMNS = [
  { key: 'level', label: 'Qualification or course', placeholder: 'For example B.Tech Mechanical' },
  { key: 'passedYear', label: 'Year of passing', placeholder: 'YYYY' },
  { key: 'institute', label: 'Name and address of institution', placeholder: 'Name of institution, address' },
] as const

/** The education table of the form itself: the server keeps one text for the institution, the form shows it as two inputs. */
export const EDUCATION_FORM_COLUMNS = [
  { key: 'level', label: 'Qualification or course', placeholder: 'For example B.Tech Mechanical' },
  { key: 'passedYear', label: 'Year of passing', placeholder: 'YYYY' },
  { key: 'institutionName', label: 'Name of institution', placeholder: 'For example ABC College' },
  { key: 'institutionAddress', label: 'Address of institution', placeholder: 'For example Chennai' },
] as const

/** "ABC College, Chennai" -> name "ABC College", address "Chennai". The first comma separates them; no comma means a name only. */
export function splitInstitution(text: string): { name: string; address: string } {
  const at = text.indexOf(',')
  if (at < 0) return { name: text, address: '' }
  return { name: text.slice(0, at), address: text.slice(at + 1).replace(/^\s/, '') }
}

/** The reverse. Nothing is trimmed here, so a space typed at the end of a box is not eaten while the person is still typing. */
export function joinInstitution(name: string, address: string): string {
  return address === '' ? name : `${name}, ${address}`
}

export const EXPERIENCE_COLUMNS = [
  { key: 'company', label: 'Name of company', placeholder: 'Company' },
  { key: 'designation', label: 'Designation', placeholder: 'Designation' },
  { key: 'country', label: 'Country', placeholder: 'Country' },
  { key: 'period', label: 'Period of employment', placeholder: 'For example Jan 2018 to Mar 2020' },
] as const

export const RECRUITMENT_FIELDS = [
  { key: 'client', label: 'Client' },
  { key: 'country', label: 'Country' },
  { key: 'position', label: 'Job' },
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
  field({ path: 'screening.familyMemberNumber', label: 'Contact number of a family member', section: 1, kind: 'tel', required: false }),
  field({ path: 'screening.jobPreference', label: 'Preferred job titles', section: 8, kind: 'list', required: false }),
  field({ path: 'screening.skills', label: 'Skills', section: 8, kind: 'list', required: false }),
  field({ path: 'screening.countryPreference', label: 'Countries preferred', section: 8, kind: 'text', required: false }),
  field({ path: 'screening.jobDurationPreference', label: 'Preferred job duration', section: 8, kind: 'select', required: false, category: 'job_duration_preference' }),
  field({ path: 'screening.bestTimeToContact', label: 'Best time to call', section: 8, kind: 'select', required: false, category: 'best_time_to_contact' }),
  field({ path: 'screening.serviceChargesStatus', label: 'Service charges', section: 8, kind: 'select', required: false, category: 'service_charges_status' }),
  field({ path: 'screening.interestLevel', label: 'Interest level', section: 8, kind: 'text', required: false }),
  field({ path: 'screening.nextFollowUpDate', label: 'Date of next follow-up', section: 9, kind: 'date', required: false }),
]

/** The slots in the contract's order. The list itself (labels, hints, rules) lives in shared/model/document-slots.ts. */
export const DOCUMENT_KINDS: DocumentKind[] = DOCUMENT_SLOTS.map((entry) => entry.slot)
export const documentName = (slot: DocumentKind): string => documentLabel(slot)

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
    ...(value.jobId != null ? { jobId: str(value.jobId) } : {}),
    ...(value.coverage != null ? { coverage: value.coverage } : {}),
    ...(Array.isArray(value.unmapped) && value.unmapped.length ? { unmapped: value.unmapped } : {}),
  }
}

/**
 * One document slot. The server's own `required` wins; an older server that does not send it (or the slot at all) gets the
 * rule from shared/model/document-slots.ts for the application's current stage, so the checklist is complete either way.
 */
function normaliseSlot(slot: DocumentKind, raw: unknown, stage: number | null): DocumentSlot {
  const value = rec(raw)
  const entry = documentSlot(slot)
  const current = normaliseDocument(value.current)
  const files = list(value.files).map(normaliseDocument).filter((item): item is FormDocument => item !== null)
  const multiple = value.multiple === true || entry?.multiple === true
  return {
    slot,
    // The shared label always wins: an older server still says "Passport size photo" for the photo.
    label: entry?.label || str(value.label) || documentLabel(slot),
    hint: str(value.hint) || entry?.hint || '',
    uploaded: value.uploaded === true || current !== null || files.length > 0,
    current,
    versions: num(value.versions, current ? 1 : 0),
    extraction: normaliseExtraction(value.extraction),
    required: typeof value.required === 'boolean' ? value.required : entry ? documentExpected(entry, stage) : false,
    requiredFromStage: value.requiredFromStage == null ? (entry?.fromStage ?? null) : num(value.requiredFromStage),
    multiple,
    files,
    listed: raw != null && typeof raw === 'object',
  }
}

/** The slots in display order: the server's `documentOrder`, else the contract's, then any slot the lists above do not know. */
function normaliseDocuments(root: Record<string, unknown>, stage: number | null) {
  const documents = rec(root.documents)
  const sent = list(root.documentOrder).map(str).filter(Boolean)
  const order = [...new Set([...(sent.length ? sent : DOCUMENT_KINDS), ...Object.keys(documents)])]
  const out: Record<DocumentKind, DocumentSlot> = {}
  for (const slot of order) {
    const raw = slot === 'photo' ? (documents.photo ?? (root.photo ? { uploaded: true, current: root.photo } : null)) : documents[slot]
    out[slot] = normaliseSlot(slot, raw, stage)
  }
  const optional = list(root.optionalDocuments).map((item): OptionalDocument => {
    const value = rec(item)
    return { slot: str(value.slot), label: documentSlot(str(value.slot))?.label || str(value.label) || documentLabel(str(value.slot)), uploaded: value.uploaded === true }
  }).filter((item) => item.slot)
  return {
    documents: out,
    documentOrder: order,
    optionalDocuments: optional.length || 'optionalDocuments' in root ? optional : order.filter((slot) => !out[slot].required && out[slot].requiredFromStage == null).map((slot) => ({ slot, label: out[slot].label, uploaded: out[slot].uploaded })),
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
    ...normaliseDocuments(root, application ? application.currentStage : null),
    assignment: normaliseAssignment(root.assignment, rec(root.recruitment), application),
    feedback: normaliseFeedback(root.feedback),
    recruitment: {
      client: str(recruitment.client), clientContact: str(recruitment.clientContact), country: str(recruitment.country),
      position: str(recruitment.position), orderRef: str(recruitment.orderRef), source: str(recruitment.source),
      sourceDetail: cleanAgencyName(str(recruitment.sourceDetail)), recruiter: personName(recruitment.recruiter),
    },
    tracking: normaliseTracking(root.tracking, application),
    extras: readExtras(root),
    extrasSupported: 'screening' in root,
    call: readCall(root),
    completeness: { percent: 0, filled: 0, total: 0, missing: [] },
    fieldSources,
    // The three experience totals are independent numbers; a server that still words a mismatch as a warning is not echoed.
    warnings: list(root.warnings).map(str).filter((text) => text && !/^Experience in India plus experience abroad does not match/i.test(text)),
    processing: root.processing === true,
    aadhaarMasked: maskedAadhaar(rec(root.aadhaar).maskedNumber),
  }

  if (root.deployment && typeof root.deployment === 'object') {
    const deployment = rec(root.deployment)
    form.deployment = {
      ready: deployment.ready === true,
      missing: list(deployment.missing).map((item) => { const value = rec(item); return { slot: str(value.slot), label: documentSlot(str(value.slot))?.label || str(value.label) || documentLabel(str(value.slot)) } }).filter((item) => item.slot),
    }
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
  if (path.startsWith('documents.')) return documentLabel(path.slice(10))
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

/** The three slots every version of the server counts. */
const ORIGINAL_SLOTS: DocumentKind[] = ['resume', 'passport', 'photo']

type CountedDocument = Pick<DocumentSlot, 'uploaded'> & Partial<Pick<DocumentSlot, 'required' | 'listed' | 'label'>>

/**
 * Completeness of the form as it stands (the draft, while editing), by the server's own rule: the nine personal fields, four
 * passport fields, education, total experience, work history and every document the server marks `required` for this
 * application's stage (contract v2 section 2). A newer slot an older server never sent (`listed: false`) is not counted, so the
 * percentage never disagrees with the server.
 */
export function completenessOf(draft: FormDraft, documents?: Record<DocumentKind, CountedDocument>): Completeness {
  const missing: MissingItem[] = []
  let total = 0
  if (documents) {
    for (const kind of Object.keys(documents)) {
      const slot = documents[kind]
      if (!slot || slot.required === false || (slot.listed === false && !ORIGINAL_SLOTS.includes(kind))) continue
      total += 1
      if (!slot.uploaded) {
        const label = documentSlot(kind)?.label || slot.label || documentLabel(kind)
        missing.push({ key: `documents.${kind}`, label, section: 5, kind: 'document', message: `${label} not uploaded` })
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
  form.processing || Object.values(form.documents).some((slot) => slot?.extraction?.status === 'processing')

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
    errors['passport.expiryDate'] = 'The date of expiry must be after the date of issue.'
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

/* ------------------------------------------------------------------ Documents: checklist, groups, progress */

/** The pipeline stage names, to say "Needed from Offer letter" (contract section 3 of the form contract). */
export const STAGE_NAMES: Record<number, string> = {
  1: 'Prescreening', 2: 'Forwarded to client', 3: 'Interview scheduling', 4: 'Interview result', 5: 'Offer letter', 6: 'Medical',
  7: 'Passport', 8: 'Advance payment', 9: 'Visa arrival', 10: 'Visa stamping', 11: 'Air ticket',
}

export type DocumentState = 'uploaded' | 'required' | 'later' | 'optional'

/** What the status chip says. An uploaded document is Uploaded whatever else is true of it. */
export function documentState(slot: Pick<DocumentSlot, 'uploaded' | 'required' | 'requiredFromStage'>): DocumentState {
  if (slot.uploaded) return 'uploaded'
  if (slot.required) return 'required'
  return slot.requiredFromStage != null ? 'later' : 'optional'
}

export const DOCUMENT_STATE_LABELS: Record<DocumentState, string> = { uploaded: 'Uploaded', required: 'Required', later: 'Not needed yet', optional: 'Optional' }

/** "Needed from Offer letter" for a document that is not asked for yet; empty for the others. */
export function requiredByStageText(slot: Pick<DocumentSlot, 'required' | 'requiredFromStage'>): string {
  if (slot.required || slot.requiredFromStage == null) return ''
  return `Needed from ${STAGE_NAMES[slot.requiredFromStage] ?? `stage ${slot.requiredFromStage}`}`
}

export interface DocumentGroups { required: DocumentSlot[]; later: DocumentSlot[]; optional: DocumentSlot[] }

/** Required now, then later in the process, then optional; the contract's order is kept inside each group. */
export function groupDocuments(documents: Record<DocumentKind, DocumentSlot>, order: DocumentKind[] = Object.keys(documents)): DocumentGroups {
  const groups: DocumentGroups = { required: [], later: [], optional: [] }
  for (const key of order) {
    const slot = documents[key]
    if (!slot) continue
    if (slot.required) groups.required.push(slot)
    else if (slot.requiredFromStage != null) groups.later.push(slot)
    else groups.optional.push(slot)
  }
  return groups
}

/** "3 of 5 required documents": uploaded and total among the required ones. */
export function requiredDocumentProgress(documents: Record<DocumentKind, Pick<DocumentSlot, 'uploaded' | 'required'>>) {
  const required = Object.values(documents).filter((slot) => slot.required)
  const uploaded = required.filter((slot) => slot.uploaded).length
  return { uploaded, total: required.length, percent: required.length ? Math.round((uploaded / required.length) * 100) : 100 }
}

export const sizeLabel = (sizeKb: number): string => (sizeKb >= 1024 ? `${(sizeKb / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(sizeKb))} KB`)

/**
 * The documents as they will be once the uploads in flight land, so the checklist and the percentage move the moment a file
 * is chosen. `pending` maps a slot to the file being sent; the real answer (or a failure) removes it again.
 */
export function applyOptimisticUploads(documents: Record<DocumentKind, DocumentSlot>, pending: Record<DocumentKind, { fileName: string; sizeKb: number }>): Record<DocumentKind, DocumentSlot> {
  const keys = Object.keys(pending).filter((key) => documents[key])
  if (!keys.length) return documents
  const next = { ...documents }
  for (const key of keys) {
    const slot = documents[key]
    const file: FormDocument = { attachmentId: `pending-${key}`, fileName: pending[key].fileName, sizeKb: pending[key].sizeKb, version: slot.versions + 1, url: '', uploadedAt: '', uploadedBy: '' }
    next[key] = slot.multiple ? { ...slot, uploaded: true, files: [file, ...slot.files] } : { ...slot, uploaded: true, current: file, versions: slot.versions + 1 }
  }
  return next
}

/* ------------------------------------------------------------------ Assignment block */

/** Automatic intake keeps a pointer to the message a candidate came from in the same field a person types an agency into. */
const MACHINE_DETAIL = /^(email_message|whatsapp_message|person_review):/i

/** An agency name as a person typed it; the intake pointers ("email_message:8a9c...") are not names and read as empty everywhere. */
export function cleanAgencyName(name: string | null | undefined): string {
  const text = (name ?? '').trim()
  return MACHINE_DETAIL.test(text) ? '' : text
}

export const SOURCE_OPTIONS: { key: string; label: string }[] = [
  { key: 'whatsapp', label: 'WhatsApp' },
  { key: 'email', label: 'Email' },
  { key: 'walk_in', label: 'Direct' },
  { key: 'referral', label: 'Referral' },
  { key: 'other', label: 'Other' },
]

/** "walk_in" -> "Direct". Anything else the server names is shown as it names it. */
export function sourceLabel(key: string, fallback = ''): string {
  return SOURCE_OPTIONS.find((item) => item.key === key)?.label ?? (fallback || key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()))
}

/** "Walk-in" / "WhatsApp" / "walk_in" -> the source key. */
export function sourceKeyFrom(text: string): string {
  const key = text.trim().toLowerCase().replace(/[\s-]+/g, '_')
  if (key === 'direct') return 'walk_in'
  return SOURCE_OPTIONS.some((item) => item.key === key) ? key : key ? 'other' : ''
}

function normaliseAssignment(raw: unknown, recruitment: Record<string, unknown>, application: ApplicationRef | null): AssignmentData {
  const supported = Boolean(raw) && typeof raw === 'object' && !Array.isArray(raw)
  const value = rec(raw)
  const can = rec(value.can)
  const options = rec(value.options)
  const pick = (item: unknown) => (item && typeof item === 'object' ? rec(item) : null)
  const client = pick(value.client)
  const job = pick(value.job)
  const agency = pick(value.agency)
  const recruiter = pick(value.recruiter)
  const source = rec(value.source)

  if (!supported) {
    // An older server: show what the recruitment details already say, read only.
    const clientName = str(recruitment.client)
    const sourceKey = str(recruitment.sourceCode) || sourceKeyFrom(str(recruitment.source))
    const detail = cleanAgencyName(str(recruitment.sourceDetail))
    const hasJob = Boolean(application) || Boolean(str(recruitment.position))
    return {
      supported: false,
      serverOptions: false,
      serverCan: false,
      client: clientName ? { id: '', name: clientName, companyId: '', companyName: clientName } : null,
      job: hasJob ? { orderId: application?.orderId ?? '', ref: str(recruitment.orderRef) || application?.orderRef || '', title: str(recruitment.position) || application?.position || '', applicationId: application?.id ?? '', stage: application?.currentStage ?? 0, country: str(recruitment.country) || application?.country || '' } : null,
      source: { key: sourceKey, label: sourceKey ? sourceLabel(sourceKey, str(recruitment.source)) : '' },
      agency: detail ? { id: '', name: detail } : null,
      recruiter: personName(recruitment.recruiter) ? { id: str(rec(recruitment.recruiter).id), name: personName(recruitment.recruiter) } : null,
      can: { job: false, source: false, agency: false, recruiter: false },
      options: { clients: [], agencies: [], recruiters: [] },
    }
  }
  const sourceKey = str(source.key)
  return {
    supported: true,
    serverOptions: Array.isArray(options.clients),
    serverCan: Boolean(value.can) && typeof value.can === 'object',
    client: client ? { id: str(client.id), name: str(client.name), companyId: str(client.companyId), companyName: str(client.companyName) } : null,
    job: job ? { orderId: str(job.orderId), ref: str(job.ref), title: str(job.title), applicationId: str(job.applicationId), stage: num(job.stage), country: str(job.country) } : null,
    source: { key: sourceKey, label: sourceKey ? sourceLabel(sourceKey, str(source.label)) : '' },
    agency: agency && cleanAgencyName(str(agency.name)) ? { id: str(agency.id), name: cleanAgencyName(str(agency.name)) } : null,
    recruiter: recruiter ? { id: str(recruiter.id), name: str(recruiter.name) } : null,
    can: { job: can.job === true, source: can.source === true, agency: can.agency === true, recruiter: can.recruiter === true },
    options: {
      clients: list(options.clients).map((item): AssignmentClientOption => {
        const entry = rec(item)
        return {
          id: str(entry.id), name: str(entry.name), companyId: str(entry.companyId), companyName: str(entry.companyName),
          jobs: list(entry.jobs).map((job) => {
            const row = rec(job)
            return { orderId: str(row.orderId), ref: str(row.ref), title: str(row.title), openings: row.openings == null ? null : num(row.openings), status: str(row.status) }
          }).filter((job) => job.orderId),
        }
      }).filter((item) => item.id),
      agencies: list(options.agencies).map((item) => ({ id: str(rec(item).id), name: str(rec(item).name) })).filter((item) => item.name),
      recruiters: list(options.recruiters).map((item) => ({ id: str(rec(item).id), name: str(rec(item).name), role: str(rec(item).role) })).filter((item) => item.id),
    },
  }
}

const BLOCKED_JOB_STATUS: Record<string, string> = {
  closed: 'Closed', filled: 'Filled', on_hold: 'On hold', hold: 'On hold', cancelled: 'Cancelled', canceled: 'Cancelled', completed: 'Completed',
}

/** Why a job cannot be chosen ("Filled", "On hold", ...), or an empty string when it can. */
export function jobUnavailableReason(job: Pick<AssignmentClientOption['jobs'][number], 'status' | 'openings'>): string {
  const status = job.status.toLowerCase().replace(/[\s-]+/g, '_')
  if (BLOCKED_JOB_STATUS[status]) return BLOCKED_JOB_STATUS[status]
  if (job.openings != null && job.openings <= 0) return 'No openings left'
  return ''
}

/** The text of one job in the Job picker: "Welder · ORD-2026-00004 · 3 openings". */
export function jobOptionText(job: Pick<AssignmentClientOption['jobs'][number], 'title' | 'ref' | 'openings'>): string {
  return [job.title, job.ref, job.openings != null && job.openings > 0 ? `${job.openings} ${job.openings === 1 ? 'opening' : 'openings'}` : ''].filter(Boolean).join(' · ')
}

/** The client that owns a job, looked up in the picker options. */
export function clientOfJob(options: AssignmentData['options'], orderId: string): AssignmentClientOption | undefined {
  return options.clients.find((item) => item.jobs.some((job) => job.orderId === orderId))
}

/**
 * The assignment block as it will look once a change is saved, so the page answers at once (the server's answer then replaces it).
 * A change of job also changes the client, because the client is implied by the job.
 */
export function applyAssignmentChange(assignment: AssignmentData, change: AssignmentChange): AssignmentData {
  const next: AssignmentData = { ...assignment }
  if ('orderId' in change) {
    if (!change.orderId) next.job = null
    else {
      const owner = clientOfJob(assignment.options, change.orderId)
      const found = owner?.jobs.find((job) => job.orderId === change.orderId)
      next.job = { orderId: change.orderId, ref: found?.ref ?? '', title: found?.title ?? '', applicationId: '', stage: 0, country: '' }
      if (owner) next.client = { id: owner.id, name: owner.name, companyId: owner.companyId, companyName: owner.companyName }
    }
  }
  if (change.source) next.source = { key: change.source, label: sourceLabel(change.source) }
  if ('agencyId' in change || 'agencyName' in change) {
    const known = change.agencyId ? assignment.options.agencies.find((item) => item.id === change.agencyId) : undefined
    const name = known?.name ?? (change.agencyName ?? '').trim()
    next.agency = name ? { id: change.agencyId ?? '', name } : null
  }
  if (change.recruiterId) {
    const known = assignment.options.recruiters.find((item) => item.id === change.recruiterId)
    next.recruiter = known ? { id: known.id, name: known.name } : assignment.recruiter
  }
  return next
}

/* ------------------------------------------------------------------ Recruiter notes and the stage clock */

export const FEEDBACK_KINDS: { value: FeedbackKind; label: string }[] = [
  { value: 'update', label: 'Update' },
  { value: 'delay', label: 'Delay' },
  { value: 'issue', label: 'Issue' },
  { value: 'client', label: 'Client feedback' },
  { value: 'candidate', label: 'Candidate feedback' },
]

export const FEEDBACK_REASONS: { value: FeedbackReason; label: string }[] = [
  { value: 'waiting_candidate', label: 'Waiting for the candidate' },
  { value: 'waiting_client', label: 'Waiting for the client' },
  { value: 'waiting_documents', label: 'Waiting for documents' },
  { value: 'waiting_visa', label: 'Waiting for the visa' },
  { value: 'waiting_payment', label: 'Waiting for payment' },
  { value: 'other', label: 'Other' },
]

export const FEEDBACK_MAX_LENGTH = 2000
/** Staff can delete their own note for this long; an administrator can always delete. */
export const FEEDBACK_DELETE_MINUTES = 15

export const feedbackKindLabel = (kind: string): string => FEEDBACK_KINDS.find((item) => item.value === kind)?.label ?? kind
export const feedbackReasonLabel = (reason: string): string => FEEDBACK_REASONS.find((item) => item.value === reason)?.label ?? ''

function normaliseFeedback(raw: unknown): FeedbackData {
  const supported = Boolean(raw) && typeof raw === 'object' && !Array.isArray(raw)
  const value = rec(raw)
  const clock = value.stageClock && typeof value.stageClock === 'object' ? rec(value.stageClock) : null
  const kinds = FEEDBACK_KINDS.map((item) => item.value as string)
  return {
    supported,
    items: list(value.items).map((item): FeedbackItem => {
      const row = rec(item)
      return {
        id: str(row.id), kind: (kinds.includes(str(row.kind)) ? str(row.kind) : 'update') as FeedbackKind, reason: str(row.reason) as FeedbackReason | '',
        body: str(row.body), stage: row.stage == null ? null : num(row.stage), authorId: str(row.authorId), authorName: str(row.authorName), createdAt: str(row.createdAt),
        canDelete: typeof row.canDelete === 'boolean' ? row.canDelete : null,
      }
    }).filter((item) => item.id),
    canWrite: value.canWrite === true,
    stageClock: clock ? {
      stage: num(clock.stage), stageLabel: str(clock.stageLabel) || STAGE_NAMES[num(clock.stage)] || '', since: str(clock.since), hours: num(clock.hours),
      limitHours: num(clock.limitHours), overdue: clock.overdue === true, needsReason: clock.needsReason === true,
    } : null,
  }
}

/** 61 -> "2 days 13 hours", 48 -> "2 days", 5 -> "5 hours", 0.4 -> "less than an hour". */
export function durationText(hours: number): string {
  const total = Math.max(0, Math.floor(hours))
  if (total < 1) return 'less than an hour'
  const days = Math.floor(total / 24)
  const rest = total % 24
  const part = (count: number, one: string) => `${count} ${one}${count === 1 ? '' : 's'}`
  if (days === 0) return part(rest, 'hour')
  return rest === 0 ? part(days, 'day') : `${part(days, 'day')} ${part(rest, 'hour')}`
}

/** "Interview scheduling, 2 days 13 hours, limit 2 days". */
export function stageClockText(clock: Pick<StageClock, 'stageLabel' | 'hours' | 'limitHours'>): string {
  return [clock.stageLabel, durationText(clock.hours), clock.limitHours > 0 ? `limit ${durationText(clock.limitHours)}` : ''].filter(Boolean).join(', ')
}

/** Calm while inside the limit, amber when three quarters of it is used, red once it is overdue. */
export function stageClockTone(clock: Pick<StageClock, 'hours' | 'limitHours' | 'overdue'>): 'ok' | 'warn' | 'over' {
  if (clock.overdue || (clock.limitHours > 0 && clock.hours > clock.limitHours)) return 'over'
  return clock.limitHours > 0 && clock.hours >= clock.limitHours * 0.75 ? 'warn' : 'ok'
}

/** An administrator may delete any note; the author only within 15 minutes of writing it. */
export function canDeleteFeedback(item: Pick<FeedbackItem, 'authorId' | 'createdAt'> & { canDelete?: boolean | null }, viewer: { id: string; role: string }, now: Date = new Date()): boolean {
  if (typeof item.canDelete === 'boolean') return item.canDelete // the server knows its own clock
  if (viewer.role === 'admin') return true
  if (!viewer.id || item.authorId !== viewer.id) return false
  const written = new Date(item.createdAt).getTime()
  return Number.isFinite(written) && now.getTime() - written <= FEEDBACK_DELETE_MINUTES * 60_000
}
