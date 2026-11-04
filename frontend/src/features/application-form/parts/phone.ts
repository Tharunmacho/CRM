/**
 * Pure helpers for the phone input with a country-code select (used by `PhoneField`).
 * `splitPhone('+91 98765 43210')` -> { code: '+91', national: '98765 43210' }; `joinPhone` is the reverse.
 * A number typed without a code gets the default (+91) when it is joined, so the stored value is always international.
 */
export interface Country { code: string; name: string; digits: number }

/** The destinations this agency recruits for, plus India. `digits` is the usual national-number length. */
export const COUNTRIES: Country[] = [
  { code: '+91', name: 'India', digits: 10 },
  { code: '+971', name: 'United Arab Emirates', digits: 9 },
  { code: '+966', name: 'Saudi Arabia', digits: 9 },
  { code: '+974', name: 'Qatar', digits: 8 },
  { code: '+968', name: 'Oman', digits: 8 },
  { code: '+965', name: 'Kuwait', digits: 8 },
  { code: '+973', name: 'Bahrain', digits: 8 },
  { code: '+94', name: 'Sri Lanka', digits: 9 },
  { code: '+977', name: 'Nepal', digits: 10 },
  { code: '+880', name: 'Bangladesh', digits: 10 },
  { code: '+92', name: 'Pakistan', digits: 10 },
]
export const DEFAULT_CODE = '+91'

export function splitPhone(value: string | null | undefined, fallback = DEFAULT_CODE): { code: string; national: string } {
  const text = (value ?? '').trim()
  if (!text.startsWith('+')) return { code: fallback, national: text }
  const compact = text.replace(/[^\d+]/g, '')
  // Longest matching code first, so +971 is not read as +97.
  const match = [...COUNTRIES].sort((a, b) => b.code.length - a.code.length).find((country) => compact.startsWith(country.code))
  if (!match) return { code: fallback, national: text.replace(/^\+/, '') }
  const rest = text.replace(/^\+\s*/, '').replace(match.code.slice(1), '').replace(/^[\s-]+/, '')
  return { code: match.code, national: rest }
}

export function joinPhone(code: string, national: string): string {
  const digits = national.trim()
  return digits ? `${code} ${digits}` : ''
}

/** "Selected: India (+91) - Requires 10 digits" */
export function phoneHint(code: string): string {
  const country = COUNTRIES.find((item) => item.code === code)
  return country ? `Selected: ${country.name} (${country.code}) - Requires ${country.digits} digits` : `Selected: ${code}`
}

/** Whether the national part has the digits the chosen country needs (empty is fine: required-ness is the caller's rule). */
export function phoneDigitsOk(code: string, national: string): boolean {
  const digits = national.replace(/\D/g, '')
  if (!digits) return true
  const country = COUNTRIES.find((item) => item.code === code)
  return country ? digits.length === country.digits : digits.length >= 7
}
