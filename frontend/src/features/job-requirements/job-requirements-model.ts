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

/** One requirement of a position ("Qualification" -> "Hotel management qualification."). */
export interface PositionRequirement { id: string; field: string; details: string }
/** One job of the client's form: title, how many are wanted and the monthly salary (both free text: "To be confirmed" is a valid answer). */
export interface Position { id: string; title: string; vacancies: string; salary: string; requirements: PositionRequirement[] }
/** One line of "General employment details": Particulars | Details | Remarks. */
export interface GeneralRow { id: string; label: string; details: string; remarks: string }

/** What is sent: the same rows, the id optional (the server gives a new row one). */
export interface RequirementDraft { id?: string; field: string; details: string }
export interface PositionDraft { id?: string; title: string; vacancies: string; salary: string; requirements: RequirementDraft[] }
export interface GeneralDraft { id?: string; label: string; details: string; remarks: string }

export interface RequirementItem {
  id: string
  docDate: string | null
  clientName: string
  country: string
  details: Details
  /** The client's job positions with their requirements; absent (or empty) on a row that only has the old flat details. */
  positions?: Position[]
  general?: GeneralRow[]
  adUrl: string | null
  adName: string | null
  status: RequirementStatus
  custom: Record<string, string>
  /** Who handles it (a recruiter or the MD); the server defaults it to the creator. */
  /** Everyone who handles it, in order (the server defaults it to the creator). `ownerId` / `ownerName` are the first of them. */
  ownerIds?: string[]
  owners?: OwnerChoice[]
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

/** A field name the staff made (or the system suggests): a requirement field of a position, or a general particular. `standard` particulars are rows of every form. */
export interface RequirementOption { id: string; kind: OptionKind; label: string; standard: boolean; sort: number }
export type OptionKind = 'requirement' | 'particular'

export interface RequirementsPage { items: RequirementItem[]; fields: RequirementField[]; total: number; summary?: RequirementSummary; options?: RequirementOption[] }

export interface HistoryEntry { at: string; actorId: string | null; actorName: string | null; action: string; summary: string }

/** What a PATCH may carry; details are merged by key and custom values by field id on the server. A positions or general list REPLACES the stored one. */
export interface RequirementPatch {
  docDate?: string | null
  clientName?: string
  country?: string
  status?: RequirementStatus
  /** Who handles it ([] unassigns, at most OWNERS_MAX). */
  ownerIds?: string[]
  closedReason?: ClosedReason
  details?: Details
  positions?: PositionDraft[]
  general?: GeneralDraft[]
  /** Never stored: the server adds new field names typed in the saved rows to the catalog. */
  rememberNewFields?: boolean
  custom?: Record<string, string>
}

export const OWNERS_MAX = 15
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

/** The people on a row, in order: the owners list, or (an older answer) the single owner. */
export function itemOwners(item: Pick<RequirementItem, 'owners' | 'ownerIds' | 'ownerId' | 'ownerName'>): OwnerChoice[] {
  if (item.owners && item.owners.length) return item.owners.map((owner) => ({ id: owner.id, name: owner.name }))
  if (item.ownerIds && item.ownerIds.length) return item.ownerIds.map((id) => ({ id, name: '' }))
  return item.ownerId ? [{ id: item.ownerId, name: item.ownerName || '' }] : []
}

/** "Asha Menon" for one person; first names for more: "Asha, Ravi" or "Asha +2"; "Unassigned" for nobody. */
export function ownersLabel(owners: readonly OwnerChoice[]): string {
  const names = owners.map((owner) => owner.name.trim() || 'Former user')
  if (names.length === 0) return 'Unassigned'
  if (names.length === 1) return names[0]
  const first = names.map((name) => name.split(' ')[0])
  return names.length === 2 ? first.join(', ') : `${first[0]} +${names.length - 1}`
}
/** Every name, for a tooltip or a screen reader. */
export const ownersFull = (owners: readonly OwnerChoice[]): string => (owners.length ? owners.map((owner) => owner.name.trim() || 'Former user').join(', ') : 'nobody')

export const sameIds = (a: readonly string[] | undefined, b: readonly string[] | undefined): boolean => {
  const left = a ?? []
  const right = b ?? []
  return left.length === right.length && left.every((id, index) => id === right[index])
}

/** The people a requirement can be assigned to: active recruiters and the MD (never an Admin, nor a removed or inactive account); the people already on the row stay listed so an old value still reads. */
export function ownerChoices(users: readonly { id: string; name: string; role: string; active: boolean; deleted?: boolean }[], current?: Pick<RequirementItem, 'owners' | 'ownerIds' | 'ownerId' | 'ownerName'>): OwnerChoice[] {
  const list = users.filter((user) => user.active && !user.deleted && (user.role === 'staff' || user.role === 'manager')).map((user) => ({ id: user.id, name: user.name }))
  for (const owner of current ? itemOwners(current) : []) if (!list.some((entry) => entry.id === owner.id)) list.push({ id: owner.id, name: owner.name || 'Former user' })
  return list.sort((a, b) => a.name.localeCompare(b.name))
}

/** What a duplicate carries: client, country, positions with their requirements, general details, details and the extra columns (new rows get new ids); not the ad, the DOC (today again), the status (active) or the history. */
export function duplicateBody(item: RequirementItem): RequirementPatch {
  const details: Details = {}
  for (const key of DETAIL_KEYS) { const value = (item.details?.[key] ?? '').trim(); if (value) details[key] = value }
  const positions = cleanPositions(item.positions ?? []).map(withoutIds)
  const general = cleanGeneral(item.general ?? []).map(({ label, details: text, remarks }) => ({ label, details: text, remarks }))
  return { clientName: item.clientName, country: item.country, details, ...(positions.length ? { positions } : {}), ...(general.length ? { general } : {}), custom: { ...item.custom } }
}

/** A line of the History drawer: "21 Nov 2026, 14:05". */
export function historyWhen(at: string): string {
  const when = new Date(at)
  if (Number.isNaN(when.getTime())) return ''
  return `${when.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}, ${when.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`
}

/* ------------------------------------------------------------------ editing */

/** Apply a patch to a row the way the server will (details merge by key, custom values by field id): the optimistic copy. */
export function applyPatch(item: RequirementItem, patch: RequirementPatch, people: readonly OwnerChoice[] = []): RequirementItem {
  const known = new Map<string, string>([...itemOwners(item), ...people].filter((owner) => owner.name).map((owner) => [owner.id, owner.name]))
  const owners = patch.ownerIds ? patch.ownerIds.map((id) => ({ id, name: known.get(id) ?? '' })) : null
  return {
    ...item,
    ...(patch.docDate !== undefined ? { docDate: patch.docDate } : {}),
    ...(patch.clientName !== undefined ? { clientName: patch.clientName } : {}),
    ...(patch.country !== undefined ? { country: patch.country } : {}),
    ...(patch.status !== undefined ? { status: patch.status, ...(patch.status === 'active' ? { closedReason: '' as ClosedReason, closedAt: null } : {}) } : {}),
    ...(patch.closedReason !== undefined ? { closedReason: patch.closedReason } : {}),
    ...(owners ? { ownerIds: owners.map((owner) => owner.id), owners, ownerId: owners[0]?.id ?? null, ownerName: owners[0]?.name || null } : {}),
    ...(patch.positions ? { positions: patch.positions.map((position) => ({ ...position, id: position.id ?? makeId(), requirements: position.requirements.map((row) => ({ ...row, id: row.id ?? makeId() })) })) } : {}),
    ...(patch.general ? { general: patch.general.map((row) => ({ ...row, id: row.id ?? makeId() })) } : {}),
    details: patch.details ? { ...item.details, ...patch.details } : item.details,
    custom: patch.custom ? { ...item.custom, ...patch.custom } : item.custom,
  }
}

/** Fold two patches for one row into one (what is retried after a failure includes everything that was not saved). A positions or general list replaces the earlier one. */
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
    ...(saved.owners && sameIds(saved.ownerIds ?? saved.owners.map((owner) => owner.id), local.ownerIds ?? itemOwners(local).map((owner) => owner.id)) ? { owners: saved.owners, ownerId: saved.owners[0]?.id ?? null, ownerName: saved.owners[0]?.name ?? null } : saved.ownerName !== undefined && saved.ownerId === local.ownerId ? { ownerName: saved.ownerName } : {}),
    ...(local.docDate === null && saved.docDate ? { docDate: saved.docDate } : {}),
    ...(withAd ? { adUrl: saved.adUrl, adName: saved.adName } : {}),
  }
}

