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

/** The live-update stream (Server-Sent Events); same origin as the API so the session cookie goes with it. */
export function eventsHref() {
  return `${BASE}/api/events`
}

/** Whether the API lives on another origin (npm run dev); EventSource then needs credentials sent explicitly. */
export const apiIsCrossOrigin = BASE !== ''

/** Resolve public API links against the same base used by API requests. */
export function absoluteApiHref(path: string) {
  return new URL(fileHref(path), window.location.origin).href
}

/** Options for a cached read (see `ReadCache` below). */
export interface ReadCacheOptions {
  /** Served without any request while younger than this. */
  ttlMs: number
  /** Between ttlMs and ttlMs + staleMs the old copy is returned at once and re-read in the background. */
  staleMs?: number
  /**
   * What changes this data, named like the live topics the server announces (`settings`, `form_options`, ...).
   * A write whose path starts with the same word as the read, a live change on a listed topic and sign-in/out
   * all drop the copy. The first word of the read's own path is always included.
   */
  tags?: readonly string[]
  /** Remember a failed read this long and answer from it instead of asking the server again (stops a 404 being hammered). */
  errorTtlMs?: number
  /** Called with the new value when a stale copy was re-read in the background and the data changed. */
  onRevalidated?: (value: never) => void
}

interface RequestOptions {
  signal?: AbortSignal
  /** Cache a read-mostly GET. Never use it for data a person must see fresh (leave flags, workflow state). */
  cache?: ReadCacheOptions
  /** Skip the cached copy (the response still refreshes it): a deliberate Refresh button. */
  fresh?: boolean
}

/** What a GET returned before it is turned into a value or an error. Shared by every caller of the same read. */
interface RawResponse { status: number; ok: boolean; statusText: string; text: string }

interface SharedRead { startedAt: number; refs: number; epoch: number; controller: AbortController; done: Promise<RawResponse> }

/**
 * Identical GETs that overlap share one network request (React StrictMode replays effects in development,
 * and two components can mount wanting the same list). Each caller keeps its own AbortSignal: the request is
 * cancelled only when every caller has gone, and only after ABORT_GRACE_MS so a component that unmounts and
 * immediately mounts again (StrictMode, a route change that keeps the widget) reuses it instead of cancelling
 * a request the server has already started. A read is joined only within JOIN_WINDOW_MS of starting, and any
 * write empties the table, so a read that began before a write is never handed to a caller that asked after it.
 */
const JOIN_WINDOW_MS = 5_000
const ABORT_GRACE_MS = 100
const reads = new Map<string, SharedRead>()

/** Bumped whenever cached data may have become wrong; a read that began before the bump is not stored. */
let epoch = 0

interface CacheEntry { raw: RawResponse; at: number; tags: ReadonlySet<string>; revalidating: boolean }
interface ErrorEntry { error: ApiError; at: number; ttlMs: number; tags: ReadonlySet<string> }
const cached = new Map<string, CacheEntry>()
const failed = new Map<string, ErrorEntry>()

/**
 * Forget GETs that are in flight. A live change event means any read that started before it may lack the change,
 * so a refetch triggered by the event must start its own request instead of joining one begun a moment earlier.
 */
export function dropSharedReads() {
  reads.clear()
}

/**
 * Drop cached copies (and in-flight reads) that depend on `tags`; with no tags, everything. Called for every write,
 * for live changes (live.ts) and on sign-in/out, so a cached copy never outlives the change that made it wrong.
 */
export function invalidateReads(tags?: readonly string[]) {
  reads.clear()
  epoch += 1
  if (!tags || tags.includes('*')) {
    cached.clear()
    failed.clear()
    return
  }
  const wanted = new Set(tags)
  for (const [key, entry] of cached) if ([...entry.tags].some((tag) => wanted.has(tag))) cached.delete(key)
  for (const [key, entry] of failed) if ([...entry.tags].some((tag) => wanted.has(tag))) failed.delete(key)
}

/** The word a path starts with: `/form-options/reorder?x=1` -> `form-options`. */
function firstSegment(path: string) {
  return path.replace(/^\/+/, '').split(/[/?#]/, 1)[0] ?? ''
}

/** Numbers for tests and tuning (client.test): how many copies are held. */
export function readCacheSize() {
  return { cached: cached.size, failed: failed.size, inFlight: reads.size }
}

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
  return { startedAt: Date.now(), refs: 0, epoch, controller, done }
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

function copyOf(error: ApiError) {
  return new ApiError(error.status, error.message, error.detail)
}

/** A shared read's answer, and whether it may be stored (no invalidation happened while it was in flight). */
interface ReadAnswer { raw: RawResponse; storable: boolean }

/** Share (or start) the one network read for `key`; resolves with the raw response. */
async function sharedRead(key: string, path: string, signal: AbortSignal | undefined): Promise<ReadAnswer> {
  let read = reads.get(key)
  if (!read || read.controller.signal.aborted || Date.now() - read.startedAt > JOIN_WINDOW_MS) {
    read = startRead('GET', path, undefined)
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
        // Wait a moment: a remount (StrictMode, a widget moving between routes) would otherwise cancel a request the
        // server is already working on and immediately start the same one again.
        setTimeout(() => {
          if (shared.refs > 0) return
          shared.controller.abort()
          if (reads.get(key) === shared) reads.delete(key)
        }, ABORT_GRACE_MS)
      }
    }
    signal.addEventListener('abort', onAbort, { once: true })
    leave = () => signal.removeEventListener('abort', onAbort)
  })
  gone.catch(() => {})
  try {
    const raw = await (signal ? Promise.race([shared.done, gone]) : shared.done)
    return { raw, storable: shared.epoch === epoch }
  } finally {
    leave()
  }
}

