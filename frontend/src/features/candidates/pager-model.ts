/** Page sizes, page numbers and the live count line of the list pager. Pure, so it is unit-tested. */

export const PAGE_SIZES = [10, 25, 50, 100, 250] as const
/** "All" asks for this many in one request (the server's cap); a note says when more exist. */
export const ALL_PAGE_SIZE = 1000

const ROW_HEIGHT = 76
/** Height taken by everything on the page that is not table rows (header, key figures, filters, pager). */
const PAGE_CHROME = 560

/** How many rows fit under the page's other content, rounded to a page-size choice (never fewer than 10, never more than 100). */
export function defaultPageSize(viewportHeight: number, choices: readonly number[] = PAGE_SIZES): number {
  const usable = Number.isFinite(viewportHeight) && viewportHeight > 0 ? viewportHeight : 900
  const fit = Math.max(1, Math.floor((usable - PAGE_CHROME) / ROW_HEIGHT) + 4)
  const pool = choices.filter((size) => size >= 10 && size <= 100)
  return pool.reduce((best, size) => (Math.abs(size - fit) < Math.abs(best - fit) ? size : best), pool[0] ?? 25)
}

const KEY = 'crm-page-size'

/** The person's own choice for this screen, or null when they have not made one. */
export function readPageSize(scope: string, allowed: readonly number[] = [...PAGE_SIZES, ALL_PAGE_SIZE]): number | null {
  try {
    const value = Number(window.localStorage.getItem(`${KEY}:${scope}`))
    return allowed.includes(value) ? value : null
  } catch { return null }
}

export function savePageSize(scope: string, size: number) {
  try { window.localStorage.setItem(`${KEY}:${scope}`, String(size)) } catch { /* the choice lasts until the page closes */ }
}

/** Page numbers to show: always the first and last, the neighbours of the current page, and "…" where pages are skipped. Zero-based in, numbers shown one-based. */
export function pageList(page: number, pageCount: number, around = 1): (number | '…')[] {
  if (pageCount <= 1) return [1]
  const current = Math.min(Math.max(0, page), pageCount - 1) + 1
  const wanted = new Set([1, pageCount, current])
  for (let step = 1; step <= around; step += 1) { wanted.add(current - step); wanted.add(current + step) }
  if (current <= 3) { wanted.add(2); wanted.add(3) }
  if (current >= pageCount - 2) { wanted.add(pageCount - 1); wanted.add(pageCount - 2) }
  const numbers = [...wanted].filter((n) => n >= 1 && n <= pageCount).sort((a, b) => a - b)
  const out: (number | '…')[] = []
  numbers.forEach((n, index) => {
    if (index > 0 && n - numbers[index - 1] === 2) out.push(n - 1)
    else if (index > 0 && n - numbers[index - 1] > 2) out.push('…')
    out.push(n)
  })
  return out
}

export interface CountInput { page: number; pageSize: number; total: number; noun: string; filtered?: boolean }

/** "Showing 1–25 of 30 matching leads" / "Showing 1–20 of 4,000 leads" / "No matching leads" / "No leads yet". */
export function countText({ page, pageSize, total, noun, filtered = true }: CountInput): string {
  if (total <= 0) return filtered ? `No matching ${noun}` : `No ${noun} yet`
  const count = Math.max(1, Math.ceil(total / pageSize))
  const safe = Math.min(Math.max(0, page), count - 1)
  const from = safe * pageSize + 1
  const to = Math.min(total, (safe + 1) * pageSize)
  return `Showing ${from.toLocaleString()}–${to.toLocaleString()} of ${total.toLocaleString()}${filtered ? ' matching' : ''} ${noun}`
}

/** The note under "All" when the server's cap cut the list short. */
export const allNote = (total: number, noun: string): string | null =>
  total > ALL_PAGE_SIZE ? `Showing the first ${ALL_PAGE_SIZE.toLocaleString()} of ${total.toLocaleString()} ${noun}. Narrow the filters or pick a smaller page size to see the rest.` : null