/** Put one item in the list (replacing the one with the same id, else on top). */
export function upsert(items: readonly RequirementItem[], item: RequirementItem): RequirementItem[] {
  return items.some((entry) => entry.id === item.id) ? items.map((entry) => (entry.id === item.id ? item : entry)) : [item, ...items]
}

/* ------------------------------------------------------------------ the requirement form (the client's "Job Requirement Form") */

/** The limits the server keeps (the inputs stop typing at them). */
export const LIMITS = { positions: 30, requirements: 40, general: 50, title: 200, vacancies: 100, salary: 200, field: 100, details: 2000, label: 100, remarks: 2000, special: 10000 } as const

export const TO_BE_CONFIRMED = 'To be confirmed'

/** Suggested names for a requirement of a position; any other text may be typed. */
export const REQUIREMENT_FIELD_SUGGESTIONS: readonly string[] = [
  'Qualification', 'Maximum Age', 'Minimum Age', 'Experience', 'Skills / Trade', 'Practical Skill / Video', 'Language', 'Gender', 'Nationality', 'Driving Licence', 'Certificate', 'Other',
]
/** The fields offered as one-press chips under a position. */
export const QUICK_REQUIREMENT_FIELDS: readonly string[] = ['Qualification', 'Maximum Age', 'Experience', 'Skills / Trade']

