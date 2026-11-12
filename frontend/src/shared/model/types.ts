/** One entry of the agency's currency list (Users > Agency settings). Inactive codes stay valid on stored records. */
export interface CurrencyOption {
  code: string
  name: string
  symbol: string | null
  active: boolean
  sortOrder: number
}

/** One entry of the agency's job category list (Users > Agency settings). A job title stores the category as plain text. */
export interface JobCategory {
  id: string
  name: string
  active: boolean
  sortOrder: number
}

/** The agency's identity and defaults, served inside /bootstrap and by GET /api/settings. */
export interface AgencySettings {
  name: string
  shortName: string | null
  defaultOrderCurrency: string
  defaultAdvanceCurrency: string
  /** International calling code with a plus sign, for example "+91"; used when matching phone numbers. */
  homeCallingCode: string
  defaultNationality: string | null
  acknowledgementText: string
  updatedAt?: string | null
  updatedBy?: string | null
  currencies: CurrencyOption[]
  /** The managed job categories. Missing on an older server: the screens then use the categories already on job titles. */
  categories?: JobCategory[]
  /** False when the server has no category table yet, so the list cannot be edited. */
  categoriesEditable?: boolean
  /** Read only: set by the server, not editable here. */
  timezone: string
  slaHours: number
  /** False when the server has no settings tables yet (the values shown are last-resort defaults). */
  editable: boolean
  /** Whether new candidates are given to a free recruiter automatically. Off by default; absent on an older server. */
  autoAssign?: boolean
}

export type Role = 'admin' | 'manager' | 'staff'

export interface User {
  id: string
  name: string
  email: string
  phone: string
  role: Role
  active: boolean
  receivesAutoAssign: boolean
  onLeave: boolean
}

export type CandidateStatus =
  | 'new'
  | 'in_process'
  | 'not_interested'
  | 'deployed'
  | 'needs_review'

export type Source = 'email' | 'whatsapp' | 'walk_in' | 'referral' | 'other'

export interface FileVersion {
  id: string
  name: string
  sizeKb: number
  version: number
  /** `/api/files/:id` — opens the file. */
  url: string
  uploadedBy: string
  uploadedAt: string
  extractionPending?: boolean
  identityMismatch?: boolean
}

export interface DocumentSlot {
  key: string
  label: string
  versions: FileVersion[]
  uploaded?: boolean
  fileUploaded?: boolean
  manualStatus?: { uploaded: boolean; by: { id: string | null; name: string | null }; at: string; note: string } | null
}

export interface Candidate {
  id: string
  ref: string
  name: string
  phone: string
  whatsapp?: string
  email?: string
  dob?: string
  /** Free text from OCR or manual entry; absent or null when unknown. */
  gender?: string | null
  nationality: string
  currentCity: string
  languages: string[]
  passportNo?: string
  passportIssue?: string
  passportExpiry?: string
  passportPlace?: string
  designations: string[]
  totalExperience: number
  gulfExperience: number
  skills: string[]
  expectedSalary?: number
  /** Application-form details the list carries (migration 0021). Education and work history are deferred and only in the profile. */
  fatherName?: string | null
  maritalStatus?: string | null
  currentAddress?: string | null
  experienceIndia?: number
  nativePlace?: string | null
  familyMemberNumber?: string | null
  jobDurationPreference?: string | null
  bestTimeToContact?: string | null
  nextFollowUpDate?: string | null
  /** Application-form screening answers (migration 0021). Free text; may hold several countries. */
  countryPreference?: string | null
  /** A value of the "service_charges_status" form-option list; absent on older payloads. */
  serviceChargesStatus?: string
  /** A value of the "interest_level" form-option list. */
  interestLevel?: string | null
  source: Source
  sourceDetail?: string
  status: CandidateStatus
  needsReview?: boolean
  /** Null while a candidate awaits assignment. */
  ownerId: string | null
  addedAt: string
  photoTone: number
  documents: DocumentSlot[]
  resumeText: string
  callOutcome?: {
    called: boolean
    interested: 'yes' | 'no' | 'unreachable'
    remarks: string
    recordedAt: string
  }
}

/** A person. One client can own several companies. */
export interface Client {
  id: string
  name: string
  email?: string
  /** The contact person's job title. */
  designation?: string
  phone?: string
  whatsapp?: string
  country?: string
  city?: string
  notes?: string
  status: 'active' | 'inactive'
  /** People on the client's side (their own staff), shown on the client card. */
  staffContacts?: StaffContact[]
  /** CRM staff (users) who look after this client end to end: candidates matched to the client's jobs are routed to them. */
  staffIds?: string[]
}

