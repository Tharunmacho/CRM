import { useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError, fileHref, type OcrInspection } from '@/shared/api/client'
import type { DocumentScans, ScanKind } from './ocr-form'
import { MAX_DOCUMENT_FILES, isQueued, trackNextInLine, type QueueClock } from './entry-upload'
import { createDraftSaver } from './draft-saver'
export { scansFromJobs } from './ocr-form'

export type IntakeDraft = { form: Record<string, string>; editedFields: string[]; jobIds?: string[]; savedCandidateId?: string | null; splitCandidateIds?: string[] }
export type IntakeKind = ScanKind | 'photo' | 'education' | 'experience'
/** Kinds that take several files (the PRD lists certificates in the plural); every other kind keeps one file and a new one replaces it. */
export const MULTI_FILE_KINDS: readonly IntakeKind[] = ['education', 'experience']
export const MAX_FILES_PER_KIND = 6
export const isMultiFileKind = (kind: IntakeKind) => MULTI_FILE_KINDS.includes(kind)
/** Is a reading worker alive? Sent with every job by newer servers (older ones omit it, which the screen must tolerate). */
export type ReaderState = { alive: boolean; workers: number; busy: number; lastSeenSeconds: number | null; outdated: boolean }
export type WaitingReason = 'reader_not_running' | 'queue' | null
export type IntakeJob = {
  id: string; groupId: string; kind: IntakeKind; filename: string; sizeKb: number; status: string;
  lastError: string | null; candidateId: string | null; attachmentId: string | null;
  /** How many reading attempts have failed so far (the server gives up after 4). Older servers omit it. */
  attempts?: number;
  /** Newer servers also send the attempt limit, a short safe reason for the last failure and when the job is next ready. Older ones omit them. */
  maxAttempts?: number; lastErrorHint?: string | null; readyAt?: string | null;
  originalUrl: string; createdAt: string; updatedAt: string;
  /** Where a waiting job stands in line (integers while pending or retrying, null otherwise). Older servers omit them. */
  queuedAt?: string | null; startedAt?: string | null; finishedAt?: string | null; queuePosition?: number | null; aheadOfYou?: number | null;
  /** Why a waiting file is waiting: no reader is running (after about a minute) or others come first. Older servers omit it. */
  waitingReason?: WaitingReason; reader?: ReaderState | null;
  result?: { scans?: DocumentScans; inspection?: OcrInspection; warnings?: string[]; /** What was read and what was not (additive: older servers omit it). */ coverage?: unknown }; draft?: IntakeDraft
}
const active = new Set(['pending', 'processing', 'retry'])
const SCAN_KINDS: readonly string[] = ['resume', 'passport', 'aadhaar']
/** How often the server is asked while any file is queued or being read. */
export const READ_POLL_MS = 1000
/** The slower look while nothing is being waited on (after Stop waiting, or after coming back to a saved upload). */
export const IDLE_POLL_MS = 5000
const pause = (signal: AbortSignal, delay = READ_POLL_MS) => new Promise<void>((resolve, reject) => {
  const cancel = () => { clearTimeout(timer); reject(new DOMException('Stopped waiting', 'AbortError')) }
  const timer = setTimeout(() => { signal.removeEventListener('abort', cancel); resolve() }, delay)
  signal.addEventListener('abort', cancel, { once: true })
  if (signal.aborted) cancel()
})
/** Resolves at once while the tab is visible; while it is hidden, waits for it to come back (so a hidden tab sends no requests at all). */
const whenVisible = (signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  if (typeof document === 'undefined' || document.visibilityState !== 'hidden') { resolve(); return }
  const done = () => { document.removeEventListener('visibilitychange', changed); signal.removeEventListener('abort', cancel) }
  const changed = () => { if (document.visibilityState !== 'hidden') { done(); resolve() } }
  const cancel = () => { done(); reject(new DOMException('Stopped waiting', 'AbortError')) }
  document.addEventListener('visibilitychange', changed)
  signal.addEventListener('abort', cancel, { once: true })
  if (signal.aborted) cancel()
})
/** A clock for the queued rows: ticks every second while any file waits (and not at all otherwise), and remembers since when each file has been "next in line". */
export function useQueueClock(jobs: IntakeJob[]): QueueClock {
  const waiting = jobs.some((job) => isQueued(job))
  const [now, setNow] = useState(() => Date.now())
  const since = useRef<Record<string, number>>({})
  useEffect(() => {
    if (!waiting) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [waiting])
  return useMemo(() => {
    since.current = trackNextInLine(since.current, jobs, now)
    return { now, nextSince: since.current }
  }, [jobs, now])
}

export function useDocumentIntake(ownerId: string) {
  const group = useRef<string>(crypto.randomUUID())
  const current = useRef<IntakeJob[]>([])
  const [jobs, setJobs] = useState<IntakeJob[]>([])
  const [uploading, setUploading] = useState(false)
  const [draftError, setDraftError] = useState('')
  const mounted = useRef(true)
  const update = (value: IntakeJob[]) => { current.current = value; if (mounted.current) setJobs(value) }
  // A fresh page starts clean: earlier uploads are never listed or reopened here (they stay saved on the server).
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; void saver.current?.flush().catch(() => {}) } // leaving the page: send what is still waiting
  }, [ownerId])

  // Draft autosave (see draft-saver.ts): typing is debounced to one request per 2.5 s, a request is never sent while another is
  // out, an unchanged draft is not sent, and what changed meanwhile goes in ONE follow-up with the latest values.
  const saver = useRef<ReturnType<typeof createDraftSaver<IntakeDraft>> | null>(null)
  if (!saver.current) {
    saver.current = createDraftSaver<IntakeDraft>({
      // The draft endpoint holds at most 60 job ids; the files beyond that stay saved in the upload and are read as usual.
      send: (draft) => api.put('/ocr/jobs/groups/' + group.current + '/draft', { ...draft, jobIds: current.current.map((job) => job.id).slice(0, 60) }),
      onResult: (error) => { if (mounted.current) setDraftError(error ? 'We could not save your latest changes. Keep this page open and try again.' : '') },
    })
  }
  const withIds = (draft: IntakeDraft): IntakeDraft => ({ ...draft, jobIds: current.current.map((job) => job.id).slice(0, 60) })
  /** Save now and wait for it (before reading, attaching or saving the candidate); an unchanged draft costs nothing. Rejects when saving failed. */
  const saveDraft = async (draft: IntakeDraft) => {
    if (!current.current.length) return
    await saver.current!.flush(withIds(draft))
  }
  /** Typing: plan ONE request at most every 2.5 s with the latest values. Returns at once. */
  const queueDraft = (draft: IntakeDraft) => {
    if (!current.current.length) return
    saver.current!.queue(withIds(draft))
  }
  /** Leaving a field, hiding the tab or leaving the page: send whatever is still waiting, now. */
  const flushDraft = () => { if (current.current.length) void saver.current!.flush().catch(() => {}) }
  const sending = useRef(0)
  /**
   * `many` adds the file next to the others instead of replacing the one of its kind. The documents zone uses it: any file may hold
   * a resume, a passport and an Aadhaar card, so every file is sent as kind "resume" (the server classifies the pages of every file and
   * reads each kind it finds) and none of them replaces another.
   */
  const upload = async (kind: IntakeKind, file: File, options: { many?: boolean } = {}) => {
    const many = Boolean(options.many) || isMultiFileKind(kind)
    sending.current += 1; setUploading(true)
    const done = () => { sending.current -= 1; if (mounted.current && sending.current <= 0) { sending.current = 0; setUploading(false) } }
    if (isMultiFileKind(kind) && current.current.filter((value) => value.kind === kind).length >= MAX_FILES_PER_KIND) { done(); const message = `You can add up to ${MAX_FILES_PER_KIND} files here.`; throw new Error(message) }
    if (options.many && current.current.filter((value) => SCAN_KINDS.includes(value.kind)).length >= MAX_DOCUMENT_FILES) { done(); const message = `You can add up to ${MAX_DOCUMENT_FILES} files here.`; throw new Error(message) }
    const form = new FormData()
    form.append('kind', kind); form.append('group_id', group.current); form.append('file', file)
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 120000)
    try {
      const response = await fetch(fileHref('/api/ocr/jobs'), { method: 'POST', credentials: 'include', body: form, signal: controller.signal })
      const payload = await response.json()
      if (!response.ok) throw new ApiError(response.status, typeof payload.detail === 'string' ? payload.detail : 'We could not save the file. Please try again.')
      const job = payload as IntakeJob
      if (job.status === 'dismissed') throw new Error('You removed this exact file from this upload earlier. Add it from the candidate profile after saving, or use a different copy.')
      if (many) {
        // Another file of the same kind is added next to the others; the same file twice returns the job it already has.
        update([...current.current.filter((value) => value.id !== job.id), job])
        return job
      }
      const previous = current.current.find((value) => value.kind === kind)
      update([...current.current.filter((value) => value.kind !== kind), job])
      if (previous && previous.id !== job.id) await api.post('/ocr/jobs/' + previous.id + '/dismiss').catch(() => {})
      return job
    } catch (err) {
      const message = err instanceof Error && err.name !== 'AbortError' ? err.message : 'The upload did not finish. Please try again.'
      throw new Error(message)
    } finally { clearTimeout(timeout); done() }
  }
  const replaceJob = (saved: IntakeJob) => update(current.current.map((value) => value.id === saved.id ? saved : value))
  /** Puts the server's latest view of this upload over what the page has: files added or removed meanwhile are kept as the page has them. */
  const merge = (fetched: IntakeJob[]) => {
    const latest = new Map(fetched.map((job) => [job.id, job]))
    update(current.current.map((job) => latest.get(job.id) ?? job))
    return current.current
  }
  const isReadable = (job: IntakeJob) => SCAN_KINDS.includes(job.kind)
  /** A saved file awaiting the user's explicit Extract details action. */
  const hasUnqueued = () => current.current.some((job) => isReadable(job) && job.status === 'uploaded')
  /** Puts one file back in the queue (Failed, or Needs a look). */
  const retry = async (jobId: string) => {
    const saved = await api.post<IntakeJob>('/ocr/jobs/' + jobId + '/retry')
    replaceJob(saved)
    return saved
  }
  /**
   * Reads only the files present when the user presses Extract details. Files added later wait for another explicit action.
   * Polls once a second while this batch is queued or reading. `jobIds` scopes a single-file retry without starting other uploads.
   */
  const read = async (signal: AbortSignal, options: { retryFailed?: boolean; jobIds?: string[]; onProgress?: (jobs: IntakeJob[]) => void } = {}) => {
    const { retryFailed = true, onProgress } = options
    const batchIds = new Set(current.current.filter((job) => isReadable(job) && (!options.jobIds || options.jobIds.includes(job.id))).map((job) => job.id))
    let stopAt = Date.now() + 20 * 60000
    let first = true
    // A file the server refuses to queue (for example one that holds several people) must not stop the others: it shows as Failed with the reason.
    const refused = new Map<string, string>()
    while (!signal.aborted) {
      for (const job of [...current.current]) {
        if (!batchIds.has(job.id) || refused.has(job.id)) continue
        if (job.status === 'uploaded' || (first && retryFailed && ['failed', 'needs_review', 'dismissed'].includes(job.status))) {
          try { await retry(job.id) }
          catch (err) { if (signal.aborted) throw err; refused.set(job.id, err instanceof Error ? err.message : 'We could not read this file.') }
        }
      }
      first = false
      const fetched = await api.get<IntakeJob[]>('/ocr/jobs/groups/' + group.current, { signal })
      merge(fetched)
      if (refused.size) update(current.current.map((job) => refused.has(job.id) ? { ...job, status: 'failed', lastError: refused.get(job.id)! } : job))
      const values = current.current
      onProgress?.(values)
      if (!values.some((job) => batchIds.has(job.id) && (active.has(job.status) || job.status === 'uploaded'))) return values
      if (Date.now() > stopAt) throw new Error('We are still reading. Your files are saved. Come back to this upload later.')
      // The next request is sent only after the previous answer (this loop never overlaps itself), READ_POLL_MS after it, and not at
      // all while the tab is hidden: it is sent the moment the tab is shown again. Time spent hidden does not count towards stopAt.
      await pause(signal)
      const hiddenAt = Date.now()
      await whenVisible(signal)
      stopAt += Date.now() - hiddenAt
    }
    throw new DOMException('Stopped waiting', 'AbortError')
  }
  /** One look at the upload, without waiting: keeps the rows honest after Stop waiting or when a saved upload is opened. */
  const refresh = async (signal?: AbortSignal) => {
    if (!current.current.length) return current.current
    return merge(await api.get<IntakeJob[]>('/ocr/jobs/groups/' + group.current, { signal }))
  }
  const attach = async (candidateId: string) => {
    const failures: Record<string, string> = {}
    for (const job of current.current) {
      if (job.candidateId === candidateId && job.status === 'attached') continue
      try {
        const saved = await api.post<IntakeJob>('/ocr/jobs/' + job.id + '/attach', { candidateId })
        update(current.current.map((value) => value.id === saved.id ? saved : value))
      } catch (err) { failures[job.kind] = job.filename + ': ' + (err instanceof Error ? err.message : 'This file could not be added to the candidate') }
    }
    return failures
  }
  /** Takes one file out of this upload before the candidate is saved (it is set aside on the server, never deleted). */
  const remove = async (jobId: string) => {
    const job = current.current.find((value) => value.id === jobId)
    if (!job) return current.current
    await api.post('/ocr/jobs/' + jobId + '/dismiss')
    update(current.current.filter((value) => value.id !== jobId))
    return current.current
  }
  const reset = () => {
    group.current = crypto.randomUUID()
    update([])
    setDraftError('')
  }
  return { jobs, uploading, draftError, upload, remove, read, retry, refresh, hasUnqueued, attach, saveDraft, queueDraft, flushDraft, reset }
}
