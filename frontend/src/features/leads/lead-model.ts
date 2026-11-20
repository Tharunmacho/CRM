import { isAdminLevel } from '@/shared/model/role-labels'
/**
 * Pure rules behind the Lead sheet: the status colours, the list query, follow-up wording, who may work a lead,
 * the upload preview (mapping table and counts) and the file checks. No React and no imports of app code except the
 * shared search helpers, so tests/lead-sheet.test.mjs loads this file directly.
 *
 * The server owns the truth (backend/app/services/leads*, docs/workflows/lead-sheet-contract.md); these helpers keep
 * the screen consistent with it and explain refusals before a request is made.
 */

/* ----------------------------------------------------------------- types */

export interface Lead {
  id: string
  /** Only the custom question answers the list was asked for (`extra=` keys), header -> text. */
  extra?: Record<string, string> | null
  externalId?: string | null
  createdTime?: string | null
  name: string
  phone?: string | null
  email?: string | null
  /** The city to show: tidied, and null when the sheet left junk (a digit, a single letter, N/A). */
  city?: string | null
  /** The city as stored (an older server does not send it). */
  cityRaw?: string | null
  /** The state to show: the server sends a real state (the stored one, or the one its place gives) or nothing; never sheet junk. */
  state?: string | null
  /** What the lead stores in its state field, untouched (an older server does not send it). The edit form starts from this. */
  stateRaw?: string | null
  positionApplied?: string | null
  experienceRestaurantYears?: number | string | null
  experienceTotalYears?: number | string | null
  experienceIndiaYears?: number | string | null
  experienceAbroadYears?: number | string | null
  gender?: string | null
  maritalStatus?: string | null
  dob?: string | null
  educationLevel?: string | null
  platform?: string | null
  campaignName?: string | null
  adName?: string | null
  formName?: string | null
  countryInterest?: string | null
  /** The built-in status code the lead is counted by (a status template counts as one of them). */
  status: string
  /** The status template the lead holds (Ringing, a custom status ...); null / missing = the built-in status as it is. */
  statusLabel?: string | null
  /** What the screens show: the template name, else the built-in label (the server's wording). */
  statusText?: string | null
  ownerId?: string | null
  ownerName?: string | null
  assignedAt?: string | null
  nextFollowUpAt?: string | null
  lastContactedAt?: string | null
  callCount?: number | null
  notes?: string | null
  candidateId?: string | null
  candidateRef?: string | null
  /** The Data Bank ID of the linked candidate: the one ID screens show; the reference above is only the fallback for a row without one. */
  candidateCareId?: string | null
  /** A Data Bank candidate with the same phone or email, found at import; not yet linked. */
  matchedCandidateId?: string | null
  matchedCandidateRef?: string | null
  matchedCandidateCareId?: string | null
  matchedCandidateName?: string | null
  hasResume?: boolean
  resumeReceivedAt?: string | null
  batchId?: string | null
  importRow?: number | null
  phoneCountryCode?: string | null
  currentLocation?: string | null
  militaryStatus?: string | null
  experienceRestaurantText?: string | null
  experienceTotalText?: string | null
  experienceRestaurantQuestion?: string | null
  experienceTotalQuestion?: string | null
  experienceIndiaText?: string | null
  experienceAbroadText?: string | null
  experienceIndiaQuestion?: string | null
  experienceAbroadQuestion?: string | null
  adsetName?: string | null
  leadStatusSource?: string | null
  isOrganic?: boolean | null
  createdAt?: string | null
}

export interface LeadFilters {
  q: string
  status: string
  campaign: string
  position: string
  country: string
  platform: string
  /** '' = everyone, 'me', 'none' (unassigned) or a user id. */
  assigned: string
  from: string
  to: string
  batchId: string
  /** '' = any, 'due' = the follow-up day has arrived (today or overdue) and the lead is still open. */
  followUp?: '' | 'due'
  /** Which date `from`/`to` (and the Today / Yesterday / Last 7 days presets) filter on: when the lead was imported, or its own platform date. */
  dateField: DateField
  /** 'today' = uploaded since midnight of the agency's day: the "Uploaded today" card. Independent of `from`/`to`. */
  recent: '' | 'today'
}

export type DateField = 'uploaded' | 'created'
export const DATE_FIELDS: { value: DateField; label: string }[] = [{ value: 'uploaded', label: 'Uploaded' }, { value: 'created', label: 'Created' }]

export type LeadCard = 'total' | 'new_today' | 'not_contacted' | 'interested' | 'resume_received' | 'converted' | 'follow_ups'

export const EMPTY_FILTERS: LeadFilters = { q: '', status: '', campaign: '', position: '', country: '', platform: '', assigned: '', from: '', to: '', batchId: '', dateField: 'created', recent: '' }

export interface LeadSummary {
  scope?: string
  total: number
  newToday: number
  unassigned?: number
  uncontacted: number
  interested: number
  resumeRequested?: number
  resumeReceived: number
  converted: number
  followUpsDue?: number
  /** The viewer's own follow-ups due today or overdue (the Follow-ups due card). */
  followUpsDueMine?: number
  matchedCandidates?: number
  withResume?: number
  lastImportAt?: string | null
}

/** One status choice of the Status filter with how many of the viewer's leads hold it (GET /leads/facets `statuses`). */
export interface FacetStatus { value: string; label: string; count: number; kind: 'builtin' | 'template'; tone?: string; builtin?: boolean }

export interface LeadFacets {
  /** Every status the filter offers (built-in codes, then status templates), with counts. */
  statuses?: FacetStatus[]
  campaigns: string[]
  forms?: string[]
  positions: string[]
  countries: string[]
  platforms: string[]
  owners?: { id: string; name: string; count: number }[]
}

/** The table's headings, in one place: a rename is a one-line change here. Custom question headings come from the uploaded sheets. */
export const LEAD_HEADINGS = {
  actions: 'Actions', created: 'Created', uploaded: 'Uploaded', name: 'Name / Age / Contact', contact: 'Contact', position: 'Job applied', experience: 'Total experience',
  experienceIndia: 'India experience', experienceAbroad: 'Abroad experience', education: 'Education', state: 'State',
  location: 'Location', campaign: 'Campaign', country: 'Country', status: 'Assigned to', followup: 'Follow‑up', resume: 'Resume',
} as const

