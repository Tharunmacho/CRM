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
}

export interface DocumentSlot {
  key: string
  label: string
  versions: FileVersion[]
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
  phone?: string
  whatsapp?: string
  country?: string
  city?: string
  notes?: string
  status: 'active' | 'inactive'
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
  phone?: string
  whatsapp?: string
  address?: string
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
  kind: 'assignment' | 'candidate_assignment' | 'attendance' | 'escalation' | 'reminder' | 'todo'
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
