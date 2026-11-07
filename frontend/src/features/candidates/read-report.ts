/**
 * The "What we read" report: what the document reader was given, what it made of it and what it could not place on the form.
 * Plain data in, plain data out (no React, no network). The server's `coverage` record and `unmapped` list are additive, so every
 * key is optional here: an older server sends neither and `buildReadReport` returns null. Sensitive details (Aadhaar number, VID,
 * enrollment id, mobile number) are never shown; the server already leaves their values out and this file hides them as well.
 */
export type ReportKind = 'resume' | 'passport' | 'aadhaar'
export const REPORT_KINDS: readonly ReportKind[] = ['resume', 'passport', 'aadhaar']
const KIND_NAME: Record<string, string> = { resume: 'Resume', passport: 'Passport', aadhaar: 'Aadhaar' }
const MAX_SAMPLES = 5
const MAX_SAMPLE_LENGTH = 80
const MAX_LISTED_PAGES = 10

export type ReportChip = {
  kind: string
  label: string
  /** verified: cross-checked and agreed. check: the two readings differ. read: read, nothing more to say. */
  state: 'verified' | 'check' | 'read'
  /** The fields that were cross-checked, in words (for a tooltip). */
  title: string
}
export type ReportNote = {
  key: string
  tone: 'warn' | 'muted'
  text: string
  /** The note is about pages that could not be read: the host may offer Read again. */
  retry?: boolean
}
export type UnmappedView = {
  key: string
  kind: string
  kindName: string
  group: string
  count: number
  samples: string[]
  /** Found but never shown (Aadhaar number, VID, enrollment id, mobile number). */
  hidden: boolean
}
export type ReadReportView = {
  headline: string
  chips: ReportChip[]
  notes: ReportNote[]
  unmapped: UnmappedView[]
  unmappedCount: number
}

