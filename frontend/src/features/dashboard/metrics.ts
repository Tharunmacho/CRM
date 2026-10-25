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

/** How many of the times fall in each of `count` equal buckets that start at `start`; times outside every bucket are ignored. */
export function countBuckets(times: number[], start: number, width: number, count: number): number[] {
  const buckets = Array.from({ length: count }, () => 0)
  for (const time of times) {
    if (!Number.isFinite(time) || time < start) continue
    const index = Math.floor((time - start) / width)
    if (index < count) buckets[index] += 1
  }
  return buckets
}

/** Like countBuckets, but the last bucket also takes everything beyond it (for "48 hours or more"). */
export function countBucketsOpenEnd(times: number[], start: number, width: number, count: number): number[] {
  const buckets = Array.from({ length: count }, () => 0)
  for (const time of times) {
    if (!Number.isFinite(time) || time < start) continue
    buckets[Math.min(count - 1, Math.floor((time - start) / width))] += 1
  }
  return buckets
}

export const startOfDay = (time: number) => { const day = new Date(time); day.setHours(0, 0, 0, 0); return day.getTime() }

/* ---------------------------------------------------------------- intake buckets (stacked columns) */

export type BucketUnit = 'day' | 'week' | 'month' | 'quarter'
export interface IntakeBucket { from: number; to: number; label: string; full: string; counts: Record<string, number>; total: number }

const shortDay = (at: number) => new Date(at).toLocaleDateString('en', { day: 'numeric', month: 'short' })
const monthOf = (at: number) => new Date(at).toLocaleDateString('en', { month: 'short' })

/** The column size for each period: days for a week or a month, weeks for 90 days, months for a year, months (or quarters when long) for all time. */
export function bucketUnit(period: Period, start: number, end: number): BucketUnit {
  if (period === 'week' || period === 'month') return 'day'
  if (period === 'quarter') return 'week'
  if (period === 'year') return 'month'
  return (end - start) / DAY / 30.4 > 18 ? 'quarter' : 'month'
}

/**
 * Calendar-aligned columns across the window, each holding the number of candidates per source.
 * Every candidate inside [start, end] lands in exactly one column, so the columns always add up to the window total.
 * A source that is not one of `sources` is counted as the last one ("other").
 */
export function intakeBuckets(candidates: Pick<DatedCandidate, 'addedAt' | 'source'>[], period: Period, start: number, end: number, sources: readonly string[]): { unit: BucketUnit; buckets: IntakeBucket[] } {
  const unit = bucketUnit(period, start, end)
  const first = new Date(start)
  const cursor = unit === 'day' || unit === 'week' ? new Date(first.getFullYear(), first.getMonth(), first.getDate())
    : unit === 'month' ? new Date(first.getFullYear(), first.getMonth(), 1) : new Date(first.getFullYear(), Math.floor(first.getMonth() / 3) * 3, 1)
  const edges: number[] = []
  for (let guard = 0; guard < 400; guard += 1) {
    edges.push(cursor.getTime())
    if (cursor.getTime() > end) break
    if (unit === 'day') cursor.setDate(cursor.getDate() + 1)
    else if (unit === 'week') cursor.setDate(cursor.getDate() + 7)
    else cursor.setMonth(cursor.getMonth() + (unit === 'month' ? 1 : 3))
  }
  if (edges.length < 2) edges.push(end + 1)
  const crossesYears = new Date(edges[0]).getFullYear() !== new Date(end).getFullYear()
  const buckets: IntakeBucket[] = edges.slice(0, -1).map((from, index) => {
    const to = edges[index + 1]
    const last = Math.min(end, to - 1)
    const label = unit === 'day' || unit === 'week' ? shortDay(from)
      : unit === 'month' ? `${monthOf(from)}${crossesYears ? ` ’${String(new Date(from).getFullYear()).slice(2)}` : ''}`
        : `Q${Math.floor(new Date(from).getMonth() / 3) + 1}${crossesYears ? ` ’${String(new Date(from).getFullYear()).slice(2)}` : ''}`
    const full = unit === 'day' ? new Date(from).toLocaleDateString('en', { weekday: 'short', day: 'numeric', month: 'short' })
      : unit === 'week' ? `${shortDay(from)} to ${shortDay(last)}`
        : unit === 'month' ? new Date(from).toLocaleDateString('en', { month: 'long', year: 'numeric' })
          : `${monthOf(from)} to ${monthOf(to - 1)} ${new Date(from).getFullYear()}`
    return { from, to, label, full, counts: Object.fromEntries(sources.map(key => [key, 0])), total: 0 }
  })
  const fallback = sources[sources.length - 1]
  for (const candidate of candidates) {
    const at = addedTime(candidate)
    if (at === null || at < start || at > end) continue
    const bucket = buckets.find(item => at >= item.from && at < item.to) ?? buckets[buckets.length - 1]
    const key = sources.includes(candidate.source) ? candidate.source : fallback
    bucket.counts[key] += 1
    bucket.total += 1
  }
  return { unit, buckets }
}

