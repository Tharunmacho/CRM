import { ApiError, api, fileHref } from '@/shared/api/client'
import { MAX_DOCUMENT_BYTES } from '@/shared/lib/document-files'

/** One candidate's folder as picked in the browser. */
export interface PickedFolder {
  folder: string
  files: { path: string; file: File }[]
}

export interface PickedFolders {
  folders: PickedFolder[]
  /** A spreadsheet found loose in the picked folder (the filled-in sheet). */
  sheet: File | null
  ignored: number
}

export type ImportAction = 'skip' | 'documents_only' | 'in_progress' | 'deploy'

export interface ImportIssue {
  level: 'error' | 'warning' | 'info'
  message: string
}

export interface PlannedFile {
  path: string
  kind: string | null
  /** One scan can be several documents ("signed offer, passport, aadhaar"). */
  kinds: string[]
  labels: string[]
  label: string | null
  skipped: boolean
  problem: string | null
  guessed?: boolean
  /** Only these pages of the file belong to this candidate (a scan shared by several people). */
  pages?: number[]
}

export interface StageCheck {
  stage: number
  title: string
  ok: boolean
  missing: string[]
}

export interface ImportPlan {
  folder: string
  name: string
  phone: string | null
  order: string | null
  recruiter: string | null
  files: PlannedFile[]
  issues: ImportIssue[]
  stages: StageCheck[]
  level: number
  completedThrough: number
  currentStage: number | null
  missingDocuments: string[]
  action: ImportAction
  duplicateOf: string | null
  /** The Care India ID of the candidate already in the CRM (`duplicateOf` is their reference). */
  duplicateOfCareId?: string | null
  row: Record<string, string>
  hasRow: boolean
  /** The folder these files were picked in (a mixed folder is separated into one candidate per person). */
  parent: string
  /** Files of the folder whose names say nobody's (or two different people's): a person places them. */
  leftOut?: string[]
  /** Made from what the files say (the "Read the files" button), not from their names. */
  identified?: boolean
  /** The evidence on this person's pages disagrees: not ticked for import until a person looks. */
  needsLook?: boolean
  /** This person (or one of their files) is already in the CRM: nothing is imported unless a person chooses to add the documents (or, for a possible match, import anyway). */
  alreadyInCrm?: AlreadyInCrm | null
  /** Always 'while_importing': the CRM is checked again for this person as they are imported. */
  identityCheck?: string
}

/** How sure the check is that a candidate already in the CRM is the same person. */
export type MatchStrength = 'certain' | 'possible'

/** What the check found: the candidate already in the CRM (no passport or phone numbers, only what matched). */
export interface AlreadyInCrm {
  candidateId: string
  careId: string | null
  ref: string
  name: string
  matchedBy: string
  files?: number
  strength: MatchStrength
}

/** What a person can do with a duplicate: leave it, add these documents to the candidate already there, or (a possible match only) import as a new candidate. */
export type DuplicateChoice = 'skip' | 'add_documents' | 'import_anyway'

/** A repeated page the import did not file again. */
export interface RepeatedPage {
  kind?: string
  label?: string
  file?: string
  pages?: number[] | number
  sameAsFile?: string
  sameAsPages?: number[] | number
  reason?: string
  keptAs?: string
  originalKept?: boolean | string
  message: string
}

/** One document of an import answer: filed (imported) or added to an existing candidate (documents_added). */
export interface ResultDocument {
  slot?: string
  label?: string
  fileName?: string
  pages?: number[] | number | null
  added?: boolean
  alreadyFiled?: boolean
  note?: string
}

export interface CheckResponse {
  plans: ImportPlan[]
  unmatchedRows: { rowNumber: number | null; name?: string; folder?: string }[]
}

/** What the server answered for one candidate: imported (also when it was imported before), a duplicate nothing was done for, or documents added to a candidate already there. */
export type ImportOutcome = 'imported' | 'duplicate' | 'already_imported' | 'documents_added'

