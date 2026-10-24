/**
 * Pure helpers behind the candidate profile's OCR evidence: no React, no JSX, so they can be
 * tested directly. Nothing here drops a value Veris returned; masking is the one exception
 * and is always an explicit, caller-chosen option.
 */

export type Scalar = string | number | boolean

export const KIND_LABEL: Record<string, string> = {
  resume: 'Resume',
  passport: 'Passport',
  aadhaar: 'Aadhaar',
}

const WORD_OVERRIDES: Record<string, string> = {
  mrz: 'MRZ',
  id: 'ID',
  vid: 'VID',
  ocr: 'OCR',
  url: 'URL',
  sha256: 'SHA-256',
  dob: 'DOB',
  html: 'HTML',
  pdf: 'PDF',
  ipa: 'IPA',
  fin: 'FIN',
  csn: 'CSN',
  aadhaar: 'Aadhaar',
  github: 'GitHub',
  linkedin: 'LinkedIn',
  whatsapp: 'WhatsApp',
}

/** "processing_time_ms" → "Processing time (ms)", "mrz_source" → "MRZ source". */
export function humanizeKey(key: string): string {
  const spaced = key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_\-.]+/g, ' ')
    .trim()
  if (!spaced) return key
  let words = spaced.split(/\s+/)
  let unit = ''
  if (words.length > 1 && words[words.length - 1].toLowerCase() === 'ms') {
    unit = ' (ms)'
    words = words.slice(0, -1)
  }
  const text = words
    .map((w, i) => {
      const lower = w.toLowerCase()
      if (WORD_OVERRIDES[lower]) return WORD_OVERRIDES[lower]
      return i === 0 ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower
    })
    .join(' ')
  return text + unit
}

export const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)

/** "3–5, 8" for [3, 4, 5, 8]; unsorted and duplicate input is tolerated. */
export function pageList(pages?: number[] | null): string {
  const nums = [...new Set((pages ?? []).filter((n) => Number.isFinite(n)))].sort((a, b) => a - b)
  const out: string[] = []
  for (let i = 0; i < nums.length; ) {
    let j = i
    while (j + 1 < nums.length && nums[j + 1] === nums[j] + 1) j++
    out.push(j > i ? `${nums[i]}–${nums[j]}` : `${nums[i]}`)
    i = j + 1
  }
  return out.join(', ')
}