/** A custom question column the stored sheets contain (GET /leads/columns). */
export interface ExtraColumn { key: string; label: string; count: number }

/**
 * The two lines of the Location column: the city, and under it the state. The server decides what the state is (a real state, or
 * the one the place gives, or none: services/place_state.py), so this only lays it out: nothing under a city when there is no
 * state or it only repeats the city; a lead with no city shows its state alone.
 */
export function locationLines(lead: Pick<Lead, 'city' | 'state'>): { first: string; second: string; title: string } {
  const city = (lead.city ?? '').trim()
  const state = (lead.state ?? '').trim()
  const second = city && state && state.toLowerCase() !== city.toLowerCase() ? state : ''
  return { first: city || state, second, title: [city, second || (city ? '' : state)].filter(Boolean).join(', ') }
}

export const EXTRA_PREFIX = 'x:'
export const extraKeyOf = (columnKey: string) => (columnKey.startsWith(EXTRA_PREFIX) ? columnKey.slice(EXTRA_PREFIX.length) : null)

/** `&extra=key` for every switched-on question column (the server returns just those answers with each lead). */
export const extraQuery = (keys: string[]) => keys.map((key) => `&extra=${encodeURIComponent(key)}`).join('')

/** The text a question column shows for one lead; '' when the lead has no answer. */
export const extraAnswer = (lead: Pick<Lead, 'extra'>, key: string) => {
  const value = lead.extra?.[key]
  return value === undefined || value === null ? '' : String(value).trim()
}

export interface LeadPage { items: Lead[]; total: number; page: number; pageSize?: number }

/* ----------------------------------------------------------------- status */

export type LeadTone = 'ok' | 'warn' | 'bad' | 'info' | 'quiet'

/** The statuses a lead moves through, in the order a recruiter works them (the server's list). `tone` colours the bold status word. */
export const LEAD_STATUSES: { value: string; label: string; tone: LeadTone }[] = [
  { value: 'new', label: 'Not contacted', tone: 'info' },
  { value: 'contacted', label: 'Contacted', tone: 'info' },
  { value: 'interested', label: 'Interested', tone: 'ok' },
  { value: 'resume_requested', label: 'Resume requested', tone: 'warn' },
  { value: 'resume_received', label: 'Interested (resume in)', tone: 'ok' },
  { value: 'converted', label: 'Converted', tone: 'ok' },
  { value: 'not_interested', label: 'Not interested', tone: 'quiet' },
  { value: 'invalid', label: 'Not reachable', tone: 'bad' },
  { value: 'wrong_number', label: 'Wrong number', tone: 'bad' },
]

/** Statuses a person may set by hand (converted is only reached through Convert to candidate). */
export const SETTABLE_STATUSES = LEAD_STATUSES.filter((status) => status.value !== 'converted')

const humanize = (value: string) => { const text = value.replace(/_/g, ' ').trim(); return text.charAt(0).toUpperCase() + text.slice(1) }

/* ---- status templates: the statuses people add to the list ("+ Add a status"). A lead that holds one has `status` (the built-in code it counts as)
 *      and `statusLabel` (the template's name). Rules and counting: backend/app/services/lead_status_templates.py. */

/** A template as the status helpers need it (the API's item has more). */
export interface StatusTemplateLike { name: string; tone: string; builtin?: boolean; active?: boolean }

/** The chip tone of a template's tone (neutral / positive / warning / negative). */
const TEMPLATE_TONE: Record<string, LeadTone> = { neutral: 'info', positive: 'ok', warning: 'warn', negative: 'bad' }
export const templateTone = (tone: string | null | undefined): LeadTone => TEMPLATE_TONE[(tone ?? '').toLowerCase()] ?? 'info'

/** The shared templates last read from the server (the API module keeps it current), so a chip anywhere can find its tone without being handed the list. */
let knownTemplates: readonly StatusTemplateLike[] = []
export const registerStatusTemplates = (list: readonly StatusTemplateLike[]) => { knownTemplates = list }
const sameName = (a: string, b: string) => a.replace(/\s+/g, ' ').trim().toLowerCase() === b.replace(/\s+/g, ' ').trim().toLowerCase()

/** Label and tone of a stored status; an unknown one reads as plain text rather than breaking the row.
 *  With `label` (the status template the lead holds) the name is shown, in the template's tone (else the tone of the code it counts as). */
export function leadStatus(value?: string | null, label?: string | null): { value: string; label: string; tone: LeadTone } {
  const key = (value ?? '').trim()
  const base = LEAD_STATUSES.find((status) => status.value === key) ?? { value: key, label: key ? humanize(key) : 'Not contacted', tone: 'quiet' as LeadTone }
  const name = (label ?? '').replace(/\s+/g, ' ').trim()
  if (!name) return base
  const template = knownTemplates.find((item) => sameName(item.name, name))
  return { value: name, label: name, tone: template ? templateTone(template.tone) : base.tone }
}

/** A status template with the "tried, not reached" tone (Ringing, No incoming calls): a call that connects ends it. */
export const isAttemptLabel = (label?: string | null): boolean => {
  const name = (label ?? '').trim()
  return Boolean(name) && knownTemplates.some((item) => sameName(item.name, name) && item.tone === 'warning')
}

/** The value of the status select for a lead: the template's name, else the built-in code (what PATCH /leads/{id} and the bulk change take). */
export const statusPick = (lead: Pick<Lead, 'status' | 'statusLabel'>): string => (lead.statusLabel ?? '').trim() || lead.status

/** The words for a status select value: a built-in code reads as its label, a template's name as it is spelled. */
export const pickLabel = (value: string): string => LEAD_STATUSES.find((status) => status.value === value)?.label ?? (value || 'Not contacted')

export interface StatusOption { value: string; label: string; tone: LeadTone; kind: 'builtin' | 'template' }

