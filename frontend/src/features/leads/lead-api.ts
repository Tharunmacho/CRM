/**
 * Every request the Lead sheet makes, in one place, so a change in the API (docs/workflows/lead-sheet-contract.md)
 * is a change to this file only. Reads go through the shared client (identical overlapping reads are shared, writes
 * and the live "leads" topic drop copies); the file upload goes through `api.form`.
 */
import { ApiError, api, fileHref } from '@/shared/api/client'
import {
  importErrorMessage, importFormFields,
  type ExtraColumn, type ImportResponse, type Lead, type LeadBatch, type LeadFacets, type LeadPage, type LeadSummary,
} from './lead-model'
import type { LeadGate } from './lead-gate'

export interface LeadActivity {
  id: string
  kind: string
  summary?: string | null
  userId?: string | null
  userName?: string | null
  meta?: Record<string, unknown> | null
  createdAt: string
}

export interface LeadResumeFile { id: string; name: string; sizeKb: number; version: number; url: string; uploadedAt: string }

export interface LeadDetail extends Lead {
  extra?: Record<string, string> | null
  raw?: Record<string, string> | null
  activities: LeadActivity[]
  resume?: LeadResumeFile | null
  dailyWorkGate?: LeadGate | null
}

export type LeadSummaryResponse = LeadSummary & { dailyWorkGate?: LeadGate | null }

export interface ConvertResponse {
  lead: Lead
  candidate: { id: string; ref: string; name: string; ownerId?: string | null; ownerName?: string | null; taskId?: string | null }
  resumeAttached: boolean
  resumeError?: string | null
}

export interface UnconvertCheck {
  allowed: boolean
  reason: string | null
  candidate: { id: string; ref: string; name: string } | null
  restoredStatus: string | null
}

export interface UnconvertResponse {
  lead: Lead
  removedCandidateId: string
  restoredStatus: string
  alreadyReversed: boolean
}

export const checkLeadUnconversion = (id: string, signal?: AbortSignal) =>
  api.get<UnconvertCheck>(`/leads/${encodeURIComponent(id)}/unconvert-check`, { signal, fresh: true })

export const unconvertLead = (id: string, candidateId: string, reason: string) =>
  api.post<UnconvertResponse>(`/leads/${encodeURIComponent(id)}/unconvert`, { candidateId, reason })

/** The candidate a 409 `candidate_exists` names. */
export interface ExistingCandidate {
  id: string
  ref: string
  name: string
  phone?: string | null
  ownerId?: string | null
  ownerName?: string | null
  matchedOn?: string[]
  canMerge?: boolean
}

export interface CallBody { outcome: string; remarks: string; nextFollowUpAt: string | null }

const enc = encodeURIComponent

export const leadApi = {
  list: (query: string, page: number, pageSize: number, signal?: AbortSignal) =>
    api.get<LeadPage>(`/leads?${query}&page=${page}&page_size=${pageSize}`, { signal }),
  summary: (scope: 'mine' | 'all', signal?: AbortSignal) => api.get<LeadSummaryResponse>(`/leads/summary?scope=${scope}`, { signal }),
  facets: (signal?: AbortSignal) => api.get<LeadFacets>('/leads/facets', { signal }),
  columns: (query: string, signal?: AbortSignal) => api.get<{ columns: ExtraColumn[] }>(`/leads/columns${query ? `?${query}` : ''}`, { signal }),
  detail: (id: string, signal?: AbortSignal) => api.get<LeadDetail>(`/leads/${enc(id)}`, { signal }),
  batches: (page: number, pageSize: number, signal?: AbortSignal) =>
    api.get<{ items: LeadBatch[]; total: number; page: number; pageSize: number }>(`/leads/batches?page=${page}&page_size=${pageSize}`, { signal }),
  update: (id: string, body: Record<string, unknown>) => api.patch<Lead>(`/leads/${enc(id)}`, body),
  logCall: (id: string, body: CallBody) => api.post<Lead>(`/leads/${enc(id)}/call`, body),
  assign: (ids: string[], ownerId: string) => api.post<{ assigned: number; skipped: number }>('/leads/assign', { ids, ownerId }),
  linkCandidate: (id: string, candidateId: string) => api.post<Lead>(`/leads/${enc(id)}/link-candidate`, { candidateId }),
  deleteBatch: (id: string) => api.del<{ deleted: number; kept: number }>(`/leads/batches/${enc(id)}`),
}

