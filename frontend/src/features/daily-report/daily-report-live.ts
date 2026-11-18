/**
 * The pure parts of keeping the report live: coalescing a burst of change events into one refetch, the "Updated 12 s ago"
 * text, and which table cells changed between two answers (so they can flash). No React, no imports: node tests it.
 */

/** The topics whose change can move a number: leads and their calls, candidates, applications and stages, tasks, staff. */
export const LIVE_TOPICS: readonly string[] = ['leads', 'candidates', 'applications', 'tasks', 'users', 'reports', 'daily_report']
/** A burst of events becomes one read, this long after the first. */
export const COALESCE_MS = 1_500
/** While the live stream is down the report asks again this often; while it is up this is only a safety net. */
export const POLL_DOWN_MS = 30_000
export const POLL_SAFETY_MS = 60_000
/** The least time between two reads caused by the window or tab getting focus back. */
export const FOCUS_GAP_MS = 5_000

export interface Coalescer {
  /** Something changed: read once, `delayMs` after the first call of a burst. During a read it asks for exactly one more. */
  trigger(): void
  /** Stop for good (the page closed); nothing fires afterwards. */
  cancel(): void
  /** A read is waiting for its window or for the running read to finish. */
  pending(): boolean
}

export function createCoalescer(
  run: () => Promise<unknown> | void,
  options: { delayMs?: number; setTimer?: (callback: () => void, ms: number) => unknown; clearTimer?: (handle: unknown) => void } = {},
): Coalescer {
  const delay = options.delayMs ?? COALESCE_MS
  const setTimer = options.setTimer ?? ((callback, ms) => setTimeout(callback, ms))
  const clearTimer = options.clearTimer ?? (handle => clearTimeout(handle as ReturnType<typeof setTimeout>))
  let timer: unknown = null
  let running = false
  let again = false
  let stopped = false
  const schedule = () => { if (timer == null && !stopped) timer = setTimer(fire, delay) }
  async function fire() {
    timer = null
    if (stopped) return
    running = true
    try { await run() } catch { /* the screen shows its own failure */ } finally {
      running = false
      if (again && !stopped) { again = false; schedule() }
    }
  }
  return {
    trigger() { if (stopped) return; if (running) again = true; else schedule() },
    cancel() { stopped = true; again = false; if (timer != null) clearTimer(timer); timer = null },
    pending: () => timer != null || again,
  }
}

/** "just now", "12 s ago", "3 min ago", "2 h ago": how long since the numbers were read. */
export function agoText(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000))
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds} s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  return `${Math.floor(minutes / 60)} h ago`
}

/** Keys of the cells whose number differs from the last answer. A cell that was not there before is not news. */
export function changedCells(previous: Record<string, number | null> | null, next: Record<string, number | null>): string[] {
  if (!previous) return []
  return Object.keys(next).filter(key => key in previous && previous[key] !== next[key])
}
