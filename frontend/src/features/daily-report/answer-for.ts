/**
 * What a live hook may show for the choice on screen. An answer carries the choice it was read for (`key`); when the page's choice has since
 * changed (the agency day rolled over at midnight, another month or person was picked) the old answer is NOT shown for the new choice, not
 * even for the one render before the new read starts: the screen says it is loading instead of showing yesterday's numbers as today's.
 * Pure, so it can be tested on its own.
 */
export function answerFor<T extends { key: string }>(held: T, key: string, empty: Omit<T, 'key'>): Omit<T, 'key'> {
  if (held.key !== key) return empty
  const { key: _held, ...answer } = held
  return answer
}
