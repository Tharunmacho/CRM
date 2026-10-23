/**
 * API client.
 *
 * Requests carry the session cookie, so a refresh keeps you signed in. Errors are
 * surfaced as a typed exception rather than swallowed — a failed save must be visible,
 * since nothing here autosaves and a silent failure would lose a recruiter's work.
 */
import type { MailMessage } from '@/shared/model/types'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /** The record was changed by someone else since it was loaded. */
  get isConflict() {
    return this.status === 409
  }

  get isUnauthorised() {
    return this.status === 401
  }
}

/** Same origin in production, where one container serves the API and the SPA. */
const BASE = import.meta.env?.VITE_API_URL ?? ''

/** Where a stored file (`/api/files/:id`) opens: the API's own origin, so the session cookie goes with it. */
export function fileHref(url: string) {
  return `${BASE}${url}`
}

/** Resolve public API links against the same base used by API requests. */
export function absoluteApiHref(path: string) {
  return new URL(fileHref(path), window.location.origin).href
}

interface RequestOptions {
  signal?: AbortSignal
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const controller = new AbortController()
  const cancel = () => controller.abort()
  if (options.signal?.aborted) cancel()
  else options.signal?.addEventListener('abort', cancel, { once: true })
  let timedOut = false
  const timeout = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, 30_000)

  try {
    const res = await fetch(`${BASE}/api${path}`, {
      method,
      credentials: 'include',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })

    if (res.status === 204) return undefined as T

    const text = await res.text()
    const payload = text ? safeParse(text) : null

    if (!res.ok) {
      const detail = (payload as { detail?: unknown } | null)?.detail
      throw new ApiError(res.status, messageFrom(detail) ?? res.statusText, detail)
    }

    if (typeof payload === 'string') {
      throw new ApiError(502, 'The server returned an unexpected response. Please try again.')
    }
    return payload as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (options.signal?.aborted) throw error
    throw new ApiError(
      0,
      timedOut
        ? 'The server took too long to respond. Please try again.'
        : 'Cannot reach the server. Check your connection and try again.',
    )
  } finally {
    clearTimeout(timeout)
    options.signal?.removeEventListener('abort', cancel)
  }
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function messageFrom(detail: unknown): string | undefined {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((item) => typeof item?.msg === 'string' ? `${(item.loc ?? []).filter((part: unknown) => part !== 'body').join('.')}: ${item.msg}` : '').filter(Boolean).join('; ') || undefined
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String((detail as { message: unknown }).message)
  }
  return undefined
}

export interface UploadedFile {
  id: string
  name: string
  sizeKb: number
  version: number
  url: string
  uploadedAt: string
}

interface FormRequestOptions extends RequestOptions {
  timeoutMs?: number
}

async function postForm<T>(
  path: string,
  form: FormData,
  failure: string,
  options: FormRequestOptions = {},
): Promise<T> {
  const controller = new AbortController()
  const cancel = () => controller.abort()
  if (options.signal?.aborted) cancel()
  else options.signal?.addEventListener('abort', cancel, { once: true })
  let timedOut = false
  const timeout = options.timeoutMs === undefined ? undefined : setTimeout(() => {
    timedOut = true
    controller.abort()
  }, options.timeoutMs)

  try {
    const res = await fetch(`${BASE}/api${path}`, {
      method: 'POST', credentials: 'include', body: form, signal: controller.signal,
    })
    const text = await res.text()
    const payload = text ? safeParse(text) : null
    if (!res.ok) {
      const detail = (payload as { detail?: unknown } | null)?.detail
      throw new ApiError(res.status, messageFrom(detail) ?? failure, detail)
    }
    if (!payload || typeof payload !== 'object') {
      throw new ApiError(502, 'The server returned an unexpected response. Please try again.')
    }
    return payload as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (options.signal?.aborted) throw error
    if (timedOut) {
      throw new ApiError(0, 'Document reading exceeded 20 minutes. You can retry or enter the details manually.')
    }
    throw new ApiError(0, `Cannot reach the server. ${failure}`)
  } finally {
    if (timeout !== undefined) clearTimeout(timeout)
    options.signal?.removeEventListener('abort', cancel)
  }
}

/**
 * Files upload the moment they are chosen rather than when the form is submitted, so a
 * lost connection cannot take a passport scan with it.
 */
async function upload(
  entityType: 'client' | 'company' | 'candidate' | 'application',
  entityId: string,
  slot: string,
  file: File,
): Promise<UploadedFile> {
  const form = new FormData()
  form.append('entity_type', entityType)
  form.append('entity_id', entityId)
  form.append('slot', slot)
  form.append('file', file)
  return postForm<UploadedFile>('/files', form, 'The file was not uploaded.')
}

/** What the OCR read, named after the add-candidate form's fields. Absent means unsure. */
export interface OcrFields {
  name?: string
  phone?: string
  whatsapp?: string
  email?: string
  dob?: string
  gender?: string
  nationality?: string
  languages?: string[]
  passportNo?: string
  passportIssue?: string
  passportExpiry?: string
  passportPlace?: string
  designation?: string
  totalExperience?: number
  gulfExperience?: number
  skills?: string[]
}

export interface OcrInspection {
  page_count: number
  resume_pages: number[]
  passport_pages?: number[]
  aadhaar_pages?: number[]
  selected_pages?: number[]
  unread_pages: number[]
  unknown_pages: number[]
  source_format?: string
  pages: {
    page_number: number
    status: 'read' | 'blank' | 'unread'
    document_type: 'resume' | 'passport' | 'aadhaar' | 'other' | 'unknown'
    method: string
    reason?: string
    confidence?: number
  }[]
  warnings: string[]
}

