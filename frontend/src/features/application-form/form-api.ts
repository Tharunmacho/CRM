/**
 * The thin API layer for the application form (docs/workflows/application-form-contract.md). Everything the server's
 * contract names lives here and in `normalizeForm`, so a change to an endpoint or a payload key touches those two only.
 */
import { api } from '@/shared/api/client'
import { normalizeForm } from './form-model'
import type { ApplicationFormData, DocumentKind } from './types'

/** Reading a document, a stage finishing and a profile edit each announce one of these. */
export const FORM_LIVE_TOPICS = ['candidates', 'documents', 'applications'] as const

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

/** Upload a resume, passport or photo. The server answers at once, reads the document in the background, and the form refreshes live. */
export async function uploadFormDocument(candidateId: string, kind: DocumentKind, file: File): Promise<ApplicationFormData | null> {
  const form = new FormData()
  form.append('slot', kind)
  form.append('file', file)
  const result = await api.form<{ form?: unknown }>(`/candidates/${encodeURIComponent(candidateId)}/application-form/documents`, form)
  return result.form ? normalizeForm(result.form) : null
}
