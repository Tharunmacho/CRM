/**
 * Country names for the job dialog. Pure functions (the name list and the country-code lookup are passed in) so they are unit tested.
 * A country typed in any case or a common short form ("KUWAIT", "uae") is kept as the proper name ("Kuwait", "United Arab Emirates").
 */
import { foldText } from '../clients/client-search'

type CodeOf = (country: string | null | undefined) => string | null

const tidy = (value: string | null | undefined) => (value ?? '').replace(/\s+/g, ' ').trim()

/** The proper spelling of a typed country: the list's own name when it is one (any case or accent) or a known short form of one. Anything else stays as typed, tidied. */
export function canonicalCountry(typed: string | null | undefined, known: readonly string[], codeOf: CodeOf): string {
  const text = tidy(typed)
  if (!text) return ''
  const key = foldText(text)
  const exact = known.find((name) => foldText(name) === key)
  if (exact) return exact
  const code = codeOf(text)
  if (code) {
    const named = known.find((name) => codeOf(name) === code)
    if (named) return named
  }
  return text
}

/**
 * Every country, ready for a list: the ones already used on clients, companies and jobs first (most used first, spelled properly), then
 * all the rest A to Z. Each name appears once.
 */
export function countryOptions(used: readonly string[], known: readonly string[], codeOf: CodeOf): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of used) {
    const name = canonicalCountry(raw, known, codeOf)
    const key = foldText(name)
    if (!name || seen.has(key)) continue
    seen.add(key)
    out.push(name)
  }
  for (const name of known) {
    const key = foldText(name)
    if (seen.has(key)) continue
    seen.add(key)
    out.push(name)
  }
  return out
}