/** The particulars every requirement starts with, in the client's order. */
export const GENERAL_DEFAULTS: readonly string[] = ['Working Hours', 'Contract Period', 'Place of Work', 'Restaurant Type', 'Weekly Off', 'Food', 'Accommodation', 'Visa Type']
/** More particulars that can be added with one press (or typed). */
export const GENERAL_SUGGESTIONS: readonly string[] = ['Overtime', 'OT Calculation', 'Insurance', 'Transport', 'Air Ticket', 'Medical', 'Uniform', 'Salary Payment', 'Leave', 'Joining Date']

let idCounter = 0
/** A row id made on this screen; the server replaces it with its own when the row is saved (see CLIENT_ID). */
export function makeId(): string {
  idCounter += 1
  return `c_${Date.now().toString(36)}${idCounter.toString(36)}${Math.random().toString(36).slice(2, 6)}`
}
const isClientId = (id: string | undefined): boolean => !id || id.startsWith('c_')

const text = (value: unknown): string => (typeof value === 'string' ? value.trim() : '')
const same = (a: string, b: string): boolean => a.trim().toLowerCase() === b.trim().toLowerCase()

export const blankRequirement = (field = ''): PositionRequirement => ({ id: makeId(), field, details: '' })
export const blankPosition = (): Position => ({ id: makeId(), title: '', vacancies: '', salary: '', requirements: [] })
export const blankGeneral = (label = ''): GeneralRow => ({ id: makeId(), label, details: '', remarks: '' })

/** The old flat keys that are one position / the general details; an old row opens pre-filled from them. */
const LEGACY_REQUIREMENT_KEYS: readonly [DetailKey, string][] = [['qualification', 'Qualification'], ['agePreference', 'Age Preference'], ['additionalSkills', 'Skills']]
const LEGACY_GENERAL_KEYS: readonly [DetailKey, string][] = [
  ['workingHours', 'Working Hours'], ['contractPeriod', 'Contract Period'], ['placeOfWork', 'Place of Work'], ['overtime', 'Overtime'], ['otCalculation', 'OT Calculation'],
  ['food', 'Food'], ['accommodation', 'Accommodation'], ['insurance', 'Insurance'], ['transport', 'Transport'],
]
const LEGACY_POSITION_KEYS: readonly DetailKey[] = ['position', 'vacancies', 'basicSalary', 'qualification', 'agePreference', 'additionalSkills']