const rec = (value: unknown): Record<string, unknown> => (value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {})
const arr = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
const text = (value: unknown): string => (typeof value === 'string' ? value.trim() : typeof value === 'number' && Number.isFinite(value) ? String(value) : '')
const whole = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0)
const numbers = (value: unknown): number[] => [...new Set(arr(value).filter((page): page is number => typeof page === 'number' && Number.isFinite(page) && page > 0))].sort((a, b) => a - b)
const plural = (count: number, one: string, many = one + 's') => (count === 1 ? one : many)
const sentence = (value: string) => (value ? value.charAt(0).toUpperCase() + value.slice(1) : value)
const joinWords = (items: string[]) => (items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`)

/** [1, 2, 3, 4, 5] -> "1 to 5"; [13, 14] -> "13 and 14"; [1, 2, 3, 7] -> "1 to 3 and 7". */
export function pageNumbersText(pages: number[]): string {
  const sorted = numbers(pages)
  const parts: string[] = []
  for (let index = 0; index < sorted.length;) {
    let end = index
    while (end + 1 < sorted.length && sorted[end + 1] === sorted[end] + 1) end += 1
    if (end - index >= 2) parts.push(`${sorted[index]} to ${sorted[end]}`)
    else for (let at = index; at <= end; at += 1) parts.push(String(sorted[at]))
    index = end + 1
  }
  return joinWords(parts)
}
/** "page 6" or "pages 13 and 14". */
export function pagesLabel(pages: number[]): string {
  const sorted = numbers(pages)
  return sorted.length ? `${plural(sorted.length, 'page')} ${pageNumbersText(sorted)}` : ''
}

/** A field name in words: "passport_number" -> "Passport number", "dob" -> "Date of birth". */
const FIELD_WORDS: Record<string, string> = { dob: 'date of birth', doe: 'date of expiry', doi: 'date of issue', mrz: 'machine-readable lines' }
export function fieldWords(name: unknown): string {
  const words = text(name).replace(/\[\d*\]/g, ' ').replace(/[._-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/\s+/g, ' ').trim().toLowerCase()
  return words.split(' ').map((word) => FIELD_WORDS[word] ?? word).join(' ')
}
const fieldNames = (value: unknown): string[] => [...new Set(arr(value).map((item) => fieldWords(typeof item === 'string' ? item : rec(item).field ?? rec(item).name)).filter(Boolean))]

/** "Passport number differs between the scan and the reader: please check." */
export function conflictText(fields: string[]): string {
  const names = joinWords(fields.map((name, index) => (index === 0 ? sentence(name) : name)))
  return `${names} ${fields.length === 1 ? 'differs' : 'differ'} between the scan and the reader: please check.`
}

// ------------------------------------------------------------------ not placed on the form

/** Groups whose values are never shown: the Aadhaar number, VID, enrollment id and mobile number. */
const SENSITIVE_GROUP = /(^|[._[\]\s-])(vid|uid|enrol\w*|mobile\w*|aadhaar_?(number|no)|personal_?number)([._[\]\s-]|$)/i
const groupWords = (path: string, kind: string): string => {
  const parts = path.replace(/\[\d*\]/g, '.').split('.').map((part) => part.trim()).filter(Boolean)
  if (parts.length > 1 && parts[0].toLowerCase() === kind) parts.shift()
  return sentence(fieldWords(parts.join(' ')) || 'Other details')
}
const clip = (value: string) => (value.length > MAX_SAMPLE_LENGTH ? value.slice(0, MAX_SAMPLE_LENGTH - 1).trimEnd() + '…' : value)

/** The server's `unmapped` list in words: kind, group, count and up to five samples (none for sensitive groups). */
export function unmappedViews(list: unknown): UnmappedView[] {
  const views = arr(list).map((entry, index): UnmappedView | null => {
    const item = rec(entry)
    const kind = text(item.kind).toLowerCase()
    const path = text(item.group ?? item.path)
    const samples = arr(item.samples ?? item.values).map(text).filter(Boolean)
    const count = whole(item.count) || samples.length
    if (!path && !count) return null
    const hidden = item.tier === 'sensitive' || item.sensitive === true || SENSITIVE_GROUP.test(path)
    return {
      key: `${kind}:${path}:${index}`, kind, kindName: KIND_NAME[kind] ?? (sentence(kind) || 'Document'), group: groupWords(path, kind), count,
      samples: hidden ? [] : samples.slice(0, MAX_SAMPLES).map(clip), hidden,
    }
  }).filter((view): view is UnmappedView => view !== null)
  const order = (kind: string) => { const at = REPORT_KINDS.indexOf(kind as ReportKind); return at < 0 ? REPORT_KINDS.length : at }
  return views.map((view, index) => ({ view, index })).sort((a, b) => order(a.view.kind) - order(b.view.kind) || a.index - b.index).map(({ view }) => view)
}
/** "3 found, hidden" for a sensitive group; "3" otherwise. */
export const unmappedCountText = (view: Pick<UnmappedView, 'count' | 'hidden'>) => (view.hidden ? `${view.count} found, hidden` : String(view.count))

// ------------------------------------------------------------------ the report

function chipFor(kind: string, part: Record<string, unknown>): ReportChip {
  const check = rec(part.crossCheck)
  const verified = fieldNames(check.verified)
  const conflicts = fieldNames(check.conflicts)
  const pages = pagesLabel(numbers(part.pages_sent ?? part.pagesSent ?? part.pages))
  const name = KIND_NAME[kind] ?? sentence(kind)
  return {
    kind, label: pages ? `${name} ${pages}` : name,
    state: conflicts.length ? 'check' : verified.length ? 'verified' : 'read',
    title: conflicts.length ? `Differs: ${joinWords(conflicts)}` : verified.length ? `Cross-checked: ${joinWords(verified)}` : '',
  }
}

function ignoredText(pages: number[]): string {
  const shown = pages.slice(0, MAX_LISTED_PAGES).join(', ')
  const more = pages.length > MAX_LISTED_PAGES ? ` and ${pages.length - MAX_LISTED_PAGES} more` : ''
  return `${pages.length} ${plural(pages.length, 'page')} ignored (${shown}${more})`
}

/** What the reader left out of each kind: rows past the form's room, and parts that disagreed. */
function leftOutNotes(kind: string, part: Record<string, unknown>): ReportNote[] {
  const name = KIND_NAME[kind] ?? sentence(kind)
  const notes: ReportNote[] = []
  const rows = arr(part.left_out ?? part.rowsLeftOut).map(rec)
  rows.forEach((row, index) => {
    const left = whole(row.left_out ?? row.leftOut)
    const what = text(row.what)
    if (left > 0) notes.push({ key: `rows:${kind}:${index}`, tone: 'warn', text: `${name}: ${left} ${what ? `${what} ${plural(left, 'entry', 'entries')}` : plural(left, 'entry', 'entries')} not placed on the form.` })
  })
  if (typeof part.rowsLeftOut === 'number' && part.rowsLeftOut > 0 && !rows.length) notes.push({ key: `rows:${kind}`, tone: 'warn', text: `${name}: ${part.rowsLeftOut} ${plural(part.rowsLeftOut, 'entry', 'entries')} not placed on the form.` })
  const differing = Array.isArray(part.conflicts) ? part.conflicts.length : whole(part.conflicts)
  if (differing > 0) notes.push({ key: `parts:${kind}`, tone: 'warn', text: `${name}: different parts gave different values. The first was kept: please check.` })
  return notes
}

/**
 * The report for one reading, or null when the server sent no coverage and nothing unplaced (an older server).
 * `unmapped` may come beside the coverage (the application form) or inside it (the upload job).
 */
export function buildReadReport(coverage: unknown, unmapped?: unknown): ReadReportView | null {
  const data = rec(coverage)
  const hasCoverage = Object.keys(data).length > 0
  const own = unmappedViews(arr(unmapped).length ? unmapped : data.unmapped)
  if (!hasCoverage && !own.length) return null

  const kinds = rec(data.kinds)
  const kindOrder = [...REPORT_KINDS.filter((kind) => kind in kinds), ...Object.keys(kinds).filter((kind) => !REPORT_KINDS.includes(kind as ReportKind))]
  const chips = kindOrder.map((kind) => chipFor(kind, rec(kinds[kind])))

  const conflictNotes = kindOrder.flatMap((kind): ReportNote[] => {
    const fields = fieldNames(rec(rec(kinds[kind]).crossCheck).conflicts)
    // The kind is named only when no field already says it ("Passport number" needs no "Passport:" in front).
    const named = fields.some((field) => field.startsWith(kind))
    return fields.length ? [{ key: `check:${kind}`, tone: 'warn', text: `${named ? '' : `${KIND_NAME[kind] ?? sentence(kind)}: `}${conflictText(fields)}` }] : []
  })
  const rowNotes = kindOrder.flatMap((kind) => leftOutNotes(kind, rec(kinds[kind])))

  const pages = rec(data.pages)
  const unresolved = numbers(pages.unresolved)
  const ignored = numbers(pages.ignored)
  const notes: ReportNote[] = [...conflictNotes, ...rowNotes]
  if (unresolved.length) notes.push({ key: 'unresolved', tone: 'warn', retry: true, text: `Unresolved ${pagesLabel(unresolved)}: could not be read.` })
  if (ignored.length) notes.push({ key: 'ignored', tone: 'muted', text: ignoredText(ignored) })

  const total = own.reduce((sum, view) => sum + view.count, 0)
  const headline = text(data.summary) || (chips.length ? `Read: ${chips.map((chip) => chip.label).join(', ')}.` : own.length ? 'Some details were not placed on the form.' : '')
  return { headline, chips, notes, unmapped: own, unmappedCount: total }
}

// ------------------------------------------------------------------ where the data sits

type Holder = { id?: string; filename?: string; kind?: string; status?: string; result?: unknown; output?: unknown }
export type JobReport = { id: string; filename: string; report: ReadReportView }
const coverageOf = (job: Holder): unknown => { const found = rec(job.result).coverage ?? rec(job.output).coverage; return found }

/** One report per uploaded file that came back with a coverage record (Add candidate). Files without one are left out. */
export function jobReports(jobs: Holder[]): JobReport[] {
  return jobs.flatMap((job): JobReport[] => {
    if (!job.id || job.status === 'dismissed') return []
    const report = buildReadReport(coverageOf(job))
    return report ? [{ id: job.id, filename: job.filename ?? '', report }] : []
  })
}

type FormSlot = { slot?: string; label?: string; extraction?: { jobId?: string; updatedAt?: string; coverage?: unknown; unmapped?: unknown } | null }
export type FormReport = { id: string; caption: string; report: ReadReportView }
/**
 * The reports in the application form's Documents block: one per reading (a file holding a resume and a passport reaches two slots
 * with the same job id and is shown once), newest first.
 */
export function formReports(slots: FormSlot[]): FormReport[] {
  const seen = new Set<string>()
  const found = slots.flatMap((slot, index) => {
    const extraction = slot.extraction
    if (!extraction) return []
    const report = buildReadReport(extraction.coverage, extraction.unmapped)
    return report ? [{ index, slot, extraction, report }] : []
  }).sort((a, b) => text(b.extraction.updatedAt).localeCompare(text(a.extraction.updatedAt)) || a.index - b.index)
  const out: FormReport[] = []
  for (const item of found) {
    const id = text(item.extraction.jobId) || `${item.slot.slot ?? 'slot'}:${item.index}`
    if (seen.has(id)) continue
    seen.add(id)
    out.push({ id, caption: text(item.slot.label) || sentence(text(item.slot.slot)), report: item.report })
  }
  return out
}
