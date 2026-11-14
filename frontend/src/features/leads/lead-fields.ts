/**
 * Every field of a lead sheet, in one registry: its heading (the sheet's own wording), the group it belongs to in the Columns
 * menu, a minimum width and how its value is written. The table's optional columns, the expandable row and the Columns menu
 * all read this list, so a heading cannot differ between them. Custom questions the ad forms ask are appended from the data
 * (GET /leads/columns); their headings are the sheet's own, tidied.
 */
import { LEAD_HEADINGS, cleanPhone, dobLabel, experienceLabel, extraAnswer, leadAge, leadDate, leadStatus, uploadedLabel, type ExtraColumn, type Lead } from './lead-model'

export const FIELD_GROUPS = ['Person', 'Contact', 'Job answers', 'Campaign & ad', 'Workflow'] as const
export type FieldGroup = (typeof FIELD_GROUPS)[number]

/** What an empty value looks like everywhere. */
export const EMPTY_VALUE = '–'

export interface LeadFieldDef {
  id: string
  label: string
  group: FieldGroup
  width: number
  value: (lead: Lead) => string
}

const text = (value: unknown): string => (value === null || value === undefined ? '' : String(value).trim())
const when = (value?: string | null): string => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}
const years = (value: number | string | null | undefined, written?: string | null) => experienceLabel(value, written) ?? ''

export const LEAD_FIELD_DEFS: readonly LeadFieldDef[] = [
  { id: 'created', label: 'Created time', group: 'Workflow', width: 110, value: (l) => when(leadDate(l)?.value) },
  { id: 'uploaded', label: 'Uploaded time', group: 'Workflow', width: 130, value: (l) => uploadedLabel(l.createdAt) },
  { id: 'name', label: 'Full name', group: 'Person', width: 140, value: (l) => text(l.name) },
  { id: 'gender', label: 'Gender', group: 'Person', width: 90, value: (l) => text(l.gender) },
  { id: 'dob', label: 'Date of birth', group: 'Person', width: 110, value: (l) => dobLabel(l.dob) },
  { id: 'age', label: 'Age', group: 'Person', width: 80, value: (l) => { const age = leadAge(l.dob); return age === null ? '' : `${age} yrs` } },
  { id: 'maritalStatus', label: 'Marital status', group: 'Person', width: 110, value: (l) => text(l.maritalStatus) },
  { id: 'educationLevel', label: 'Education level', group: 'Person', width: 140, value: (l) => text(l.educationLevel) },
  { id: 'militaryStatus', label: 'Military status', group: 'Person', width: 110, value: (l) => text(l.militaryStatus) },
  { id: 'phone', label: 'Phone number', group: 'Contact', width: 150, value: (l) => cleanPhone(l.phone) },
  { id: 'email', label: 'Email', group: 'Contact', width: 180, value: (l) => text(l.email) },
  { id: 'city', label: 'City', group: 'Contact', width: 110, value: (l) => text(l.city) },
  { id: 'state', label: 'State', group: 'Contact', width: 110, value: (l) => text(l.state) },
  { id: 'currentLocation', label: 'Current location', group: 'Contact', width: 130, value: (l) => text(l.currentLocation) },
  { id: 'position', label: 'Which position are you applying for?', group: 'Job answers', width: 150, value: (l) => text(l.positionApplied) },
  { id: 'restaurantYears', label: 'Restaurant experience', group: 'Job answers', width: 120, value: (l) => years(l.experienceRestaurantYears, l.experienceRestaurantText) },
  { id: 'indiaYears', label: 'India experience', group: 'Job answers', width: 120, value: (l) => years(l.experienceIndiaYears, l.experienceIndiaText) },
  { id: 'abroadYears', label: 'Abroad experience', group: 'Job answers', width: 120, value: (l) => years(l.experienceAbroadYears, l.experienceAbroadText) },
  { id: 'totalYears', label: 'Total work experience', group: 'Job answers', width: 120, value: (l) => years(l.experienceTotalYears, l.experienceTotalText) },
  { id: 'country', label: LEAD_HEADINGS.country, group: 'Job answers', width: 110, value: (l) => text(l.countryInterest) },
  { id: 'platform', label: 'Platform', group: 'Campaign & ad', width: 90, value: (l) => text(l.platform) },
  { id: 'campaign', label: 'Campaign name', group: 'Campaign & ad', width: 150, value: (l) => text(l.campaignName) },
  { id: 'adset', label: 'Ad set name', group: 'Campaign & ad', width: 150, value: (l) => text(l.adsetName) },
  { id: 'ad', label: 'Ad name', group: 'Campaign & ad', width: 150, value: (l) => text(l.adName) },
  { id: 'form', label: 'Form name', group: 'Campaign & ad', width: 150, value: (l) => text(l.formName) },
  { id: 'organic', label: 'Organic', group: 'Campaign & ad', width: 80, value: (l) => (l.isOrganic === true ? 'Yes' : l.isOrganic === false ? 'No' : '') },
  { id: 'sourceStatus', label: 'Lead status (in the sheet)', group: 'Campaign & ad', width: 120, value: (l) => text(l.leadStatusSource) },
  { id: 'status', label: 'Status', group: 'Workflow', width: 110, value: (l) => leadStatus(l.status).label },
  { id: 'owner', label: 'Assigned to', group: 'Workflow', width: 110, value: (l) => text(l.ownerName) },
  { id: 'followUp', label: LEAD_HEADINGS.followup, group: 'Workflow', width: 110, value: (l) => when(l.nextFollowUpAt) },
  { id: 'lastContacted', label: 'Last contacted', group: 'Workflow', width: 110, value: (l) => when(l.lastContactedAt) },
  { id: 'calls', label: 'Calls made', group: 'Workflow', width: 80, value: (l) => (l.callCount ? String(l.callCount) : '') },
  { id: 'notes', label: 'Notes', group: 'Workflow', width: 180, value: (l) => text(l.notes) },
]

