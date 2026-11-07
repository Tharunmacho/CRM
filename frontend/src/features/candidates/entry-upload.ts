import type { IntakeJob } from './use-document-intake'

/**
 * The model behind the single "Upload documents" zone on Add candidate. Any file may hold a resume, a passport and
 * an Aadhaar card in any mix; the server classifies the pages, so the page only has to show, per file, how far the
 * reading is and, per kind, what has been found so far. Plain data in, plain data out (no React, no network).
 */
export type DocKind = 'resume' | 'passport' | 'aadhaar'
export const DOC_KINDS: readonly DocKind[] = ['resume', 'passport', 'aadhaar']
export const KIND_NAME: Record<DocKind, string> = { resume: 'Resume', passport: 'Passport', aadhaar: 'Aadhaar' }
/** The draft endpoint accepts 20 job ids; the documents zone leaves room for the photo and the certificates. */
export const MAX_DOCUMENT_FILES = 12

export type Tone = 'good' | 'warn' | 'bad' | 'busy' | 'muted'
const ACTIVE = new Set(['pending', 'processing', 'retry'])
export const isDocKind = (kind: string): kind is DocKind => (DOC_KINDS as readonly string[]).includes(kind)
/** Files in the documents zone: everything the server reads (resume, passport, Aadhaar), never the photo or the certificates. */
export const isDocumentJob = (job: Pick<IntakeJob, 'kind' | 'status'>) => isDocKind(job.kind) && job.status !== 'dismissed'
/** Still to be read or being read (an upload that has not been queued yet counts). */
export const isWorking = (job: Pick<IntakeJob, 'status'>) => ACTIVE.has(job.status) || job.status === 'uploaded'

type Scan = NonNullable<NonNullable<IntakeJob['result']>['scans']>[DocKind]
/** Provenance keys older responses stored inside `raw`; they are not read data (same rule as ocr-form). */
const LEGACY_KEYS = new Set(['local_inspection', 'source_sha256', 'source_file', 'provider_page_number'])
function scanHasData(scan: Scan | undefined): boolean {
  return !!scan && (scan.extraction_status === undefined || scan.extraction_status === 'complete')
    && !!scan.raw && Object.keys(scan.raw).some((key) => !LEGACY_KEYS.has(key))
}

const pagesOf = (value: unknown): number[] => Array.isArray(value) ? value.filter((page): page is number => typeof page === 'number' && Number.isFinite(page)) : []

/** "page 4", "pages 1-3, 5". */
export function pagesText(pages: number[]): string {
  const sorted = [...new Set(pages)].sort((a, b) => a - b)
  if (!sorted.length) return ''
  if (sorted.length === 1) return `page ${sorted[0]}`
  const parts: string[] = []
  let start = sorted[0]
  let previous = start
  for (const page of [...sorted.slice(1), Infinity]) {
    if (page === previous + 1) { previous = page; continue }
    parts.push(start === previous ? String(start) : previous === start + 1 ? `${start}, ${previous}` : `${start}–${previous}`)
    start = previous = page
  }
  return `pages ${parts.join(', ')}`
}

/** What the screen knows about the time: `now` (ms) and since when each file has been "next in line" (see `trackNextInLine`). */
export interface QueueClock { now: number; nextSince?: Record<string, number> }
/** "Next in line" is only a first-moments state: after this long it becomes "Starting…". */
export const NEXT_IN_LINE_MS = 20_000
/** An older server tells nothing about the reader: after this long in line the screen says it is still waiting. */
export const NO_READER_INFO_MS = 45_000
export const READER_DOWN = 'The reading service is not running. Ask your administrator to start it.'
export const READER_OLD = 'The document reader is running an older version. Ask your administrator to restart the CRM server.'
/** The server stops after this many failed reading attempts (a newer server says so itself as `maxAttempts`). */
export const MAX_ATTEMPTS = 4
export const STILL_WAITING = 'Taking longer than usual. Press Check again.'
export const READ_FAILED_NOTE = 'Reading failed. The file is saved.'
const maxAttemptsOf = (job: Pick<IntakeJob, 'maxAttempts'>) => typeof job.maxAttempts === 'number' && job.maxAttempts >= 1 ? Math.floor(job.maxAttempts) : MAX_ATTEMPTS
/** "Reading did not finish. Trying again (attempt 2 of 4)." */
export const retryNote = (job: Pick<IntakeJob, 'attempts' | 'maxAttempts'>) => {
  const max = maxAttemptsOf(job)
  return `Reading did not finish. Trying again (attempt ${Math.min(max, Math.max(1, job.attempts ?? 0) + 1)} of ${max}).`
}
/**
 * The short reason the server may send as `lastErrorHint`, shown only when it is plain words: one line, no stack traces,
 * paths, ids or code names, and never longer than a short sentence. Anything else is dropped (the file is safe either way).
 */
