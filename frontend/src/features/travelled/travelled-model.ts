/**
 * The Travelled screen's pure logic: what the address (?year=2026&month=05&client=...) means, how a click on a
 * breakdown changes it, and the words shown for it. No React and no imports, so it can be tested on its own.
 *
 * The date filters are a chain (year, then month, then day): a month without a year or a day without a month means
 * nothing, so they are dropped on the way in and cleared when the part before them changes. A complete travel date can also
 * set all three parts at once. Calendar quick choices fill those same controls; multi-month ranges use From / To.
 */

export interface TravelledRow {
  applicationId: string
  candidateId: string
  ref: string
  name: string
  client: string
  clientId: string | null
  passportNumber: string | null
  country: string | null
  job: string | null
  orderRef: string | null
  orderId: string
  agency: string | null
  travelDate: string | null
  airline: string | null
  pnr: string | null
  recruiter: string | null
  status: string
  /** How many of the mandatory documents are on file, and which are not. */
  documents?: { have: number; required: number; missing: string[] }
}

export interface TravelledSummary {
  byYear: { year: number | string; count: number }[]
  byMonth: { month: string; count: number }[]
  byDay: { date: string; count: number }[]
  byClient: { client: string; count: number }[]
  byAgency: { agency: string; count: number }[]
  byCountry: { country: string; count: number }[]
}

export interface TravelledResult {
  rows: TravelledRow[]
  total: number
  summary: TravelledSummary
  options: { years: (number | string)[]; months?: { month: string; count: number }[]; undated?: number; clients: string[]; agencies: string[]; countries: string[];
    orders?: { id: string; ref: string | null; job: string | null; client: string }[] }
}

export interface TravelledFilters {
  year: string
  month: string
  day: string
  from: string
  to: string
  client: string
  order: string
  agency: string
  country: string
  q: string
  /** 'true' = only candidates with a mandatory document still missing. */
  incomplete: string
  /** 'true' = only candidates who travelled but have no travel date on file. */
  undated: string
}

export type TravelledFilterKey = keyof TravelledFilters

/** The order the filters are written in an address and sent to the server. */
export const FILTER_KEYS: readonly TravelledFilterKey[] = ['year', 'month', 'day', 'from', 'to', 'client', 'order', 'agency', 'country', 'q', 'incomplete', 'undated']

export const EMPTY_FILTERS: TravelledFilters = { year: '', month: '', day: '', from: '', to: '', client: '', order: '', agency: '', country: '', q: '', incomplete: '', undated: '' }

export const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'] as const
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const

const pad = (value: number) => String(value).padStart(2, '0')

export function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate()
}

function validYear(value: string | null | undefined): string {
  const text = (value ?? '').trim()
  if (!/^\d{4}$/.test(text)) return ''
  const year = Number(text)
  return year >= 1990 && year <= 2100 ? text : ''
}

/** "5" and "05" are the same month; anything outside 1 to 12 is no month. */
function validMonth(value: string | null | undefined): string {
  const text = (value ?? '').trim()
  if (!/^\d{1,2}$/.test(text)) return ''
  const month = Number(text)
  return month >= 1 && month <= 12 ? pad(month) : ''
}

function validDay(value: string | null | undefined, year: string, month: string): string {
  const text = (value ?? '').trim()
  if (!year || !month || !/^\d{1,2}$/.test(text)) return ''
  const day = Number(text)
  return day >= 1 && day <= daysInMonth(Number(year), Number(month)) ? pad(day) : ''
}

/** A real calendar date written YYYY-MM-DD, else ''. */
export function validIsoDate(value: string | null | undefined): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec((value ?? '').trim())
  if (!match) return ''
  const [, year, month, day] = match
  return validYear(year) && validDay(day, year, month) && validMonth(month) ? `${year}-${month}-${day}` : ''
}

const clean = (value: string | null | undefined, max = 200) => (value ?? '').replace(/\s+/g, ' ').trim().slice(0, max)

/** The filters an address describes. Unknown or impossible values are dropped rather than sent to the server. */
export function filtersFromParams(params: URLSearchParams): TravelledFilters {
  const year = validYear(params.get('year'))
  const month = year ? validMonth(params.get('month')) : ''
  const day = month ? validDay(params.get('day'), year, month) : ''
  const from = validIsoDate(params.get('from'))
  const to = validIsoDate(params.get('to'))
  return {
    year, month, day, from, to,
    client: clean(params.get('client')),
    order: clean(params.get('order')),
    agency: clean(params.get('agency')),
    country: clean(params.get('country')),
    q: clean(params.get('q'), 100),
    incomplete: params.get('incomplete') === 'true' ? 'true' : '',
    // "date not recorded" and a date are opposites; a date in the address wins
    undated: params.get('undated') === 'true' && !year && !from && !to ? 'true' : '',
  }
}

