/**
 * The thin API layer for the application form (docs/workflows/application-form-contract.md). Everything the server's
 * contract names lives here and in `normalizeForm`, so a change to an endpoint or a payload key touches those two only.
 */
import { api } from '@/shared/api/client'
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
export async function uploadFormDocument(candidateId: string, kind: DocumentKind, file: File, target: FormTarget = {}): Promise<ApplicationFormData | null> {
  const form = new FormData()
  form.append('slot', kind)
  form.append('file', file)
  if (['resume', 'passport', 'aadhaar'].includes(kind)) form.append('defer_extraction', 'true')
  const result = await api.form<{ form?: unknown }>(formPath(candidateId, target, '/documents'), form)
  return result.form ? normalizeForm(result.form) : null
}

export async function extractFormDocuments(candidateId: string, attachmentIds: string[], target: FormTarget = {}): Promise<ApplicationFormData | null> {
  const result = await api.post<{ form?: unknown }>(formPath(candidateId, target, '/extract'), { attachmentIds })
  return result.form ? normalizeForm(result.form) : null
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