function legacyPosition(details: Details | null | undefined): Position | null {
  if (!LEGACY_POSITION_KEYS.some((key) => text(details?.[key]))) return null
  return {
    id: makeId(), title: text(details?.position), vacancies: text(details?.vacancies), salary: text(details?.basicSalary),
    requirements: LEGACY_REQUIREMENT_KEYS.filter(([key]) => text(details?.[key])).map(([key, field]) => ({ id: makeId(), field, details: text(details?.[key]) })),
  }
}
const legacyGeneral = (details: Details | null | undefined): GeneralRow[] =>
  LEGACY_GENERAL_KEYS.filter(([key]) => text(details?.[key])).map(([key, label]) => ({ id: makeId(), label, details: text(details?.[key]), remarks: '' }))

/** The saved rows laid over the defaults: each default in the client's order (filled from a saved row of the same name, any case), then the other saved rows. */
export function mergeGeneral(saved: readonly GeneralRow[], defaults: readonly string[] = GENERAL_DEFAULTS): GeneralRow[] {
  const taken = new Set<number>()
  const base = defaults.map((label) => {
    const at = saved.findIndex((row, index) => !taken.has(index) && same(row.label, label))
    if (at < 0) return blankGeneral(label)
    taken.add(at)
    return { ...saved[at] }
  })
  return [...base, ...saved.filter((_, index) => !taken.has(index)).map((row) => ({ ...row }))]
}

/** What the form holds: the lists as typed, the deadline and the client's confirmation. `fromLegacy` says a list was read from the old flat keys, not from saved rows. */
export interface RequirementForm {
  positions: Position[]
  general: GeneralRow[]
  deadline: string
  remarks: string
  /** The particulars every form starts with (the built-in eight, then the catalog's standard ones): an empty one is not stored. */
  defaults: string[]
  fromLegacy: { positions: boolean; general: boolean }
}

const copyPosition = (position: Position): Position => ({ ...position, requirements: (position.requirements ?? []).map((row) => ({ ...row })) })

/** The editable state of a row: its saved positions (or its old flat details read as position 1), at least one position, the general rows over the defaults. */
export function formFor(item: Pick<RequirementItem, 'details' | 'positions' | 'general'>, options: readonly RequirementOption[] = []): RequirementForm {
  const defaults = defaultParticulars(options)
  const saved = (item.positions ?? []).map(copyPosition)
  const old = saved.length ? null : legacyPosition(item.details)
  const savedGeneral = (item.general ?? []).map((row) => ({ ...row }))
  const oldGeneral = savedGeneral.length ? [] : legacyGeneral(item.details)
  return {
    positions: saved.length ? saved : old ? [old] : [blankPosition()],
    general: mergeGeneral(savedGeneral.length ? savedGeneral : oldGeneral, defaults),
    deadline: text(item.details?.deadline),
    remarks: item.details?.remarks ?? '',
    defaults,
    fromLegacy: { positions: old !== null, general: oldGeneral.length > 0 },
  }
}

/** Trimmed rows with the blank ones removed (a requirement or general row needs a word in some box; a position needs a title, a figure or a requirement). */
export function cleanPositions(positions: readonly Position[]): Position[] {
  return positions.map((position) => ({
    id: position.id, title: text(position.title), vacancies: text(position.vacancies), salary: text(position.salary),
    requirements: (position.requirements ?? []).map((row) => ({ id: row.id, field: text(row.field), details: text(row.details) })).filter((row) => row.field || row.details),
  })).filter((position) => position.title || position.vacancies || position.salary || position.requirements.length > 0)
}
/** A particular is kept when it has details or remarks, or a name of the person's own; a default particular left empty is not stored. */
export function cleanGeneral(rows: readonly GeneralRow[], defaults: readonly string[] = GENERAL_DEFAULTS): GeneralRow[] {
  return rows.map((row) => ({ id: row.id, label: text(row.label), details: text(row.details), remarks: text(row.remarks) }))
    .filter((row) => row.details || row.remarks || (row.label && !defaults.some((name) => same(name, row.label))))
}