export interface ImportResult {
  folder: string
  action: ImportAction
  status: ImportOutcome
  name?: string
  candidateId?: string
  ref?: string
  careId?: string | null
  order?: string | null
  completedThrough?: number
  currentStage?: number | null
  issues: ImportIssue[]
  /** duplicate / documents_added / already_imported: what matched the candidate already in the CRM. */
  matchedBy?: string
  strength?: MatchStrength
  files?: number
  /** duplicate: the words for the headline ("Already in the CRM as CI-139 · Name (certain: same passport)") and what the person may choose. */
  message?: string
  choices?: DuplicateChoice[]
  nameSource?: string
  documents?: ResultDocument[]
  /** Repeated pages that were not filed again. */
  duplicates?: RepeatedPage[]
}

const OUTCOMES: ImportOutcome[] = ['imported', 'duplicate', 'already_imported', 'documents_added']
const CHOICES: DuplicateChoice[] = ['skip', 'add_documents', 'import_anyway']

/** The server's answer for one candidate, made safe to use: an answer with no status (an older server) is a normal import; a certain match is never offered "import anyway". */
export function parseImportResult(raw: unknown): ImportResult {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const status = OUTCOMES.includes(r.status as ImportOutcome) ? (r.status as ImportOutcome) : 'imported'
  const strength: MatchStrength | undefined = r.strength === 'certain' || r.strength === 'possible' ? r.strength : undefined
  const result: ImportResult = {
    ...(r as unknown as ImportResult),
    status,
    folder: typeof r.folder === 'string' ? r.folder : '',
    action: (typeof r.action === 'string' ? r.action : 'documents_only') as ImportAction,
    issues: Array.isArray(r.issues) ? (r.issues as ImportIssue[]) : [],
    strength,
  }
  if (Array.isArray(r.documents)) result.documents = r.documents as ResultDocument[]
  if (Array.isArray(r.duplicates)) result.duplicates = r.duplicates as RepeatedPage[]
  if (status === 'duplicate') {
    const offered: DuplicateChoice[] = Array.isArray(r.choices) ? (r.choices as unknown[]).filter((c): c is DuplicateChoice => CHOICES.includes(c as DuplicateChoice)) : ['skip', 'add_documents']
    result.choices = offered.filter((c) => !(c === 'import_anyway' && strength !== 'possible'))
    if (!result.choices.includes('skip')) result.choices.unshift('skip')
  }
  return result
}

/** An imported candidate as it is now: filled in as their documents are read. */
export interface ImportedStatus {
  candidateId: string
  name: string
  careId: string | null
  ref: string
  /**
   * Where the name came from: the documents (read), the sheet, typed by a person, or 'pending' (no name read yet, the candidate is "Name not read yet").
   * Older servers also say 'files' or 'folder': both mean the same as 'pending' (see `normalizeNameSource`).
   */
  nameSource: 'documents' | 'sheet' | 'typed' | 'pending' | 'files' | 'folder'
  needsReview: boolean
  phone: string | null
  documents: { slot: string; label: string; fileName: string; versions: number }[]
  reading: { total: number; open: number; done: number; review: number; failed: number }
  detailsFound: number
  experience: { total: number | null; india: number | null; abroad: number | null }
}

