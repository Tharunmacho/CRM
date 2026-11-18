/**
 * The thin API layer for the application form (docs/workflows/application-form-contract.md). Everything the server's
 * contract names lives here and in `normalizeForm`, so a change to an endpoint or a payload key touches those two only.
 */
import { api, type SplitReport } from '@/shared/api/client'
import { normalizeForm } from './form-model'
import type { ApplicationFormData, AssignmentChange, DocumentKind, FeedbackKind, FeedbackReason } from './types'

/** Reading a document, a stage finishing, a profile edit and a recruiter note each announce one of these. */
export const FORM_LIVE_TOPICS = ['candidates', 'documents', 'applications', 'feedback'] as const

export interface FormTarget { orderId?: string; applicationId?: string }

function formPath(candidateId: string, target: FormTarget, suffix = '') {
  const query = new URLSearchParams()
  if (target.applicationId) query.set('applicationId', target.applicationId)
  else if (target.orderId) query.set('orderId', target.orderId)
  const text = query.toString()
  return `/candidates/${encodeURIComponent(candidateId)}/application-form${suffix}${text ? `?${text}` : ''}`
}

export async function loadApplicationForm(candidateId: string, target: FormTarget, signal?: AbortSignal): Promise<ApplicationFormData> {
  return normalizeForm(await api.get<unknown>(formPath(candidateId, target), { signal }))
}

export async function saveApplicationForm(candidateId: string, target: FormTarget, body: Record<string, unknown>): Promise<ApplicationFormData> {
  return normalizeForm(await api.put<unknown>(formPath(candidateId, target), body))
}

/** Upload a file into one document slot (resume, passport, photo, education certificate, ...). The server answers at once, reads the document in the background, and the form refreshes live. */
export async function uploadFormDocument(candidateId: string, kind: DocumentKind, file: File, target: FormTarget = {}): Promise<{ form: ApplicationFormData | null; split: SplitReport | null }> {
  const form = new FormData()
  form.append('slot', kind)
  form.append('file', file)
  const result = await api.form<{ form?: unknown; split?: SplitReport; alreadyUploaded?: boolean; message?: string; notes?: string[] }>(formPath(candidateId, target, '/documents'), form)
  // The server never stores a file twice. The same bytes again answer `alreadyUploaded` ("This file is already uploaded (version 2).") and a file that is
  // also filed elsewhere answers quiet `notes`; the screens show `split.message` as their neutral line, so these ride on it. (A file that is another
  // candidate's personal document is a 409: the server's sentence is the error the upload already shows.)
  const quiet = result.alreadyUploaded ? result.message : result.notes?.join(' ')
  return { form: result.form ? normalizeForm(result.form) : null, split: result.split ?? (quiet ? { message: quiet, pieces: [] } : null) }
}

export interface ReadPendingResult { allowed: boolean; queued: number; more: boolean; changed: boolean }

/** Queue the reading of this candidate's stored resume, passport, Aadhaar and education files that were never read, and file the other pages of a bundle already read. Nothing to press: the form calls it when it opens. */
export async function readPending(candidateId: string): Promise<ReadPendingResult> {
  const result = await api.post<Partial<ReadPendingResult>>(`/candidates/${encodeURIComponent(candidateId)}/read-pending`, {})
  return { allowed: result.allowed !== false, queued: Number(result.queued) || 0, more: result.more === true, changed: result.changed === true }
}

export async function extractFormDocuments(candidateId: string, attachmentIds: string[], target: FormTarget = {}): Promise<ApplicationFormData | null> {
  const result = await api.post<{ form?: unknown }>(formPath(candidateId, target, '/extract'), { attachmentIds })
  return result.form ? normalizeForm(result.form) : null
}

/** The plain-text report of how this candidate's documents were read (no names or numbers in it): what the owner pastes to the developer. */
export async function loadDocumentReport(candidateId: string): Promise<string> {
  const result = await api.get<{ report?: unknown }>(`/candidates/${encodeURIComponent(candidateId)}/documents/diagnostics`)
  return typeof result.report === 'string' ? result.report : ''
}

export type IdentityCheck = 'read_again' | 'confirm_name'

/** Judge a rejected document's SAVED reading again (nothing is sent to the reader), or confirm that a name-only mismatch is the right person. */
export async function checkDocumentIdentity(candidateId: string, attachmentId: string, action: IdentityCheck, target: FormTarget = {}): Promise<ApplicationFormData> {
  return normalizeForm(await api.post<unknown>(formPath(candidateId, target, '/identity'), { attachmentId, action }))
}

export async function saveDocumentStatus(candidateId: string, target: FormTarget, kind: DocumentKind, uploaded: boolean, note: string): Promise<ApplicationFormData> {
  return normalizeForm(await api.put<unknown>(formPath(candidateId, target, `/documents/${encodeURIComponent(kind)}/status`), { uploaded, note }))
}

/** Change the client's job, the source, the agency or the recruiter. Partial; the answer is the whole form. */
export async function saveAssignment(candidateId: string, change: AssignmentChange): Promise<ApplicationFormData> {
  return normalizeForm(await api.put<unknown>(`/candidates/${encodeURIComponent(candidateId)}/application-form/assignment`, change))
}

export interface FeedbackDraft { kind: FeedbackKind; reason?: FeedbackReason | ''; body: string; applicationId?: string }

/** Write a recruiter note ("why is this taking time"). The answer is the whole form, with the new note in it. */
export async function addFeedback(candidateId: string, note: FeedbackDraft): Promise<ApplicationFormData> {
  const body = { kind: note.kind, body: note.body.trim(), ...(note.reason ? { reason: note.reason } : {}), ...(note.applicationId ? { applicationId: note.applicationId } : {}) }
  return normalizeForm(await api.post<unknown>(`/candidates/${encodeURIComponent(candidateId)}/application-form/feedback`, body))
}

export async function deleteFeedback(candidateId: string, feedbackId: string): Promise<ApplicationFormData | null> {
  const result = await api.del<{ form?: unknown } | Record<string, unknown>>(`/candidates/${encodeURIComponent(candidateId)}/application-form/feedback/${encodeURIComponent(feedbackId)}`)
  // The contract does not say what a delete answers: use the form when it is one, otherwise the caller refetches.
  return result && typeof result === 'object' && 'personal' in result ? normalizeForm(result) : result && 'form' in result && result.form ? normalizeForm(result.form) : null
}
