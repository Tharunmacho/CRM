/** A 409 can be a rejected window/template OR a committed duplicate request.
 * Inspect the exact durable key before unlocking the composer. Network failures
 * remain uncertain; a 404 alone never proves that a timed-out write was rejected.
 */
export async function classifySendFailure(
  status: number | undefined,
  lookup: () => Promise<unknown>,
  current: () => boolean,
): Promise<'recorded' | 'rejected' | 'uncertain' | 'stale'> {
  if (!current()) return 'stale'
  if ([400, 401, 403, 404, 413, 415, 422].includes(status ?? 0)) return 'rejected'
  if (status !== 409) return 'uncertain'
  try {
    await lookup()
    return current() ? 'recorded' : 'stale'
  } catch (error) {
    if (!current()) return 'stale'
    return typeof error === 'object' && error !== null && 'status' in error && error.status === 404 ? 'rejected' : 'uncertain'
  }
}