export function safeHint(value: unknown): string {
  if (typeof value !== 'string') return ''
  const line = value.replace(/\s+/g, ' ').trim()
  if (!line || line.length > 140) return ''
  if (/traceback|exception|error:|[\\/]\w|https?:|\b[0-9a-f]{8}-[0-9a-f]{4}-|\b[0-9a-f]{24,}\b|\w+Error\b|\bat \S+:\d+/i.test(line)) return ''
  return /[.!?]$/.test(line) ? line : `${line}.`
}

const WAITING = new Set(['pending', 'retry'])
/** Waiting in the reading queue (saved and queued, not yet being read). */
export const isQueued = (job: Pick<IntakeJob, 'status'>) => WAITING.has(job.status) || job.status === 'uploaded'
const aheadOf = (job: Pick<IntakeJob, 'aheadOfYou' | 'queuePosition'>): number | null => {
  const ahead = typeof job.aheadOfYou === 'number' ? job.aheadOfYou : typeof job.queuePosition === 'number' ? job.queuePosition - 1 : null
  return ahead === null || ahead < 0 ? null : ahead
}
const readerDown = (job: Pick<IntakeJob, 'reader' | 'waitingReason'>) => job.reader?.alive === false || job.waitingReason === 'reader_not_running'
/** Next to be read while a reader is alive: the only time "Next in line" is true. */
export const isNextInLine = (job: IntakeJob) => WAITING.has(job.status) && job.reader?.alive === true && !readerDown(job) && aheadOf(job) === 0
/** Remembers since when each file has been next in line (kept while it stays so, forgotten otherwise). */
export function trackNextInLine(previous: Record<string, number>, jobs: IntakeJob[], now: number): Record<string, number> {
  const next: Record<string, number> = {}
  for (const job of jobs) if (isNextInLine(job)) next[job.id] = previous[job.id] ?? now
  return next
}

/** "Waiting, 3 ahead of you" once the server tells the place in line; plain "Queued" before that. */
export function queueText(job: Pick<IntakeJob, 'aheadOfYou' | 'queuePosition'>): string {
  const ahead = typeof job.aheadOfYou === 'number' ? job.aheadOfYou : typeof job.queuePosition === 'number' ? job.queuePosition - 1 : null
  if (ahead === null || ahead < 0) return 'Queued'
  return ahead === 0 ? 'Next in line' : `Waiting, ${ahead} ahead of you`
}

export interface Finding { kind: DocKind; pages: number[]; read: boolean }
/** What one file turned out to contain, as far as the server has said. A kind is found once pages were assigned to it or it was read. */
export function jobFindings(job: IntakeJob): Finding[] {
  const result = job.result
  if (!result) return []
  const found: Finding[] = []
  for (const kind of DOC_KINDS) {
    const scan = result.scans?.[kind]
    const inspection = scan?.inspection ?? result.inspection
    const pages = pagesOf(scan?.inspection?.selected_pages).length ? pagesOf(scan?.inspection?.selected_pages) : pagesOf((inspection as Record<string, unknown> | undefined)?.[`${kind}_pages`])
    const read = scanHasData(scan)
    if (read || pages.length) found.push({ kind, pages, read })
  }
  return found
}