/** The query string for the address and for the server: only the filters that are set, always in the same order. */
export function queryFor(filters: TravelledFilters): string {
  const query = new URLSearchParams()
  for (const key of FILTER_KEYS) if (filters[key]) query.set(key, filters[key])
  return query.toString()
}

/** Change one filter. Changing a date part clears the parts after it; a year, month or day replaces any From / To range. */
export function setFilter(filters: TravelledFilters, key: TravelledFilterKey, value: string): TravelledFilters {
  const next = { ...filters, [key]: value }
  if (key === 'year' || key === 'month' || key === 'day' || key === 'from' || key === 'to') next.undated = ''
  if (key === 'year') { next.month = ''; next.day = '' }
  if (key === 'month') next.day = ''
  if (key === 'client') next.order = ''
  if (key === 'year' || key === 'month' || key === 'day') { next.from = ''; next.to = '' }
  return filtersFromParams(new URLSearchParams(queryFor(next)))
}

/** A click on a date breakdown: a year ("2026"), a month ("2026-05") or a day ("2026-05-09") sets that part and what is before it. */
export function drillTo(filters: TravelledFilters, value: string): TravelledFilters {
  const [year = '', month = '', day = ''] = String(value).split('-')
  return filtersFromParams(new URLSearchParams(queryFor({ ...filters, year, month, day, from: '', to: '', undated: '' })))
}

/** Pick a complete date directly; clearing it widens the selected month by removing only its day. */
export function chooseTravelDate(filters: TravelledFilters, value: string): TravelledFilters {
  if (!value) return filters.day ? { ...filters, day: '' } : filters
  const date = validIsoDate(value)
  return date ? drillTo(filters, date) : filters
}

/** Unknown travel dates are a separate view, mutually exclusive with every calendar filter. */
export function toggleUndated(filters: TravelledFilters): TravelledFilters {
  return filters.undated
    ? { ...filters, undated: '' }
    : { ...filters, year: '', month: '', day: '', from: '', to: '', undated: 'true' }
}

/** Click a client, agency or country in a breakdown: choose it, or clear it when it is already the chosen one. */
export function toggleFilter(filters: TravelledFilters, key: 'client' | 'agency' | 'country', value: string): TravelledFilters {
  return setFilter(filters, key, filters[key] === value ? '' : value)
}

/** Which date breakdown to show next: years, then the months of the chosen year, then the days of the chosen month. */
export function dateLevel(filters: TravelledFilters): 'year' | 'month' | 'day' {
  if (!filters.year) return 'year'
  if (!filters.month) return 'month'
  return 'day'
}

export function monthName(month: string): string {
  return MONTH_NAMES[Number(month) - 1] ?? month
}

/** "2026-05-09" -> "09 May 2026"; '' for a missing or unreadable date. */
export function formatTravelDay(value: string | null | undefined): string {
  const iso = validIsoDate(value)
  if (!iso) return ''
  const [year, month, day] = iso.split('-')
  return `${day} ${monthName(month).slice(0, 3)} ${year}`
}

/** "Sat 9" for a day in the month being shown. */
export function dayLabel(value: string): string {
  const iso = validIsoDate(value)
  if (!iso) return value
  const [year, month, day] = iso.split('-').map(Number)
  return `${WEEKDAYS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()]} ${day}`
}

/** The label of one bar of the date breakdown. */
export function dateBarLabel(level: 'year' | 'month' | 'day', value: string): string {
  if (level === 'year') return value
  if (level === 'month') return monthName(value.slice(5, 7))
  return dayLabel(value)
}

/** The date filter in words: "2026", "May 2026", "9 May 2026", or '' when no date is chosen. */
export function dateSummary(filters: TravelledFilters): string {
  if (filters.undated) return 'Date not recorded'
  if (filters.year && filters.month && filters.day) return `${Number(filters.day)} ${monthName(filters.month)} ${filters.year}`
  if (filters.year && filters.month) return `${monthName(filters.month)} ${filters.year}`
  if (filters.year) return filters.year
  if (filters.from && filters.to) return `${formatTravelDay(filters.from)} to ${formatTravelDay(filters.to)}`
  if (filters.from) return `From ${formatTravelDay(filters.from)}`
  if (filters.to) return `Until ${formatTravelDay(filters.to)}`
  return ''
}

export type QuickKey = 'this-month' | 'last-month' | 'last-3-months' | 'this-year'
export interface QuickRange { key: QuickKey; label: string; from: string; to: string }

/** Today in the agency's own time zone (Asia/Kolkata), as YYYY-MM-DD, whatever the browser's zone is. */
export function agencyToday(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now)
  const get = (type: string) => parts.find(part => part.type === type)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')}`
}

/**
 * The quick ranges for a given today: this calendar month, last calendar month, the last three calendar months (this one and the
 * two before it) and this calendar year. Both ends are whole days, so a trip booked later this month still counts.
 */