function storeRead(key: string, path: string, answer: ReadAnswer, options: ReadCacheOptions) {
  if (!answer.storable) return
  const { raw } = answer
  const tags = new Set([firstSegment(path), ...(options.tags ?? [])])
  if (raw.ok) {
    failed.delete(key)
    cached.set(key, { raw, at: Date.now(), tags, revalidating: false })
  } else if (options.errorTtlMs && options.errorTtlMs > 0) {
    try { interpret(raw) } catch (caught) {
      failed.set(key, { error: caught as ApiError, at: Date.now(), ttlMs: options.errorTtlMs, tags })
    }
  }
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
    // Anything cached about what this write touches is stale the moment it commits; sign-in/out changes whose data it is.
    const segment = firstSegment(path)
    // Presence heartbeats are sent on every focus change and every 30 s and change nothing any read returns; letting them
    // clear the table would split reads that were about to be shared (the daily list asked for by two components).
    const invalidate = segment === 'presence' ? () => {} : () => invalidateReads(segment === 'auth' ? undefined : [segment])
    invalidate()
    const write = startRead(method, path, body)
    const cancel = () => write.controller.abort()
    signal?.addEventListener('abort', cancel, { once: true })
    try {
      return interpret<T>(await write.done)
    } finally {
      signal?.removeEventListener('abort', cancel)
      invalidate()
    }
  }

  const key = `${BASE}/api${path}`
  const policy = options.cache
  if (policy && !options.fresh) {
    const hit = cached.get(key)
    const age = hit ? Date.now() - hit.at : Infinity
    if (hit && age < policy.ttlMs) return interpret<T>(hit.raw)
    if (hit && age < policy.ttlMs + (policy.staleMs ?? 0)) {
      if (!hit.revalidating) {
        hit.revalidating = true
        const before = hit.raw.text
        sharedRead(key, path, undefined).then(
          (answer) => {
            storeRead(key, path, answer, policy)
            if (answer.raw.ok && answer.raw.text !== before) policy.onRevalidated?.(interpret<never>(answer.raw))
          },
          () => { /* keep serving the stale copy; the next read retries */ },
        ).finally(() => { if (cached.get(key) === hit) hit.revalidating = false })
      }
      return interpret<T>(hit.raw)
    }
    const known = failed.get(key)
    if (known && Date.now() - known.at < known.ttlMs) throw copyOf(known.error)
  }

  try {
    const answer = await sharedRead(key, path, signal)
    if (policy) storeRead(key, path, answer, policy)
    return interpret<T>(answer.raw)
  } catch (error) {
    if (error instanceof ApiError && error.isUnauthorised) invalidateReads() // the session is gone: nothing cached is ours any more
    throw error
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
  if (!early) return replayEarlyRead<T>(key, path)
  delete window.__crmEarly![key]
  if (early.path !== path || Date.now() - early.startedAt > 20_000) return null
  const answer = readEarly(early)
  handedOver.set(key, { path, at: Date.now(), answer })
  return (await answer) as T | null
}

async function readEarly(early: EarlyRead): Promise<unknown | null> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const res = await Promise.race([
      early.response,
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), 30_000) }),
    ])
    if (!res.ok || res.status === 204) return null
    const payload = safeParse(await res.text())
    return payload !== null && typeof payload === 'object' ? payload : null
  } catch {
    return null
  } finally {
    if (timer !== undefined) clearTimeout(timer)
  }
}

/**
 * React StrictMode (development) runs a screen's effect, cleans it up and runs it again at once. The first run took the
 * early response and its result was thrown away with the cleanup, so the second run would fetch the same data again.
 * For a moment after the hand-over the same answer (even while it is still arriving) is given once more, as a fresh copy.
 */
const REPLAY_MS = 1_500
const handedOver = new Map<string, { path: string; at: number; answer: Promise<unknown | null> }>()
async function replayEarlyRead<T>(key: string, path: string): Promise<T | null> {
  const found = handedOver.get(key)
  handedOver.delete(key)
  if (!found || found.path !== path || Date.now() - found.at > REPLAY_MS) return null
  const value = await found.answer
  return value === null ? null : (JSON.parse(JSON.stringify(value)) as T)
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
  // An upload changes what other reads return (candidate documents, intake queues): none that began earlier may be reused.
  const dropRelated = () => invalidateReads([firstSegment(path), 'documents', 'candidates'])
  dropRelated()
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
    dropRelated()
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
  options: { deferExtraction?: boolean } = {},
): Promise<UploadedFile> {
  const form = new FormData()
  form.append('entity_type', entityType)
  form.append('entity_id', entityId)
  form.append('slot', slot)
  form.append('file', file)
  if (options.deferExtraction) form.append('defer_extraction', 'true')
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
  /** Application-form details (docx): read from the resume, or the passport where printed. */
  fatherName?: string
  maritalStatus?: string
  currentAddress?: string
  experienceIndia?: number
  education?: { level?: string | null; passedYear?: number | null; institute?: string | null }[]
  workHistory?: { company?: string | null; designation?: string | null; country?: string | null; period?: string | null }[]
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
