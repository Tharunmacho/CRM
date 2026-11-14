/**
 * The two Care India identifiers of a candidate, and which one a screen shows first. No React and no imports, so it is
 * tested on its own.
 *
 *  - Care India ID (`careId`, "CI-26-27-001"): the Data Bank ID every candidate has. It is the PRIMARY identifier.
 *  - Traveller ID (`travellerId`, "CI-T-26-27-001"): only candidates who have travelled; a separate series.
 *  - The CRM reference (`ref`, "CND-01092") stays, shown second and muted, so references people already know still work.
 *
 * Every field is optional and may be null (an older server, or a candidate that has not travelled), so nothing here throws.
 */

export interface CandidateIds {
  ref?: string | null
  careId?: string | null
  travellerId?: string | null
}

const clean = (value: unknown): string => (typeof value === 'string' ? value.trim() : '')

export const careIdOf = (candidate: CandidateIds | null | undefined): string => clean(candidate?.careId)
export const travellerIdOf = (candidate: CandidateIds | null | undefined): string => clean(candidate?.travellerId)

/** The Care India ID when there is one, else the CRM reference. */
export const primaryId = (candidate: CandidateIds | null | undefined): string => careIdOf(candidate) || clean(candidate?.ref)

/** The CRM reference as the quieter second identifier: empty when it is the primary one already, missing, or the same text. */
export function secondaryId(candidate: CandidateIds | null | undefined): string {
  const care = careIdOf(candidate)
  const ref = clean(candidate?.ref)
  return care && ref && ref.toLowerCase() !== care.toLowerCase() ? ref : ''
}

/** "Traveller ID CI-T-26-27-001"; empty for a candidate who has not travelled. */
export const travellerLabel = (candidate: CandidateIds | null | undefined): string => {
  const id = travellerIdOf(candidate)
  return id ? `Traveller ID ${id}` : ''
}

/** "CI-26-27-001 · CND-01092" (or just the reference before the Care India ID exists): plain text for tooltips and pickers. */
export const idLine = (candidate: CandidateIds | null | undefined): string =>
  [primaryId(candidate), secondaryId(candidate)].filter(Boolean).join(' · ')

/** Every identifier on one string, for a search haystack. */
export const idSearchText = (candidate: CandidateIds | null | undefined): string =>
  [careIdOf(candidate), travellerIdOf(candidate), clean(candidate?.ref)].filter(Boolean).join(' ')

/** True when the (already lower-cased) search text appears in the Care India ID, Traveller ID or CRM reference. */
export const matchesIdText = (candidate: CandidateIds | null | undefined, text: string): boolean =>
  Boolean(text) && idSearchText(candidate).toLowerCase().includes(text.toLowerCase())

/** The paper form's header line: "Care India ID CI-26-27-001 · Reference CND-01092 · Traveller ID CI-T-26-27-001". */
export function paperIdLine(candidate: CandidateIds | null | undefined, readable: (reference: string) => string = (reference) => reference): string {
  const ref = readable(careIdOf(candidate) ? secondaryId(candidate) : clean(candidate?.ref))
  return [careIdOf(candidate) && `Care India ID ${careIdOf(candidate)}`, ref && `Reference ${ref}`, travellerLabel(candidate)].filter(Boolean).join(' · ')
}
