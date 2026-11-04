/**
 * Phone numbers on the Add candidate page: one stored string ("+91 98400 00000") shown as a country-code select and a
 * number box. Pure, so the split / join and the hint are tested without a browser.
 */
export interface CountryCode { code: string; country: string; digits: number }

/** The countries the agency recruits from or for; `digits` is the national number length (without a trunk 0). */
export const COUNTRY_CODES: readonly CountryCode[] = [
  { code: '+91', country: 'India', digits: 10 },
  { code: '+971', country: 'United Arab Emirates', digits: 9 },
  { code: '+966', country: 'Saudi Arabia', digits: 9 },
  { code: '+974', country: 'Qatar', digits: 8 },
  { code: '+968', country: 'Oman', digits: 8 },
  { code: '+965', country: 'Kuwait', digits: 8 },
  { code: '+973', country: 'Bahrain', digits: 8 },
  { code: '+94', country: 'Sri Lanka', digits: 9 },
  { code: '+977', country: 'Nepal', digits: 10 },
  { code: '+880', country: 'Bangladesh', digits: 10 },
  { code: '+92', country: 'Pakistan', digits: 10 },
  { code: '+60', country: 'Malaysia', digits: 9 },
  { code: '+65', country: 'Singapore', digits: 8 },
  { code: '+44', country: 'United Kingdom', digits: 10 },
  { code: '+1', country: 'United States / Canada', digits: 10 },
]
export const DEFAULT_COUNTRY_CODE = '+91'
const byLongestCode = [...COUNTRY_CODES].sort((a, b) => b.code.length - a.code.length)

/** Digits only, with full-width digits folded to ASCII. */
export const phoneDigits = (value: string) => value.normalize('NFKC').replace(/\D/g, '')

/**
 * Split a stored number into its country code and the rest. A number without a "+" belongs to the default country; a
 * "+" with a code the list does not know keeps the whole text in the number box (code "").
 */
export function splitPhone(value: string, fallback = DEFAULT_COUNTRY_CODE): { code: string; national: string } {
  const text = value.normalize('NFKC').trim()
  if (!text) return { code: fallback, national: '' }
  const international = text.startsWith('+') || text.startsWith('00')
  if (!international) {
    const digits = phoneDigits(text)
    // 91XXXXXXXXXX written without the plus.
    if (digits.length === 12 && digits.startsWith('91') && fallback === '+91') return { code: '+91', national: digits.slice(2) }
    return { code: fallback, national: text }
  }
  const digits = phoneDigits(text).replace(/^00/, '')
  const found = byLongestCode.find((entry) => digits.startsWith(entry.code.slice(1)))
  if (!found) return { code: '', national: text }
  const rest = text.replace(/^\+|^00/, '').trimStart().slice(found.code.length - 1).replace(/^[\s\-.()]+/, '')
  return { code: found.code, national: rest }
}

/** The stored text for a code and a number; empty when no number was typed, so a required check still fires. */
export function joinPhone(code: string, national: string): string {
  const number = national.trim()
  if (!number) return ''
  return code ? `${code} ${number}` : number
}

/** The line under the phone box: which country is chosen and whether the digit count fits it. */
export function phoneHint(code: string, national: string): { text: string; tone: 'info' | 'warn' | 'ok' } {
  const entry = COUNTRY_CODES.find((item) => item.code === code)
  if (!entry) return { text: code ? `Selected: ${code}` : 'Type the full number with its country code.', tone: 'info' }
  const label = `Selected: ${entry.country} (${entry.code}) - requires ${entry.digits} digits`
  const digits = phoneDigits(national).replace(/^0+/, '')
  if (!digits) return { text: label, tone: 'info' }
  if (digits.length === entry.digits) return { text: label, tone: 'ok' }
  return { text: `${entry.country} (${entry.code}) numbers have ${entry.digits} digits. You typed ${digits.length}.`, tone: 'warn' }
}
