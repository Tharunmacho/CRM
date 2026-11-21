/**
 * Job requirements: the sheet recruiters fill in by hand (S.No, DOC, Client name, Country, Requirements details, Ad, Status, then the
 * extra columns the team adds). Pure rules only (rows, serial numbers, typed dates, summaries, validation), so they are tested without a browser.
 * The screen is src/screens/job-requirements/job-requirements.tsx; the server contract is in job-requirements-api.ts.
 */

export type RequirementStatus = 'active' | 'closed'
export type StatusFilter = 'all' | RequirementStatus
export type FieldType = 'text' | 'number' | 'date' | 'yes_no'
export type ClosedReason = 'filled' | 'client_cancelled' | 'expired' | 'other' | ''
/** A tab of the sheet: the two statuses, or the two deadline views the summary cards open (they only look at active rows). */
export type RowFilter = StatusFilter | 'dueSoon' | 'overdue'

/**
 * The keys of the "Requirements" form, exactly as the server stores them (details.*): the owner's sheet "Job Requirements Details" (16 items, in his
 * order) and, as the 17th, the Deadline that the due and overdue chip, the summary cards and the reminders use.
 */
export const DETAIL_KEYS = [
  'position', 'vacancies', 'placeOfWork', 'contractPeriod', 'qualification', 'agePreference', 'additionalSkills', 'basicSalary', 'workingHours',
  'overtime', 'otCalculation', 'food', 'accommodation', 'insurance', 'transport', 'remarks', 'deadline',
] as const
export type DetailKey = typeof DETAIL_KEYS[number]
export type Details = Partial<Record<DetailKey, string>>

export interface RequirementItem {
  id: string
  docDate: string | null
  clientName: string
  country: string
  details: Details
  adUrl: string | null
  adName: string | null
  status: RequirementStatus
  custom: Record<string, string>
  /** Who handles it (a recruiter or the MD); the server defaults it to the creator. */
  ownerId?: string | null
  ownerName?: string | null
  closedReason?: ClosedReason | null
  closedAt?: string | null
  createdAt: string
  updatedAt: string
  createdBy: { id: string; name: string } | null
  updatedBy: { id: string; name: string } | null
}

export interface RequirementField {
  id: string
  key: string
  label: string
  type: FieldType
  sort: number
}

export interface RequirementSummary {
  active: number
  closed: number
  addedThisWeek: number
  dueSoon: number
  overdue: number
  byOwner: { id: string; name: string; active: number }[]
}

export interface RequirementsPage { items: RequirementItem[]; fields: RequirementField[]; total: number; summary?: RequirementSummary }

export interface HistoryEntry { at: string; actorId: string | null; actorName: string | null; action: string; summary: string }

/** What a PATCH may carry; details are merged by key and custom values by field id on the server. */
export interface RequirementPatch {
  docDate?: string | null
  clientName?: string
  country?: string
  status?: RequirementStatus
  ownerId?: string
  closedReason?: ClosedReason
  details?: Details
  custom?: Record<string, string>
}

export const PAGE_LIMIT = 200
export const AD_MAX_BYTES = 10 * 1024 * 1024
export const FIELD_LABEL_MAX = 60

export const STATUS_TABS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'active', label: 'Active' },
  { key: 'closed', label: 'Closed' },
]

export const FIELD_TYPES: { value: FieldType; label: string }[] = [
  { value: 'text', label: 'Text' },
  { value: 'number', label: 'Number' },
  { value: 'date', label: 'Date' },
  { value: 'yes_no', label: 'Yes / No' },
]

export const fieldTypeLabel = (type: FieldType): string => FIELD_TYPES.find((entry) => entry.value === type)?.label ?? 'Text'

/* ------------------------------------------------------------------ dates (the same rules as the Tasks DOC cell, screens/tasks/doc-input.tsx) */

/** "2026-11-19" -> "19/11/2026"; anything that is not a date -> "". */
export function isoToText(iso: string | null | undefined): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '')
  return match ? `${match[3]}/${match[2]}/${match[1]}` : ''
}

