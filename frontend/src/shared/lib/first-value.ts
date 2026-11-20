/**
 * "The first value does not count": a read that starts at mount cannot know what a second read of other data (the
 * summary, the filter choices) is about to say, and when that second read lands its value would otherwise look like
 * a change and trigger a repeat of the first read. This hides the first non-empty value (reporting it as '') so the
 * repeat only happens when the value later really changes. `resetKey` (the signed-in person, say) starts over.
 */
export function createFirstValueFilter() {
  let key: string | null = null
  let first: string | null = null
  return (value: string, resetKey = ''): string => {
    if (key !== resetKey) { key = resetKey; first = null }
    if (value !== '' && first === null) first = value
    return value !== '' && value === first ? '' : value
  }
}
