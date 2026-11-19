/**
 * The Agencies screen's filters (Specialisation, Preferred country, Activity), their address form (?spec=oil-and-gas&country=gulf&active=1)
 * and the active-first order. Pure and import-free so it can be unit tested on its own; the option lists mirror the agency form
 * (client-form.ts) and the server's fixed sets (reference_agencies.py).
 */

export const ALL_SECTORS_LABEL = 'All sectors'

/** One choice of a filter: `value` is what the address carries, `label` is the name the form and the table use. */
export interface FilterOption { value: string; label: string }

const slug = (label: string) => label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
const optionsOf = (labels: readonly string[]): FilterOption[] => labels.map((label) => ({ value: slug(label), label }))

/** Same names and order as the agency form's Specialisation chips. */
export const SPEC_LABELS: readonly string[] = ['Oil and gas', 'Hospitality', 'Construction', 'General worker', 'Medical', ALL_SECTORS_LABEL]
/** Same names and order as the agency form's Preferred country chips. */
export const COUNTRY_LABELS: readonly string[] = ['Singapore', 'Malaysia', 'Europe', 'Gulf', 'Others']

export const SPEC_OPTIONS: readonly FilterOption[] = optionsOf(SPEC_LABELS)
export const COUNTRY_OPTIONS: readonly FilterOption[] = optionsOf(COUNTRY_LABELS)

export type ActivityFilter = '' | 'active' | 'inactive'

/** An empty string means "All" for each filter. */
export interface AgencyFilters { spec: string; country: string; activity: ActivityFilter }

export const NO_FILTERS: AgencyFilters = { spec: '', country: '', activity: '' }

export const SPEC_PARAM = 'spec'
export const COUNTRY_PARAM = 'country'
export const ACTIVE_PARAM = 'active'

interface ParamReader { get(name: string): string | null }

/** The filters named in the address. Unknown or hand-edited values are ignored (they read as "All"), never an error. */
export function parseAgencyFilters(params: ParamReader): AgencyFilters {
  const spec = (params.get(SPEC_PARAM) ?? '').trim().toLowerCase()
  const country = (params.get(COUNTRY_PARAM) ?? '').trim().toLowerCase()
  const active = (params.get(ACTIVE_PARAM) ?? '').trim().toLowerCase()
  return {
    spec: SPEC_OPTIONS.some((option) => option.value === spec) ? spec : '',
    country: COUNTRY_OPTIONS.some((option) => option.value === country) ? country : '',
    activity: active === '1' ? 'active' : active === '0' ? 'inactive' : '',
  }
}

/** The address entries for these filters; a null value removes the entry, so "All" leaves the address clean. */
export function agencyFilterParams(filters: AgencyFilters): Record<string, string | null> {
  return {
    [SPEC_PARAM]: filters.spec || null,
    [COUNTRY_PARAM]: filters.country || null,
    [ACTIVE_PARAM]: filters.activity === 'active' ? '1' : filters.activity === 'inactive' ? '0' : null,
  }
}

export const hasAgencyFilters = (filters: AgencyFilters) => !!(filters.spec || filters.country || filters.activity)

const labelOf = (options: readonly FilterOption[], value: string) => options.find((option) => option.value === value)?.label ?? ''

/** "Oil and gas · Gulf · Active": the chosen filters in words, for the result count. Empty when nothing is chosen. */
export function describeAgencyFilters(filters: AgencyFilters): string {
  return [
    labelOf(SPEC_OPTIONS, filters.spec),
    labelOf(COUNTRY_OPTIONS, filters.country),
    filters.activity === 'active' ? 'Active' : filters.activity === 'inactive' ? 'Not active' : '',
  ].filter(Boolean).join(' · ')
}

export interface FilterableAgency {
  specialisations?: readonly string[] | null
  preferredCountries?: readonly string[] | null
  activeAgency?: boolean | null
}

/**
 * Exact match on the saved name, with one rule: an agency tagged "All sectors" serves every sector, so it also shows under each
 * specific sector. The "All sectors" filter itself shows only agencies tagged "All sectors".
 */
export function matchesSpecialisation(agency: FilterableAgency, value: string): boolean {
  if (!value) return true
  const wanted = labelOf(SPEC_OPTIONS, value)
  if (!wanted) return true
  const saved = agency.specialisations ?? []
  return saved.includes(wanted) || (wanted !== ALL_SECTORS_LABEL && saved.includes(ALL_SECTORS_LABEL))
}

export function matchesCountry(agency: FilterableAgency, value: string): boolean {
  if (!value) return true
  const wanted = labelOf(COUNTRY_OPTIONS, value)
  return !wanted || (agency.preferredCountries ?? []).includes(wanted)
}

export function matchesActivity(agency: FilterableAgency, value: ActivityFilter): boolean {
  if (!value) return true
  return value === 'active' ? agency.activeAgency === true : agency.activeAgency !== true
}

/** The three filters combine (AND). */
export const matchesAgencyFilters = (agency: FilterableAgency, filters: AgencyFilters): boolean =>
  matchesSpecialisation(agency, filters.spec) && matchesCountry(agency, filters.country) && matchesActivity(agency, filters.activity)

/** Active agencies first, the others last; inside each group the incoming order is kept (the server lists the newest first). */
export function activeFirst<T extends { activeAgency?: boolean | null }>(agencies: readonly T[]): T[] {
  return [...agencies.filter((agency) => agency.activeAgency === true), ...agencies.filter((agency) => agency.activeAgency !== true)]
}
