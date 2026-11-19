/**
 * The Client tracker on the Reports screen: the running sheet of every placement in progress (and travelled), grouped by client,
 * exactly as the clients ask for it. The server (GET /api/reports/client-tracker) builds the rows and numbers them; this file is
 * the screen's side: the columns, the query string and the wording around them.
 */
export interface TrackerRow {
  sNo: number
  applicationId: string; candidateId: string; orderId: string; orderRef: string | null
  client: string; country: string; name: string; position: string; referral: string
  advance: string; stage: string; remarks: string
  stageNo: number; travelled: boolean; travelDate: string | null
}
export interface TrackerOptions { clients: string[]; countries: string[]; agencies: string[]; stages: { no: number; label: string }[] }
export interface TrackerResult { columns: string[]; rows: TrackerRow[]; total: number; options: TrackerOptions }
export interface TrackerFilters { client: string; country: string; agency: string; stage: string; includeTravelled: boolean }

export const EMPTY_TRACKER_FILTERS: TrackerFilters = { client: '', country: '', agency: '', stage: '', includeTravelled: true }
export const NO_TRACKER_OPTIONS: TrackerOptions = { clients: [], countries: [], agencies: [], stages: [] }

/** The columns of the sheet, in order: the header the client reads and the row field each one shows. */
export const TRACKER_COLUMNS: readonly { key: keyof TrackerRow; label: string; numeric?: boolean }[] = [
  { key: 'sNo', label: 'S.No', numeric: true }, { key: 'client', label: 'Client' }, { key: 'country', label: 'Country' },
  { key: 'name', label: 'Name' }, { key: 'position', label: 'Position' }, { key: 'referral', label: 'Ref' },
  { key: 'advance', label: 'Advance' }, { key: 'stage', label: 'Stage' }, { key: 'remarks', label: 'Remarks' },
]

/** The text of one cell: a missing value is an empty cell, never a placeholder that could be mistaken for data. */
export function cellText(row: TrackerRow, key: keyof TrackerRow): string {
  const value = row[key]
  return value === null || value === undefined ? '' : String(value)
}

/** The query string for these filters; a choice left at "all" is not sent, and travelled placements are left out only when asked. */
export function trackerQuery(filters: TrackerFilters, format?: 'xlsx' | 'csv'): string {
  const query = new URLSearchParams()
  const text = (key: string, value: string) => { const clean = value.replace(/\s+/g, ' ').trim(); if (clean) query.set(key, clean) }
  text('client', filters.client); text('country', filters.country); text('agency', filters.agency)
  if (/^\d{1,2}$/.test(filters.stage.trim())) query.set('stage', String(Number(filters.stage)))
  if (!filters.includeTravelled) query.set('include_travelled', 'false')
  if (format) query.set('format', format)
  return query.toString()
}

export function trackerPath(filters: TrackerFilters, format?: 'xlsx' | 'csv'): string {
  const query = trackerQuery(filters, format)
  return `/reports/client-tracker${query ? `?${query}` : ''}`
}

/** How many choices narrow the sheet (the travelled switch counts when it is off). */
export function activeTrackerFilters(filters: TrackerFilters): number {
  return [filters.client, filters.country, filters.agency, filters.stage].filter(value => value.trim()).length + (filters.includeTravelled ? 0 : 1)
}

/** The picker's list, with a chosen value kept in it even when the current data no longer offers it. */
export function withChosen(values: string[], chosen: string): string[] {
  return chosen && !values.includes(chosen) ? [chosen, ...values] : values
}

export function trackerFileName(day: string, format: 'xlsx' | 'csv'): string {
  return `client-tracker-${day}.${format}`
}

/** One line under the table: how many placements, and how many of them have travelled. */
export function trackerSummary(result: Pick<TrackerResult, 'rows' | 'total'>): string {
  const travelled = result.rows.filter(row => row.travelled).length
  const inProgress = result.total - travelled
  if (!result.total) return 'No placements'
  return `${result.total.toLocaleString()} ${result.total === 1 ? 'placement' : 'placements'}: ${inProgress.toLocaleString()} in progress, ${travelled.toLocaleString()} travelled`
}
