/**
 * The Care India identifiers of a candidate, and which one a screen shows. No React and no imports, so it is tested on its own.
 *
 * One person, ONE visible ID per screen:
 *  - Data Bank ID (`careId`, "CI-078"): every candidate has one. Every screen except Travelled shows only this; the string is shown
 *    exactly as the API sends it (no format assumptions here).
 *  - Traveller ID (`travellerId`, "CI 94"): only candidates who have travelled, numbered by date of travel. Only the Travelled screen shows it.
 *  - The CRM reference (`ref`, "CND-01092") is never shown while a Data Bank ID exists. It is the fallback for very old rows that
 *    have no Data Bank ID, and it stays searchable (idSearchText) so people can still find a candidate by a reference they know.
 *
 * Every field is optional and may be null (an older server, or a candidate that has not travelled), so nothing here throws.
 */

export interface CandidateIds {
  ref?: string | null
  careId?: string | null
  travellerId?: string | null
}

/** The traveller number as the API sends it: "CI 01" ... "CI 99", "CI 100", "CI 101" (two digits at least, then it just grows). */
export const formatTravellerId = (serial: number): string => {
  if (!Number.isInteger(serial) || serial < 1) throw new RangeError('A traveller number starts at 1.')
  return `CI ${String(serial).padStart(2, '0')}`
}

/** The serial inside a traveller number ("CI 07" -> 7), or null when the text is not one. */
export const travellerSerialOf = (value: unknown): number | null => {
  const match = /^CI (0[1-9]|[1-9][0-9]+)$/.exec(typeof value === 'string' ? value : '')
  return match ? Number(match[1]) : null
}

const clean = (value: unknown): string => (typeof value === 'string' ? value.trim() : '')

export const careIdOf = (candidate: CandidateIds | null | undefined): string => clean(candidate?.careId)
export const travellerIdOf = (candidate: CandidateIds | null | undefined): string => clean(candidate?.travellerId)

/** The ONE ID a screen shows for a candidate: the Data Bank ID, or the CRM reference only when there is no Data Bank ID. */
export const primaryId = (candidate: CandidateIds | null | undefined): string => careIdOf(candidate) || clean(candidate?.ref)

/** The single ID as plain text for tooltips, pickers and search results; the same value primaryId gives. */
export const idLine = (candidate: CandidateIds | null | undefined): string => primaryId(candidate)

/** Every identifier on one string, for a search haystack (search still finds a candidate by the CRM reference or Traveller ID). */
export const idSearchText = (candidate: CandidateIds | null | undefined): string =>
  [careIdOf(candidate), travellerIdOf(candidate), clean(candidate?.ref)].filter(Boolean).join(' ')

/** True when the (already lower-cased) search text appears in the Data Bank ID, Traveller ID or CRM reference. */
export const matchesIdText = (candidate: CandidateIds | null | undefined, text: string): boolean => {
  if (!text) return false
  if (idSearchText(candidate).toLowerCase().includes(text.toLowerCase())) return true
  // "ci 7" finds the traveller number "CI 07": the stored numbers are zero-padded.
  const typed = /^ci\s*(\d+)$/i.exec(text.trim())
  return Boolean(typed) && Number(typed![1]) >= 1 && travellerSerialOf(travellerIdOf(candidate)) === Number(typed![1])
}

/** The paper form's header line: "Care India ID CI-078", or "Reference CND-01092" for a row with no Data Bank ID. */
export function paperIdLine(candidate: CandidateIds | null | undefined, readable: (reference: string) => string = (reference) => reference): string {
  const care = careIdOf(candidate)
  if (care) return `Care India ID ${care}`
  const ref = readable(clean(candidate?.ref))
  return ref ? `Reference ${ref}` : ''
}
