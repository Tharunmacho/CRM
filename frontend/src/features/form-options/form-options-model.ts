/**
 * Form options: the lists behind the dropdowns and cards of the candidate form (marital status, service charges, the
 * interest-level cards, job duration, best time to contact, source, ...). They live in the database and admins edit
 * them under Users > Agency settings; the screens only read them (see use-form-options.ts).
 *
 * Pure: no React, no network. `FALLBACK_OPTIONS` is used ONLY when the request fails, so the form still works offline.
 * Category keys are the server's (backend/app/services/form_options.py).
 */
export const FORM_OPTION_CATEGORIES = {
  maritalStatus: 'marital_status',
  gender: 'gender',
  serviceCharges: 'service_charges_status',
  interestLevel: 'interest_level',
  source: 'candidate_source',
  jobDuration: 'job_duration_preference',
  bestTime: 'best_time_to_contact',
  educationLevel: 'education_level',
  language: 'language',
} as const
export type FormOptionCategory = (typeof FORM_OPTION_CATEGORIES)[keyof typeof FORM_OPTION_CATEGORIES]

export interface FormOption {
  id?: string
  category: string
  value: string
  label: string
  hint?: string | null
  /** The server's tone (neutral, success, info, warning, danger); a colour name or a #hex colour is accepted too. Used by the interest cards. */
  tone?: string | null
  sort: number
  active: boolean
  /** Built into the system: it can be renamed or hidden, but not deleted. */
  system?: boolean
}
export type FormOptions = Record<string, FormOption[]>
/** What the server says about a category: strict lists only accept their own values; a fixed list cannot get new values. */
export interface CategoryMeta { key: string; label: string; strict: boolean; fixedValues: boolean; allowCustom: boolean }
export type CategoryMetas = Record<string, CategoryMeta>

const make = (category: string, rows: [string, string, string?, string?][]): FormOption[] =>
  rows.map(([value, label, hint, tone], index) => ({ category, value, label, hint: hint ?? null, tone: tone ?? null, sort: (index + 1) * 10, active: true }))

/** The built-in lists, shown only when the options could not be loaded. */
export const FALLBACK_OPTIONS: FormOptions = {
  marital_status: make('marital_status', [['Single', 'Single'], ['Married', 'Married'], ['Divorced', 'Divorced'], ['Widowed', 'Widowed'], ['Separated', 'Separated']]),
  gender: make('gender', [['male', 'Male'], ['female', 'Female']]),
  service_charges_status: make('service_charges_status', [['not_discussed', 'Not discussed'], ['agreed', 'Agreed'], ['pending', 'Pending'], ['declined', 'Declined']]),
  interest_level: make('interest_level', [
    ['more_interested', 'More interested', 'Keen to go abroad now', 'success'], ['interested', 'Interested', 'Wants to go and is open to offers', 'info'],
    ['somewhat_interested', 'Somewhat interested', 'May go if the offer is right', 'warning'], ['not_interested', 'Not interested', 'Does not want to go abroad', 'danger'],
  ]),
  candidate_source: make('candidate_source', [['walk_in', 'Walk-in'], ['referral', 'Referral / agency'], ['email', 'Email'], ['whatsapp', 'WhatsApp'], ['other', 'Other']]),
  job_duration_preference: make('job_duration_preference', [['1 year', '1 year'], ['2 years', '2 years'], ['3 years', '3 years'], ['Any', 'Any duration']]),
  best_time_to_contact: make('best_time_to_contact', [['Morning', 'Morning'], ['Afternoon', 'Afternoon'], ['Evening', 'Evening'], ['Anytime', 'Anytime']]),
  education_level: [],
  language: [],
}

const text = (value: unknown) => (typeof value === 'string' ? value : value == null ? '' : String(value))

/** One row from the API (camelCase `sortOrder` / `isActive`, with the shorter spellings accepted too); null when it has no value or label. */
export function normalizeOption(raw: unknown, fallbackCategory = ''): FormOption | null {
  if (!raw || typeof raw !== 'object') return null
  const row = raw as Record<string, unknown>
  const value = text(row.value).trim()
  const label = text(row.label ?? row.value).trim()
  if (!value || !label) return null
  const order = Number(row.sortOrder ?? row.sort)
  const active = row.isActive ?? row.active
  return {
    id: row.id == null ? undefined : text(row.id),
    category: text(row.category ?? fallbackCategory),
    value,
    label: text(row.category ?? fallbackCategory) === 'candidate_source' && value === 'referral' && label === 'Referral / agent'
      ? 'Referral / agency' : label,
    hint: row.hint == null || row.hint === '' ? null : text(row.hint),
    tone: row.tone == null || row.tone === '' ? null : text(row.tone),
    sort: Number.isFinite(order) ? order : 0,
    active: active === undefined ? true : Boolean(active),
    system: row.isSystem === undefined ? undefined : Boolean(row.isSystem),
  }
}