/** "19/11/2026" (also 19-11-2026, 19.11.2026, 19112026) -> "2026-11-19" when it is a real day from 2000 to 2100, else null. */
export function textToIso(text: string): string | null {
  const digits = text.replace(/\D/g, '')
  if (digits.length !== 8) return null
  const [day, month, year] = [Number(digits.slice(0, 2)), Number(digits.slice(2, 4)), Number(digits.slice(4))]
  if (year < 2000 || year > 2100) return null
  const real = new Date(Date.UTC(year, month - 1, day))
  if (real.getUTCFullYear() !== year || real.getUTCMonth() !== month - 1 || real.getUTCDate() !== day) return null
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

/** What the person has typed so far, shaped as dd/mm/yyyy with the slashes added for them. */
export function shapeDateText(raw: string): string {
  const digits = raw.replace(/\D/g, '').slice(0, 8)
  if (digits.length <= 2) return digits
  if (digits.length <= 4) return `${digits.slice(0, 2)}/${digits.slice(2)}`
  return `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`
}

export type DateEdit = { ok: true; iso: string | null } | { ok: false; error: string }

/** A typed date: empty clears it, a complete real date is accepted, a half-typed or impossible one is refused with a message and never sent. */
export function readTypedDate(text: string): DateEdit {
  if (text.trim() === '') return { ok: true, iso: null }
  const iso = textToIso(text)
  return iso ? { ok: true, iso } : { ok: false, error: 'Type the date as dd/mm/yyyy' }
}

/* ------------------------------------------------------------------ rows */

/** The running number of a row in the list on screen: 1 for the first row, then 2, 3 ... */
export const serialNumber = (index: number): number => index + 1

export interface RequirementRow { serial: number; item: RequirementItem }

/** The rows of one tab, numbered 1..n in the order shown (newest first, as the server sends them). */
export function buildRows(items: readonly RequirementItem[], filter: RowFilter, today = ''): RequirementRow[] {
  const keep = (item: RequirementItem) =>
    filter === 'all' || item.status === filter || (filter === 'dueSoon' && isDueSoon(item, today)) || (filter === 'overdue' && isOverdue(item, today))
  return items.filter(keep).map((item, index) => ({ serial: serialNumber(index), item }))
}

export function statusCounts(items: readonly RequirementItem[]): Record<StatusFilter, number> {
  const active = items.filter((item) => item.status === 'active').length
  return { all: items.length, active, closed: items.length - active }
}

export const flipStatus = (status: RequirementStatus): RequirementStatus => (status === 'active' ? 'closed' : 'active')
export const statusLabel = (status: RequirementStatus): string => (status === 'active' ? 'Active' : 'Closed')

/** The filters the server applies (the status tabs and the deadline views are applied to what is loaded, so their counts stay right). */
export interface ListFilters { q: string; owner: string; client: string; country: string; from: string; to: string; due: '' | 'soon' | 'overdue' }
export const NO_FILTERS: ListFilters = { q: '', owner: '', client: '', country: '', from: '', to: '', due: '' }
export const filterCount = (filters: ListFilters): number => [filters.owner, filters.client, filters.country, filters.from, filters.to].filter(Boolean).length

/** The query string of the list read (and of the export). `due` (soon or overdue) is what the Due in 7 days and Overdue cards ask for. */
export function filterParams(filters: ListFilters): URLSearchParams {
  const query = new URLSearchParams()
  const q = filters.q.replace(/\s+/g, ' ').trim()
  if (q) query.set('q', q)
  for (const key of ['owner', 'client', 'country', 'from', 'to'] as const) if (filters[key].trim()) query.set(key, filters[key].trim())
  if (filters.due) query.set('due', filters.due)
  return query
}

/** Status is always read as `all`: the tabs filter what is loaded, so their counts stay right. */
export function listQuery(filters: ListFilters, limit = PAGE_LIMIT, offset = 0): string {
  const query = new URLSearchParams({ status: 'all', limit: String(limit), offset: String(offset) })
  filterParams(filters).forEach((value, key) => query.set(key, value))
  return query.toString()
}

/** The same filters as a file download: format=xlsx|csv. */
export const exportQuery = (filters: ListFilters, format: 'xlsx' | 'csv'): string => {
  const query = filterParams(filters)
  query.set('format', format)
  return query.toString()
}

/** The day `days` before an ISO day ("2026-11-21", 6 -> "2026-11-15"). */
export function daysBefore(iso: string, days: number): string {
  const [year, month, day] = iso.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, day - days)).toISOString().slice(0, 10)
}

