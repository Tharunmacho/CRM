/** Small date and time helpers shared by the attendance screens (all times are shown in the agency timezone). */

export function stamp(value: string | null | undefined, timezone: string) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat('en-GB', { timeZone: timezone, month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(parsed)
}

export function clockTime(value: string | null | undefined, timezone: string) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat('en-GB', { timeZone: timezone, hour: '2-digit', minute: '2-digit' }).format(parsed)
}

/** "12 Oct" for a YYYY-MM-DD date. */
export function dayLabel(date: string) {
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(date + 'T12:00:00Z'))
}

export function weekdayLabel(date: string, style: 'long' | 'short' = 'long') {
  return new Intl.DateTimeFormat('en-GB', { weekday: style, timeZone: 'UTC' }).format(new Date(date + 'T12:00:00Z'))
}

export function monthTitle(month: string) {
  return new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${month}-01T12:00:00Z`))
}

export function shiftMonth(month: string, delta: number) {
  const [year, number] = month.split('-').map(Number)
  const next = new Date(Date.UTC(year, number - 1 + delta, 1, 12))
  return `${next.getUTCFullYear()}-${String(next.getUTCMonth() + 1).padStart(2, '0')}`
}

export function daysInMonth(month: string) {
  const [year, number] = month.split('-').map(Number)
  return new Date(Date.UTC(year, number, 0, 12)).getUTCDate()
}

/** Monday-first weekday index (0 to 6) of the first day of a month. */
export function firstWeekdayIndex(month: string) {
  return (new Date(`${month}-01T12:00:00Z`).getUTCDay() + 6) % 7
}

/** Same deterministic hue as RecordIdentity, so a person keeps one colour everywhere. */
export function hueClass(name: string) {
  return 'avatar-hue-' + (name.split('').reduce((total, character) => total + character.charCodeAt(0), 0) % 6)
}