export function quickRanges(today: string): QuickRange[] {
  const [year, month] = today.split('-').map(Number)
  const first = (y: number, m: number) => { const d = new Date(Date.UTC(y, m - 1, 1)); return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-01` }
  const last = (y: number, m: number) => { const d = new Date(Date.UTC(y, m, 0)); return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` }
  return [
    { key: 'this-month', label: 'This month', from: first(year, month), to: last(year, month) },
    { key: 'last-month', label: 'Last month', from: first(year, month - 1), to: last(year, month - 1) },
    { key: 'last-3-months', label: 'Last 3 months', from: first(year, month - 2), to: last(year, month) },
    { key: 'this-year', label: 'This year', from: `${year}-01-01`, to: `${year}-12-31` },
  ]
}

/** Recognize calendar selections and previously bookmarked equivalent From / To quick ranges. */
export function activeQuick(filters: TravelledFilters, today: string): QuickKey | '' {
  if (filters.undated || filters.day) return ''
  const ranges = quickRanges(today)
  if (filters.from || filters.to) {
    if (filters.year || filters.month) return ''
    return ranges.find(range => range.from === filters.from && range.to === filters.to)?.key ?? ''
  }
  if (!filters.year) return ''
  if (!filters.month) return filters.year === today.slice(0, 4) ? 'this-year' : ''
  return ranges.find(range => (range.key === 'this-month' || range.key === 'last-month')
    && range.from.slice(0, 7) === `${filters.year}-${filters.month}`)?.key ?? ''
}

/** Choose a quick range, or clear it when it is the one already chosen. Any year / month / day is replaced by it. */
export function toggleQuick(filters: TravelledFilters, key: QuickKey, today: string): TravelledFilters {
  if (activeQuick(filters, today) === key) return drillTo(filters, '')
  const range = quickRanges(today).find(item => item.key === key)
  if (!range) return filters
  if (key === 'this-year') return drillTo(filters, range.from.slice(0, 4))
  if (key === 'this-month' || key === 'last-month') return drillTo(filters, range.from.slice(0, 7))
  return { ...filters, year: '', month: '', day: '', from: range.from, to: range.to, undated: '' }
}

/** "Missing documents only": on or off. */
export function toggleIncomplete(filters: TravelledFilters): TravelledFilters {
  return { ...filters, incomplete: filters.incomplete ? '' : 'true' }
}

/** "9 / 12" for the Documents column; '' when the server sent no counts (an older server). */
export function documentsLabel(row: Pick<TravelledRow, 'documents'>): string {
  return row.documents ? `${row.documents.have} / ${row.documents.required}` : ''
}

/** What the Documents cell says when you point at it: the missing ones, or that nothing is missing. */
export function documentsTitle(row: Pick<TravelledRow, 'documents'>): string {
  if (!row.documents) return ''
  return row.documents.missing.length ? `Missing: ${row.documents.missing.join(', ')}` : 'All mandatory documents are on file'
}

export interface FilterChip { key: TravelledFilterKey | 'date'; label: string }

/** The filters in use, one chip each (the date parts count as one), for the "Clear" row. */
export function activeChips(filters: TravelledFilters): FilterChip[] {
  const chips: FilterChip[] = []
  const date = dateSummary(filters)
  if (date) chips.push({ key: 'date', label: date })
  if (filters.client) chips.push({ key: 'client', label: filters.client })
  if (filters.order) chips.push({ key: 'order', label: 'Selected job / order' })
  if (filters.agency) chips.push({ key: 'agency', label: filters.agency })
  if (filters.country) chips.push({ key: 'country', label: filters.country })
  if (filters.q) chips.push({ key: 'q', label: `"${filters.q}"` })
  if (filters.incomplete) chips.push({ key: 'incomplete', label: 'Missing documents' })
  return chips
}

/** Remove one chip's filter (the date chip clears the year, month, day and range together). */
export function clearChip(filters: TravelledFilters, key: FilterChip['key']): TravelledFilters {
  if (key === 'date') return { ...filters, year: '', month: '', day: '', from: '', to: '', undated: '' }
  return { ...filters, [key]: '' }
}

/** A bar's width as a percentage of the largest bar, never invisible for a non-zero count. */
export function barPercent(count: number, largest: number): number {
  if (!(count > 0) || !(largest > 0)) return 0
  return Math.max(4, Math.round((count / largest) * 100))
}

/** Every chosen value must be selectable even when the server's list does not (yet) hold it, e.g. an address typed by hand. */
export function withChosen(options: readonly string[], chosen: string): string[] {
  return chosen && !options.includes(chosen) ? [chosen, ...options] : [...options]
}

/** The file name for a download: the dates in use make the file easy to tell apart. */
export function exportName(filters: TravelledFilters, format: 'xlsx' | 'csv'): string {
  const part = filters.undated ? 'date-not-recorded' : filters.year ? [filters.year, filters.month, filters.day].filter(Boolean).join('-') : 'all'
  return `travelled-${part}.${format}`
}