/* ------------------------------------------------------------------ the deadline (details.deadline) */

const DAY_MS = 86_400_000
export const DUE_SOON_DAYS = 7

/** Whole days from `today` to the ISO `deadline` (negative once it has passed); null when there is no real date. */
export function daysUntil(deadline: string | undefined | null, today: string): number | null {
  const a = /^(\d{4})-(\d{2})-(\d{2})/.exec(deadline ?? '')
  const b = /^(\d{4})-(\d{2})-(\d{2})/.exec(today)
  if (!a || !b) return null
  return Math.round((Date.UTC(+a[1], +a[2] - 1, +a[3]) - Date.UTC(+b[1], +b[2] - 1, +b[3])) / DAY_MS)
}

export interface DeadlineChip { tone: 'red' | 'amber' | 'plain'; text: string; days: number }

/** The chip on an ACTIVE row with a deadline: "Overdue 3 days" (red), "Due today" / "Due in 5 days" (amber at 7 days or fewer), else a plain "Due in 20 days". */
export function deadlineChip(item: Pick<RequirementItem, 'status' | 'details'>, today: string): DeadlineChip | null {
  if (item.status !== 'active') return null
  const days = daysUntil(item.details?.deadline, today)
  if (days === null) return null
  const unit = (n: number) => `${n} ${n === 1 ? 'day' : 'days'}`
  if (days < 0) return { tone: 'red', text: `Overdue ${unit(-days)}`, days }
  if (days === 0) return { tone: 'amber', text: 'Due today', days }
  return { tone: days <= DUE_SOON_DAYS ? 'amber' : 'plain', text: `Due in ${unit(days)}`, days }
}

/** Active rows whose deadline is today or up to 7 days away. */
export function isDueSoon(item: Pick<RequirementItem, 'status' | 'details'>, today: string): boolean {
  const chip = deadlineChip(item, today)
  return chip !== null && chip.days >= 0 && chip.days <= DUE_SOON_DAYS
}

/** Active rows whose deadline has passed. */
export function isOverdue(item: Pick<RequirementItem, 'status' | 'details'>, today: string): boolean {
  const chip = deadlineChip(item, today)
  return chip !== null && chip.days < 0
}

/* ------------------------------------------------------------------ closing */

export const CLOSED_REASONS: { value: Exclude<ClosedReason, ''>; label: string }[] = [
  { value: 'filled', label: 'Filled' },
  { value: 'client_cancelled', label: 'Client cancelled' },
  { value: 'expired', label: 'Expired' },
  { value: 'other', label: 'Other' },
]
export const closedReasonLabel = (reason: ClosedReason | null | undefined): string => CLOSED_REASONS.find((entry) => entry.value === reason)?.label ?? ''

/** The tooltip of a closed pill: "Closed on 21 Nov 2026 (Filled)". */
export function closedTitle(item: Pick<RequirementItem, 'status' | 'closedAt' | 'closedReason'>): string {
  if (item.status !== 'closed') return ''
  const when = item.closedAt ? new Date(item.closedAt) : null
  const day = when && !Number.isNaN(when.getTime()) ? when.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : ''
  const reason = closedReasonLabel(item.closedReason)
  return ['Closed', day && `on ${day}`, reason && `(${reason})`].filter(Boolean).join(' ')
}

/* ------------------------------------------------------------------ who handles it */

export interface OwnerChoice { id: string; name: string }