/** Our own provenance for a scan, kept beside (never inside) the verbatim Veris result. `{}` when nothing was extracted. */
export interface OcrScanSource {
  local_inspection?: Record<string, unknown>
  source_sha256?: string
  selected_pages?: number[]
  [key: string]: unknown
}

export interface OcrScan {
  fields: OcrFields
  warnings: string[]
  /** The verbatim Veris result, sent back unchanged with the save. Its page numbers are Veris's own 1..n. */
  raw: Record<string, unknown>
  /** Provenance for `raw`, sent back as `ocr_data._sources[kind]`. Absent on older responses, which kept it inside `raw`. */
  source?: OcrScanSource
  /** Optional while passport and older deployments retain the existing response. */
  inspection?: OcrInspection
  extraction_status?: 'complete' | 'needs_review' | 'no_resume'
}

export type MailProvider = 'gmail' | 'microsoft' | 'imap' | 'zoho'

export interface InboxAccount {
  id: string
  address: string
  provider: MailProvider
  providerLabel?: string
  messageCount?: number
  unreadCount?: number
  autoIngest: boolean
  lastSyncAt: string | null
  configured: boolean
  connectionStatus: string
  lastError: string | null
  disconnectedAt?: string | null
}

export interface InboxInspection {
  attachmentId: string
  fileName: string
  pageCount: number
  resumePages: number[]
  passportPages: number[]
  aadhaarPages: number[]
  unreadPages: number[]
  unknownPages: number[]
  status: string
}

export interface InboxJob {
  id: string
  messageId: string
  status: 'pending' | 'processing' | 'retry' | 'needs_review' | 'completed' | 'ignored' | 'failed'
  attempts: number
  lastError: string | null
  candidateId: string | null
  updatedAt: string
  inspection: InboxInspection[]
}

export interface InboxSnapshot {
  accounts: InboxAccount[]
  availableAccounts: string[]
  providers?: { provider: MailProvider; label: string; ready: boolean; message: string }[]
  total?: number
  offset?: number
  limit?: number
  hasMore?: boolean
  messages: (Omit<MailMessage, 'attachments'> & {
    attachments: { id: string; fileName: string; sizeKb: number; kind: string; url?: string }[]
  })[]
  jobs: InboxJob[]
  worker: { enabled: boolean; lastHeartbeat: string | null; extractionHeartbeat?: string | null }
}

export interface InboxHealth {
  accounts: Pick<InboxAccount, 'id' | 'address' | 'lastSyncAt' | 'lastError' | 'autoIngest'>[]
  counts: Record<InboxJob['status'], number>
  worker: InboxSnapshot['worker']
}

export type AttentionSource = 'email' | 'whatsapp' | 'manual'

/** One failed or needs-review intake, whichever channel it arrived through. */
export interface AttentionItem {
  id: string
  source: AttentionSource
  jobId: string
  messageId: string | null
  groupId?: string
  status: 'failed' | 'needs_review'
  title: string
  sender: string
  channel: string
  files: string[]
  reason: string
  attempts: number
  updatedAt: string
  candidateId: string | null
  /** Server-decided: admin only, and only where the matching retry route will accept it. */
  retryable: boolean
}

export interface InboxAttention {
  items: AttentionItem[]
  totals: Record<AttentionSource | 'all', number>
  limit: number
  canRetry: boolean
}

export interface InboxUnreadCount {
  total: number
  accounts: Record<string, number>
}

/** Reads a resume or passport. Nothing is saved; the result only pre-fills the form. */
async function ocr(kind: 'resume' | 'passport', file: File, options: RequestOptions = {}): Promise<OcrScan> {
  const form = new FormData()
  form.append('file', file)
  return postForm<OcrScan>(`/ocr/${kind}`, form, 'The file could not be read.', {
    ...options, timeoutMs: 20 * 60_000,
  })
}

export const api = {
  upload,
  ocr,
  form: <T>(path: string, form: FormData) => postForm<T>(path, form, 'The attachment could not be uploaded.'),
  inbox: {
    get: (options?: RequestOptions) => request<InboxSnapshot>('GET', '/inbox', undefined, options),
    revision: (options?: RequestOptions) => request<{ revision?: string }>('GET', '/inbox/revision', undefined, options),
    health: (options?: RequestOptions) => request<InboxHealth>('GET', '/inbox/health', undefined, options),
    unreadCount: (options?: RequestOptions) => request<InboxUnreadCount>('GET', '/inbox/unread-count', undefined, options),
    attention: (options?: RequestOptions) => request<InboxAttention>('GET', '/inbox/attention', undefined, options),
    connect: (autoIngest: boolean, address?: string) => request<InboxAccount>('POST', '/inbox/connect', {
      auto_ingest: autoIngest, ...(address ? { address } : {}),
    }),
    setAutoIngest: (accountId: string, enabled: boolean) =>
      request<InboxAccount>('PATCH', `/inbox/accounts/${encodeURIComponent(accountId)}`, { auto_ingest: enabled }),
    ingest: (messageId: string) => request<{ status: 'queued' | InboxJob['status'] }>('POST', `/inbox/messages/${encodeURIComponent(messageId)}/ingest`),
    retry: (jobId: string) => request<{ status: 'queued' }>('POST', `/inbox/jobs/${encodeURIComponent(jobId)}/retry`),
  },
  get: <T>(path: string, options?: RequestOptions) => request<T>('GET', path, undefined, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) => request<T>('POST', path, body, options),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
}