/** Built-in templates first (the server's order), then the rest A to Z; hidden ones are left out. */
export function orderedTemplates<T extends StatusTemplateLike>(list: readonly T[]): T[] {
  return list.filter((item) => item.active !== false).sort((a, b) => Number(Boolean(b.builtin)) - Number(Boolean(a.builtin)) || (a.builtin ? 0 : a.name.toLowerCase().localeCompare(b.name.toLowerCase())))
}

/** The choices of a status select, in order: the built-in statuses a person may set (the usual order), the built-in templates, every other
 *  template A to Z. `current` (the lead's own label) stays listed even when its template was hidden, so the select never goes blank. */
export function statusOptions(templates: readonly StatusTemplateLike[], current?: Pick<Lead, 'status' | 'statusLabel'> | null): StatusOption[] {
  const out: StatusOption[] = SETTABLE_STATUSES.map((status) => ({ value: status.value, label: status.label, tone: status.tone, kind: 'builtin' as const }))
  for (const item of orderedTemplates(templates)) out.push({ value: item.name, label: item.name, tone: templateTone(item.tone), kind: 'template' })
  const own = (current?.statusLabel ?? '').trim()
  if (own && !out.some((option) => sameName(option.value, own))) out.push({ value: own, label: own, tone: leadStatus(current?.status, own).tone, kind: 'template' })
  return out
}

/** Call outcomes the "Add remarks" form offers (the server's list) and what each one does to the status. */
export const CALL_OUTCOMES: { value: string; label: string; effect: string }[] = [
  { value: 'answered', label: 'Spoke to them', effect: 'Moves a new lead to Contacted' },
  { value: 'interested', label: 'Interested', effect: 'Moves the lead to Interested' },
  { value: 'no_answer', label: 'No answer', effect: 'The status stays as it is' },
  { value: 'wrong_number', label: 'Wrong number', effect: 'Moves the lead to Wrong number' },
  { value: 'not_interested', label: 'Not interested', effect: 'Moves the lead to Not interested' },
]

/** The status a call outcome leads to, mirroring the server's table; null when the status stays. */
export function statusAfterCall(outcome: string, current: string): string | null {
  const keep = ['resume_requested', 'resume_received', 'converted']
  if (outcome === 'answered') return current === 'new' ? 'contacted' : null
  if (outcome === 'interested') return keep.includes(current) ? null : 'interested'
  if (outcome === 'not_interested') return 'not_interested'
  if (outcome === 'wrong_number') return 'wrong_number'
  return null
}

/** Statuses after which nothing more is expected from the person: no follow-up is due. */
const CLOSED = new Set(['converted', 'not_interested', 'invalid', 'wrong_number'])
export const isClosed = (status?: string | null) => CLOSED.has((status ?? '').trim())

/* ------------------------------------------------------------------- query */

export const LEAD_SORTS: [string, string][] = [
  ['created:desc', 'Newest first'], ['created:asc', 'Oldest first'], ['uploaded:desc', 'Recently uploaded'], ['name:asc', 'Name A-Z'], ['name:desc', 'Name Z-A'],
  ['position:asc', 'Job A-Z'], ['campaign:asc', 'Campaign A-Z'], ['status:asc', 'Status'], ['owner:asc', 'Assigned to'], ['follow_up:asc', 'Follow-up soonest'],
  ['last_contacted:desc', 'Last contacted'], ['calls:desc', 'Most calls'], ['experience:desc', 'Most experience'],
]

/** Same term splitting as the Data Bank and the server: commas combine terms, case and extra spaces are ignored. */
export function searchTerms(query: string): string[] {
  const terms: string[] = []
  for (const part of query.split(',')) {
    const term = part.trim().replace(/\s+/g, ' ')
    if (term && !terms.some((known) => known.toLowerCase() === term.toLowerCase())) terms.push(term)
  }
  return terms
}
export const normalizeSearch = (query: string): string => searchTerms(query).join(',')

/** A short line under the search box that reacts to what was typed. */
export interface FooterHintInput {
  supervisor: boolean
  /** Only an admin or MD hands out leads. Unset = same as `supervisor`. */
  canAssign?: boolean
  /** The daily-work lock's reason when the sheet is read-only for this person. */
  lockReason?: string | null
  /** Leads assigned to the viewer (staff) / waiting for a recruiter (admin and MD). */
  assignedToMe?: number
  unassigned?: number
  /** Leads the current list matches, and every lead the viewer may see. */
  matching: number
  total: number
  filtersOn: boolean
}

/** The line under the table, worked out from who is looking and what the list shows (nothing about it is fixed text). */
export function footerHint(input: FooterHintInput): string {
  const count = (n: number | undefined, what: string) => (n === undefined ? '' : ` (${n.toLocaleString()} ${what})`)
  const parts: string[] = []
  if (input.filtersOn) parts.push(`Filters are on: showing ${input.matching.toLocaleString()} of ${input.total.toLocaleString()} ${input.total === 1 ? 'lead' : 'leads'}.`)
  if (input.total === 0) {
    parts.push(input.supervisor ? 'Upload a lead sheet to start.' : 'Leads appear here when the admin assigns them to you.')
  } else if (input.supervisor && input.canAssign !== false) {
    parts.push(`Click a row to open the lead. You can work, assign and export any lead${count(input.unassigned, 'unassigned')}.`)
  } else if (input.supervisor) {
    parts.push(`Click a row to open the lead. You can work and export the leads assigned to you${count(input.assignedToMe, 'assigned to you')}.`)
  } else {
    parts.push(`Click a row to see everything about the lead. You can work the leads assigned to you${count(input.assignedToMe, 'assigned to you')}.`)
  }
  if (input.lockReason) parts.push(`${input.lockReason} The sheet is read-only until then.`)
  return parts.join(' ')
}

/** The agency's own time zone: every "day" the lead filters mention is a calendar day here, whatever the browser's zone is. */
export const AGENCY_TIME_ZONE = 'Asia/Kolkata'

/** The agency calendar day of `now` (plus `offsetDays`) as YYYY-MM-DD. */
export function agencyDay(now: Date = new Date(), offsetDays = 0): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: AGENCY_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now)
  const pick = (type: string) => Number(parts.find((part) => part.type === type)?.value)
  return new Date(Date.UTC(pick('year'), pick('month') - 1, pick('day') + offsetDays)).toISOString().slice(0, 10)
}