/** The row as the server gets it: a client-made id is left out (the server gives the row its own). */
const draftId = (id: string): { id?: string } => (isClientId(id) ? {} : { id })
const withoutIds = (position: Position): PositionDraft => ({ title: position.title, vacancies: position.vacancies, salary: position.salary, requirements: position.requirements.map((row) => ({ field: row.field, details: row.details })) })
const positionDraft = (position: Position): PositionDraft => ({ ...draftId(position.id), ...withoutIds(position), requirements: position.requirements.map((row) => ({ ...draftId(row.id), field: row.field, details: row.details })) })
const generalDraft = (row: GeneralRow): GeneralDraft => ({ ...draftId(row.id), label: row.label, details: row.details, remarks: row.remarks })

const positionsKey = (positions: readonly Position[]): string => JSON.stringify(cleanPositions(positions).map(withoutIds))
const generalKey = (rows: readonly GeneralRow[], defaults: readonly string[]): string => JSON.stringify(cleanGeneral(rows, defaults).map(({ label, details, remarks }) => [label, details, remarks]))

/**
 * Only what changed since the form opened: a positions list and/or a general list (whole lists, blank rows removed, strings trimmed), details.deadline and
 * details.remarks; an empty patch when nothing changed. A row read from the old flat keys that is emptied clears those keys too, or it would come back.
 */
export function buildPatch(start: RequirementForm, form: RequirementForm): RequirementPatch {
  const patch: RequirementPatch = {}
  const details: Details = {}
  if (positionsKey(start.positions) !== positionsKey(form.positions)) {
    const positions = cleanPositions(form.positions)
    patch.positions = positions.map(positionDraft)
    if (positions.length === 0 && start.fromLegacy.positions) for (const key of LEGACY_POSITION_KEYS) details[key] = ''
  }
  if (generalKey(start.general, start.defaults) !== generalKey(form.general, form.defaults)) {
    const general = cleanGeneral(form.general, form.defaults)
    patch.general = general.map(generalDraft)
    if (general.length === 0 && start.fromLegacy.general) for (const [key] of LEGACY_GENERAL_KEYS) details[key] = ''
  }
  if (text(form.deadline) !== text(start.deadline)) details.deadline = text(form.deadline)
  if (text(form.remarks) !== text(start.remarks)) details.remarks = text(form.remarks)
  if (Object.keys(details).length) patch.details = details
  return patch
}

export const isEmptyPatch = (patch: RequirementPatch): boolean => Object.keys(patch).length === 0

export interface FormProblem { id: string; kind: 'position' | 'requirement' | 'general'; message: string }

/** What stops a save: a position with figures or requirements but no title, a requirement or particular with a value but no name. Shown on the row, never dropped silently. */
export function formProblems(form: Pick<RequirementForm, 'positions' | 'general'>): FormProblem[] {
  const problems: FormProblem[] = []
  for (const position of form.positions) {
    const rows = position.requirements.filter((row) => text(row.field) || text(row.details))
    if (!text(position.title) && (text(position.vacancies) || text(position.salary) || rows.length)) problems.push({ id: position.id, kind: 'position', message: 'Type the job title' })
    for (const row of rows) if (!text(row.field)) problems.push({ id: row.id, kind: 'requirement', message: 'Type or choose the requirement field' })
  }
  for (const row of form.general) if (!text(row.label) && (text(row.details) || text(row.remarks))) problems.push({ id: row.id, kind: 'general', message: 'Type the particular (for example Food)' })
  return problems
}

/** "To be confirmed" in every vacancies and salary box that is still empty. */
export function fillToBeConfirmed(positions: readonly Position[]): Position[] {
  return positions.map((position) => (text(position.title) || text(position.vacancies) || text(position.salary) || position.requirements.length)
    ? { ...position, vacancies: text(position.vacancies) ? position.vacancies : TO_BE_CONFIRMED, salary: text(position.salary) ? position.salary : TO_BE_CONFIRMED }
    : position)
}

/** The suggestions not used yet in a list (case-insensitive). */
export const unusedNames = (suggestions: readonly string[], used: readonly string[]): string[] => suggestions.filter((name) => !used.some((entry) => same(entry, name)))

/* ------------------------------------------------------------------ the table cell */

