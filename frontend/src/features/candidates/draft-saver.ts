/**
 * Autosave of the Add candidate draft, kept off the typing path.
 *
 * Against a hosted database every save is a round trip, and the form changes on every keystroke. So:
 *
 *  - `queue(draft)` (typing) plans a request `settle` ms (0.8 s) after the first change of a burst and never sends more than ONE
 *    request every `interval` ms (2.5 s); it always sends the latest values;
 *  - `flush()` (leaving a field, hiding the tab, reading, saving the candidate) sends now, and resolves once the latest
 *    values are saved;
 *  - a request is never sent while another is in flight: whatever changed meanwhile is coalesced into ONE follow-up that
 *    carries the latest values (the old chain sent every stale intermediate draft in turn);
 *  - a draft equal to the last one the server confirmed is not sent at all (compared by content, key order ignored).
 *
 * It never blocks the form: `queue` returns at once, and a failed save leaves the draft pending so that the next
 * `queue` or `flush` tries again.
 */

export const DRAFT_INTERVAL_MS = 2500
/** After a change the request is planned this long ahead, so a burst of keystrokes becomes one request. */
export const DRAFT_SETTLE_MS = 800

export type DraftSaverOptions<T> = {
  /** Sends the draft (the caller adds anything that must be fresh at send time, such as the document ids). */
  send: (draft: T) => Promise<unknown>
  interval?: number
  settle?: number
  now?: () => number
  setTimer?: (callback: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
  /** Called with the outcome of every attempt: null when saved, the error when it failed. */
  onResult?: (error: unknown | null) => void
}

/** JSON with sorted object keys: the same content always gives the same text. */
export function stableKey(value: unknown): string {
  return JSON.stringify(value, (_key, item) => {
    if (item && typeof item === 'object' && !Array.isArray(item)) {
      return Object.fromEntries(Object.entries(item as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
    }
    return item
  })
}

export function createDraftSaver<T>(options: DraftSaverOptions<T>) {
  const interval = options.interval ?? DRAFT_INTERVAL_MS
  const settle = options.settle ?? DRAFT_SETTLE_MS
  const now = options.now ?? (() => Date.now())
  const setTimer = options.setTimer ?? ((callback, ms) => setTimeout(callback, ms))
  const clearTimer = options.clearTimer ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>))

  let latest: T | undefined
  let latestKey: string | undefined
  let savedKey: string | undefined
  let lastSentAt = -Infinity
  let inflight: Promise<void> | null = null
  let timer: unknown = null
  let stopped = false

  const pending = () => latestKey !== undefined && latestKey !== savedKey

  const cancelTimer = () => {
    if (timer !== null) { clearTimer(timer); timer = null }
  }

  async function sendNow(): Promise<void> {
    cancelTimer()
    if (!pending() || stopped) return
    const draft = latest as T
    const key = latestKey as string
    lastSentAt = now()
    const attempt = (async () => {
      try {
        await options.send(draft)
        savedKey = key
        options.onResult?.(null)
      } catch (error) {
        options.onResult?.(error)
        throw error
      }
    })()
    inflight = attempt.catch(() => {})
    try { await attempt } finally { inflight = null }
    // Values typed while that request was out go in ONE more request, no sooner than `interval` after it started.
    if (pending() && !stopped) schedule()
  }

  function schedule() {
    if (timer !== null || inflight || stopped) return
    const wait = Math.max(settle, lastSentAt + interval - now())
    timer = setTimer(() => { timer = null; void sendNow().catch(() => {}) }, wait)
  }

  return {
    /** Typing: remember the latest values and make sure one request is planned. Never throws, never waits. */
    queue(draft: T) {
      latest = draft
      latestKey = stableKey(draft)
      if (pending()) schedule()
      else cancelTimer()
    },
    /** Send now (waiting for a request already out) and resolve when the latest values are saved. Rejects if saving failed. */
    async flush(draft?: T): Promise<void> {
      if (draft !== undefined) { latest = draft; latestKey = stableKey(draft) }
      if (stopped) return
      for (let round = 0; round < 3; round += 1) {
        if (inflight) await inflight
        if (!pending()) return
        await sendNow()
      }
    },
    /** Latest values not yet confirmed by the server. */
    hasPending: pending,
    /** Forget what the server holds (a new intake starts): the next draft is always sent. */
    reset() { cancelTimer(); latest = undefined; latestKey = undefined; savedKey = undefined; lastSentAt = -Infinity },
    stop() { stopped = true; cancelTimer() },
  }
}