export interface FileRow {
  id: string
  name: string
  sizeKb: number
  url: string
  state: 'queued' | 'reading' | 'done' | 'review' | 'failed' | 'attached'
  label: string
  tone: Tone
  /** The reason, for a failed or unclear file, or what a stuck queue needs. */
  note: string
  /** A small safe reason from the server (`lastErrorHint`), or ''. Never raw error text. */
  hint: string
  canRetry: boolean
  /** When set, the retry is a text button with this label ("Try now" while retrying, "Try again" once failed) instead of the small icon. */
  retryLabel: string
  /** Offer "Enter details manually" (a file that could not be read; the form is filled by hand). */
  canEnterManually: boolean
  /** Why a queued file is not moving: no reader is running, or an older server said nothing for a long time. null while all is well. */
  stalled: 'reader' | 'unknown' | null
  /** Offer "Check again" (ask the server for the reader state now). */
  canCheckAgain: boolean
  findings: Finding[]
}

/** "245 KB" or "1.2 MB". */
export function sizeText(sizeKb: number): string {
  return sizeKb >= 1024 ? `${(sizeKb / 1024).toFixed(1).replace(/\.0$/, '')} MB` : `${Math.max(1, Math.round(sizeKb))} KB`
}

/** "Resume · Passport": which documents one file turned out to hold, without page numbers. */
export function findingsNames(findings: Finding[]): string {
  return findings.map((item) => KIND_NAME[item.kind]).join(' · ')
}

const NEEDS_LOOK = 'We could not find anything we can read here. Check the file, or type the details yourself.'

/** A file in line, told honestly: never "Next in line" for good, and a dead or unknown reader is said out loud. */
function queuedRow(job: IntakeJob, base: Omit<FileRow, 'state' | 'label' | 'tone'>, clock?: QueueClock): FileRow {
  const now = clock?.now ?? Date.now()
  const queued = { ...base, state: 'queued' as const }
  if (job.status === 'uploaded') return { ...queued, label: 'Queued', tone: 'muted' }
  if (job.status === 'retry' && (job.attempts ?? 0) > 0) {
    // A reading attempt ended without a result: say so, and that it is being tried again. Never "restart the server".
    const down = readerDown(job)
    return {
      ...queued, label: 'Trying again', tone: 'warn', note: down ? `${retryNote(job)} ${READER_DOWN}` : retryNote(job), hint: safeHint(job.lastErrorHint),
      canRetry: true, retryLabel: 'Try now', stalled: down ? 'reader' : null, canCheckAgain: down,
    }
  }
  if (readerDown(job)) return { ...queued, label: 'Waiting', tone: 'warn', note: READER_DOWN, stalled: 'reader', canCheckAgain: true }
  const ahead = aheadOf(job)
  if (!job.reader) {
    // An older server sends nothing about the reader. Say what is known, and own up to it only for a file never tried yet.
    const since = Date.parse(job.queuedAt ?? job.createdAt)
    if ((job.attempts ?? 0) === 0 && Number.isFinite(since) && now - since > NO_READER_INFO_MS) return { ...queued, label: 'Still waiting', tone: 'warn', note: STILL_WAITING, stalled: 'unknown', canCheckAgain: true }
    return { ...queued, label: ahead !== null && ahead > 0 ? queueText(job) : 'Queued', tone: 'muted' }
  }
  const note = job.reader.outdated ? READER_OLD : ''
  if (ahead === 0) {
    const waited = now - (clock?.nextSince?.[job.id] ?? now)
    return { ...queued, label: waited > NEXT_IN_LINE_MS ? 'Starting…' : 'Next in line', tone: 'muted', note }
  }
  return { ...queued, label: queueText(job), tone: 'muted', note }
}

export function fileRow(job: IntakeJob, clock?: QueueClock): FileRow {
  const findings = jobFindings(job)
  const base = { id: job.id, name: job.filename, sizeKb: job.sizeKb, url: job.originalUrl, findings, note: '', hint: '', canRetry: false, retryLabel: '', canEnterManually: false, stalled: null, canCheckAgain: false }
  switch (job.status) {
    case 'processing': return { ...base, state: 'reading', label: 'Reading', tone: 'busy' }
    case 'pending': case 'retry': case 'uploaded': return queuedRow(job, base, clock)
    case 'failed': return { ...base, state: 'failed', label: 'Reading failed', tone: 'bad', note: READ_FAILED_NOTE, hint: safeHint(job.lastErrorHint), canRetry: true, retryLabel: 'Try again', canEnterManually: true }
    case 'needs_review': return { ...base, state: 'review', label: 'Needs a look', tone: 'warn', note: job.lastError || '', canRetry: true }
    case 'attached': return { ...base, state: 'attached', label: 'Attached', tone: 'good' }
    default: {
      const readSomething = findings.length > 0 || !job.result
      return readSomething ? { ...base, state: 'done', label: 'Done', tone: 'good' } : { ...base, state: 'review', label: 'Needs a look', tone: 'warn', note: NEEDS_LOOK, canRetry: true }
    }
  }
}

