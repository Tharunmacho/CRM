/**
 * Several triggers close together become one call.
 *
 * `trailing` (default): runs once, `waitMs` after the last trigger, but never later than `maxWaitMs` after the first
 * (so a steady stream of live events cannot postpone the refetch for ever). `leading: true` runs at once for the
 * first trigger of a burst and once more at the end if more arrived. `flush()` runs a pending call now, `cancel()`
 * drops it. Pure: timers are injectable so tests do not wait.
 */
export interface CoalescerOptions {
  waitMs: number
  maxWaitMs?: number
  leading?: boolean
  setTimeout?: (callback: () => void, ms: number) => unknown
  clearTimeout?: (handle: unknown) => void
  now?: () => number
}

export interface Coalescer {
  (): void
  flush(): void
  cancel(): void
  pending(): boolean
}

export function createCoalescer(run: () => void, options: CoalescerOptions): Coalescer {
  const schedule = options.setTimeout ?? ((callback, ms) => globalThis.setTimeout(callback, ms))
  const clear = options.clearTimeout ?? ((handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>))
  const now = options.now ?? (() => Date.now())
  let timer: unknown
  let burstStart = 0
  let inBurst = false
  let queued = false

  const fire = () => {
    timer = undefined
    if (!queued) return
    queued = false
    inBurst = false
    run()
  }

  const trigger = (() => {
    const at = now()
    if (!inBurst) {
      inBurst = true
      burstStart = at
      if (options.leading) {
        run()
        timer = schedule(() => { timer = undefined; inBurst = false; if (queued) { queued = false; run() } }, options.waitMs)
        return
      }
    }
    queued = true
    if (timer !== undefined) clear(timer)
    const wait = options.maxWaitMs === undefined ? options.waitMs : Math.max(0, Math.min(options.waitMs, burstStart + options.maxWaitMs - at))
    timer = schedule(fire, wait)
  }) as Coalescer

  trigger.flush = () => {
    if (timer !== undefined) clear(timer)
    if (queued) fire()
    else timer = undefined
  }
  trigger.cancel = () => {
    if (timer !== undefined) clear(timer)
    timer = undefined
    queued = false
    inBurst = false
  }
  trigger.pending = () => queued
  return trigger
}
