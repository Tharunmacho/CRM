/**
 * Search for the Clients page. Pure functions (no React, no imports) so the rules are unit tested.
 *
 * Rules: the query is split on spaces and commas and every term must match (AND). A term is a case- and accent-insensitive
 * piece of any text field. A term made only of digits and phone punctuation also matches the phone numbers by digits alone,
 * so "98765" finds "+91 98765-43210".
 */

export interface Searchable {
  name?: string | null
  agencyName?: string | null
  email?: string | null
  address?: string | null
  state?: string | null
  phone?: string | null
  /** Names of the allocated CRM staff. */
  staffNames?: string[]
  /** The record's own staff details (name, designation, phone, email). */
  staffContacts?: { name?: string | null; phone?: string | null }[]
  /** Short labels shown as chips on the record (an agency's specialisations and preferred countries). */
  tags?: string[]
}

/** Lower case with accents removed: "Zoë" and "ZOE" fold to the same text. */
function foldChar(char: string) {
  return char.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()
}

/** Folds a text and remembers, for every folded character, where it came from in the original (for highlighting). */
function fold(text: string) {
  let folded = ''
  const map: number[] = []
  let index = 0
  for (const char of text) {
    const piece = foldChar(char)
    for (let i = 0; i < piece.length; i += 1) map.push(index)
    folded += piece
    index += char.length
  }
  return { folded, map }
}

export const foldText = (text: string | null | undefined) => fold(text ?? '').folded

/** The distinct terms of a query, folded; an empty list means "no search". */
export function searchTerms(query: string | null | undefined): string[] {
  const terms = foldText(query).split(/[\s,;]+/).filter(Boolean)
  return [...new Set(terms)]
}

const digitsOf = (text: string | null | undefined) => (text ?? '').replace(/\D/g, '')

/** True for a term like "98765", "+91", "(044)" or "98-76": only digits and phone punctuation, at least one digit. */
export const isPhoneTerm = (term: string) => /^[\d+\-().]+$/.test(term) && /\d/.test(term)

function textFields(record: Searchable): string[] {
  return [
    record.name, record.agencyName, record.email, record.address, record.state,
    ...(record.staffNames ?? []),
    ...(record.staffContacts ?? []).map((contact) => contact.name),
    ...(record.tags ?? []),
  ].map(foldText).filter(Boolean)
}

function phoneDigits(record: Searchable): string[] {
  return [record.phone, ...(record.staffContacts ?? []).map((contact) => contact.phone)].map(digitsOf).filter(Boolean)
}

/** Whether the record matches the query. A blank query matches everything. */
export function matchesSearch(record: Searchable, query: string | null | undefined): boolean {
  const terms = searchTerms(query)
  if (terms.length === 0) return true
  const texts = textFields(record)
  const phones = phoneDigits(record)
  return terms.every((term) => {
    if (texts.some((text) => text.includes(term))) return true
    return isPhoneTerm(term) && phones.some((digits) => digits.includes(digitsOf(term)))
  })
}

export interface Piece { text: string; hit: boolean }

function ranges(folded: string, map: number[], needles: string[], originalLength: number): [number, number][] {
  const found: [number, number][] = []
  for (const needle of needles) {
    if (!needle) continue
    let from = folded.indexOf(needle)
    while (from !== -1) {
      const start = map[from]
      const end = (map[from + needle.length - 1] ?? originalLength - 1) + 1
      found.push([start, end])
      from = folded.indexOf(needle, from + needle.length)
    }
  }
  found.sort((a, b) => a[0] - b[0] || a[1] - b[1])
  const merged: [number, number][] = []
  for (const range of found) {
    const last = merged[merged.length - 1]
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1])
    else merged.push([range[0], range[1]])
  }
  return merged
}

function pieces(text: string, merged: [number, number][]): Piece[] {
  if (merged.length === 0) return [{ text, hit: false }]
  const out: Piece[] = []
  let cursor = 0
  for (const [start, end] of merged) {
    if (start > cursor) out.push({ text: text.slice(cursor, start), hit: false })
    out.push({ text: text.slice(start, end), hit: true })
    cursor = end
  }
  if (cursor < text.length) out.push({ text: text.slice(cursor), hit: false })
  return out
}

/** Splits a text into plain and matched pieces for the terms of the query (rendered with <mark>). */
export function highlightPieces(text: string | null | undefined, query: string | null | undefined): Piece[] {
  const value = text ?? ''
  const terms = searchTerms(query)
  if (!value || terms.length === 0) return [{ text: value, hit: false }]
  const { folded, map } = fold(value)
  return pieces(value, ranges(folded, map, terms, value.length))
}

/** Same for a phone number: a digit term lights the digits it matches, however the number is spaced ("+91 98765-43210"). */
export function highlightPhone(phone: string | null | undefined, query: string | null | undefined): Piece[] {
  const value = phone ?? ''
  const terms = searchTerms(query).filter(isPhoneTerm)
  if (!value || terms.length === 0) return [{ text: value, hit: false }]
  let digits = ''
  const where: number[] = []
  for (let i = 0; i < value.length; i += 1) if (/\d/.test(value[i])) { digits += value[i]; where.push(i) }
  const found: [number, number][] = []
  for (const term of terms) {
    const needle = digitsOf(term)
    if (!needle) continue
    let from = digits.indexOf(needle)
    while (from !== -1) {
      found.push([where[from], where[from + needle.length - 1] + 1])
      from = digits.indexOf(needle, from + needle.length)
    }
  }
  found.sort((a, b) => a[0] - b[0] || a[1] - b[1])
  const merged: [number, number][] = []
  for (const range of found) {
    const last = merged[merged.length - 1]
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1])
    else merged.push([range[0], range[1]])
  }
  return pieces(value, merged)
}