/** "1 file", "2 files": a count with its noun in the right number. */
export function countOf(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

/** What "Read the documents" tells the person: what was queued, or why nothing was (none to read, or all read already). */
export function readAgainMessage(queued: number, readable: number): { text: string; tone?: 'warn' } {
  if (queued > 0) return { text: `Reading ${countOf(queued, 'document')}. The details fill in as each one is read.` }
  if (readable === 0) return { text: 'Nothing to read: these candidates have no resume, passport, Aadhaar or education files yet.', tone: 'warn' }
  return { text: 'Everything has been read already.' }
}

/** The Import screen asks for the imported candidates' progress at most this many times (the cap of the old fixed 3 s loop). */
export const POLL_ROUNDS = 100

/** How long to wait before the next progress call: 3 s for the first ten, 6 s for the next ten, then 10 s (the server is shared and each call costs it a few queries). */
export function pollDelay(round: number): number {
  return round <= 10 ? 3000 : round <= 20 ? 6000 : 10000
}

/** The candidates whose documents are still being read: after the first full load only these are asked about again. */
export function openReadings(items: Pick<ImportedStatus, 'candidateId' | 'reading'>[]): string[] {
  return items.filter((item) => item.reading.open > 0).map((item) => item.candidateId)
}

export interface SheetRows {
  rows: Record<string, string | number>[]
  recognised: string[]
  ignored: string[]
}

export interface ImportDefaults {
  order: string
  recruiterId: string
}

const JUNK = /^(\.|~\$|thumbs\.db$|desktop\.ini$)/i
const SHEET = /\.(xlsx|csv)$/i

/** Group the files of a picked folder: one sub-folder per candidate. Files straight inside the picked folder are not candidates. */
export function groupPicked(list: FileList | File[]): PickedFolders {
  const folders = new Map<string, PickedFolder>()
  const loose: File[] = []
  let ignored = 0
  const files = Array.from(list)
  for (const file of files) {
    const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name
    const parts = relative.split('/').filter(Boolean)
    if (parts.some((part) => JUNK.test(part))) { ignored += 1; continue }
    if (parts.length <= 2) { loose.push(file); continue }
    const folder = parts[1]
    const entry = folders.get(folder) ?? { folder, files: [] }
    entry.files.push({ path: parts.slice(2).join('/'), file })
    folders.set(folder, entry)
  }
  const sheets = loose.filter((file) => SHEET.test(file.name))
  const sheet = sheets.find((file) => /sheet|candidate|import|stage|manifest/i.test(file.name)) ?? sheets[0] ?? null
  const rest = loose.filter((file) => !SHEET.test(file.name))
  if (folders.size === 0 && rest.length > 0) {
    // Someone picked a single candidate's folder rather than the folder that holds them all.
    const root = ((rest[0] as File & { webkitRelativePath?: string }).webkitRelativePath || '').split('/')[0] || 'Candidate'
    folders.set(root, { folder: root, files: rest.map((file) => ({ path: file.name, file })) })
  } else {
    ignored += rest.length
  }
  const sorted = Array.from(folders.values()).sort((a, b) => a.folder.localeCompare(b.folder, undefined, { numeric: true }))
  sorted.forEach((entry) => entry.files.sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true })))
  return { folders: sorted, sheet, ignored }
}

// ------------------------------------------------------------------------------ reading the files to find each candidate

export interface IdentifiedFile {
  path: string
  /** null: the whole file */
  pages: number[] | null
  carried: number[]
  adjacent: number[]
  lowConfidence: number[]
  unreadable: number[]
}

export interface IdentifiedPerson {
  key: string
  name: string
  nameSource: string
  evidence: string[]
  /** Only the last characters of each identity number, e.g. { passport: "…873" }. */
  identifiers: Record<string, string>
  files: IdentifiedFile[]
  pageCount: number
  needsLook: boolean
  conflicts: { kind: string; message: string; probable?: boolean }[]
  warnings: { kind: string; message: string }[]
  /** This person is already in the CRM (matched by a passport, phone or e-mail; the numbers themselves are not sent). */
  alreadyInCrm?: { matchedBy: 'passport' | 'phone' | 'email'; strength: MatchStrength; careId: string | null; candidateId: string; name: string } | null
}

/** Pages (or a whole file, pages null) that nobody owns yet, and why. */
export interface LeftoverRow { path: string; pages: number[] | null; reason: string }

export interface IdentifyTotals { files: number; pages: number; assigned: number; unplaced: number; unreadable: number }

export interface IdentifyResult {
  folder: string
  people: IdentifiedPerson[]
  unplaced: LeftoverRow[]
  unreadable: LeftoverRow[]
  files: { path: string; pageCount: number; readable: boolean; lowConfidence: number[] }[]
  totals: IdentifyTotals
  stopped: boolean
}

/** What the person decided for a row nobody owns: a person's key to place it with, or '' to leave it out. */
export type Placement = Record<string, string>

export const leftoverKey = (kind: 'unplaced' | 'unreadable', row: LeftoverRow) => `${kind}:${row.path}#${row.pages ? row.pages.join(',') : '*'}`

/** The rows nobody owns yet, from both lists. */
export function leftovers(result: IdentifyResult): { id: string; kind: 'unplaced' | 'unreadable'; row: LeftoverRow }[] {
  return [
    ...result.unplaced.map((row) => ({ id: leftoverKey('unplaced', row), kind: 'unplaced' as const, row })),
    ...result.unreadable.map((row) => ({ id: leftoverKey('unreadable', row), kind: 'unreadable' as const, row })),
  ]
}

