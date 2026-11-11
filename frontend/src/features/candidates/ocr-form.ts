import type { OcrScan } from '@/shared/api/client'

export type ScanKind = 'resume' | 'passport' | 'aadhaar'
export type DocumentScans = Partial<Record<ScanKind, OcrScan>>
const SCAN_KINDS: ScanKind[] = ['resume', 'passport', 'aadhaar']

/** Provenance keys older responses mixed into `raw`; they are not Veris output. */
const LEGACY_PROVENANCE_KEYS = new Set(['local_inspection', 'source_sha256', 'source_file', 'provider_page_number'])

/**
 * An inspection report alone is not extracted candidate data. `raw` is the verbatim Veris
 * result, so any Veris key counts; provenance that older responses stored inside it does not.
 */
export function hasOcrExtraction(scan: OcrScan | undefined): scan is OcrScan {
  return !!scan
    && (scan.extraction_status === undefined || scan.extraction_status === 'complete')
    && !!scan.raw
    && Object.keys(scan.raw).some((key) => !LEGACY_PROVENANCE_KEYS.has(key))
}

/** A later file still being read, or requiring review, must not erase a finished file's suggestions. */
export function scansFromJobs(jobs: { result?: { scans?: DocumentScans } }[]): DocumentScans {
  const scans: DocumentScans = {}
  for (const job of jobs) {
    for (const kind of SCAN_KINDS) {
      const scan = job.result?.scans?.[kind]
      if (!scan) continue
      const previous = scans[kind]
      if (!previous || hasOcrExtraction(scan) || !hasOcrExtraction(previous)) scans[kind] = scan
    }
  }
  return scans
}

/**
 * The `ocr_data` sent with a save: each scanned kind's verbatim Veris result, plus its provenance
 * under `_sources` (omitted for older responses that carry the provenance inside `raw`).
 */
export function ocrSavePayload(scans: DocumentScans): Record<string, unknown> | undefined {
  const kinds = SCAN_KINDS.filter((kind) => hasOcrExtraction(scans[kind]))
  if (!kinds.length) return undefined
  const data: Record<string, unknown> = {}
  const sources: Record<string, unknown> = {}
  for (const kind of kinds) {
    const scan = scans[kind]!
    data[kind] = scan.raw
    if (scan.source && typeof scan.source === 'object') sources[kind] = scan.source
  }
  if (Object.keys(sources).length) data._sources = sources
  return data
}

/**
 * Rebuild only OCR-owned values from the currently selected documents. An edited
 * field stays owned by the recruiter, even when they intentionally clear it.
 */
export function mergeOcrForm<T extends Record<string, string>>(
  current: T,
  defaults: T,
  scans: DocumentScans,
  edited: ReadonlySet<keyof T>,
): T {
  const extracted: Record<string, string> = {}
  // Preserve the existing passport-over-resume precedence for unedited fields.
  for (const kind of ['resume', 'aadhaar', 'passport'] as const) {
    const scan = scans[kind]
    if (!hasOcrExtraction(scan)) continue
    for (const [key, value] of Object.entries(scan.fields)) {
      if (!Object.hasOwn(defaults, key) || value == null) continue
      // An Aadhaar address is the registered one, not necessarily where the person lives: it only fills an empty address.
      if (kind === 'aadhaar' && key === 'currentAddress' && extracted[key]) continue
      // Lists of rows (education, work history) are kept as JSON text; lists of words are comma-separated.
      const text = Array.isArray(value) ? (value.some((item) => item && typeof item === 'object') ? JSON.stringify(value) : value.join(', ')) : String(value)
      if (text.trim()) extracted[key] = text
    }
  }
  const next = { ...current }
  for (const key of Object.keys(defaults) as (keyof T)[]) {
    if (!edited.has(key)) next[key] = (extracted[key as string] ?? defaults[key]) as T[keyof T]
  }
  return next
}
