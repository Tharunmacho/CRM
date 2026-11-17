/** Where a document cut from a larger scan came from: the scan's name and the pages of it this file holds (1-based). */
export interface SplitSource {
  fileName: string
  pages: number[]
}

/** Page numbers as the office reads them: consecutive pages as a range, the rest separated by commas ("1, 8", "2-3", "4-7"). */
export function formatPageRanges(pages: number[]): string {
  const sorted = [...new Set(pages)].sort((a, b) => a - b)
  const parts: string[] = []
  for (let start = 0; start < sorted.length;) {
    let end = start
    while (end + 1 < sorted.length && sorted[end + 1] === sorted[end] + 1) end += 1
    parts.push(end > start ? `${sorted[start]}-${sorted[end]}` : String(sorted[start]))
    start = end + 1
  }
  return parts.join(', ')
}

/** "Pages 1, 8 of Scan.pdf" / "Page 3 of Scan.pdf"; empty when the file was not cut from a scan. */
export function splitSourceCaption(source: SplitSource | null | undefined): string {
  if (!source || !source.fileName || !source.pages?.length) return ''
  const unique = new Set(source.pages)
  return `${unique.size === 1 ? 'Page' : 'Pages'} ${formatPageRanges(source.pages)} of ${source.fileName}`
}
