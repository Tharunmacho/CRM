/**
 * Arrival history for the Data Bank KPI cards, measured on the real `addedAt` / `createdAt`
 * stamps of the records already loaded. Nothing here invents a baseline: a month with no
 * arrivals yields `pct: null` so the card says so instead of printing a percentage.
 */

export interface ArrivalTrend {
  /** Arrivals so far this month. */
  current: number
  /** Arrivals in the same span (first N days) of last month. */
  previous: number
  /** Percent change, or null when last month had nothing to compare against. */
  pct: number | null
}

const stamp = (value?: string | null): number => {
  const time = value ? new Date(value).getTime() : NaN
  return Number.isNaN(time) ? NaN : time
}

/** This month so far against the same number of days of last month, so a half month is not set against a whole one. */
export function arrivalTrend(dates: (string | null | undefined)[], now: Date = new Date()): ArrivalTrend {
  const thisStart = new Date(now.getFullYear(), now.getMonth(), 1).getTime()
  const lastStart = new Date(now.getFullYear(), now.getMonth() - 1, 1).getTime()
  const span = now.getTime() - thisStart
  const lastEnd = Math.min(lastStart + span, thisStart)
  let current = 0
  let previous = 0
  for (const value of dates) {
    const time = stamp(value)
    if (Number.isNaN(time)) continue
    if (time >= thisStart && time <= now.getTime()) current += 1
    else if (time >= lastStart && time < lastEnd) previous += 1
  }
  return { current, previous, pct: previous > 0 ? ((current - previous) / previous) * 100 : null }
}

/** Arrivals per week for the last `weeks` weeks, oldest first, for the small trend line on a card. */
export function weeklyArrivals(dates: (string | null | undefined)[], weeks = 12, now: Date = new Date()): number[] {
  const week = 7 * 24 * 60 * 60 * 1000
  const buckets = Array.from({ length: weeks }, () => 0)
  for (const value of dates) {
    const time = stamp(value)
    if (Number.isNaN(time)) continue
    const age = Math.floor((now.getTime() - time) / week)
    if (age >= 0 && age < weeks) buckets[weeks - 1 - age] += 1
  }
  return buckets
}

/** The words under a KPI figure: the arrow and percent when a baseline exists, otherwise a plain statement. */
export function trendCaption(trend: ArrivalTrend): { tone: 'up' | 'down' | 'flat' | 'quiet'; lead: string; rest: string } {
  if (trend.pct !== null) {
    const rounded = Math.abs(trend.pct).toFixed(1)
    if (trend.pct === 0) return { tone: 'flat', lead: '0.0%', rest: 'since last month' }
    return { tone: trend.pct > 0 ? 'up' : 'down', lead: `${trend.pct > 0 ? '↑' : '↓'} ${rounded}%`, rest: 'since last month' }
  }
  if (trend.current > 0) return { tone: 'up', lead: `+${trend.current}`, rest: 'new this month' }
  return { tone: 'quiet', lead: 'None added', rest: 'this month' }
}