/** Today in the agency time zone as YYYY-MM-DD (the date inputs' upper limit and the "Today" preset). */
export const todayKey = (now: Date = new Date()): string => agencyDay(now)

export type DatePreset = 'today' | 'yesterday' | 'last7'
export const DATE_PRESETS: { id: DatePreset; label: string }[] = [{ id: 'today', label: 'Today' }, { id: 'yesterday', label: 'Yesterday' }, { id: 'last7', label: 'Last 7 days' }]

/** The from / to days a preset stands for (agency calendar days, both included; "Last 7 days" is today and the six before). */
export function presetRange(preset: DatePreset, now: Date = new Date()): { from: string; to: string } {
  if (preset === 'yesterday') return { from: agencyDay(now, -1), to: agencyDay(now, -1) }
  if (preset === 'last7') return { from: agencyDay(now, -6), to: agencyDay(now) }
  return { from: agencyDay(now), to: agencyDay(now) }
}

/** The preset the current from / to are exactly equal to, if any (it is shown pressed). */
export function activePreset(filters: Pick<LeadFilters, 'from' | 'to'>, now: Date = new Date()): DatePreset | '' {
  return DATE_PRESETS.find(({ id }) => { const range = presetRange(id, now); return range.from === filters.from && range.to === filters.to })?.id ?? ''
}

/** What the "Uploaded today" card is called, and what it counts. */
export const NEW_CARD_LABEL = 'Uploaded today'
export const NEW_CARD_DETAIL = 'Since midnight'

/** A date the server accepts: `YYYY-MM-DD` and a real calendar day. A half-typed date input (a 5-digit year, "0000-00-00") is not sent, so it never turns the list into an error. */
export function validDay(value: string | undefined | null): value is string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? '')
  if (!match) return false
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  if (year < 1900 || month < 1 || month > 12 || day < 1) return false
  return day <= new Date(Date.UTC(year, month, 0)).getUTCDate()
}

/** The Status select's choices. A card can set several statuses at once (Interested = interested + resume received): that value gets its own
 *  option, so the select shows what is filtered instead of "Any status" while the list is narrowed. */
export function statusChoices(current: string, facets?: Pick<LeadFacets, 'statuses'> | null): [string, string][] {
  let choices: [string, string][] = LEAD_STATUSES.map((status) => [status.value, status.label])
  if (facets?.statuses?.length) {
    // The server's list: the built-in statuses (our wording), then the status templates, each with how many leads hold it.
    choices = facets.statuses.map((status) => {
      const label = status.kind === 'builtin' ? leadStatus(status.value).label : status.label
      return [status.value, `${label} (${status.count.toLocaleString()})`]
    })
  }
  if (current && !choices.some(([value]) => value === current)) {
    const labels = current.split(',').map((part) => part.trim()).filter(Boolean).map((part) => leadStatus(part).label)
    choices.push([current, labels.join(' or ')])
  }
  return choices
}

/** Query string for GET /leads (and the export): only the filters that are set, so equal views give equal strings. */
export function buildLeadQuery(filters: LeadFilters, sort: { key: string; dir: 'asc' | 'desc' }): string {
  const params = new URLSearchParams()
  const q = normalizeSearch(filters.q)
  if (q) params.set('q', q)
  if (filters.status) params.set('status', filters.status)
  if (filters.campaign) params.set('campaign', filters.campaign)
  if (filters.position) params.set('position', filters.position)
  if (filters.country) params.set('country', filters.country)
  if (filters.platform) params.set('platform', filters.platform)
  if (filters.assigned) params.set('owner', filters.assigned)
  const from = validDay(filters.from) ? filters.from : ''
  const to = validDay(filters.to) ? filters.to : ''
  if (from) params.set('created_from', from)
  if (to) params.set('created_to', to)
  if (from || to) params.set('date_field', filters.dateField === 'created' ? 'created' : 'uploaded')
  if (filters.recent === 'today') params.set('uploaded_today', 'true')
  if (filters.batchId) params.set('batch_id', filters.batchId)
  if (filters.followUp) params.set('follow_up', filters.followUp)
  params.set('sort', sort.key)
  params.set('direction', sort.dir)
  return params.toString()
}

/** The question columns of the form being looked at: narrowed by the campaign or upload filter, else every sheet's. */
export function columnsQuery(filters: Pick<LeadFilters, 'campaign' | 'batchId'>): string {
  const params = new URLSearchParams()
  if (filters.campaign) params.set('campaign', filters.campaign)
  if (filters.batchId) params.set('batch_id', filters.batchId)
  return params.toString()
}

/** The KPI summary scope that matches the list: the viewer's own leads when the list is narrowed to them. */
export const summaryScope = (filters: Pick<LeadFilters, 'assigned'>): 'mine' | 'all' => (filters.assigned === 'me' ? 'mine' : 'all')

export function activeLeadFilterCount(filters: LeadFilters): number {
  const { q, dateField, ...rest } = filters  // the date choice alone filters nothing
  void q; void dateField
  return Object.values(rest).filter((value) => value !== '' && value !== undefined).length
}

const dayLabel = (value: string) => { const [year, month, day] = value.split('-').map(Number); return `${two(day)} ${MONTHS[month - 1]} ${year}` }

/** The filters that are on but have no select of their own, as removable chips: the upload, "Uploaded today", "Follow-ups due" and the date range
 *  (named after the date it filters on). Nothing that narrows the list may be invisible, or an empty list cannot be explained. */
export function filterChips(filters: LeadFilters): { key: string; label: string; clear: Partial<LeadFilters> }[] {
  const chips: { key: string; label: string; clear: Partial<LeadFilters> }[] = []
  if (filters.batchId) chips.push({ key: 'batch', label: 'From one upload', clear: { batchId: '' } })
  if (filters.recent === 'today') chips.push({ key: 'recent', label: `${NEW_CARD_LABEL}: ${NEW_CARD_DETAIL.toLowerCase()}`, clear: { recent: '' } })
  if (filters.followUp === 'due') chips.push({ key: 'followUp', label: 'Follow-ups due', clear: { followUp: '' } })
  const from = validDay(filters.from) ? filters.from : ''
  const to = validDay(filters.to) ? filters.to : ''
  if (from || to) {
    const field = filters.dateField === 'created' ? 'Created' : 'Uploaded'
    const range = from && to ? (from === to ? `on ${dayLabel(from)}` : `${dayLabel(from)} to ${dayLabel(to)}`) : from ? `from ${dayLabel(from)}` : `up to ${dayLabel(to)}`
    chips.push({ key: 'dates', label: `${field} ${range}`, clear: { from: '', to: '' } })
  }
  return chips
}

