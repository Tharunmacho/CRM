/**
 * A polling timer that behaves: it does nothing while the tab is hidden (and catches up once when it is shown
 * again), waits longer after failures (doubling up to `maxBackoff` times the normal interval) and goes back to the
 * normal pace after the first success. `run` may return a promise; a rejection counts as a failure.
 *
 * Pure: the clock, visibility and timers are injectable so the rules are testable without a browser.
 */
export interface PollerOptions {
  run: () => unknown
  /** The normal interval right now; read again before every wait (it changes when the live stream goes up or down). */
  intervalMs: () => number
  isVisible?: () => boolean
  maxBackoff?: number
  /** Longest wait caused by backoff (the normal interval wins if it is longer). Default five minutes. */
  maxWaitMs?: number
  setTimeout?: (callback: () => void, ms: number) => unknown
  clearTimeout?: (handle: unknown) => void
}

export interface Poller {
  start(): void
  stop(): void
  /** Re-arm with the current interval (after the live stream changed state). */
  rearm(): void
  /** The tab became visible (or focus returned): run once now if a tick was skipped while hidden. */
  shown(): void
  /** Current multiplier (1 when healthy); for tests and diagnostics. */
  backoff(): number
}

export function createPoller(options: PollerOptions): Poller {
  const schedule = options.setTimeout ?? ((callback, ms) => globalThis.setTimeout(callback, ms))
  const clear = options.clearTimeout ?? ((handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>))
  const visible = options.isVisible ?? (() => typeof document === 'undefined' || document.visibilityState !== 'hidden')
  const maxBackoff = options.maxBackoff ?? 8
  let timer: unknown
  let running = false
  let missed = false
  let factor = 1

  function wait() {
    const base = Math.max(250, options.intervalMs())
    return Math.max(base, Math.min(options.maxWaitMs ?? 300_000, base * factor))
  }

  function arm() {
    if (!running) return
    if (timer !== undefined) clear(timer)
    timer = schedule(tick, wait())
  }

  function succeeded() { factor = 1 }
  function failed() { factor = Math.min(maxBackoff, factor * 2) }

  function execute() {
    let result: unknown
    try { result = options.run() } catch { failed(); return }
    if (result && typeof (result as Promise<unknown>).then === 'function') {
      void (result as Promise<unknown>).then(succeeded, () => { failed(); arm() })
    }
  }

  function tick() {
    timer = undefined
    if (!running) return
    if (!visible()) {
      missed = true // nothing to refresh for nobody; shown() catches up
    } else {
      missed = false
      execute()
    }
    arm()
  }

  return {
    start() { if (running) return; running = true; arm() },
    stop() { running = false; missed = false; if (timer !== undefined) clear(timer); timer = undefined },
    rearm: arm,
    shown() {
      if (!running || !missed || !visible()) return
      missed = false
      factor = 1
      execute()
      arm()
    },
    backoff: () => factor,
  }
}