export interface PositionsInfo { titles: string[]; count: number; vacancies: number | null }

/** The job titles of a row, how many positions it has and the total of the vacancies (only when every position has a plain number). */
export function positionsInfo(item: Pick<RequirementItem, 'positions'>): PositionsInfo {
  const positions = (item.positions ?? []).filter((position) => text(position.title) || text(position.vacancies) || text(position.salary) || (position.requirements ?? []).length > 0)
  const numeric = positions.length > 0 && positions.every((position) => /^\d+$/.test(text(position.vacancies)))
  return {
    titles: positions.map((position) => text(position.title)).filter(Boolean), count: positions.length,
    vacancies: numeric ? positions.reduce((sum, position) => sum + Number(text(position.vacancies)), 0) : null,
  }
}

/** The table cell's text: "Paratha Master, Captain, Waiter · 3 positions · 12 vacancies"; a row with only the old flat details falls back to their summary. */
export function positionsSummary(item: Pick<RequirementItem, 'positions' | 'details'>): string {
  const info = positionsInfo(item)
  if (info.count === 0) return detailsSummary(item.details)
  const unit = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`
  return [info.titles.join(', '), info.count > 1 ? unit(info.count, 'position', 'positions') : '', info.vacancies !== null ? unit(info.vacancies, 'vacancy', 'vacancies') : ''].filter(Boolean).join(' · ')
}

/** The file name of one downloaded form: "Client-name-requirement-form.xlsx". */
export function formFileName(clientName: string): string {
  const base = clientName.replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/g, '').slice(0, 60)
  return `${base || 'job'}-requirement-form.xlsx`
}

/* ------------------------------------------------------------------ the field catalog (names the staff add) */

export const OPTION_LABEL_MAX = 100
export const OPTION_KINDS: { kind: OptionKind; label: string; builtIn: readonly string[] }[] = [
  { kind: 'requirement', label: 'Requirement fields', builtIn: REQUIREMENT_FIELD_SUGGESTIONS },
  { kind: 'particular', label: 'Particulars', builtIn: [...GENERAL_DEFAULTS, ...GENERAL_SUGGESTIONS] },
]
export const builtInNames = (kind: OptionKind): readonly string[] => OPTION_KINDS.find((entry) => entry.kind === kind)?.builtIn ?? []

const byOrder = (a: RequirementOption, b: RequirementOption) => a.sort - b.sort || a.label.localeCompare(b.label)
/** The catalog entries of one kind, in the order the server keeps them. */
export const optionsOf = (options: readonly RequirementOption[], kind: OptionKind): RequirementOption[] => options.filter((option) => option.kind === kind).sort(byOrder)

/** What a field picker suggests: the built-in names first, then the catalog's, each name once (any case). */
export function mergeSuggestions(builtIn: readonly string[], options: readonly RequirementOption[], kind: OptionKind): string[] {
  const out: string[] = []
  for (const name of [...builtIn, ...optionsOf(options, kind).map((option) => option.label)]) {
    const label = name.trim()
    if (label && !out.some((entry) => same(entry, label))) out.push(label)
  }
  return out
}

/** The particulars every form starts with: the built-in eight in the client's order, then the catalog's "show on every form" ones. */
export function defaultParticulars(options: readonly RequirementOption[]): string[] {
  const out = [...GENERAL_DEFAULTS]
  for (const option of optionsOf(options, 'particular')) if (option.standard && option.label.trim() && !out.some((entry) => same(entry, option.label))) out.push(option.label.trim())
  return out
}

/** "" when `label` may be a new or renamed catalog name: required, at most 100 characters, and not already in the list (built-in or made by staff, any case). */
export function optionNameError(label: string, kind: OptionKind, options: readonly RequirementOption[], exceptId?: string): string {
  const name = label.replace(/\s+/g, ' ').trim()
  if (!name) return 'Type a name'
  if (name.length > OPTION_LABEL_MAX) return `Keep the name to ${OPTION_LABEL_MAX} characters`
  const own = exceptId ? options.find((option) => option.id === exceptId) : undefined
  if (own && same(own.label, name)) return ''
  if (builtInNames(kind).some((entry) => same(entry, name))) return 'This name is already in the list'
  if (options.some((option) => option.kind === kind && option.id !== exceptId && same(option.label, name))) return 'This name is already in the list'
  return ''
}

/** The catalog after a change made on this screen (the server's own copy arrives with the next read). */
export const upsertOption = (options: readonly RequirementOption[], option: RequirementOption): RequirementOption[] =>
  (options.some((entry) => entry.id === option.id) ? options.map((entry) => (entry.id === option.id ? option : entry)) : [...options, option]).sort(byOrder)

/** True when the row has anything filled in: positions, general rows, or any of the flat details (deadline and remarks included). */
export const hasDetails = (item: Pick<RequirementItem, 'details' | 'positions' | 'general'>): boolean =>
  positionsInfo(item).count > 0 || (item.general ?? []).some((row) => text(row.label) || text(row.details) || text(row.remarks)) || DETAIL_KEYS.some((key) => text(item.details?.[key]) !== '')

/** The one-line summary shown in the table: "Welder \u00B7 5 \u00B7 Kuwait \u00B7 1200 KWD" (position, number needed, place of work, basic salary); "" when nothing is filled in. */
export function detailsSummary(details: Details | null | undefined): string {
  const line = (['position', 'vacancies', 'placeOfWork', 'basicSalary'] as const).map((key) => (details?.[key] ?? '').trim()).filter(Boolean).join(' \u00B7 ')
  if (line) return line
  const remarks = (details?.remarks ?? '').replace(/\s+/g, ' ').trim()
  if (remarks) return remarks.length > 36 ? `${remarks.slice(0, 35)}\u2026` : remarks
  return DETAIL_KEYS.some((key) => text(details?.[key]) !== '') ? 'Details added' : ''
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
    if (item.status !== 'active') continue
    for (const owner of itemOwners(item)) {
      const entry = owners.get(owner.id) ?? { id: owner.id, name: owner.name || 'Unknown', active: 0 }
      entry.active += 1
      owners.set(owner.id, entry)
    }
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
export const FIXED_WIDTHS = { serial: 46, doc: 118, country: 104, owner: 164, ad: 84, status: 90, actions: 112 } as const
export const FIXED_TOTAL = Object.values(FIXED_WIDTHS).reduce((sum, width) => sum + width, 0)
export const CUSTOM_WIDTH = 150
/** Client name and Requirements details together never get less than this; below it the box scrolls sideways. */
export const FLEX_MIN = 330

export interface ColumnWidths { client: number; details: number; country: number; owner: number; total: number }
/** The longest text typed in each growing column (characters). */
export interface ColumnTexts { client: number; details: number; country?: number; owner?: number }

const COUNTRY_MAX = 134
const OWNER_MAX = 204

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
  const wantDetails = Math.min(42, Math.max(32, texts.details * 0.5))
  const client = Math.min(flex - 180, Math.max(150, Math.round((flex * wantClient) / (wantClient + wantDetails))))
  return { client, details: flex - client, country, owner, total: FIXED_TOTAL + countryExtra + ownerExtra + flex + custom }
}

/** A live, poll or focus read must not be applied (or even started) while a save or a create is being sent, or while a typed change is held unsaved. */
export const readBlocked = (inFlight: number, unsavedCount: number): boolean => inFlight > 0 || unsavedCount > 0

/**
 * What a silent read becomes on screen: the server's rows, each with its unsaved change laid back on top, plus any row this person created
 * a moment ago that the read (started before the create finished) does not have yet, kept at the top, so a new row never flickers away and
 * the Client-name box it was going to focus never disappears.
 */
export function mergeRead(page: readonly RequirementItem[], held: Readonly<Record<string, RequirementPatch>>, current: readonly RequirementItem[], justCreated: ReadonlySet<string>, people: readonly OwnerChoice[] = []): RequirementItem[] {
  const merged = page.map((item) => (held[item.id] ? applyPatch(item, held[item.id], people) : item))
  const have = new Set(merged.map((item) => item.id))
  const kept = current.filter((item) => justCreated.has(item.id) && !have.has(item.id))
  return kept.length ? [...kept, ...merged] : merged
}