/** One row per file, in the order they were added (so rows never jump while the reading goes on). */
export function fileRows(jobs: IntakeJob[], clock?: QueueClock): FileRow[] {
  return jobs.filter(isDocumentJob)
    .map((job, index) => ({ job, index }))
    .sort((a, b) => a.job.createdAt.localeCompare(b.job.createdAt) || a.index - b.index)
    .map(({ job }) => fileRow(job, clock))
}

export interface FoundChip {
  kind: DocKind
  /** idle: not found yet. reading: found, being read. done: read and the form filled. review: found but could not be read. */
  state: 'idle' | 'reading' | 'done' | 'review'
  label: string
  /** "page 4", "pages 2, 3" or "2 files". */
  detail: string
  pages: number[]
  files: number
}

/** The three kinds in a fixed order: found ones carry the state of the reading, the others stay muted. */
export function foundChips(jobs: IntakeJob[]): FoundChip[] {
  const docs = jobs.filter(isDocumentJob)
  return DOC_KINDS.map((kind) => {
    const hits = docs.map((job) => ({ job, finding: jobFindings(job).find((item) => item.kind === kind) })).filter((hit) => hit.finding)
    const name = KIND_NAME[kind]
    if (!hits.length) return { kind, state: 'idle' as const, label: name, detail: '', pages: [], files: 0 }
    const pages = [...new Set(hits.flatMap((hit) => hit.finding!.pages))].sort((a, b) => a - b)
    const read = hits.some((hit) => hit.finding!.read)
    const working = hits.some((hit) => isWorking(hit.job))
    const state = read ? 'done' as const : working ? 'reading' as const : 'review' as const
    const label = state === 'review' ? `${name} needs a look` : `${name} found`
    return { kind, state, label, detail: hits.length > 1 ? `${hits.length} files` : pagesText(pages), pages, files: hits.length }
  })
}

/** What is in the form for the progress strip: a kind counts once found, or while a file of that kind is still being read. */
export function documentPresence(jobs: IntakeJob[]): Record<DocKind, boolean> {
  const chips = foundChips(jobs)
  const docs = jobs.filter(isDocumentJob)
  const result = {} as Record<DocKind, boolean>
  for (const chip of chips) result[chip.kind] = chip.state !== 'idle' || docs.some((job) => job.kind === chip.kind && (isWorking(job) || !job.result))
  return result
}

export interface ReadProgress { total: number; finished: number; working: number; failed: number; percent: number; text: string }
/** "3 of 10 read" over all files in the zone. */
export function readProgress(rows: FileRow[]): ReadProgress {
  const total = rows.length
  const working = rows.filter((row) => row.state === 'queued' || row.state === 'reading').length
  const failed = rows.filter((row) => row.state === 'failed').length
  const finished = total - working
  return { total, finished, working, failed, percent: total ? Math.round((100 * finished) / total) : 0, text: `${finished} of ${total} read` }
}

const SEVERAL_PEOPLE = /different candidate identities|more than one person|people and pages/i
/** The server found several people in the upload (or said so when saving). Only then does "Check people in documents" appear. */
export function severalPeopleFound(jobs: IntakeJob[], suggested = false): boolean {
  if (suggested) return true
  return jobs.some((job) => {
    if (job.lastError && SEVERAL_PEOPLE.test(job.lastError)) return true
    const result = job.result as { warnings?: unknown; people?: unknown; peopleCount?: unknown; scans?: Record<string, { warnings?: unknown }> } | undefined
    if (!result) return false
    if (typeof result.peopleCount === 'number' && result.peopleCount > 1) return true
    if (Array.isArray(result.people) && result.people.length > 1) return true
    const warnings = [result.warnings, ...Object.values(result.scans ?? {}).map((scan) => scan?.warnings)].flatMap((value) => Array.isArray(value) ? value : [])
    return warnings.some((warning) => typeof warning === 'string' && SEVERAL_PEOPLE.test(warning))
  })
}