/** The people a requirement can be assigned to: active recruiters and the MD (never an Admin, nor a removed or inactive account); the current owner stays listed so an old value still reads. */
export function ownerChoices(users: readonly { id: string; name: string; role: string; active: boolean; deleted?: boolean }[], current?: { ownerId?: string | null; ownerName?: string | null }): OwnerChoice[] {
  const list = users.filter((user) => user.active && !user.deleted && (user.role === 'staff' || user.role === 'manager')).map((user) => ({ id: user.id, name: user.name }))
  if (current?.ownerId && !list.some((entry) => entry.id === current.ownerId)) list.push({ id: current.ownerId, name: current.ownerName || 'Former user' })
  return list.sort((a, b) => a.name.localeCompare(b.name))
}

/** What a duplicate carries: client, country, details and the extra columns; not the ad, the DOC (today again), the status (active) or the history. */
export function duplicateBody(item: RequirementItem): RequirementPatch {
  const details: Details = {}
  for (const key of DETAIL_KEYS) { const value = (item.details?.[key] ?? '').trim(); if (value) details[key] = value }
  return { clientName: item.clientName, country: item.country, details, custom: { ...item.custom } }
}

/** A line of the History drawer: "21 Nov 2026, 14:05". */
export function historyWhen(at: string): string {
  const when = new Date(at)
  if (Number.isNaN(when.getTime())) return ''
  return `${when.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}, ${when.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`
}

/* ------------------------------------------------------------------ editing */

/** Apply a patch to a row the way the server will (details merge by key, custom values by field id): the optimistic copy. */
export function applyPatch(item: RequirementItem, patch: RequirementPatch): RequirementItem {
  return {
    ...item,
    ...(patch.docDate !== undefined ? { docDate: patch.docDate } : {}),
    ...(patch.clientName !== undefined ? { clientName: patch.clientName } : {}),
    ...(patch.country !== undefined ? { country: patch.country } : {}),
    ...(patch.status !== undefined ? { status: patch.status, ...(patch.status === 'active' ? { closedReason: '' as ClosedReason, closedAt: null } : {}) } : {}),
    ...(patch.closedReason !== undefined ? { closedReason: patch.closedReason } : {}),
    ...(patch.ownerId !== undefined ? { ownerId: patch.ownerId } : {}),
    details: patch.details ? { ...item.details, ...patch.details } : item.details,
    custom: patch.custom ? { ...item.custom, ...patch.custom } : item.custom,
  }
}

/** Fold two patches for one row into one (what is retried after a failure includes everything that was not saved). */
export function mergePatches(first: RequirementPatch, second: RequirementPatch): RequirementPatch {
  return {
    ...first, ...second,
    ...(first.details || second.details ? { details: { ...first.details, ...second.details } } : {}),
    ...(first.custom || second.custom ? { custom: { ...first.custom, ...second.custom } } : {}),
  }
}

/** After a save the server's answer only brings what the screen cannot know (who and when, the file of the Ad); what the person typed since stays as typed. */
export function mergeSaved(local: RequirementItem, saved: RequirementItem, withAd = false): RequirementItem {
  return {
    ...local, updatedAt: saved.updatedAt, updatedBy: saved.updatedBy,
    ...(saved.closedAt !== undefined ? { closedAt: saved.closedAt } : {}),
    ...(saved.ownerName !== undefined && saved.ownerId === local.ownerId ? { ownerName: saved.ownerName } : {}),
    ...(local.docDate === null && saved.docDate ? { docDate: saved.docDate } : {}),
    ...(withAd ? { adUrl: saved.adUrl, adName: saved.adName } : {}),
  }
}

/** Put one item in the list (replacing the one with the same id, else on top). */
export function upsert(items: readonly RequirementItem[], item: RequirementItem): RequirementItem[] {
  return items.some((entry) => entry.id === item.id) ? items.map((entry) => (entry.id === item.id ? item : entry)) : [item, ...items]
}

/* ------------------------------------------------------------------ the requirements form */

/** One line of the form: its number on the owner's sheet, his label, and the kind of box (a long text, or a date picker for the Deadline). */
export interface DetailInput { key: DetailKey; no: number; label: string; kind: 'text' | 'area' | 'date'; hint?: string }

