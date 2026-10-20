import type { OcrInspection, OcrScan } from '@/shared/api/client'

export type ScanKind = 'resume' | 'passport'
export type DocumentScans = Partial<Record<ScanKind, OcrScan>>

export function inspectedPageCount(inspection: OcrInspection): number {
  return inspection.pages.filter((page) => page.method !== 'not_processed').length
}

/** An inspection report alone is not extracted candidate data. */
export function hasOcrExtraction(scan: OcrScan | undefined): scan is OcrScan {
  return !!scan
    && (scan.extraction_status === undefined || scan.extraction_status === 'complete')
    && !!scan.raw
    && Object.keys(scan.raw).some((key) => key !== 'local_inspection')
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
  for (const kind of ['resume', 'passport'] as const) {
    const scan = scans[kind]
    if (!hasOcrExtraction(scan)) continue
    for (const [key, value] of Object.entries(scan.fields)) {
      if (!Object.hasOwn(defaults, key) || value == null) continue
      const text = Array.isArray(value) ? value.join(', ') : String(value)
      if (text.trim()) extracted[key] = text
    }
  }
  const next = { ...current }
  for (const key of Object.keys(defaults) as (keyof T)[]) {
    if (!edited.has(key)) next[key] = (extracted[key as string] ?? defaults[key]) as T[keyof T]
  }
  return next
}

/** Preserve each warning once, including when extraction was deliberately skipped. */
export function scanWarnings(scan: OcrScan): string[] {
  return [...new Set([...(scan.warnings ?? []), ...(scan.inspection?.warnings ?? [])])]
}