/** The numbers behind `summaryText`: pages placed, waiting for a person, unreadable and left out (all counted in pages). */
export interface SummaryCounts { pages: number; placed: number; needYou: number; unreadable: number; leftOut: number }

export function summaryCounts(result: IdentifyResult, placement: Placement = {}): SummaryCounts {
  const whole = new Map(result.files.map((file) => [file.path, file.pageCount]))
  let placedUnplaced = 0
  let leftOut = 0
  let placedUnreadable = 0
  for (const { id, kind, row } of leftovers(result)) {
    const target = placement[id]
    if (target === undefined) continue
    const count = row.pages ? row.pages.length : whole.get(row.path) ?? 1
    if (kind === 'unreadable') { if (target !== '') placedUnreadable += count }
    else if (target === '') leftOut += count
    else placedUnplaced += count
  }
  const { pages, assigned, unplaced, unreadable } = result.totals
  return {
    pages,
    placed: assigned + placedUnplaced + placedUnreadable,
    needYou: Math.max(0, unplaced - placedUnplaced - leftOut),
    unreadable: Math.max(0, unreadable - placedUnreadable),
    leftOut,
  }
}

/**
 * "42 pages: 38 placed, 3 need you, 1 unreadable": the counts add up to the pages. A row a person placed counts as placed; an unplaced
 * row they chose to leave out is neither placed nor waiting for them, so it is counted apart ("2 left out", only when there are any).
 */
export function summaryText(result: IdentifyResult, placement: Placement = {}): string {
  const { pages, placed, needYou, unreadable, leftOut } = summaryCounts(result, placement)
  const parts = [`${placed} placed`, `${needYou} need you`, `${unreadable} unreadable`]
  if (leftOut > 0) parts.push(`${leftOut} left out`)
  return `${pages} page${pages === 1 ? '' : 's'}: ${parts.join(', ')}`
}

/**
 * What the list shows, counted from the candidates actually listed: how many, and how many different files they hold (a scan shared
 * by two people, or a group ticket given to everyone, is one file), and how many files belong to nobody yet. Never the picked folders.
 */
export function listCounts(plans: Pick<ImportPlan, 'parent' | 'folder' | 'files' | 'leftOut'>[]): { candidates: number; files: number; unassigned: number } {
  const files = new Set<string>()
  const unassigned = new Set<string>()
  for (const plan of plans) {
    for (const file of plan.files) if (!file.skipped) files.add(`${plan.parent ?? plan.folder}/${file.path}`)
    for (const path of plan.leftOut ?? []) unassigned.add(`${plan.parent ?? plan.folder}/${path}`)
  }
  // a file a person moved to a candidate is no longer waiting
  for (const key of files) unassigned.delete(key)
  return { candidates: plans.length, files: files.size, unassigned: unassigned.size }
}

export interface PersonOut { name: string; needsLook: boolean; files: { path: string; pages?: number[] }[] }

/** The people to send to the check: the identified ones, with the rows a person placed added to them. Pure. */
export function peopleFor(result: IdentifyResult, placement: Placement = {}): PersonOut[] {
  const people = new Map<string, { name: string; needsLook: boolean; files: Map<string, number[] | null> }>()
  for (const person of result.people) {
    people.set(person.key, { name: person.name, needsLook: person.needsLook, files: new Map(person.files.map((f) => [f.path, f.pages ? [...f.pages] : null])) })
  }
  for (const { id, row } of leftovers(result)) {
    const owner = placement[id] ? people.get(placement[id]) : undefined
    if (!owner) continue
    const have = owner.files.get(row.path)
    if (have === undefined) owner.files.set(row.path, row.pages ? [...row.pages] : null)
    else if (have === null || row.pages === null) owner.files.set(row.path, null)
    else owner.files.set(row.path, [...new Set([...have, ...row.pages])].sort((a, b) => a - b))
  }
  return [...people.values()].map((p) => ({
    name: p.name, needsLook: p.needsLook,
    files: [...p.files].map(([path, pages]) => (pages ? { path, pages } : { path })),
  }))
}

/** Rows still waiting for a person (neither placed nor left out). */
export const waiting = (result: IdentifyResult, placement: Placement = {}) => leftovers(result).filter(({ id }) => placement[id] === undefined)