/** What each KPI card sets on the filters. */
export function cardFilters(card: LeadCard): Partial<LeadFilters> {
  switch (card) {
    case 'new_today': return { status: '', from: '', to: '', recent: 'today' }  // uploaded since midnight today, whatever the lead's own date says
    case 'not_contacted': return { status: 'new', from: '', to: '', recent: '' }
    case 'interested': return { status: 'interested,resume_received', from: '', to: '', recent: '' }  // said yes, or the resume has arrived
    case 'resume_received': return { status: 'resume_received', from: '', to: '', recent: '' }
    case 'converted': return { status: 'converted', from: '', to: '', recent: '' }
    case 'follow_ups': return { status: '', from: '', to: '', recent: '', followUp: 'due' }
    default: return { status: '', from: '', to: '', recent: '' }
  }
}

/** Which KPI card the current filters correspond to (a card is lit only while the filters are exactly its own). */
export function activeCard(filters: LeadFilters): LeadCard | '' {
  if (filters.followUp === 'due') return !filters.status && !filters.from && !filters.to && !filters.recent ? 'follow_ups' : ''
  if (filters.recent === 'today') return !filters.status && !filters.from && !filters.to ? 'new_today' : ''
  if (filters.from || filters.to) return ''
  if (filters.status === 'new') return 'not_contacted'
  if (filters.status === 'interested' || filters.status === 'interested,resume_received') return 'interested'
  if (filters.status === 'resume_received') return 'resume_received'
  if (filters.status === 'converted') return 'converted'
  if (!filters.status) return 'total'
  return ''
}

/* ----------------------------------------------------------------- dates */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const two = (value: number) => String(value).padStart(2, '0')

/** "04 Nov" over "2026" for the Created column; null when the value is not a date. */
export function createdParts(value?: string | null): { day: string; year: string } | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return { day: `${two(date.getDate())} ${MONTHS[date.getMonth()]}`, year: String(date.getFullYear()) }
}

const uploadedFormat = new Intl.DateTimeFormat('en-GB', { timeZone: AGENCY_TIME_ZONE, day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })

/** When the lead was uploaded (imported into the CRM), in agency time: "14 Nov 2026, 10:12 IST"; '' when unknown. */
export function uploadedLabel(value?: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : `${uploadedFormat.format(date)} IST`
}

/** "14 Nov" over "10:12" for the Uploaded column (agency time); null when the value is not a date. */
export function uploadedParts(value?: string | null): { day: string; time: string } | null {
  const match = /^(\d{2} \w{3}) \d{4}, (\d{2}:\d{2})/.exec(uploadedLabel(value))
  return match ? { day: match[1], time: match[2] } : null
}

/** Source creation date when supplied; otherwise the CRM import time, explicitly labelled. */
export function leadDate(lead: Pick<Lead, 'createdTime' | 'createdAt'>): { value: string; label: 'Created' | 'Imported' } | null {
  if (lead.createdTime && createdParts(lead.createdTime)) return { value: lead.createdTime, label: 'Created' }
  if (lead.createdAt && createdParts(lead.createdAt)) return { value: lead.createdAt, label: 'Imported' }
  return null
}

export type FollowUpState = 'none' | 'overdue' | 'today' | 'soon' | 'later' | 'closed'

const startOfDay = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()

/** Where a follow-up stands against `now`: overdue (red) when its day has passed, today when it falls today. */
export function followUpState(value: string | null | undefined, status?: string | null, now: Date = new Date()): FollowUpState {
  if (!value) return 'none'
  if (isClosed(status)) return 'closed'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'none'
  const days = Math.round((startOfDay(date) - startOfDay(now)) / 86_400_000)
  if (days < 0) return 'overdue'
  if (days === 0) return 'today'
  return days <= 2 ? 'soon' : 'later'
}

/** "Today", "Tomorrow", "2 days late" or "06 Nov": the follow-up cell's first line. */
export function followUpLabel(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const days = Math.round((startOfDay(date) - startOfDay(now)) / 86_400_000)
  if (days === 0) return 'Today'
  if (days === 1) return 'Tomorrow'
  if (days === -1) return 'Yesterday'
  if (days < 0 && days >= -14) return `${-days} days late`
  return `${two(date.getDate())} ${MONTHS[date.getMonth()]}`
}

