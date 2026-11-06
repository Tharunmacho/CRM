/**
 * Pure helpers behind the Data Bank table's cells: the preferred-country pills, the colour of a form-option value
 * (service charges, interest level), the two-line date, the date of birth and the WhatsApp link.
 * No React, no imports, so they are unit-tested directly (tests/data-bank-view.test.mjs).
 */

export type Tone = 'ok' | 'info' | 'warn' | 'bad' | 'quiet'

/* ------------------------------------------------------------ preferred country */

/** How many solid pill colours there are (the classes `is-c0` ... `is-c7` in data-bank-v2.css). */
export const COUNTRY_TONES = 8

/**
 * The countries written in one "preferred country" answer. It is free text, so people type "Saudi Arabia, UAE",
 * "Qatar / Kuwait" or "Dubai; Oman": split on commas, semicolons, slashes and bars, drop blanks and repeats (any case).
 */
export function splitCountries(value?: string | null): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const part of (value ?? '').split(/[,;/|]+/)) {
    const name = part.replace(/\s+/g, ' ').trim()
    const key = name.toLowerCase()
    if (name && !seen.has(key)) { seen.add(key); out.push(name) }
  }
  return out
}

/**
 * A stable pill colour (0 to COUNTRY_TONES - 1) for a country: the same country always gets the same colour, whatever the
 * letter case or spacing. Pass the ISO code when it is known so "UAE" and "United Arab Emirates" share a colour.
 */
export function countryTone(country: string, isoCode?: string | null): number {
  const key = (isoCode || country).normalize('NFKD').toLowerCase().replace(/[^a-z0-9]+/g, '')
  if (!key) return 0
  let hash = 2166136261
  for (let index = 0; index < key.length; index++) {
    hash ^= key.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0) % COUNTRY_TONES
}

/* -------------------------------------------------------------- option colours */

/** A form option's tone as the colour classes use it. `style` is what toneStyle() (form-options-model) returned. */
const NAME_TONE: Record<string, Tone> = { green: 'ok', blue: 'info', amber: 'warn', red: 'bad', gray: 'quiet', purple: 'info', teal: 'info' }

/** Used when an option carries no tone of its own. */
export const SERVICE_CHARGE_TONE: Record<string, Tone> = { agreed: 'ok', pending: 'warn', declined: 'bad', not_discussed: 'quiet' }
export const INTEREST_TONE: Record<string, Tone> = { more_interested: 'ok', interested: 'ok', somewhat_interested: 'warn', not_interested: 'bad' }

/**
 * The colour of a stored option value. The admin's tone wins (a named tone, or a #hex colour); otherwise the built-in
 * meaning of the value; otherwise a muted grey.
 */
export function optionTone(style: { name?: string; color?: string }, value: string | null | undefined, fallback: Record<string, Tone>): { tone: Tone; color?: string } {
  if (style.color) return { tone: 'quiet', color: style.color }
  if (style.name && NAME_TONE[style.name]) return { tone: NAME_TONE[style.name] }
  return { tone: fallback[(value ?? '').trim()] ?? 'quiet' }
}

/* ------------------------------------------------------------------ dates */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const two = (value: number) => String(value).padStart(2, '0')

/** "04 Nov" over "2026": the two lines of the Created cell. Null when the value is not a date. */
export function createdParts(value?: string | null): { day: string; year: string } | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return { day: `${two(date.getDate())} ${MONTHS[date.getMonth()]}`, year: String(date.getFullYear()) }
}

/** A date of birth ("1994-03-12", no time zone) as "12 Mar 1994", and the age today; null when it is not a plausible date. */
export function dobParts(dob?: string | null, now: Date = new Date()): { label: string; age: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(dob ?? '')
  if (!match) return null
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  if (month < 1 || month > 12 || day < 1 || day > 31) return null
  let age = now.getFullYear() - year
  if (now.getMonth() + 1 < month || (now.getMonth() + 1 === month && now.getDate() < day)) age -= 1
  if (age < 0 || age > 120) return null
  return { label: `${two(day)} ${MONTHS[month - 1]} ${year}`, age }
}

/* --------------------------------------------------------------- whatsapp */

/** A wa.me link for a saved WhatsApp number, or null when it holds too few digits to be a number. */
export function whatsappHref(number?: string | null): string | null {
  const digits = (number ?? '').replace(/\D/g, '')
  return digits.length >= 7 && digits.length <= 15 ? `https://wa.me/${digits}` : null
}
