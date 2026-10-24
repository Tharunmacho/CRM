/** Pure period maths for the dashboard overview cards. Everything is computed from candidates already loaded. */
export type Period = 'year' | 'quarter' | 'month' | 'week' | 'all'
export interface Point { label: string; value: number }
export interface DatedCandidate { addedAt: string; source: string; status: string; needsReview?: boolean; ownerId: string | null }

const DAY = 86_400_000

export const PERIODS: { value: Period; label: string; short: string }[] = [
  { value: 'week', label: 'Last 7 days', short: '7 days' }, { value: 'month', label: 'Last 30 days', short: '30 days' },
  { value: 'quarter', label: 'Last 90 days', short: '90 days' }, { value: 'year', label: 'This year', short: 'This year' }, { value: 'all', label: 'All time', short: 'All time' },
]

export function addedTime(candidate: Pick<DatedCandidate, 'addedAt'>): number | null {
  const at = Date.parse(candidate.addedAt || '')
  return Number.isFinite(at) ? at : null
}

export function periodStart(period: Period, anchor: Date, candidates: Pick<DatedCandidate, 'addedAt'>[]): number {
  if (period === 'year') return new Date(anchor.getFullYear(), 0, 1).getTime()
  if (period === 'all') {
    const times = candidates.flatMap(candidate => { const at = addedTime(candidate); return at === null ? [] : [at] })
    return Math.min(anchor.getTime() - 90 * DAY, ...times)
  }
  return anchor.getTime() - (period === 'quarter' ? 90 : period === 'month' ? 30 : 7) * DAY
}

export function inWindow<T extends Pick<DatedCandidate, 'addedAt'>>(candidates: T[], start: number, end: number): T[] {
  return candidates.filter(candidate => { const at = addedTime(candidate); return at !== null && at >= start && at <= end })
}

/** A candidate counts as verified when no review flag is set on the profile. */
export const isVerified = (candidate: Pick<DatedCandidate, 'status' | 'needsReview'>) => !candidate.needsReview && candidate.status !== 'needs_review'

function bucketLabel(at: number, span: number) {
  return new Date(at).toLocaleDateString('en', { month: 'short', ...(span < 100 * DAY ? { day: 'numeric' } : {}) })
}

/** Seven equal buckets across the window, either per-bucket counts or a running total. */
export function seriesFor(candidates: DatedCandidate[], start: number, end: number, predicate: (candidate: DatedCandidate) => boolean = () => true, cumulative = false, count = 7): Point[] {
  const width = Math.max(DAY, end - start + 1) / count
  let running = 0
  return Array.from({ length: count }, (_, index) => {
    const from = start + index * width
    const to = start + (index + 1) * width
    const value = candidates.filter(candidate => { const at = addedTime(candidate); return at !== null && at >= from && at < to && predicate(candidate) }).length
    running += value
    return { label: bucketLabel(Math.min(end, to - 1), end - start), value: cumulative ? running : value }
  })
}

/** Share of each bucket that is verified, 0 where the bucket is empty. */
export function verificationSeries(candidates: DatedCandidate[], start: number, end: number): Point[] {
  const all = seriesFor(candidates, start, end)
  const ok = seriesFor(candidates, start, end, isVerified)
  return all.map((point, index) => ({ label: point.label, value: point.value ? Math.round(ok[index].value / point.value * 100) : 0 }))
}

/** Change against the window of the same length just before; null when there is nothing earlier to compare with. */
export function trendPercent(period: Period, total: number, previous: number): number | null {
  if (period === 'all' || !previous) return null
  return (total - previous) / previous * 100
}

export function previousCount(candidates: DatedCandidate[], start: number, end: number): number {
  return inWindow(candidates, start - (end - start), start - 1).length
}

export const ratio = (count: number, total: number) => (total ? Math.round(count / total * 100) : 0)
