/** Match recorded skills without guessing a trade from a title or resume keyword. */
export function skillKey(value: string): string {
  return value.normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase()
}

export function normalizedSkills(values: readonly string[]): string[] {
  const seen = new Set<string>()
  return values.flatMap((value) => {
    const label = value.normalize('NFKC').trim().replace(/\s+/g, ' ')
    const key = skillKey(label)
    if (!key || seen.has(key)) return []
    seen.add(key)
    return [label]
  })
}

export function skillFacets(candidates: readonly { skills: string[] }[]) {
  const facets = new Map<string, { key: string; label: string; count: number }>()
  for (const candidate of candidates) {
    for (const label of normalizedSkills(candidate.skills)) {
      const key = skillKey(label)
      const facet = facets.get(key)
      if (facet) facet.count += 1
      else facets.set(key, { key, label, count: 1 })
    }
  }
  return [...facets.values()].sort((a, b) => a.label.localeCompare(b.label))
}

export function matchesSkills(
  candidate: { skills: string[] }, selected: readonly string[], mode: 'all' | 'any' = 'all',
): boolean {
  const wanted = normalizedSkills(selected).map(skillKey)
  if (!wanted.length) return true
  const recorded = new Set(candidate.skills.map(skillKey))
  return mode === 'all'
    ? wanted.every((key) => recorded.has(key))
    : wanted.some((key) => recorded.has(key))
}
