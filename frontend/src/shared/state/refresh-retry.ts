/**
 * Silent retries for "the workspace snapshot has not caught up with a write yet".
 *
 * Right after the client and job are saved, or a stage is completed, the server has the new placement but the browser's workspace
 * snapshot is still the old one for a moment (or its re-read failed once). That is never the person's problem to solve with a
 * "Refresh" button: the screen shows "Loading…" and re-reads by itself, quickly at first (0.3, 0.6, 1.2, 2.4 s), then every 5 s,
 * and only says something after GIVE_UP_MS. No imports: this file is unit-tested on its own with an injected clock.
 */
export const REFRESH_DELAYS_MS: readonly number[] = [300, 600, 1200, 2400]
export const REFRESH_STEADY_MS = 5000
export const REFRESH_GIVE_UP_MS = 30_000
/** After a read finishes the screen needs a moment to commit the new snapshot before it can be asked "is it there now?". */
export const REFRESH_SETTLE_MS = 60

/** The pause before retry number `attempt` (0 = first retry), or null once `elapsedMs` has reached the give-up time. */
export function nextRefreshDelay(attempt: number, elapsedMs: number): number | null {
  if (elapsedMs >= REFRESH_GIVE_UP_MS) return null
  const wanted = attempt < REFRESH_DELAYS_MS.length ? REFRESH_DELAYS_MS[Math.max(0, attempt)] : REFRESH_STEADY_MS
  return Math.min(wanted, REFRESH_GIVE_UP_MS - elapsedMs)
}

export interface RefreshLoop {
  /** One re-read of the workspace (a failure is swallowed and retried). */
  read: () => Promise<unknown>
  /** True once the thing being waited for is on screen. Asked before every wait and after every read. */
  done: () => boolean
  /** True when the screen no longer cares (it closed or changed): the loop stops at once. */
  cancelled?: () => boolean
  sleep?: (ms: number) => Promise<void>
  now?: () => number
}

const realSleep = (ms: number) => new Promise<void>((resolve) => { setTimeout(resolve, ms) })

/** Re-reads until `done()` is true: 'found', or 'gave_up' after REFRESH_GIVE_UP_MS, or 'cancelled'. Never throws. */
export async function refreshUntil(loop: RefreshLoop): Promise<'found' | 'gave_up' | 'cancelled'> {
  const sleep = loop.sleep ?? realSleep
  const now = loop.now ?? (() => Date.now())
  const started = now()
  let attempt = 0
  for (;;) {
    if (loop.cancelled?.()) return 'cancelled'
    if (loop.done()) return 'found'
    const delay = nextRefreshDelay(attempt, now() - started)
    if (delay === null) return 'gave_up'
    await sleep(delay)
    if (loop.cancelled?.()) return 'cancelled'
    if (loop.done()) return 'found'
    try { await loop.read() } catch { /* the next round tries again */ }
    await sleep(REFRESH_SETTLE_MS)
    attempt += 1
  }
}

/** Reads now; if that fails, retries with the same backoff until it works or the give-up time passes. True when a read succeeded. Never throws. */
export async function refreshSilently(read: () => Promise<unknown>, options: { cancelled?: () => boolean; sleep?: (ms: number) => Promise<void>; now?: () => number } = {}): Promise<boolean> {
  let ok = false
  const sleep = options.sleep ?? realSleep
  const now = options.now ?? (() => Date.now())
  const started = now()
  let attempt = 0
  for (;;) {
    if (options.cancelled?.()) return false
    try { await read(); ok = true } catch { ok = false }
    if (ok) return true
    const delay = nextRefreshDelay(attempt, now() - started)
    if (delay === null) return false
    await sleep(delay)
    attempt += 1
  }
}
