import type { AgencySettings, CurrencyOption } from '@/shared/model/types'

/**
 * Pure helpers for the agency settings the server owns (name, default currencies, home calling code, currency list).
 * Nothing here knows an agency's actual values: they all arrive from the database through the store.
 */

/** A neutral label shown only until the settings arrive (the page title is likewise "CRM" until then). */
export const NEUTRAL_NAME = 'CRM'

export const EMPTY_SETTINGS: AgencySettings = {
  name: '', shortName: null, defaultOrderCurrency: '', defaultAdvanceCurrency: '', homeCallingCode: '',
  defaultNationality: null, acknowledgementText: '', currencies: [], categories: [], categoriesEditable: false, timezone: '', slaHours: 0, editable: false,
}

export const agencyName = (settings?: Pick<AgencySettings, 'name'> | null, signedOut?: { name?: string } | null) =>
  settings?.name?.trim() || signedOut?.name?.trim() || NEUTRAL_NAME

/** The short form for tight places such as the sidebar; the full name when no short name is set. */
export const agencyShortName = (settings?: Pick<AgencySettings, 'name' | 'shortName'> | null, signedOut?: { name?: string; shortName?: string | null } | null) =>
  settings?.shortName?.trim() || signedOut?.shortName?.trim() || agencyName(settings, signedOut)

export const documentTitle = (name: string) => (name === NEUTRAL_NAME ? NEUTRAL_NAME : `${name} | Recruitment CRM`)

/** "+91" -> "91". Empty when the code is missing or malformed. */
export const homeDigits = (code?: string | null) => (/^\+\d{1,4}$/.test(code ?? '') ? (code as string).slice(1) : '')

const bySort = (a: CurrencyOption, b: CurrencyOption) => a.sortOrder - b.sortOrder || a.code.localeCompare(b.code)

export const activeCurrencies = (currencies: CurrencyOption[]) => currencies.filter((item) => item.active).sort(bySort)

/**
 * What a currency picker offers: the active currencies, plus the value already stored on the record being edited
 * even when it was switched off or never listed, so opening an old order never loses its currency.
 */
export function currencyChoices(currencies: CurrencyOption[], keep?: string | null): CurrencyOption[] {
  const list = activeCurrencies(currencies)
  if (!keep || list.some((item) => item.code === keep)) return list
  const known = currencies.find((item) => item.code === keep)
  return [...list, { code: keep, name: known ? `${known.name} (not in use)` : 'not in the list', symbol: known?.symbol ?? null, active: false, sortOrder: Number.MAX_SAFE_INTEGER }]
}

export const currencyLabel = (item: Pick<CurrencyOption, 'code' | 'name'>) => `${item.code} — ${item.name}`

export interface AgencyForm {
  name: string
  shortName: string
  defaultOrderCurrency: string
  defaultAdvanceCurrency: string
  homeCallingCode: string
  defaultNationality: string
  acknowledgementText: string
}

export const formFromSettings = (settings: AgencySettings): AgencyForm => ({
  name: settings.name, shortName: settings.shortName ?? '', defaultOrderCurrency: settings.defaultOrderCurrency,
  defaultAdvanceCurrency: settings.defaultAdvanceCurrency, homeCallingCode: settings.homeCallingCode,
  defaultNationality: settings.defaultNationality ?? '', acknowledgementText: settings.acknowledgementText,
})

/** The same rules the server applies, so a mistake is shown beside the field before anything is sent. */
export function agencyFormErrors(form: AgencyForm, currencies: CurrencyOption[]): Partial<Record<keyof AgencyForm, string>> {
  const errors: Partial<Record<keyof AgencyForm, string>> = {}
  const name = form.name.trim()
  if (!name) errors.name = 'Enter the agency name.'
  else if (name.length > 120) errors.name = 'Use 120 characters or fewer.'
  if (form.shortName.trim().length > 60) errors.shortName = 'Use 60 characters or fewer.'
  const active = new Set(activeCurrencies(currencies).map((item) => item.code))
  if (!active.has(form.defaultOrderCurrency)) errors.defaultOrderCurrency = 'Choose an active currency.'
  if (!active.has(form.defaultAdvanceCurrency)) errors.defaultAdvanceCurrency = 'Choose an active currency.'
  if (!/^\+\d{1,4}$/.test(form.homeCallingCode.replace(/\s+/g, ''))) errors.homeCallingCode = 'Enter a plus sign and 1 to 4 digits, for example +91.'
  if (form.defaultNationality.trim().length > 80) errors.defaultNationality = 'Use 80 characters or fewer.'
  const text = form.acknowledgementText.trim()
  if (text.length < 10) errors.acknowledgementText = 'Write at least 10 characters.'
  else if (text.length > 2000) errors.acknowledgementText = 'Use 2,000 characters or fewer.'
  return errors
}

export function agencyFormChanged(form: AgencyForm, settings: AgencySettings) {
  const saved = formFromSettings(settings)
  return (Object.keys(saved) as (keyof AgencyForm)[]).some((key) => form[key].trim() !== saved[key].trim())
}

export interface CurrencyForm { code: string; name: string; symbol: string }

export function currencyFormErrors(form: CurrencyForm, existing: CurrencyOption[], creating: boolean): Partial<Record<keyof CurrencyForm, string>> {
  const errors: Partial<Record<keyof CurrencyForm, string>> = {}
  const code = form.code.trim().toUpperCase()
  if (creating) {
    if (!/^[A-Z]{3}$/.test(code)) errors.code = 'Enter the 3-letter currency code, for example AED.'
    else if (existing.some((item) => item.code === code)) errors.code = `${code} is already in the list.`
  }
  const name = form.name.trim()
  if (!name) errors.name = 'Enter the currency name.'
  else if (name.length > 80) errors.name = 'Use 80 characters or fewer.'
  if (form.symbol.trim().length > 8) errors.symbol = 'Use 8 characters or fewer.'
  return errors
}

/** Field errors the server sent back ({ errors: [{ field, message }] }), or none. */
export function serverFieldErrors(detail: unknown): Record<string, string> {
  const list = (detail as { errors?: unknown } | null)?.errors
  const found: Record<string, string> = {}
  if (Array.isArray(list)) {
    for (const item of list) {
      const entry = item as { field?: unknown; message?: unknown }
      if (typeof entry.field === 'string' && typeof entry.message === 'string') found[entry.field] = entry.message
    }
  }
  return found
}