/** Local datetime-local value ("2026-11-06T14:30") for an ISO string, or '' when empty. */
export function toLocalInput(value?: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())}T${two(date.getHours())}:${two(date.getMinutes())}`
}

/** A datetime-local or date value back to an ISO string for the server; '' becomes null (clears the follow-up). */
export function fromLocalInput(value: string): string | null {
  if (!value) return null
  const date = new Date(value.length === 10 ? `${value}T09:00` : value)
  return Number.isNaN(date.getTime()) ? null : date.toISOString()
}

/** A date of birth as "12 Mar 1994" (no time zone shifts), or the raw text when it is not ISO. */
export function dobLabel(value?: string | null): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value ?? '')
  if (!match) return (value ?? '').trim()
  const month = Number(match[2])
  if (month < 1 || month > 12) return (value ?? '').trim()
  return `${match[3]} ${MONTHS[month - 1]} ${match[1]}`
}

/** Completed years from a valid recorded birth date; missing, invalid and future dates stay unknown. */
export function leadAge(value?: string | null, today: Date = new Date()): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? '')
  if (!match || Number.isNaN(today.getTime())) return null
  const [year, month, day] = match.slice(1).map(Number)
  const birth = new Date(Date.UTC(year, month - 1, day))
  if (birth.getUTCFullYear() !== year || birth.getUTCMonth() !== month - 1 || birth.getUTCDate() !== day) return null
  const beforeBirthday = today.getMonth() + 1 < month || (today.getMonth() + 1 === month && today.getDate() < day)
  const age = today.getFullYear() - year - Number(beforeBirthday)
  return age >= 0 ? age : null
}

/* ------------------------------------------------------------ small cells */

/** "3 yrs" / "1 yr" / "0 yrs" from a number or numeric text; null when the value is not a number. */
export function yearsLabel(value: number | string | null | undefined): string | null {
  if (value === null || value === undefined || value === '') return null
  const numeric = typeof value === 'number' ? String(value) : value.trim()
  if (!/^\d+(?:\.\d+)?(?:\s*(?:years?|yrs?))?$/i.test(numeric)) return null
  const years = Number.parseFloat(numeric)
  if (!Number.isFinite(years) || years < 0) return null
  const rounded = Math.round(years * 10) / 10
  return `${rounded} ${rounded === 1 ? 'yr' : 'yrs'}`
}

/** Keep the sheet's answer, including Yes/No and ranges, instead of presenting it as an exact duration. */
export function experienceLabel(years: number | string | null | undefined, written?: string | null): string | null {
  const answer = written?.trim()
  if (answer) {
    if (/^(yes|no)$/i.test(answer)) return answer.toLowerCase() === 'yes' ? 'Yes' : 'No'
    if (!/^\d+(?:\.\d+)?$/.test(answer)) return answer
    return yearsLabel(years) ?? yearsLabel(answer)
  }
  return yearsLabel(years)
}

export type LeadExperienceKind = 'restaurant' | 'india' | 'abroad' | 'total'

/** Each experience category uses its own supplied answer; one category never fills another. */
export function experienceAnswer(lead: Lead, kind: LeadExperienceKind): { value: string | null; question: string | null } {
  const prefix = { restaurant: 'experienceRestaurant', india: 'experienceIndia', abroad: 'experienceAbroad', total: 'experienceTotal' }[kind] as 'experienceRestaurant' | 'experienceIndia' | 'experienceAbroad' | 'experienceTotal'
  return { value: experienceLabel(lead[`${prefix}Years`], lead[`${prefix}Text`]), question: lead[`${prefix}Question`] || null }
}

/** A compact cue; the full source question stays available alongside the answer. */
export function experienceQuestionSummary(question?: string | null): string {
  if (!question) return 'Experience question'
  const text = question.replace(/_/g, ' ')
  const duration = text.match(/\b((?:(?:at least|more than|less than|up to|over|minimum(?: of)?)\s+)?\d+(?:\.\d+)?(?:\s*(?:-|–|—|to)\s*\d+(?:\.\d+)?)?\s*(?:\+|plus|or more)?\s*(?:years?|yrs?))\b/i)?.[1]
  const qualification = /\b(mba|qualification|degree|diploma)\b/i.test(text)
  if (duration) return `${duration.replace(/\s+/g, ' ').trim()}${qualification ? ' + qualification' : ''}`
  return qualification ? 'Experience + qualification' : 'Experience question'
}

/** Restaurant and total answers for the shared table/card experience cell. */
export function experienceLines(lead: Pick<Lead, 'experienceRestaurantYears' | 'experienceTotalYears' | 'experienceRestaurantText' | 'experienceTotalText'>): { restaurant: string | null; total: string | null } {
  return {
    restaurant: experienceLabel(lead.experienceRestaurantYears, lead.experienceRestaurantText),
    total: experienceLabel(lead.experienceTotalYears, lead.experienceTotalText),
  }
}

export type PlatformKind = 'facebook' | 'instagram' | 'google' | 'other'

/** The ad platform behind Meta's short codes ("fb", "ig") and plain names. */
export function platformKind(value?: string | null): PlatformKind {
  const text = (value ?? '').trim().toLowerCase()
  if (!text) return 'other'
  if (text === 'fb' || text.includes('facebook') || text === 'f') return 'facebook'
  if (text === 'ig' || text.includes('instagram')) return 'instagram'
  if (text.includes('google') || text === 'g') return 'google'
  return 'other'
}

export const PLATFORM_LABEL: Record<PlatformKind, string> = { facebook: 'Facebook', instagram: 'Instagram', google: 'Google', other: 'Other' }

/** wa.me link for a phone number (digits only, 7 to 15 of them); null when it cannot be a number. */
export function whatsappLink(phone?: string | null): string | null {
  const digits = (phone ?? '').replace(/\D/g, '')
  return digits.length >= 7 && digits.length <= 15 ? `https://wa.me/${digits}` : null
}

/** Meta writes phones as "p:+971501234567"; show them without the prefix. */
export const cleanPhone = (phone?: string | null): string => (phone ?? '').replace(/^\s*p:\s*/i, '').trim()

/* ------------------------------------------------------------ permissions */

export interface LeadActor { id: string; role: string }
export const isSupervisor = (me?: LeadActor | null) => !!me && isAdminLevel(me.role)

/** Admin and MD work any lead; staff only the leads assigned to them. */
export function canWorkLead(me: LeadActor | null | undefined, lead: Pick<Lead, 'ownerId'> | null | undefined): boolean {
  if (!me || !lead) return false
  if (isAdminLevel(me.role)) return true
  return Boolean(lead.ownerId) && lead.ownerId === me.id
}

/** Why an action is unavailable, or null when the person may work this lead. Mirrors candidate-access.ts wording. */
export function leadRefusal(me: LeadActor | null | undefined, lead: Pick<Lead, 'ownerId'> | null | undefined, ownerName?: string | null): string | null {
  if (canWorkLead(me, lead)) return null
  if (!lead?.ownerId) return 'This lead has no recruiter yet. Only the admin can assign it.'
  return `This lead is assigned to ${ownerName || 'another recruiter'}. Only they or the admin can work on it.`
}

/** Convert needs the lead to be workable and not converted already. */
export function convertRefusal(me: LeadActor | null | undefined, lead: Pick<Lead, 'ownerId' | 'candidateId'> | null | undefined, ownerName?: string | null): string | null {
  const refusal = leadRefusal(me, lead, ownerName)
  if (refusal) return refusal
  if (lead?.candidateId) return 'This lead is already a candidate.'
  return null
}

