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
/** A document slot key (`resume`, `passport`, `photo`, `education_certificate`, ... see shared/model/document-slots.ts). */
export type DocumentKind = string

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
  extraction?: Extraction | null
  extractionPending?: boolean
  identityMismatch?: boolean
}

export type ExtractionStatusCode = 'processing' | 'done' | 'failed' | 'unreadable' | 'needs_review'

export interface Extraction {
  status: ExtractionStatusCode
  identityMismatch?: boolean
  message: string
  filled: string[]
  kept: string[]
  kindsFound: string[]
  updatedAt: string
  /** The reading this belongs to, what was read (`coverage`) and what has no place on the form (`unmapped`). Additive: an older server sends neither. */
  jobId?: string
  coverage?: unknown
  unmapped?: unknown
}

export interface DocumentSlot {
  slot: DocumentKind
  label: string
  hint: string
  uploaded: boolean
  fileUploaded?: boolean
  manualStatusSupported?: boolean
  manualStatus?: { uploaded: boolean; by: { id: string | null; name: string | null }; at: string; note: string } | null
  current: FormDocument | null
  versions: number
  extraction: Extraction | null
  extractionPending?: boolean
  /** Counted towards completeness for this application (the server decides; worked out from the stage for an older server). */
  required: boolean
  /** The stage from which it is expected; null when always expected or optional. */
  requiredFromStage: number | null
  /** More than one file is kept (Passport and Other documents). */
  multiple: boolean
  /** Newest first; only filled for slots that keep several files. */
  files: FormDocument[]
  /** Whether the server sent this slot (false: filled in locally so the checklist is complete on an older server). */
  listed: boolean
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

/** One job a client has, for the Job picker. */
export interface AssignmentJobOption {
  orderId: string
  ref: string
  title: string
  openings: number | null
  status: string
}

export interface AssignmentClientOption {
  id: string
  name: string
  companyId: string
  companyName: string
  jobs: AssignmentJobOption[]
}

/** What this candidate is for: client, job, source, agency, recruiter (contract v2 section 1). */
export interface AssignmentData {
  /** False when the server does not send `assignment`: the block is then read only and built from the recruitment details. */
  supported: boolean
  /** Whether the server sent the pickers' options / the permissions; when it did not, the screen builds them from what it already holds. */
  serverOptions: boolean
  serverCan: boolean
  client: { id: string; name: string; companyId: string; companyName: string } | null
  job: { orderId: string; ref: string; title: string; applicationId: string; stage: number; country: string } | null
  source: { key: string; label: string }
  agency: { id: string; name: string } | null
  recruiter: { id: string; name: string } | null
  can: { job: boolean; source: boolean; agency: boolean; recruiter: boolean }
  options: {
    clients: AssignmentClientOption[]
    agencies: { id: string; name: string }[]
    recruiters: { id: string; name: string; role: string }[]
  }
}

/** A partial change to the assignment block, as the PUT sends it. */
export interface AssignmentChange {
  orderId?: string | null
  source?: string
  agencyId?: string | null
  agencyName?: string | null
  recruiterId?: string
}

export type FeedbackKind = 'update' | 'delay' | 'issue' | 'client' | 'candidate'
export type FeedbackReason = 'waiting_candidate' | 'waiting_client' | 'waiting_documents' | 'waiting_visa' | 'waiting_payment' | 'other'

export interface FeedbackItem {
  id: string
  kind: FeedbackKind
  reason: FeedbackReason | ''
  body: string
  stage: number | null
  authorId: string
  authorName: string
  createdAt: string
  /** What the server says this viewer may do with the note; null when it does not say (an older server), then the 15 minute rule is worked out here. */
  canDelete: boolean | null
}

export interface StageClock {
  stage: number
  stageLabel: string
  since: string
  hours: number
  limitHours: number
  overdue: boolean
  needsReason: boolean
}

/** Recruiter notes ("why is this taking time") and the clock of the stage the candidate is in. */
export interface FeedbackData {
  /** False when the server has no feedback yet: the panel is then left out. */
  supported: boolean
  items: FeedbackItem[]
  canWrite: boolean
  stageClock: StageClock | null
}

export interface OptionalDocument { slot: string; label: string; uploaded: boolean }

export interface ApplicationFormData {
  capabilities?: { deferredDocumentReading: boolean }
  header: { company: string; title: string; address: string; phone: string; email: string }
  candidate: { id: string; ref: string; careId?: string | null; name: string; status: string; needsReview: boolean; ownerId: string; ownerName: string; updatedAt: string; canEdit: boolean }
  application: ApplicationRef | null
  applications: ApplicationRef[]
  personal: PersonalDetails
  passport: PassportDetails & { expiry: ExpiryInfo }
  education: EducationRow[]
  experience: ExperienceDetails
  /** Every document slot, keyed by slot. */
  documents: Record<DocumentKind, DocumentSlot>
  /** The slots in display order. */
  documentOrder: DocumentKind[]
  optionalDocuments: OptionalDocument[]
  /** Whether the candidate can be deployed, and the documents still to upload before that (the last stage row says so). Absent on an older server. */
  deployment?: { ready: boolean; missing: { slot: string; label: string }[] }
  assignment: AssignmentData
  feedback: FeedbackData
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