/**
 * The response of GET /form-options: `{ categories: [{ key, label, strict, fixedValues, allowCustom, options: [...] }] }`.
 * Returns the rows by category key and what the server said about each category.
 */
export function normalizeFormOptions(payload: unknown): { options: FormOptions; meta: CategoryMetas } {
  const options: FormOptions = {}
  const meta: CategoryMetas = {}
  const body = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
  const list = Array.isArray(body.categories) ? body.categories : []
  for (const item of list) {
    if (!item || typeof item !== 'object') continue
    const category = item as Record<string, unknown>
    const key = text(category.key).trim()
    if (!key) continue
    options[key] = (Array.isArray(category.options) ? category.options : []).map((row) => normalizeOption(row, key)).filter((row): row is FormOption => row !== null)
    meta[key] = {
      key, label: text(category.label) || key, strict: Boolean(category.strict), fixedValues: Boolean(category.fixedValues),
      allowCustom: category.allowCustom === undefined ? !category.strict : Boolean(category.allowCustom),
    }
  }
  return { options, meta }
}

/** Active options of a category in their saved order. */
export function activeOptions(options: FormOptions, category: string): FormOption[] {
  return [...(options[category] ?? [])].filter((option) => option.active).sort((a, b) => a.sort - b.sort || a.label.localeCompare(b.label))
}

/**
 * What a dropdown offers: the active options, plus the current value when it is not among them (a switched-off option
 * a candidate already has, or a value read from a document), so a saved value is never silently hidden.
 */
export function choicesFor(options: FormOptions, category: string, current = ''): FormOption[] {
  const list = activeOptions(options, category)
  const value = current.trim()
  if (!value || list.some((option) => option.value === value)) return list
  const known = (options[category] ?? []).find((option) => option.value === value)
  return [...list, known ?? { category, value, label: value, sort: Number.MAX_SAFE_INTEGER, active: false }]
}

/** The label shown for a stored value (the value itself when it is not in the list). */
export function labelFor(options: FormOptions, category: string, value: string): string {
  return (options[category] ?? []).find((option) => option.value === value)?.label ?? value
}

/** The server's tones and a few colour names, mapped to the card colours. */
const TONE_NAMES: Record<string, string> = {
  success: 'green', green: 'green', info: 'blue', blue: 'blue', warning: 'amber', amber: 'amber', danger: 'red', red: 'red',
  neutral: 'gray', gray: 'gray', grey: 'gray', purple: 'purple', teal: 'teal',
}
/** The tones an admin can pick (the server's set). */
export const TONE_CHOICES: { value: string; label: string }[] = [
  { value: 'success', label: 'Green' }, { value: 'info', label: 'Blue' }, { value: 'warning', label: 'Amber' }, { value: 'danger', label: 'Red' }, { value: 'neutral', label: 'Grey' },
]
/** How a card is coloured: a known tone becomes `data-tone` (green, blue, amber, red, gray), a #hex becomes an inline `--tone`. */
export function toneStyle(tone: string | null | undefined): { name?: string; color?: string } {
  const value = (tone ?? '').trim().toLowerCase()
  if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/.test(value)) return { color: value }
  return TONE_NAMES[value] ? { name: TONE_NAMES[value] } : {}
}

/** A stable value for a new option from its label ("Somewhat keen" -> "somewhat_keen"). */
export function slugValue(label: string): string {
  return label.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 60)
}

/** Move one row up or down among its category's rows and renumber the sort values 10, 20, 30, ... */
export function reorder(rows: FormOption[], value: string, direction: -1 | 1): FormOption[] {
  const sorted = [...rows].sort((a, b) => a.sort - b.sort)
  const index = sorted.findIndex((row) => row.value === value)
  const target = index + direction
  if (index >= 0 && target >= 0 && target < sorted.length) [sorted[index], sorted[target]] = [sorted[target], sorted[index]]
  return sorted.map((row, position) => ({ ...row, sort: (position + 1) * 10 }))
}

/** A message when an option's name cannot be saved, otherwise undefined. */
export function optionLabelError(label: string, rows: FormOption[], ownValue?: string): string | undefined {
  const clean = label.trim().replace(/\s+/g, ' ')
  if (!clean) return 'Enter a name.'
  if (clean.length > 80) return 'Use at most 80 characters.'
  if (rows.some((row) => row.value !== ownValue && row.label.toLowerCase() === clean.toLowerCase())) return 'This name is already in the list.'
  return undefined
}