/** Why Convert to candidate is unavailable before the resume arrives (also the server's `resume_required` refusal). */
export const RESUME_REQUIRED_REASON = 'Upload the resume first. A lead becomes a candidate only after the resume is received.'

/* ------------------------------------------------------------- file checks */

export const LEAD_FILE_EXTENSIONS = ['.csv', '.tsv', '.txt', '.xlsx'] as const
export const LEAD_ACCEPT = LEAD_FILE_EXTENSIONS.join(',')
/** Mirrors the server limit; the server has the last word. */
export const LEAD_MAX_BYTES = 25 * 1024 * 1024

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** Plain-language reason a chosen file cannot be used, or null when it can. */
export function leadFileError(file: { name: string; size: number }): string | null {
  const name = file.name.toLowerCase()
  if (name.endsWith('.xls')) return 'Old Excel files (.xls) are not supported. Open it in Excel and save it as .xlsx or CSV, then upload it again.'
  if (!LEAD_FILE_EXTENSIONS.some((extension) => name.endsWith(extension))) return 'Choose a CSV, TSV, TXT or Excel (.xlsx) file.'
  if (file.size === 0) return 'This file is empty.'
  if (file.size > LEAD_MAX_BYTES) return `This file is ${formatBytes(file.size)}. The limit is ${formatBytes(LEAD_MAX_BYTES)}. Split it into smaller files.`
  return null
}

/* ---------------------------------------------------------- import preview */

/** The lead fields a column can map to (the server's names), as the preview's select offers them. `null` keeps the column as an extra answer. */
export const LEAD_FIELDS: { value: string; label: string }[] = [
  { value: 'externalId', label: 'Lead ID' },
  { value: 'createdTime', label: 'Created time' },
  { value: 'name', label: 'Full name' },
  { value: 'firstName', label: 'First name' },
  { value: 'lastName', label: 'Last name' },
  { value: 'phone', label: 'Phone' },
  { value: 'email', label: 'Email' },
  { value: 'whatsapp', label: 'WhatsApp' },
  { value: 'city', label: 'City' },
  { value: 'state', label: 'State' },
  { value: 'currentLocation', label: 'Current location' },
  { value: 'positionApplied', label: 'Job applied' },
  { value: 'experienceRestaurant', label: 'Restaurant experience' },
  { value: 'experienceTotal', label: 'Total experience' },
  { value: 'gender', label: 'Gender' },
  { value: 'maritalStatus', label: 'Marital status' },
  { value: 'dob', label: 'Date of birth' },
  { value: 'educationLevel', label: 'Education' },
  { value: 'militaryStatus', label: 'Military status' },
  { value: 'platform', label: 'Platform' },
  { value: 'isOrganic', label: 'Organic (not an ad)' },
  { value: 'leadStatusSource', label: 'Lead status (from platform)' },
  { value: 'campaignId', label: 'Campaign ID' },
  { value: 'campaignName', label: 'Campaign' },
  { value: 'adsetId', label: 'Ad set ID' },
  { value: 'adsetName', label: 'Ad set' },
  { value: 'adId', label: 'Ad ID' },
  { value: 'adName', label: 'Ad' },
  { value: 'formId', label: 'Form ID' },
  { value: 'formName', label: 'Form' },
  { value: 'countryInterest', label: 'Country interest' },
]

/** Select value for "keep as an extra answer" (sent to the server as null). */
export const EXTRA_FIELD = '__extra__'

export type Confidence = 'exact' | 'alias' | 'override' | null

/** One column of the file as the server read it. */
export interface ColumnInfo { header: string; field: string | null; confidence: Confidence }

/** A column with its few example values, built from the dry run's first rows. */
export interface MappingEntry extends ColumnInfo { samples?: string[] }

export type ConfidenceKind = 'high' | 'medium' | 'low'

/** Exact header matches are certain, known aliases likely; anything the person set is theirs. */
export function confidenceKind(value: Confidence): ConfidenceKind {
  if (value === 'exact' || value === 'override') return 'high'
  if (value === 'alias') return 'medium'
  return 'low'
}

export const CONFIDENCE_LABEL: Record<ConfidenceKind, string> = { high: 'Matched', medium: 'Likely', low: 'Check' }

export const fieldLabel = (field: string | null | undefined): string => LEAD_FIELDS.find((item) => item.value === field)?.label ?? (field ? humanize(field.replace(/([A-Z])/g, ' $1').toLowerCase()) : '')

/** Columns that map to a field, and those kept as extra answers, in file order. */
export function splitMappings(columns: ColumnInfo[]): { mapped: ColumnInfo[]; extra: ColumnInfo[] } {
  const mapped: ColumnInfo[] = []
  const extra: ColumnInfo[] = []
  for (const column of columns) (column.field ? mapped : extra).push(column)
  return { mapped, extra }
}

/** The overrides to send with the dry run: only what the person changed; null sends the column to extra. */
export function setMappingOverride(overrides: Record<string, string | null>, column: ColumnInfo, selected: string): Record<string, string | null> {
  const next = { ...overrides }
  const wanted = selected === EXTRA_FIELD ? null : selected
  if (wanted === column.field) delete next[column.header]
  else next[column.header] = wanted
  return next
}

/** The columns as they will be mapped once the overrides apply. */
export function applyOverrides(columns: ColumnInfo[], overrides: Record<string, string | null>): ColumnInfo[] {
  return columns.map((column) => (column.header in overrides ? { ...column, field: overrides[column.header], confidence: 'override' as Confidence } : column))
}

/** Fields that two or more columns map to (after overrides); the server keeps one, so the person should know. */
export function duplicateTargets(columns: ColumnInfo[], overrides: Record<string, string | null>): string[] {
  const seen = new Map<string, number>()
  for (const column of applyOverrides(columns, overrides)) if (column.field) seen.set(column.field, (seen.get(column.field) ?? 0) + 1)
  return [...seen].filter(([, count]) => count > 1).map(([field]) => field)
}

