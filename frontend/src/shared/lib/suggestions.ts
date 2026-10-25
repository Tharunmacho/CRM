/**
 * Suggestions come from what is already saved, never from a list in code: distinct values
 * (case-insensitive, first spelling seen wins), most used first, then alphabetical.
 */
export function frequentValues(values: Iterable<string | null | undefined>, limit?: number): string[] {
  const counts = new Map<string, { text: string; count: number }>()
  for (const raw of values) {
    const text = (raw ?? '').trim()
    if (!text) continue
    const key = text.toLowerCase()
    const entry = counts.get(key)
    if (entry) entry.count += 1
    else counts.set(key, { text, count: 1 })
  }
  const sorted = [...counts.values()].sort((a, b) => b.count - a.count || a.text.localeCompare(b.text)).map((entry) => entry.text)
  return limit === undefined ? sorted : sorted.slice(0, limit)
}