/** A person on a client's or reference agency's own team. */
export interface StaffContact {
  name: string
  designation?: string
  phone?: string
  email?: string
}

/** An agency that refers candidates. Same details as a client. */
export interface ReferenceAgency {
  id: string
  /** The contact person. */
  name: string
  agencyName: string
  phone?: string
  email?: string
  address?: string
  /** Town, city or local place, kept separate from a street address. */
  place?: string
  state?: string
  notes?: string
  staffContacts?: StaffContact[]
  /** Sectors the agency specialises in; `All sectors` stands alone. Absent on a server that predates migration 0033. */
  specialisations?: string[]
  /** Countries or regions the agency prefers. */
  preferredCountries?: string[]
  createdAt?: string
}

/** The employer an order is placed for. Always belongs to a client. */
export interface Company {
  id: string
  clientId: string
  name: string
  country?: string
  city?: string
  industry?: string
  contactPerson?: string
  contactEmail?: string
  /** The company's web address, saved as https://... */
  website?: string
  phone?: string
  whatsapp?: string
  address?: string
  /** The state the company is in. */
  state?: string
  notes?: string
  status: 'active' | 'inactive'
  /** The latest signed agreement, if one was uploaded. */
  agreement?: { id: string; name: string; url: string } | null
}

export interface Designation {
  id: string
  title: string
  category: string | null
  description: string | null
  requiredSkills: string[]
  minExperience: number
}

export type OrderStatus = 'open' | 'on_hold' | 'filled' | 'closed'

export interface Order {
  id: string
  ref: string
  companyId: string
  designationId: string
  openings: number
  country: string | null
  city: string | null
  salary: number | null
  currency: string
  benefits: string[]
  contractPeriod: string | null
  interviewDate?: string | null
  deadline: string | null
  status: OrderStatus
  createdAt: string
}

export type ApplicationStatus = 'active' | 'closed' | 'deployed'

export type ClosureReason =
  | 'prescreen_failed'
  | 'not_interested'
  | 'resume_not_received'
  | 'interview_rejected'
  | 'medical_unfit'
  | 'unmapped'

export interface StageRecord {
  id?: string
  ownerId?: string
  stage: number
  attempt: number
  data: Record<string, unknown>
  completedAt: string
  completedBy: string
  outcome: string
  reopenedReason?: string
}

export interface Application {
  id: string
  candidateId: string
  orderId: string
  ownerId?: string
  currentStage: number
  status: ApplicationStatus
  closureReason?: ClosureReason
  stages: StageRecord[]
  draft: Record<string, unknown>
  /** Optimistic lock — sent back on save so a stale write is refused, not merged. */
  lockVersion: number
  createdAt: string
  updatedAt: string
}

export interface Remark {
  id: string
  taskId: string
  body: string
  authorId: string
  createdAt: string
}

export type TaskKind = 'initial_call' | 'stage' | 'remedical'

export interface Task {
  id: string
  kind: TaskKind
  title: string
  candidateId: string
  applicationId?: string
  stage?: number
  ownerId: string
  createdAt: string
  completedAt?: string
  cancelledAt?: string
  escalated: boolean
  escalatedAt?: string
  slaStartedAt?: string
  dueAt?: string
}

export type Priority = 'high' | 'normal' | 'low'

export interface TodoItem {
  id: string
  ownerId: string
  title: string
  notes?: string
  dueAt: string
  priority: Priority
  done: boolean
  linkLabel?: string
  linkTo?: string
}

export interface MailAttachment {
  name: string
  sizeKb: number
  kind: 'pdf' | 'doc' | 'image'
}

export interface MailMessage {
  documentBundle?: { id: string; status: string; candidateIds: string[] } | null
  id: string
  accountId: string
  fromName: string
  fromAddress: string
  subject: string
  preview: string
  body: string
  receivedAt: string
  unread: boolean
  attachments: MailAttachment[]
  candidateId?: string
}

export interface Notification {
  id: string
  /** Every kind the backend writes: workflow scheduler, assignments, intake and attendance. */
  kind: 'assignment' | 'candidate_assignment' | 'attendance' | 'escalation' | 'reminder' | 'todo' | 'daily_work'
  title: string
  detail: string
  createdAt: string
  read: boolean
  linkTo?: string
}

export interface AuditEntry {
  id: string
  actorId: string
  action: string
  target: string
  at: string
}