/** Fixed texts of the owner's sheet: do not reword. */
export const DETAIL_INPUTS: DetailInput[] = [
  { key: 'position', no: 1, label: 'Name of the Position', kind: 'text' },
  { key: 'vacancies', no: 2, label: 'No. of Requirements', kind: 'text' },
  { key: 'placeOfWork', no: 3, label: 'Place of Work', kind: 'text' },
  { key: 'contractPeriod', no: 4, label: 'Period of Contract', kind: 'text' },
  { key: 'qualification', no: 5, label: 'Qualification', kind: 'text' },
  { key: 'agePreference', no: 6, label: 'Age Preference', kind: 'text' },
  { key: 'additionalSkills', no: 7, label: 'Additional Skills', kind: 'text' },
  { key: 'basicSalary', no: 8, label: 'Basic Salary', kind: 'text' },
  { key: 'workingHours', no: 9, label: 'Working Hours', kind: 'text' },
  { key: 'overtime', no: 10, label: 'Overtime', kind: 'text' },
  { key: 'otCalculation', no: 11, label: 'OT Calculation', kind: 'text' },
  { key: 'food', no: 12, label: 'Food', kind: 'text' },
  { key: 'accommodation', no: 13, label: 'Accommodation', kind: 'text' },
  { key: 'insurance', no: 14, label: 'Insurance', kind: 'text' },
  { key: 'transport', no: 15, label: 'Transport', kind: 'text' },
  { key: 'remarks', no: 16, label: 'Remarks / Special Conditions', kind: 'area' },
  { key: 'deadline', no: 17, label: 'Deadline', kind: 'date', hint: 'used for due-date reminders' },
]

export function emptyDetails(): Record<DetailKey, string> {
  return Object.fromEntries(DETAIL_KEYS.map((key) => [key, ''])) as Record<DetailKey, string>
}

export const detailsFor = (item: Pick<RequirementItem, 'details'>): Record<DetailKey, string> => ({ ...emptyDetails(), ...cleanDetails(item.details) })

function cleanDetails(details: Details | null | undefined): Details {
  const clean: Details = {}
  for (const key of DETAIL_KEYS) { const value = details?.[key]; if (typeof value === 'string') clean[key] = value }
  return clean
}

/** Only the keys that changed, trimmed: what the form sends. */
export function detailsChanges(before: Details, after: Details): Details {
  const changes: Details = {}
  for (const key of DETAIL_KEYS) {
    const next = (after[key] ?? '').trim()
    if (next !== (before[key] ?? '').trim()) changes[key] = next
  }
  return changes
}

export const hasDetails = (details: Details | null | undefined): boolean => DETAIL_KEYS.some((key) => (details?.[key] ?? '').trim() !== '')

/** The one-line summary shown in the table: "Welder \u00B7 5 \u00B7 Kuwait \u00B7 1200 KWD" (position, number needed, place of work, basic salary); "" when nothing is filled in. */
export function detailsSummary(details: Details | null | undefined): string {
  const line = (['position', 'vacancies', 'placeOfWork', 'basicSalary'] as const).map((key) => (details?.[key] ?? '').trim()).filter(Boolean).join(' \u00B7 ')
  if (line) return line
  const remarks = (details?.remarks ?? '').replace(/\s+/g, ' ').trim()
  if (remarks) return remarks.length > 36 ? `${remarks.slice(0, 35)}\u2026` : remarks
  return hasDetails(details) ? 'Details added' : ''
}

/* ------------------------------------------------------------------ the extra columns */

export type FieldEdit = { ok: true; value: string } | { ok: false; error: string }

/** What a custom cell shows for its stored value. */
export function fieldDisplay(field: Pick<RequirementField, 'type'>, value: string | undefined): string {
  const text = (value ?? '').trim()
  if (!text) return ''
  if (field.type === 'date') return isoToText(text) || text
  if (field.type === 'yes_no') return text === 'yes' ? 'Yes' : text === 'no' ? 'No' : ''
  return text
}