/** Example values of a column from the dry run's sample rows, by lead field. */
export function sampleValues(sample: Record<string, unknown>[], field: string | null): string[] {
  if (!field) return []
  const key = field === 'experienceRestaurant' ? 'experienceRestaurantText' : field === 'experienceTotal' ? 'experienceTotalText' : field
  const values: string[] = []
  for (const row of sample) {
    const value = row[key] ?? row[field]
    const text = value === null || value === undefined ? '' : String(value).trim()
    if (text && !values.includes(text)) values.push(text)
    if (values.length >= 2) break
  }
  return values
}

export interface ImportCounts { total: number; new: number; duplicate: number; invalid: number; existingCandidate: number; updated: number }

export interface DuplicateRow {
  row: number
  reason: string
  scope: 'file' | 'database'
  name?: string | null
  phone?: string | null
  existingLeadId?: string | null
  existingRow?: number | null
}

export interface InvalidRow { row: number; reason: string; message?: string | null; name?: string | null; phone?: string | null }

export interface ImportResponse {
  dryRun: boolean
  batchId: string | null
  fileName: string
  fileSize: number
  fileSha256: string
  sameFileBefore?: { batchId: string | null; uploadedAt: string | null; rowsNew: number | null } | null  // details are null for someone who did not upload that earlier file
  format?: string
  encoding?: string
  delimiter?: string
  source?: string
  rowsTotal: number
  rowsNew: number
  rowsDuplicate: number
  rowsInvalid: number
  rowsMatchedCandidates: number
  rowsUpdated: number
  columns: ColumnInfo[]
  unmappedColumns: string[]
  duplicates: DuplicateRow[]
  duplicatesTruncated?: boolean
  invalid: InvalidRow[]
  invalidTruncated?: boolean
  sample?: (Partial<Lead> & { outcome?: 'new' | 'duplicate' | 'invalid' })[]
  /** A recruiter's id, 'auto', or 'multiple' when the leads were shared between several recruiters. */
  assignedTo?: string | null
  assignedCount?: number
  /** Who got how many of the new leads. */
  assignedOwners?: { id: string; name: string; count: number }[]
}

export const countsOf = (data: Pick<ImportResponse, 'rowsTotal' | 'rowsNew' | 'rowsDuplicate' | 'rowsInvalid' | 'rowsMatchedCandidates' | 'rowsUpdated'>): ImportCounts => ({
  total: data.rowsTotal, new: data.rowsNew, duplicate: data.rowsDuplicate, invalid: data.rowsInvalid, existingCandidate: data.rowsMatchedCandidates, updated: data.rowsUpdated,
})

export interface LeadBatch {
  id: string
  fileName: string
  fileSize?: number
  fileSha256?: string
  source?: string
  format?: string
  encoding?: string
  uploadedBy?: string | null
  uploadedByName?: string | null
  createdAt: string
  status?: string
  campaignLabel?: string | null
  assignedTo?: string | null
  rowsTotal: number
  rowsNew: number
  rowsDuplicate: number
  rowsInvalid: number
  rowsMatchedCandidates?: number
  leadsRemaining?: number
}

/** The sentence a result needs. */
export function resultSummary(counts: Pick<ImportCounts, 'new' | 'duplicate' | 'invalid'>): string {
  const parts = [`${counts.new} new ${counts.new === 1 ? 'lead' : 'leads'} added`, `${counts.duplicate} ${counts.duplicate === 1 ? 'duplicate' : 'duplicates'} ignored`]
  if (counts.invalid > 0) parts.push(`${counts.invalid} invalid ${counts.invalid === 1 ? 'row' : 'rows'}`)
  return parts.join(', ')
}

/** The label of the import report download, and the friendly line for an upload with nothing ignored. */
export const REPORT_BUTTON = 'Download import report (Excel)'
export function allNewLine(counts: Pick<ImportCounts, 'total' | 'new' | 'duplicate' | 'invalid'>): string | null {
  if (counts.duplicate > 0 || counts.invalid > 0 || counts.total === 0) return null
  return counts.total === 1 ? 'The row was new. Nothing was ignored.' : `All ${counts.total.toLocaleString()} rows were new. Nothing was ignored.`
}

/** Why a row was ignored, in words. */
export function duplicateReason(row: Pick<DuplicateRow, 'reason' | 'scope' | 'existingRow'>): string {
  const by = row.reason === 'lead_id' ? 'lead ID' : row.reason === 'phone' ? 'phone number' : row.reason === 'email' ? 'email' : row.reason
  if (row.scope === 'file') return `Same ${by} as row ${row.existingRow ?? 'above'} in this file`
  return `Same ${by} as a lead already stored`
}

export const INVALID_REASON: Record<string, string> = {
  no_contact: 'No usable phone number or email',
  bad_phone: 'The phone number is not valid',
}
export const invalidReason = (row: Pick<InvalidRow, 'reason' | 'message'>): string => row.message || INVALID_REASON[row.reason] || row.reason

/** What the importer sends with the file (form fields as the API expects them). */
export function importFormFields(options: { dryRun: boolean; mapping?: Record<string, string | null>; assignTo?: string; assigneeIds?: string[]; label?: string }): Record<string, string> {
  const fields: Record<string, string> = { dry_run: options.dryRun ? 'true' : 'false' }
  if (options.mapping && Object.keys(options.mapping).length) fields.mapping = JSON.stringify(options.mapping)
  if (options.assigneeIds?.length) fields.assignee_ids = JSON.stringify([...new Set(options.assigneeIds)])  // shared equally between exactly these recruiters
  else if (options.assignTo) fields.assign_to = options.assignTo
  if (options.label?.trim()) fields.campaign_label = options.label.trim()
  return fields
}

/** The error text for a failed check or import; the server's own message wins when it sent one. */
export function importErrorMessage(status: number, fallback: string, message?: string): string {
  if (message) return message
  if (status === 413) return `The file is too large. The limit is ${formatBytes(LEAD_MAX_BYTES)}.`
  if (status === 415) return 'This kind of file is not supported. Choose a CSV, TSV, TXT or Excel (.xlsx) file.'
  if (status === 0) return 'Cannot reach the server. Check your connection and try again.'
  return fallback
}
