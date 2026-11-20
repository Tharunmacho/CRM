/**
 * Phone numbers for links and screens (pure, no imports so tests load this file on its own).
 *
 * `waDigits` is the ONE place a wa.me number is made: it adds the agency's home calling code to a national number, so a
 * ten-digit "98765 43210" opens the chat with +91 98765 43210 and never the wrong one. `formatPhone` writes a number the
 * same way on every screen ("+91 98765 43210"). The server stores new candidate numbers as "+91XXXXXXXXXX"
 * (backend/app/services/phone_format.py, which this mirrors); older rows may still hold what was typed.
 */

/** The code assumed only when the agency settings have not arrived (or give none). */
export const FALLBACK_HOME_DIGITS = '91'
const NATIONAL_MIN = 8
const NATIONAL_MAX = 10
const TOTAL_MIN = 7
const TOTAL_MAX = 15

const clean = (value: string | null | undefined) => (value ?? '').replace(/^\s*(?:p|tel|phone|mobile|whatsapp)\s*:/i, '').trim()

/**
 * The digits to put after wa.me, or null when the value cannot be a phone number.
 * "+" or "00" mean the number already carries its country code; otherwise one leading trunk "0" is dropped and a number of
 * 8 to 10 digits gets `homeDigits` (the agency's calling code as digits, "91"; 91 only when none is given). A longer number
 * is taken to carry its own code. Fewer than 7 or more than 15 digits is not a number.
 */
export function waDigits(phone: string | null | undefined, homeDigits?: string | null): string | null {
  const raw = clean(phone)
  if (!raw || /[^\d\s\-().+/]/.test(raw)) return null
  let digits = raw.replace(/\D/g, '')
  if (!digits || new Set(digits).size === 1) return null
  const home = (homeDigits ?? '').replace(/\D/g, '') || FALLBACK_HOME_DIGITS
  if (raw.startsWith('+')) return digits.length >= TOTAL_MIN && digits.length <= TOTAL_MAX ? digits : null
  if (digits.startsWith('00')) {
    digits = digits.slice(2)
    return digits.length >= TOTAL_MIN && digits.length <= TOTAL_MAX ? digits : null
  }
  if (digits.startsWith('0')) digits = digits.slice(1)
  if (digits.length >= NATIONAL_MIN && digits.length <= NATIONAL_MAX) return `${home}${digits}`
  return digits.length > NATIONAL_MAX && digits.length <= TOTAL_MAX ? digits : null
}

/** Calling codes recognised when a number is written in groups (same list as backend/app/services/phone_format.py). */
const GROUPING_CODES = new Set(['1', '7', '20', '27', '33', '44', '49', '60', '61', '62', '63', '65', '81', '82', '86', '90', '91', '92', '94', '98', '880', '965', '966', '968', '971', '973', '974', '977'])

/**
 * "+91 98765 43210" for a readable number, the text as it was typed otherwise (and an empty string for nothing).
 * `homeDigits` is the agency's calling code ("91") for a number saved without one.
 */
export function formatPhone(phone: string | null | undefined, homeDigits?: string | null): string {
  const typed = clean(phone)
  const digits = waDigits(phone, homeDigits)
  if (!digits) return typed
  const home = (homeDigits ?? '').replace(/\D/g, '') || FALLBACK_HOME_DIGITS
  const code = [1, 2, 3].map((length) => digits.slice(0, length)).find((candidate) => GROUPING_CODES.has(candidate) || candidate === home) ?? ''
  const national = digits.slice(code.length)
  if (!code || national.length < 7 || national.length > 11) return `+${digits}`
  const groups = national.length === 10 ? [national.slice(0, 5), national.slice(5)]
    : national.length === 9 ? [national.slice(0, 3), national.slice(3, 6), national.slice(6)] : [national]
  return `+${code} ${groups.join(' ')}`
}

/** True when two written numbers are the same phone (so a WhatsApp number equal to the phone is not shown twice). */
export function samePhone(a: string | null | undefined, b: string | null | undefined, homeDigits?: string | null): boolean {
  const left = waDigits(a, homeDigits) ?? (a ?? '').replace(/\D/g, '')
  const right = waDigits(b, homeDigits) ?? (b ?? '').replace(/\D/g, '')
  return Boolean(left) && left === right
}