/** A typed value for a custom cell, checked by the field's type; the stored value is what comes back (dates as YYYY-MM-DD). */
export function readFieldInput(field: Pick<RequirementField, 'type'>, text: string): FieldEdit {
  const value = text.trim()
  if (field.type === 'date') {
    const edit = readTypedDate(value)
    return edit.ok ? { ok: true, value: edit.iso ?? '' } : edit
  }
  if (field.type === 'number') {
    if (!value) return { ok: true, value: '' }
    return /^-?\d+([.,]\d+)?$/.test(value) ? { ok: true, value: value.replace(',', '.') } : { ok: false, error: 'Type a number' }
  }
  if (field.type === 'yes_no') return value === 'yes' || value === 'no' || value === '' ? { ok: true, value } : { ok: false, error: 'Choose Yes or No' }
  return { ok: true, value }
}

/** The name of a new or renamed column: required, at most 60 characters, and not one that is already on the sheet. */
export function fieldLabelError(label: string, fields: readonly RequirementField[], exceptId?: string): string {
  const text = label.replace(/\s+/g, ' ').trim()
  if (!text) return 'Give the column a name'
  if (text.length > FIELD_LABEL_MAX) return `Keep the name to ${FIELD_LABEL_MAX} characters`
  const clash = fields.some((field) => field.id !== exceptId && field.label.trim().toLowerCase() === text.toLowerCase())
  return clash ? 'There is already a column with this name' : ''
}

export const sortedFields = (fields: readonly RequirementField[]): RequirementField[] => [...fields].sort((a, b) => a.sort - b.sort || a.label.localeCompare(b.label))

/* ------------------------------------------------------------------ the Ad file */

export type AdKind = 'image' | 'pdf' | 'none'

export function adKind(item: Pick<RequirementItem, 'adUrl' | 'adName'>): AdKind {
  if (!item.adUrl) return 'none'
  return /\.pdf$/i.test(item.adName ?? '') || /\.pdf(\?|$)/i.test(item.adUrl) ? 'pdf' : 'image'
}

/** The address the Ad is shown from; the saved time is added so a replaced file is not served from the browser's old copy. */
export function adSrc(item: Pick<RequirementItem, 'adUrl' | 'updatedAt'>): string {
  if (!item.adUrl) return ''
  if (/^https?:/i.test(item.adUrl)) return item.adUrl
  return `${item.adUrl}${item.adUrl.includes('?') ? '&' : '?'}v=${encodeURIComponent(item.updatedAt ?? '')}`
}

/** "" when the file may be uploaded as an Ad (an image or a PDF up to 10 MB), else the reason. */
export function adFileError(file: { name: string; size: number; type: string }): string {
  const okType = /^image\//i.test(file.type) || file.type === 'application/pdf' || /\.(png|jpe?g|gif|webp|bmp|heic|pdf)$/i.test(file.name)
  if (!okType) return 'Choose a photo (image) or a PDF'
  if (file.size > AD_MAX_BYTES) return 'The file is larger than 10 MB'
  if (file.size === 0) return 'The file is empty'
  return ''
}

/* ------------------------------------------------------------------ the people */

/** Who may delete a requirement or a column: only admin level (pass the result of isAdminLevel from role-labels). */
export const canDeleteBy = (adminLevel: boolean): boolean => adminLevel

/** The row's tooltip: "Added by Asha on 21 Nov 2026 \u00B7 last updated by Ravi on 22 Nov 2026". */
export function whoWhen(item: Pick<RequirementItem, 'updatedAt' | 'updatedBy' | 'createdAt' | 'createdBy'>): string {
  const day = (value: string | undefined) => {
    const when = new Date(value ?? '')
    return Number.isNaN(when.getTime()) ? '' : when.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
  }
  const added = [item.createdBy?.name && `Added by ${item.createdBy.name}`, day(item.createdAt) && `on ${day(item.createdAt)}`].filter(Boolean).join(' ')
  const updated = item.updatedBy?.name ? `last updated by ${item.updatedBy.name}${day(item.updatedAt) ? ` on ${day(item.updatedAt)}` : ''}` : ''
  return [added, updated].filter(Boolean).join(' \u00B7 ')
}