export const assignKey = (parent: string, path: string) => `${parent}/${path}`

export const fileTooLarge = (file: File) => file.size > MAX_DOCUMENT_BYTES

/** What a person decided about an import: the attempt's key, and what to do when the candidate is already in the CRM. */
export interface ImportOptions {
  importKey?: string
  duplicateAction?: DuplicateChoice
}

/** A new key for one import attempt of a folder (made once per card, reused when the same attempt is retried). */
export function newImportKey(): string {
  const c = globalThis.crypto as Crypto | undefined
  if (c?.randomUUID) return c.randomUUID()
  return `ik-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

// ------------------------------------------------------------------------------ the same-file check (SHA-256 of each file)

const HEX = Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, '0'))
const hashes = new WeakMap<Blob, Promise<string | null>>()

/** True when files can be hashed here at all (crypto.subtle only exists on a secure page). */
export const canHash = () => Boolean(globalThis.crypto?.subtle)

/** The lowercase hex SHA-256 of a file's bytes; null when it cannot be made (no crypto.subtle, an empty, oversize or unreadable file). Never throws; made once per file. */
export function hashFile(file: File): Promise<string | null> {
  const known = hashes.get(file)
  if (known) return known
  const made = (async () => {
    if (!canHash() || file.size === 0 || fileTooLarge(file)) return null
    try {
      const digest = new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', await file.arrayBuffer()))
      let hex = ''
      for (const byte of digest) hex += HEX[byte]
      return hex
    } catch {
      return null
    }
  })()
  hashes.set(file, made)
  return made
}

/**
 * The SHA-256 of every picked file, by folder then path (a file that could not be hashed is simply absent). A few files at a time, each read
 * whole only while it is hashed. `onProgress` says how many of how many are done; `stop` is asked between files and ends the work early.
 */
export async function hashPicked(
  folders: PickedFolder[],
  options: { concurrency?: number; onProgress?: (done: number, total: number) => void; stop?: () => boolean } = {},
): Promise<Record<string, Record<string, string>>> {
  const out: Record<string, Record<string, string>> = {}
  if (!canHash()) return out
  const jobs = folders.flatMap((picked) => picked.files.filter(({ file }) => file.size > 0 && !fileTooLarge(file)).map(({ path, file }) => ({ folder: picked.folder, path, file })))
  let next = 0
  let done = 0
  options.onProgress?.(0, jobs.length)
  const worker = async () => {
    while (next < jobs.length && !options.stop?.()) {
      const job = jobs[next++]
      const sha = await hashFile(job.file)
      if (sha) (out[job.folder] ??= {})[job.path] = sha
      done += 1
      options.onProgress?.(done, jobs.length)
    }
  }
  await Promise.all(Array.from({ length: Math.max(1, Math.min(options.concurrency ?? 3, jobs.length)) }, worker))
  return out
}

export const importApi = {
  readSheet(file: File) {
    const form = new FormData()
    form.append('file', file, file.name)
    return api.form<SheetRows>('/candidate-import/manifest', form)
  },
  check(body: {
    /** `sha256`: lowercase hex of the file's bytes, so a file that is already in the CRM is found whatever it is called. */
    folders: { folder: string; files: { path: string; kind?: string; sha256?: string }[] }[]
    rows: unknown[]
    defaultOrder: string
    defaultRecruiterId: string
    jobs: Record<string, string>
    /** "auto" separates a folder only when its file names start with different people's names; "one" never separates. */
    groupMode: 'auto' | 'one'
    grouping: Record<string, 'auto' | 'one'>
    /** A person's choice per file: "<picked folder>/<path>" -> the candidate's key to move it to, or "" to leave it out. */
    assign: Record<string, string>
    /** The people found by reading each folder's files (see `identify`), by picked folder. */
    people?: Record<string, PersonOut[]>
  }) {
    return api.post<CheckResponse>('/candidate-import/check', body)
  },
  /** Read ONE folder's files locally and find each candidate in them. Slow (minutes), on demand, cancellable with `signal`. */
  async identify(picked: PickedFolder, signal?: AbortSignal): Promise<IdentifyResult> {
    const send = picked.files.filter(({ file }) => file.size > 0 && !fileTooLarge(file))
    const form = new FormData()
    form.append('spec', JSON.stringify({ folder: picked.folder, files: send.map(({ path }) => ({ path })) }))
    send.forEach(({ path, file }) => form.append('files', file, path.split('/').pop() ?? file.name))
    let response: Response
    try {
      response = await fetch(fileHref('/api/candidate-import/identify'), { method: 'POST', credentials: 'include', body: form, signal })
    } catch (error) {
      if (signal?.aborted) throw error
      throw new ApiError(0, 'Cannot reach the server. Try again.')
    }
    const text = await response.text()
    let payload: unknown = null
    try { payload = text ? JSON.parse(text) : null } catch { payload = null }
    if (!response.ok) {
      const detail = (payload as { detail?: unknown } | null)?.detail
      throw new ApiError(response.status, typeof detail === 'string' ? detail : 'The files could not be read. Try again.', detail)
    }
    const result = payload as IdentifyResult
    // Files too big to send are still part of the answer: they appear as unreadable so a person can place them by hand.
    const sent = new Set(send.map(({ path }) => path))
    const skipped = picked.files.filter(({ path }) => !sent.has(path))
    if (skipped.length === 0) return result
    return {
      ...result,
      unreadable: [...result.unreadable, ...skipped.map(({ path }) => ({ path, pages: null, reason: 'The file is empty or over 25 MB and was not read.' }))],
      files: [...result.files, ...skipped.map(({ path }) => ({ path, pageCount: 1, readable: false, lowConfidence: [] as number[] }))],
      totals: { ...result.totals, files: result.totals.files + skipped.length, pages: result.totals.pages + skipped.length, unreadable: result.totals.unreadable + skipped.length },
    }
  },
  /**
   * `name` is the candidate's name as read from their documents on this screen (or typed by a person); null sends no name at all: the
   * server then calls the candidate "Name not read yet" and takes the name from the documents when they are read. The folder name is never sent as a name.
   */
  async importOne(picked: PickedFolder, plan: ImportPlan, kinds: Record<string, string>, defaults: ImportDefaults, name: string | null = null, options: ImportOptions = {}): Promise<ImportResult> {
    const wanted = new Set(plan.files.filter((f) => !f.skipped).map((f) => f.path))
    const pagesOf = new Map(plan.files.filter((f) => f.pages?.length).map((f) => [f.path, f.pages]))
    const send = picked.files.filter(({ path, file }) => wanted.has(path) && file.size > 0 && !fileTooLarge(file))
    const form = new FormData()
    form.append('spec', JSON.stringify({
      folder: plan.folder,
      // A name read from this person's own files on this screen: the import records that (it stays provisional until the documents are read).
      ...(name ? { name, nameFromFiles: true } : {}),
      row: plan.row,
      files: send.map(({ path }) => ({ path, ...(kinds[path] ? { kind: kinds[path] } : {}), ...(pagesOf.get(path) ? { pages: pagesOf.get(path) } : {}) })),
      defaultOrder: defaults.order || null,
      defaultRecruiterId: defaults.recruiterId || null,
      // One key per import attempt of this folder: asking again after a dropped connection is answered "already imported" instead of making a second candidate.
      ...(options.importKey ? { importKey: options.importKey } : {}),
      ...(options.duplicateAction ? { duplicateAction: options.duplicateAction } : {}),
    }))
    send.forEach(({ path, file }) => form.append('files', file, path.split('/').pop() ?? file.name))
    return parseImportResult(await api.form<unknown>('/candidate-import/candidate', form))
  },
  async downloadSheet(folders: string[]) {
    const response = await fetch(fileHref('/api/candidate-import/template'), {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folders }),
    })
    if (!response.ok) throw new Error('We could not make the sheet. Please try again.')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'candidate-import-sheet.xlsx'
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
  status: (candidateIds: string[]) => api.post<{ items: ImportedStatus[] }>('/candidate-import/status', { candidateIds }),
  readAgain: (candidateIds: string[]) => api.post<{ queued: number; readable: number }>('/candidate-import/read-again', { candidateIds }),
  kinds: () => api.get<{ kinds: { key: string; label: string }[] }>('/candidate-import/columns'),
}
