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

/** What a GET returned before it is turned into a value or an error. Shared by every caller of the same read. */
interface RawResponse { status: number; ok: boolean; statusText: string; text: string }

interface SharedRead { startedAt: number; refs: number; controller: AbortController; done: Promise<RawResponse> }

/**
 * Identical GETs that overlap share one network request (React StrictMode replays effects in development,
 * and two components can mount wanting the same list). Each caller keeps its own AbortSignal: the request is
 * cancelled only when every caller has gone. A read is joined only within JOIN_WINDOW_MS of starting, and any
 * write empties the table, so a read that began before a write is never handed to a caller that asked after it.
 */
const JOIN_WINDOW_MS = 300
const reads = new Map<string, SharedRead>()

async function fetchRaw(method: string, path: string, body: unknown, controller: AbortController, timeout: { fired: boolean }): Promise<RawResponse> {
  try {
    const res = await fetch(`${BASE}/api${path}`, {
      method,
      credentials: 'include',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })
    if (res.status === 204) return { status: 204, ok: true, statusText: res.statusText, text: '' }
    return { status: res.status, ok: res.ok, statusText: res.statusText, text: await res.text() }
  } catch (error) {
    if (controller.signal.aborted && !timeout.fired) throw error // cancelled by the caller, not a failure
    throw new ApiError(
      0,
      timeout.fired
        ? 'The server took too long to respond. Please try again.'
        : 'Cannot reach the server. Check your connection and try again.',
    )
  }
}

function startRead(method: string, path: string, body: unknown): SharedRead {
  const controller = new AbortController()
  const timeout = { fired: false }
  const timer = setTimeout(() => {
    timeout.fired = true
    controller.abort()
  }, 30_000)
  const done = fetchRaw(method, path, body, controller, timeout).finally(() => clearTimeout(timer))
  done.catch(() => {}) // a read every caller abandoned must not surface as an unhandled rejection
  return { startedAt: Date.now(), refs: 0, controller, done }
}

function interpret<T>(raw: RawResponse): T {
  if (raw.status === 204) return undefined as T
  const payload = raw.text ? safeParse(raw.text) : null

  if (!raw.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail
    throw new ApiError(raw.status, messageFrom(detail) ?? raw.statusText, detail)
  }

  if (typeof payload === 'string') {
    throw new ApiError(502, 'The server returned an unexpected response. Please try again.')
  }
  return payload as T
}

function abortError() {
  return new DOMException('The operation was aborted.', 'AbortError')
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const signal = options.signal
  if (signal?.aborted) throw abortError()

  if (method !== 'GET') {
    reads.clear()
    const write = startRead(method, path, body)
    const cancel = () => write.controller.abort()
    signal?.addEventListener('abort', cancel, { once: true })
    try {
      return interpret<T>(await write.done)
    } finally {
      signal?.removeEventListener('abort', cancel)
      reads.clear()
    }
  }

  const key = `${BASE}/api${path}`
  let read = reads.get(key)
  if (!read || read.controller.signal.aborted || Date.now() - read.startedAt > JOIN_WINDOW_MS) {
    read = startRead(method, path, undefined)
    reads.set(key, read)
    const created = read
    created.done.then(() => {}, () => {}).finally(() => { if (reads.get(key) === created) reads.delete(key) })
  }
  const shared = read
  shared.refs += 1
  let leave = () => {}
  const gone = new Promise<never>((_, reject) => {
    if (!signal) return
    const onAbort = () => {
      reject(abortError())
      shared.refs -= 1
      if (shared.refs <= 0) {
        shared.controller.abort()
        if (reads.get(key) === shared) reads.delete(key)
      }
    }
    signal.addEventListener('abort', onAbort, { once: true })
    leave = () => signal.removeEventListener('abort', onAbort)
  })
  gone.catch(() => {})
  try {
    return interpret<T>(await (signal ? Promise.race([shared.done, gone]) : shared.done))
  } finally {
    leave()
  }
}

/** A read the page started from index.html before this code had loaded (session + workspace, dashboard). */
interface EarlyRead { path: string; startedAt: number; response: Promise<Response> }

declare global {
  interface Window { __crmEarly?: Record<string, EarlyRead | undefined> }
}

/**
 * Take the response of a request that index.html already started, so the first screen does not wait for the
 * JavaScript bundle before the server begins working. It is handed over once, only for the same path and only
 * shortly after page load. Any problem (not OK, not JSON, slow) returns null and the caller makes the normal
 * request, so this can only make startup faster, never different.
 */
export async function takeEarlyRead<T>(key: string, path: string): Promise<T | null> {
  const early = typeof window === 'undefined' ? undefined : window.__crmEarly?.[key]
  if (!early) return null
  delete window.__crmEarly![key]
  if (early.path !== path || Date.now() - early.startedAt > 20_000) return null
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const res = await Promise.race([
      early.response,
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), 30_000) }),
    ])
    if (!res.ok || res.status === 204) return null
    const payload = safeParse(await res.text())
    return payload !== null && typeof payload === 'object' ? (payload as T) : null
  } catch {
    return null
  } finally {
    if (timer !== undefined) clearTimeout(timer)
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

/** The two change signals the desk polls, in one response: the intake revision and the unread badge. */
export interface InboxPulse {
  revision?: string
  unread?: InboxUnreadCount
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
    pulse: (options?: RequestOptions) => request<InboxPulse>('GET', '/inbox/pulse', undefined, options),
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