/** The summary worked out from the rows on screen: what the cards show until (or if) the server's own summary is missing. */
export function localSummary(items: readonly RequirementItem[], today: string): RequirementSummary {
  const weekStart = daysBefore(today, 6)
  const owners = new Map<string, { id: string; name: string; active: number }>()
  for (const item of items) {
    if (item.status !== 'active' || !item.ownerId) continue
    const entry = owners.get(item.ownerId) ?? { id: item.ownerId, name: item.ownerName || 'Unknown', active: 0 }
    entry.active += 1
    owners.set(item.ownerId, entry)
  }
  const active = items.filter((item) => item.status === 'active').length
  return {
    active, closed: items.length - active,
    addedThisWeek: items.filter((item) => (item.createdAt ?? '').slice(0, 10) >= weekStart).length,
    dueSoon: items.filter((item) => isDueSoon(item, today)).length,
    overdue: items.filter((item) => isOverdue(item, today)).length,
    byOwner: [...owners.values()],
  }
}

/** The tab counts: All, Active, Closed. */
export const summaryCounts = (summary: RequirementSummary): Record<StatusFilter, number> => ({ all: summary.active + summary.closed, active: summary.active, closed: summary.closed })

/* ------------------------------------------------------------------ the column widths */

/** The columns that never change width (px): S.No, DOC (box and calendar button on one line), Country, Assigned to, Ad (a fixed thumbnail slot and its two buttons), Status (the switch), the row actions. */
export const FIXED_WIDTHS = { serial: 46, doc: 118, country: 104, owner: 140, ad: 84, status: 90, actions: 100 } as const
export const FIXED_TOTAL = Object.values(FIXED_WIDTHS).reduce((sum, width) => sum + width, 0)
export const CUSTOM_WIDTH = 150
/** Client name and Requirements details together never get less than this; below it the box scrolls sideways. */
export const FLEX_MIN = 352

export interface ColumnWidths { client: number; details: number; country: number; owner: number; total: number }
/** The longest text typed in each growing column (characters). */
export interface ColumnTexts { client: number; details: number; country?: number; owner?: number }

const COUNTRY_MAX = 134
const OWNER_MAX = 176

/**
 * Client name and Requirements details share the room the fixed columns leave, in proportion to how long their longest texts are (a long client name
 * gets more, a long summary gets more), Client at least 170px and Details at least 160px. Country and Assigned to start at their base width and grow
 * (to 134px and 176px) for a long country or recruiter name, but only out of room that is left once Client and Details have their minimum, so with no
 * extra columns the table is exactly `avail` wide (no scroll bar) from 1053px up; extra columns add 150px each and, once the total is wider than the box,
 * the box scrolls. Whatever does not fit a line wraps inside its cell.
 */
export function columnWidths(avail: number, fieldCount: number, texts: ColumnTexts): ColumnWidths {
  const custom = fieldCount * CUSTOM_WIDTH
  const room = Math.max(0, Math.floor(avail) - FIXED_TOTAL - custom - FLEX_MIN)
  const wantCountry = Math.min(COUNTRY_MAX, Math.max(FIXED_WIDTHS.country, 16 + Math.min(texts.country ?? 0, 18) * 7))
  const wantOwner = Math.min(OWNER_MAX, Math.max(FIXED_WIDTHS.owner, 44 + Math.min(texts.owner ?? 0, 22) * 6.2))
  const ownerExtra = Math.min(Math.round(wantOwner - FIXED_WIDTHS.owner), room)
  const countryExtra = Math.min(Math.round(wantCountry - FIXED_WIDTHS.country), room - ownerExtra)
  const country = FIXED_WIDTHS.country + countryExtra
  const owner = FIXED_WIDTHS.owner + ownerExtra
  const flex = Math.max(FLEX_MIN, Math.floor(avail) - FIXED_TOTAL - countryExtra - ownerExtra - custom)
  const wantClient = Math.min(44, Math.max(14, texts.client))
  const wantDetails = Math.min(26, Math.max(18, texts.details * 0.5))
  const client = Math.min(flex - 160, Math.max(170, Math.round((flex * wantClient) / (wantClient + wantDetails))))
  return { client, details: flex - client, country, owner, total: FIXED_TOTAL + countryExtra + ownerExtra + flex + custom }
}