/** Axis for a column chart: the top is a quarter above the highest column (never under 2), with three or four whole-number steps. */
export function axisFor(peak: number): { max: number; step: number; ticks: number[] } {
  const wanted = Math.max(2, Math.ceil(peak * 1.25))
  let step = 1
  for (let magnitude = 1; magnitude < 1e9; magnitude *= 10) {
    const found = [1, 2, 5].map(factor => factor * magnitude).find(value => value >= wanted / 3)
    if (found) { step = found; break }
  }
  const max = step * Math.ceil(wanted / step)
  return { max, step, ticks: Array.from({ length: Math.round(max / step) + 1 }, (_, index) => index * step) }
}

/* ---------------------------------------------------------------- arrivals heatmap */

export interface ArrivalDay { time: number; count: number; label: string; future: boolean }
const dayLabel = (at: number) => new Date(at).toLocaleDateString('en', { weekday: 'short', day: 'numeric', month: 'short' })

/** The last `weeks` calendar weeks (Monday first) with the number of candidates added on each day. */
export function arrivalGrid(candidates: Pick<DatedCandidate, 'addedAt'>[], now: number, weeks = 12) {
  const today = new Date(startOfDay(now))
  const monday = new Date(today)
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7) - (weeks - 1) * 7)
  const key = (date: Date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`
  const perDay = new Map<string, number>()
  const first = monday.getTime()
  for (const candidate of candidates) {
    const at = addedTime(candidate)
    if (at === null || at < first || at > now) continue
    const k = key(new Date(at))
    perDay.set(k, (perDay.get(k) ?? 0) + 1)
  }
  const columns: ArrivalDay[][] = []
  const cursor = new Date(monday)
  let max = 0, total = 0, activeDays = 0, elapsed = 0
  let busiest: ArrivalDay | null = null
  for (let week = 0; week < weeks; week += 1) {
    const column: ArrivalDay[] = []
    for (let day = 0; day < 7; day += 1) {
      const future = cursor.getTime() > today.getTime()
      const count = future ? 0 : perDay.get(key(cursor)) ?? 0
      const cell = { time: cursor.getTime(), count, label: dayLabel(cursor.getTime()), future }
      column.push(cell)
      if (!future) elapsed += 1
      if (count > 0) { activeDays += 1; total += count; max = Math.max(max, count); if (!busiest || count > busiest.count) busiest = cell }
      cursor.setDate(cursor.getDate() + 1)
    }
    columns.push(column)
  }
  return { columns, max, total, activeDays, elapsed, busiest }
}

/**
 * Funnel figures from the active applications per stage. "Reached" is everyone now at that stage or further, so it only
 * ever falls; "moved on" is the share of those who are already past the stage. The holdup is the busy stage with the lowest share, and only counts once somebody has moved past it.
 */
export function stageReach(counts: number[]) {
  const reached = counts.map((_, index) => counts.slice(index).reduce((sum, value) => sum + value, 0))
  const moved = counts.map((_, index) => index < counts.length - 1 && reached[index] > 0 ? reached[index + 1] / reached[index] : null)
  let holdup: number | null = null
  moved.forEach((share, index) => {
    // Only stages that most of the funnel passes through can be the holdup; two people at the very end are not a trend.
    if (share === null || share >= 1 || share === 0 || reached[index] < Math.max(2, Math.ceil(reached[0] / 4))) return
    if (holdup === null || share < (moved[holdup] as number) || (share === moved[holdup] && counts[index] > counts[holdup])) holdup = index
  })
  return { reached, moved, holdup }
}

/** One column per calendar day for the last `days` days (today included), split by source. */
export function recentDays(candidates: Pick<DatedCandidate, 'addedAt' | 'source'>[], days: number, now: number, sources: readonly string[]): IntakeBucket[] {
  const first = new Date(startOfDay(now))
  first.setDate(first.getDate() - (days - 1))
  return intakeBuckets(candidates, 'week', first.getTime(), now, sources).buckets
}