export const FIELD_PREFIX = 'f:'
export const fieldIdOf = (columnKey: string) => (columnKey.startsWith(FIELD_PREFIX) ? columnKey.slice(FIELD_PREFIX.length) : null)
export const fieldDef = (id: string) => LEAD_FIELD_DEFS.find((field) => field.id === id)

/** Fields that already have a column of their own in the default table (the Columns menu lists them once, under that column). */
export const DEFAULT_COLUMN_FIELDS: ReadonlySet<string> = new Set([
  'created', 'uploaded', 'name', 'gender', 'dob', 'age', 'phone', 'email', 'city', 'state', 'position', 'indiaYears', 'abroadYears', 'totalYears', 'educationLevel',
  'platform', 'campaign', 'form', 'status', 'owner',
])

/** The sheet's own wording for a custom question: `which_position_are_you_applying_for?` becomes `Which position are you applying for`. */
export function prettyHeader(key: string): string {
  const words = String(key ?? '').replace(/[\s_]+/g, ' ').trim().replace(/[?:]+$/, '').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : ''
}

export interface LeadFieldRow { key: string; label: string; value: string; custom: boolean }

/** Every field of one lead with its heading, for the expandable row: the registry first, then the custom questions it answered. */
export function leadFieldRows(lead: Lead, extras: readonly ExtraColumn[] = []): LeadFieldRow[] {
  const rows: LeadFieldRow[] = LEAD_FIELD_DEFS.map((field) => ({ key: field.id,
    label: (field.id === 'created' ? `${leadDate(lead)?.label || 'Created'} time` : field.id === 'restaurantYears' ? lead.experienceRestaurantQuestion : field.id === 'indiaYears' ? lead.experienceIndiaQuestion : field.id === 'abroadYears' ? lead.experienceAbroadQuestion : field.id === 'totalYears' ? lead.experienceTotalQuestion : null) || field.label,
    value: field.value(lead), custom: false }))
  const labels = new Map(extras.map((column) => [column.key, column.label]))
  const keys = [...new Set([...extras.map((column) => column.key), ...Object.keys(lead.extra ?? {})])]
  for (const key of keys) rows.push({ key: `x:${key}`, label: labels.get(key) ?? (prettyHeader(key) || key), value: extraAnswer(lead, key), custom: true })
  return rows
}
