/**
 * The application form as the API serves it (docs/workflows/application-form-contract.md).
 *
 * One form per candidate + order application, laid out like CareIndia_Application_Form_Order_Wise.docx. Every value is
 * either read from an uploaded document ('extracted') or typed by a person ('manual'); `fieldSources` says which.
 * Field names here are the server's own, so a field's dotted path (`personal.fatherName`) is the same key in
 * `fieldSources`, in the missing list and in a save.
 */

export type FieldSource = 'extracted' | 'manual'
export type SourceDocument = 'resume' | 'passport'
export type DocumentKind = 'resume' | 'passport' | 'photo'

export interface PersonalDetails {
  candidateName: string
  fatherName: string
  dateOfBirth: string
  maritalStatus: string
  gender: string
  nationality: string
  languagesKnown: string[]
  mobileNumber: string
  currentAddress: string
  whatsapp: string
  email: string
}

export interface PassportDetails {
  passportNumber: string
  placeOfIssue: string
  issueDate: string
  expiryDate: string
}

export interface EducationRow {
  /** Stable key for React while rows are added, removed and reordered; never sent to the server. */
  rowId: string
  level: string
  passedYear: string
  institute: string
}

export interface ExperienceRow {
  rowId: string
  company: string
  designation: string
  country: string
  period: string
}

export interface ExperienceDetails {
  totalExperience: string
  experienceIndia: string
  experienceAbroad: string
  history: ExperienceRow[]
}

/** The part of the form a person edits. Documents, tracking and section 6 come from elsewhere. */
export interface FormDraft {
  personal: PersonalDetails
  passport: PassportDetails
  education: EducationRow[]
  experience: ExperienceDetails
  /** Preference and follow-up details, keyed by dotted server path (`preferences.countryPreference`). Empty when the server has none. */
  extras: Record<string, string>
}

export interface RecruitmentDetails {
  client: string
  clientContact: string
  country: string
  position: string
  orderRef: string
  source: string
  sourceDetail: string
  recruiter: string
}

export type TrackingStatus = 'not_started' | 'in_progress' | 'completed' | 'failed' | 'not_applicable'

export interface TrackingRow {
  key: string
  /** The paper form's wording (Pre-screening, Forwarded to Client, ...). */
  label: string
  /** The CRM's own stage title, when it differs. */
  systemLabel: string
  /** The real stage number 1..11, or 0 for a row the CRM has no stage for (PCC, Invoice Generated). */
  stage: number
  /** A CRM stage the paper form does not list. */
  extra: boolean
  tracked: boolean
  status: TrackingStatus
  date: string
  completedBy: string
  outcome: string
  attempt: number
  note: string
  /** The stage the active application is on right now. */
  current: boolean
}

export interface TrackingSummary {
  completed: number
  tracked: number
  percent: number
  currentStage: number
  currentStageLabel: string
  applicationStatus: string
  closureLabel: string
}

export interface FormDocument {
  attachmentId: string
  fileName: string
  sizeKb: number
  version: number
  url: string
  uploadedAt: string
  uploadedBy: string
}

export type ExtractionStatusCode = 'processing' | 'done' | 'failed' | 'unreadable' | 'needs_review'

export interface Extraction {
  status: ExtractionStatusCode
  message: string
  filled: string[]
  kept: string[]
  kindsFound: string[]
  updatedAt: string
}

export interface DocumentSlot {
  slot: DocumentKind
  label: string
  uploaded: boolean
  current: FormDocument | null
  versions: number
  extraction: Extraction | null
}

export interface MissingItem {
  /** Dotted field path (`passport.passportNumber`, `education`, `experience.history`) or `documents.passport`. */
  key: string
  label: string
  /** Form section number 1..5 the item belongs to. */
  section: number
  kind: 'document' | 'field'
  message: string
}

export interface ApplicationRef {
  id: string
  orderId: string
  orderRef: string
  clientName: string
  position: string
  country: string
  status: string
  closureReason: string
  currentStage: number
  selected: boolean
}

export interface ExpiryInfo {
  status: 'valid' | 'expiring_soon' | 'expired' | 'unknown'
  daysLeft: number | null
}

export interface ApplicationFormData {
  header: { company: string; title: string; address: string; phone: string; email: string }
  candidate: { id: string; ref: string; name: string; status: string; needsReview: boolean; ownerId: string; ownerName: string; updatedAt: string; canEdit: boolean }
  application: ApplicationRef | null
  applications: ApplicationRef[]
  personal: PersonalDetails
  passport: PassportDetails & { expiry: ExpiryInfo }
  education: EducationRow[]
  experience: ExperienceDetails
  documents: Record<DocumentKind, DocumentSlot>
  recruitment: RecruitmentDetails
  tracking: { summary: TrackingSummary; rows: TrackingRow[] }
  completeness: { percent: number; filled: number; total: number; missing: MissingItem[] }
  fieldSources: Record<string, FieldSource>
  warnings: string[]
  processing: boolean
  /** "XXXX XXXX 1234" when an Aadhaar copy was read for this candidate; only the last four digits ever reach the browser. */
  aadhaarMasked?: string
  /** Server values for the assessment / follow-up fields (see EXTRA_FIELDS), keyed by dotted path. */
  extras: Record<string, string>
  /** Whether the server sends those fields at all; an older server does not, and the sections are then left out. */
  extrasSupported: boolean
  /** The initial call's result and the telemarketer's notes, read only (screening.callOutcome). */
  call: { called: boolean; interested: string; remarks: string; recordedAt: string } | null
}