/** 0.923 → "92%". Out-of-range or missing input yields an empty string. */
export function confidenceLabel(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return ''
  const pct = Math.round(value * 1000) / 10
  return `${Number.isInteger(pct) ? pct : pct.toFixed(1)}%`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "2030-03-12" → "12 Mar 2030" without any timezone shift; anything else is shown as written. */
export function isoDateLabel(value?: string | null): string {
  const v = (value ?? '').trim()
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v)
  if (m && Number(m[2]) >= 1 && Number(m[2]) <= 12) return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`
  return v
}

const MONTH_NAMES = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']

/** 0-11 for "Jan", "Sept", "September"; -1 for anything else (so "Marital" is not a month). */
export function monthIndex(word: string): number {
  const w = word.toLowerCase()
  if (w === 'sept') return 8
  if (w.length < 3) return -1
  return MONTH_NAMES.findIndex((name) => name === w || (w.length === 3 && name.startsWith(w)))
}

/**
 * Veris returns birth and issue dates as free text ("25th May 1976", "25/05/1976", "1976-05-25").
 * Anything that reads unambiguously becomes "25 May 1976"; anything else is shown as written.
 */
export function humanDate(value?: string | null): string {
  const v = (value ?? '').trim()
  if (!v) return ''
  const out = (day: number, month: number, year: string) =>
    day >= 1 && day <= 31 && month >= 0 && month <= 11 ? `${day} ${MONTHS[month]} ${year}` : v
  let m = /^(\d{4})-(\d{2})-(\d{2})(?:[T\s].*)?$/.exec(v)
  if (m) return out(Number(m[3]), Number(m[2]) - 1, m[1])
  m = /^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$/.exec(v)
  if (m) return out(Number(m[1]), Number(m[2]) - 1, m[3])
  m = /^(\d{1,2})(?:st|nd|rd|th)?[\s\-/,.]+(?:of\s+)?([A-Za-z]{3,9})\.?[\s\-/,.]+(\d{4})$/i.exec(v)
  if (m && monthIndex(m[2]) >= 0) return out(Number(m[1]), monthIndex(m[2]), m[3])
  m = /^([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$/i.exec(v)
  if (m && monthIndex(m[1]) >= 0) return out(Number(m[2]), monthIndex(m[1]), m[3])
  return v
}

/* ---------------------------------------------------------------- Aadhaar masking */

/** "123456789012" → "XXXX XXXX 9012". Anything that is not 12 digits is hidden except its last four. */
export function maskAadhaarNumber(value?: string | null): string {
  const digits = (value ?? '').replace(/\D/g, '')
  if (!digits) return ''
  // Already masked by Veris (e.g. "XXXX XXXX 1234"): show it as stored.
  if (digits.length < 12 && /x/i.test(value ?? '')) return (value ?? '').trim()
  if (digits.length === 12) return `XXXX XXXX ${digits.slice(8)}`
  return `${'X'.repeat(Math.max(digits.length - 4, 0))}${digits.slice(-4)}`
}

const AADHAAR_IN_TEXT = /(?<!\d)(\d{4})([ \-]?)(\d{4})([ \-]?)(\d{4})(?!\d)/g
const VID_IN_TEXT = /(?<!\d)(\d{4})([ \-]?)(\d{4})([ \-]?)(\d{4})([ \-]?)(\d{4})(?!\d)/g

/** Masks every 12-digit Aadhaar number and 16-digit VID inside free text, leaving the last four digits. */
export function maskAadhaarText(text: string): string {
  return text
    .replace(VID_IN_TEXT, (_m, _a, s1, _b, s2, _c, s3, d) => `XXXX${s1}XXXX${s2}XXXX${s3}${d}`)
    .replace(AADHAAR_IN_TEXT, (_m, _a, s1, _b, s2, c) => `XXXX${s1}XXXX${s2}${c}`)
}

export interface AadhaarNumberView {
  /** What everyone sees: Veris's masked number, or one derived from the full number. */
  masked: string
  /** The full number exactly as stored — only filled for a user allowed to see it. */
  full: string
}

export function aadhaarNumberView(
  data: { aadhaar_number?: string | null; masked_aadhaar_number?: string | null } | null | undefined,
  canReveal: boolean,
): AadhaarNumberView {
  const stored = (data?.aadhaar_number ?? '').trim()
  const masked = (data?.masked_aadhaar_number ?? '').trim() || maskAadhaarNumber(stored)
  return { masked, full: canReveal ? stored : '' }
}

/* ------------------------------------------------------------- Stored payload → entries */

/**
 * Where a stored document came from. The current storage shape keeps this beside the
 * verbatim Veris response (`_sources[kind]`, or `_source` on an additional document);
 * rows stored earlier kept the same keys inside the response itself.
 */
export interface OcrSource {
  channel: string
  messageId: string
  attachmentId: string
  fileName: string
  /** Pages of the received file that were sent to Veris, as original page numbers. */
  selectedPages: number[]
  sha256: string
  /** The provenance record exactly as stored; null when the row predates it. */
  raw: Record<string, unknown> | null
}

export interface OcrDocumentEntry {
  id: string
  /** resume | passport | aadhaar, or whatever other kind arrived. */
  kind: string
  label: string
  /** The Veris response for this document. */
  payload: Record<string, unknown>
  /** An additional document of a kind already stored. */
  additional: boolean
  /** `current`: verbatim response + separate provenance. `legacy`: provenance mixed into the response. */
  shape: 'current' | 'legacy'
  source: OcrSource
  /** Pages with the page number of the ORIGINAL received file (see `pageViews`). */
  pagesView: PageView[]
  inspection: InspectionView | null
  /** What the complete-data tree shows: the response, plus the provenance when it is stored apart. */
  treeValue: unknown
}

export interface OcrExtraEntry {
  id: string
  label: string
  payload: unknown
}

const DOCUMENT_KINDS = ['resume', 'passport', 'aadhaar']
const HANDLED_KEYS = new Set([...DOCUMENT_KINDS, 'additional_documents', 'person_split', '_sources'])

const hasContent = (v: unknown): boolean =>
  v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && v.length === 0) && !(isRecord(v) && Object.keys(v).length === 0)

const str = (v: unknown): string => (typeof v === 'string' ? v.trim() : typeof v === 'number' ? String(v) : '')
const nums = (v: unknown): number[] => (Array.isArray(v) ? v.filter((n): n is number => typeof n === 'number' && Number.isFinite(n)) : [])

/** Reads the provenance keys from `container` (a `_sources` record, or a legacy response). */
function readSource(container: Record<string, unknown> | null): OcrSource {
  const file = container?.source_file
  const fileRecord = isRecord(file) ? file : null
  const report = isRecord(container?.local_inspection) ? (container.local_inspection as Record<string, unknown>) : null
  const selected = nums(container?.selected_pages)
  const fromFile = nums(fileRecord?.selected_pages)
  return {
    channel: str(fileRecord?.channel),
    messageId: str(fileRecord?.message_id),
    attachmentId: str(fileRecord?.attachment_id),
    fileName: typeof file === 'string' ? file.trim() : str(fileRecord?.file_name ?? fileRecord?.filename ?? fileRecord?.name),
    selectedPages: selected.length ? selected : fromFile.length ? fromFile : nums(report?.selected_pages),
    sha256: str(container?.source_sha256),
    raw: container,
  }
}

function makeEntry(
  id: string,
  kind: string,
  label: string,
  payload: Record<string, unknown>,
  provenance: Record<string, unknown> | null,
  additional: boolean,
): OcrDocumentEntry {
  const source = readSource(provenance ?? payload)
  // Current rows number pages within the pages sent to Veris; legacy rows already hold the original number.
  const remap = provenance && source.selectedPages.length > 0 ? source.selectedPages : undefined
  const report = provenance ? provenance.local_inspection : payload.local_inspection
  return {
    id,
    kind,
    label,
    payload,
    additional,
    shape: provenance ? 'current' : 'legacy',
    source: provenance ? source : { ...source, raw: null },
    pagesView: pageViews(payload, remap),
    inspection: inspectionFromReport(report),
    treeValue: provenance ? { ...payload, _source: provenance } : payload,
  }
}

/**
 * Every stored Veris document — the primary resume/passport/aadhaar, then `additional_documents` —
 * in reading order, from either storage shape:
 *  - current: `{ resume, passport, aadhaar, _sources: { kind: {…provenance} }, additional_documents: [{ kind: payload, _source }] }`
 *  - legacy: provenance (`local_inspection`, `source_file`, `source_sha256`) lives inside each payload.
 */
export function normalizeOcr(ocr: unknown): OcrDocumentEntry[] {
  if (!isRecord(ocr)) return []
  const sources = isRecord(ocr._sources) ? ocr._sources : {}
  const out: OcrDocumentEntry[] = []
  for (const kind of DOCUMENT_KINDS) {
    const payload = ocr[kind]
    if (isRecord(payload) && hasContent(payload)) {
      const provenance = isRecord(sources[kind]) ? (sources[kind] as Record<string, unknown>) : null
      out.push(makeEntry(kind, kind, KIND_LABEL[kind], payload, provenance, false))
    }
  }
  const extra = ocr.additional_documents
  if (Array.isArray(extra)) {
    let n = 0
    extra.forEach((item, index) => {
      if (!isRecord(item)) return
      const provenance = isRecord(item._source) ? item._source : null
      for (const [kind, payload] of Object.entries(item)) {
        if (kind === '_source' || !isRecord(payload) || !hasContent(payload)) continue
        n += 1
        out.push(
          makeEntry(
            `additional-${index}-${kind}`,
            kind,
            `${KIND_LABEL[kind] ?? humanizeKey(kind)} (additional document ${n})`,
            payload,
            provenance,
            true,
          ),
        )
      }
    })
  }
  return out
}

/** Kept under its earlier name: the entries are now the normalised documents. */
export const documentEntries = normalizeOcr

/** The primary stored document of a kind (never an additional document). */
export function primaryDocument(ocr: unknown, kind: string): OcrDocumentEntry | undefined {
  return normalizeOcr(ocr).find((d) => d.kind === kind && !d.additional)
}

/** Stored data that is not a document: the person-split provenance and any key this UI does not know. */
export function extraEntries(ocr: unknown): OcrExtraEntry[] {
  if (!isRecord(ocr)) return []
  const out: OcrExtraEntry[] = []
  for (const [key, payload] of Object.entries(ocr)) {
    if (!hasContent(payload)) continue
    if (key === 'person_split') out.push({ id: key, label: 'How the documents were split', payload })
    else if (!HANDLED_KEYS.has(key)) out.push({ id: key, label: humanizeKey(key), payload })
  }
  const documents = normalizeOcr(ocr)
  // Provenance is shown with its document; one whose document is gone must not vanish silently.
  if (isRecord(ocr._sources)) {
    for (const [kind, payload] of Object.entries(ocr._sources)) {
      if (hasContent(payload) && !documents.some((d) => d.kind === kind && !d.additional)) {
        out.push({ id: `source-${kind}`, label: `Where the ${(KIND_LABEL[kind] ?? humanizeKey(kind)).toLowerCase()} came from`, payload })
      }
    }
  }
  // A non-array additional_documents (or one holding non-document values) must not vanish silently.
  const extra = ocr.additional_documents
  if (hasContent(extra) && documents.every((d) => !d.id.startsWith('additional-'))) {
    out.push({ id: 'additional_documents', label: 'Additional documents', payload: extra })
  }
  return out
}

/* --------------------------------------------------------------- Page evidence */

export interface PageView {
  /** Page of the original received file. */
  page: number
  /** Position within the pages sent to Veris, when it differs. */
  sent?: number
  language: string
  confidence: string
  width?: number
  height?: number
  processingMs?: number
  text: string
}

/**
 * `selectedPages` is given for the current storage shape, where `page_number` counts the pages
 * sent to Veris and the original page is `selectedPages[page_number - 1]`. Without it
 * (legacy rows) `page_number` is already the original page.
 */
export function pageViews(payload: unknown, selectedPages?: number[]): PageView[] {
  const pages = isRecord(payload) && Array.isArray(payload.pages) ? payload.pages : []
  const views: PageView[] = []
  pages.forEach((p, i) => {
    if (!isRecord(p)) return
    const given = typeof p.page_number === 'number' ? p.page_number : i + 1
    const mapped = selectedPages?.length ? selectedPages[given - 1] : undefined
    const page = typeof mapped === 'number' ? mapped : given
    const sent = selectedPages?.length
      ? page !== given
        ? given
        : undefined
      : typeof p.provider_page_number === 'number' && p.provider_page_number !== page
        ? p.provider_page_number
        : undefined
    views.push({
      page,
      sent,
      language: typeof p.language === 'string' ? p.language : '',
      confidence: confidenceLabel(typeof p.average_confidence === 'number' ? p.average_confidence : null),
      width: typeof p.width === 'number' ? p.width : undefined,
      height: typeof p.height === 'number' ? p.height : undefined,
      processingMs: typeof p.processing_time_ms === 'number' ? p.processing_time_ms : undefined,
      text: typeof p.text === 'string' ? p.text : '',
    })
  })
  return views
}

export interface InspectionPageRow {
  page: number
  status: string
  type: string
  method: string
  confidence: string
  reason: string
}

export interface InspectionView {
  sourceFormat: string
  pageCount?: number
  complete?: boolean
  rows: InspectionPageRow[]
  groups: { label: string; pages: string }[]
  warnings: string[]
}

const TYPE_LABEL: Record<string, string> = { resume: 'Resume', passport: 'Passport', aadhaar: 'Aadhaar', other: 'Other', unknown: 'Unknown' }

/** The local page classification stored inside a legacy payload; null when none was kept. */
export function inspectionView(payload: unknown): InspectionView | null {
  return inspectionFromReport(isRecord(payload) ? payload.local_inspection : null)
}

/** The local page classification report that decided what was sent to Veris. */
export function inspectionFromReport(report: unknown): InspectionView | null {
  if (!isRecord(report)) return null
  const rows: InspectionPageRow[] = []
  if (Array.isArray(report.pages)) {
    for (const p of report.pages) {
      if (!isRecord(p) || typeof p.page_number !== 'number') continue
      const type = typeof p.document_type === 'string' ? p.document_type : ''
      rows.push({
        page: p.page_number,
        status: typeof p.status === 'string' ? p.status : '',
        type: TYPE_LABEL[type] ?? humanizeKey(type),
        method: typeof p.method === 'string' ? humanizeKey(p.method.replace(/\+/g, ' and ')) : '',
        confidence: confidenceLabel(typeof p.confidence === 'number' ? p.confidence : null),
        reason: typeof p.reason === 'string' ? p.reason : '',
      })
    }
  }
  const groups = [
    ['Identified as resume', report.resume_pages],
    ['Identified as passport', report.passport_pages],
    ['Identified as Aadhaar', report.aadhaar_pages],
    ['Sent to the reader for this document', report.selected_pages],
    ['Pages that could not be read', report.unread_pages],
    ['Pages not identified', report.unknown_pages],
  ]
    .map(([label, pages]) => ({ label: label as string, pages: pageList(nums(pages)) }))
    .filter((g) => g.pages)
  return {
    sourceFormat: typeof report.source_format === 'string' ? report.source_format : '',
    pageCount: typeof report.page_count === 'number' ? report.page_count : undefined,
    complete: typeof report.complete === 'boolean' ? report.complete : undefined,
    rows,
    groups,
    warnings: Array.isArray(report.warnings) ? report.warnings.filter((w): w is string => typeof w === 'string') : [],
  }
}

/* ------------------------------------------------------------------- Value tree */

export type TreeNode =
  | { kind: 'value'; label: string; text: string; mono: boolean }
  | { kind: 'group'; label: string; list: boolean; children: TreeNode[] }

export interface TreeOptions {
  /** Rewrites a string before it is shown (used to mask Aadhaar identifiers). */
  transform?: (path: string[], key: string, text: string) => string
  /** Keep null / empty values as "—". Off by default: an unread field is not a value. */
  includeEmpty?: boolean
}

const isBlob = (s: string) => /^data:[^,]*;base64,/i.test(s) || (s.length > 4096 && !/\s/.test(s))

function scalarText(v: unknown): string {
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  return String(v)
}

function build(value: unknown, label: string, path: string[], key: string, opts: TreeOptions): TreeNode | null {
  if (value === null || value === undefined || value === '') {
    return opts.includeEmpty ? { kind: 'value', label, text: '—', mono: false } : null
  }
  if (Array.isArray(value)) {
    const children: TreeNode[] = []
    value.forEach((item, i) => {
      const node = build(item, `#${i + 1}`,[...path, key], key, opts)
      if (node) children.push(node)
    })
    return children.length || opts.includeEmpty ? { kind: 'group', label, list: true, children } : null
  }
  if (isRecord(value)) {
    const children: TreeNode[] = []
    const here = [...path, key]
    for (const [k, v] of Object.entries(value)) {
      // The rendered page markup duplicates the page text and can be enormous.
      if (k === 'html' && key === 'pages') continue
      // Per-line word boxes are layout coordinates for the same text shown above; a
      // summary keeps the tree readable (the boxes stay stored with the document).
      if (k === 'lines' && key === 'pages' && Array.isArray(v)) {
        if (v.length) children.push({ kind: 'value', label: humanizeKey(k), text: `${v.length.toLocaleString('en-US')} text lines with word positions stored`, mono: false })
        continue
      }
      const node = build(v, humanizeKey(k), here, k, opts)
      if (node) children.push(node)
    }
    return children.length || opts.includeEmpty ? { kind: 'group', label, list: false, children } : null
  }
  if (typeof value === 'string') {
    if (isBlob(value)) return { kind: 'value', label, text: `[binary data omitted · ${value.length.toLocaleString('en-US')} characters]`, mono: false }
    const text = opts.transform ? opts.transform(path, key, value) : value
    return { kind: 'value', label, text, mono: text.includes('\n') }
  }
  return { kind: 'value', label, text: scalarText(value), mono: false }
}

/** The whole payload as labelled nodes: humanised keys, arrays as lists, objects nested. */
export function buildTree(payload: unknown, opts: TreeOptions = {}): TreeNode[] {
  if (isRecord(payload)) {
    const out: TreeNode[] = []
    for (const [k, v] of Object.entries(payload)) {
      const node = build(v, humanizeKey(k), [], k, opts)
      if (node) out.push(node)
    }
    return out
  }
  const node = build(payload, 'Value', [], 'value', opts)
  return node ? [node] : []
}

/** Counts the value leaves of a tree — the "n values" shown on a collapsed document. */
export function countValues(nodes: TreeNode[]): number {
  return nodes.reduce((sum, n) => sum + (n.kind === 'value' ? 1 : countValues(n.children)), 0)
}

/** Masks Aadhaar identifiers in an Aadhaar document for a viewer who may not see them in full. */
export function aadhaarMasker(): NonNullable<TreeOptions['transform']> {
  return (_path, key, text) => (key === 'aadhaar_number' ? maskAadhaarNumber(text) : maskAadhaarText(text))
}