/** Dry run or real import of one file. Dry runs save nothing. */
export async function importLeads(
  file: File,
  options: { dryRun: boolean; mapping?: Record<string, string | null>; assignTo?: string; label?: string },
): Promise<ImportResponse> {
  const form = new FormData()
  form.append('file', file)
  for (const [key, value] of Object.entries(importFormFields(options))) form.append(key, value)
  try {
    return await api.form<ImportResponse>('/leads/import', form)
  } catch (error) {
    if (error instanceof ApiError) {
      const message = error.status >= 400 && error.status < 500 ? error.message : undefined
      throw new ApiError(error.status, importErrorMessage(error.status, 'The file could not be read. Please try again.', message), error.detail)
    }
    throw error
  }
}

/** Attach the updated resume to a lead before converting it (the lead becomes "Resume received"). */
export async function uploadLeadResume(leadId: string, file: File): Promise<LeadResumeFile> {
  const form = new FormData()
  form.append('entity_type', 'lead')
  form.append('entity_id', leadId)
  form.append('slot', 'resume')
  form.append('file', file)
  return api.form<LeadResumeFile>('/files', form)
}

/** Convert a lead to a candidate, with the updated resume when the recruiter has it (multipart). */
export async function convertLead(id: string, resume: File | null, overrides: Record<string, unknown> = {}, resumeCheckId?: string): Promise<ConvertResponse> {
  const form = new FormData()
  if (resume) form.append('resume', resume)
  if (resumeCheckId) form.append('resume_check_id', resumeCheckId)
  if (Object.keys(overrides).length) form.append('overrides', JSON.stringify(overrides))
  return api.form<ConvertResponse>(`/leads/${enc(id)}/convert`, form)
}

type Detail = { code?: string; message?: string; candidate?: ExistingCandidate; dailyWorkGate?: LeadGate }
const detailOf = (error: unknown): Detail | null => {
  const detail = error instanceof ApiError ? error.detail : null
  return detail && typeof detail === 'object' ? (detail as Detail) : null
}

/** The existing candidate a 409 `candidate_exists` names, or null when the error is something else. */
export function existingCandidateFrom(error: unknown): ExistingCandidate | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null
  const detail = detailOf(error)
  return detail?.code === 'candidate_exists' && detail.candidate ? detail.candidate : null
}

/** The gate carried by a 403 `daily_work_pending`, or null when the error is something else. */
export function gateFromRefusal(error: unknown): LeadGate | null {
  if (!(error instanceof ApiError) || error.status !== 403) return null
  const detail = detailOf(error)
  if (detail?.code !== 'daily_work_pending') return null
  return detail.dailyWorkGate ?? { locked: true, open: 0, total: 0, postponed: 0, message: detail.message ?? null }
}

/** The file name the server chose (Content-Disposition), else the fallback. */
export function fileNameFrom(disposition: string | null, fallback: string): string {
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition ?? '')
  if (!match) return fallback
  try { return decodeURIComponent(match[1].trim()) } catch { return match[1].trim() }
}

async function download(path: string, fileName: string) {
  const response = await fetch(fileHref(`/api${path}`), { credentials: 'include' })
  if (!response.ok) throw new Error('We could not make the file. Please try again.')
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = fileNameFrom(response.headers.get('content-disposition'), fileName)
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** The import report: Excel by default (summary, every row, ignored and invalid), CSV as the plain alternative. */
export const downloadBatchReport = (batchId: string, kind: 'xlsx' | 'csv' = 'xlsx') => download(`/leads/batches/${enc(batchId)}/report.${kind}`, `lead-import-report.${kind}`)
export const downloadLeadExport = (query: string) => download(`/leads/export.xlsx?${query}`, 'lead-sheet.xlsx')

export interface LeadResumeCheck {
  jobId: string
  status: 'pending' | 'processing' | 'verified' | 'rejected' | 'needs_review' | 'failed'
  canConvert: boolean
  message: string | null
}

export async function checkLeadResume(id: string, resume: File | null, name: string): Promise<LeadResumeCheck> {
  const form = new FormData()
  if (resume) form.append('resume', resume)
  form.append('overrides', JSON.stringify({ name }))
  return api.form<LeadResumeCheck>(`/leads/${enc(id)}/resume-check`, form)
}

export const readLeadResumeCheck = (id: string, jobId: string, signal?: AbortSignal) =>
  api.get<LeadResumeCheck>(`/leads/${enc(id)}/resume-check/${enc(jobId)}`, { signal, fresh: true })
