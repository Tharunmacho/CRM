/**
 * Live updates: one Server-Sent-Events stream (`GET /api/events`) for the whole app.
 *
 * The server announces *that* something changed (topic names, never data). Screens subscribe to the topics they
 * show and refetch through the ordinary, permission-checked API, so the stream cannot leak anything and a missed
 * event only ever costs a refetch.
 *
 *  - `start()` after sign-in opens the stream; `stop()` on sign-out closes it.
 *  - A dropped stream reconnects with exponential backoff. Anything announced while it was down is lost, so the
 *    first `hello` after an outage tells every subscriber to resynchronise (`resync: true`, topic `*`).
 *  - A silent connection (a proxy that swallows the heartbeat) is noticed by a watchdog and replaced.
 *  - Each subscription has its own coalescing window: a burst of events becomes one call, at most `debounceMs`
 *    after the first. Nothing is delivered to a hidden tab until it is visible again.
 *
 * This module imports nothing from the app so it can be unit-tested with a fake EventSource and fake timers.
 */

export type LiveStatus = 'off' | 'connecting' | 'up' | 'down'

export interface LiveChange {
  /** Topics that changed; `*` after a resynchronisation. */
  topics: string[]
  /** Opaque ids per topic (admin/manager only, and only when few). */
  ids: Record<string, string[]>
  /** Server clock (epoch ms) when the change was committed, if known. */
  at: number | null
  /** The stream was interrupted or the server fell behind: treat everything as possibly changed. */
  resync: boolean
}

export type LiveHandler = (change: LiveChange) => void

export interface EventSourceLike {
  readyState: number
  close(): void
  addEventListener(type: string, listener: (event: { data?: string }) => void): void
  onerror: ((event: unknown) => void) | null
}

export interface LiveEventsOptions {
  url: string
  createSource: (url: string) => EventSourceLike
  setTimeout?: (callback: () => void, ms: number) => unknown
  clearTimeout?: (handle: unknown) => void
  random?: () => number
  isVisible?: () => boolean
  /** Runs for every change before subscribers are told, e.g. to drop reads that began before the commit. */
  beforeDispatch?: (change: LiveChange) => void
}

export interface LiveEvents {
  start(): void
  stop(): void
  status(): LiveStatus
  isUp(): boolean
  onStatus(listener: (status: LiveStatus) => void): () => void
  subscribe(topics: readonly string[] | '*', handler: LiveHandler, options?: { debounceMs?: number }): () => void
  /** Deliver what was held back while the tab was hidden. Call when the tab becomes visible. */
  flushHeld(): void
}

export const DEFAULT_DEBOUNCE_MS = 350
const BACKOFF_START_MS = 1_000
const BACKOFF_MAX_MS = 60_000
const DEFAULT_HEARTBEAT_MS = 20_000

interface Subscription {
  topics: ReadonlySet<string> | '*'
  handler: LiveHandler
  debounceMs: number
  held: LiveChange | null
  timer: unknown
}

function parse(data: string | undefined): Record<string, unknown> | null {
  try {
    const value = data ? JSON.parse(data) : null
    return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
  } catch {
    return null
  }
}

function toChange(raw: Record<string, unknown>): LiveChange {
  const topics = Array.isArray(raw.topics) ? raw.topics.filter((topic): topic is string => typeof topic === 'string') : []
  const ids: Record<string, string[]> = {}
  if (raw.ids && typeof raw.ids === 'object') {
    for (const [topic, found] of Object.entries(raw.ids as Record<string, unknown>)) {
      if (Array.isArray(found)) ids[topic] = found.filter((id): id is string => typeof id === 'string')
    }
  }
  return { topics, ids, at: typeof raw.at === 'number' ? raw.at : null, resync: raw.resync === true || topics.includes('*') }
}

function merge(first: LiveChange | null, second: LiveChange): LiveChange {
  if (!first) return second
  const ids: Record<string, string[]> = { ...first.ids }
  for (const [topic, found] of Object.entries(second.ids)) ids[topic] = [...new Set([...(ids[topic] ?? []), ...found])]
  return {
    topics: [...new Set([...first.topics, ...second.topics])],
    ids: first.resync || second.resync ? {} : ids,
    at: Math.max(first.at ?? 0, second.at ?? 0) || null,
    resync: first.resync || second.resync,
  }
}

export function createLiveEvents(options: LiveEventsOptions): LiveEvents {
  const schedule = options.setTimeout ?? ((callback, ms) => globalThis.setTimeout(callback, ms))
  const cancel = options.clearTimeout ?? ((handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>))
  const random = options.random ?? Math.random
  const isVisible = options.isVisible ?? (() => typeof document === 'undefined' || document.visibilityState !== 'hidden')

  let wanted = false
  let source: EventSourceLike | null = null
  let state: LiveStatus = 'off'
  let attempts = 0
  let retryTimer: unknown
  let watchdog: unknown
  let heartbeatMs = DEFAULT_HEARTBEAT_MS
  let interrupted = false // the stream has been down since it last said hello
  const subscriptions = new Set<Subscription>()
  const statusListeners = new Set<(status: LiveStatus) => void>()

  function setStatus(next: LiveStatus) {
    if (state === next) return
    state = next
    for (const listener of [...statusListeners]) {
      try { listener(next) } catch { /* a listener must not break the stream */ }
    }
  }

  function flush(subscription: Subscription) {
    subscription.timer = undefined
    const change = subscription.held
    if (!change) return
    if (!isVisible()) return // held; flushHeld() delivers it when the tab is visible again
    subscription.held = null
    try { subscription.handler(change) } catch { /* a screen's refetch failing is its own problem */ }
  }

  function dispatch(change: LiveChange) {
    try { options.beforeDispatch?.(change) } catch { /* optional */ }
    for (const subscription of [...subscriptions]) {
      const relevant = change.resync || subscription.topics === '*' || change.topics.some((topic) => (subscription.topics as ReadonlySet<string>).has(topic))
      if (!relevant) continue
      const mine: LiveChange = subscription.topics === '*' || change.resync
        ? change
        : {
            ...change,
            topics: change.topics.filter((topic) => (subscription.topics as ReadonlySet<string>).has(topic)),
            ids: Object.fromEntries(Object.entries(change.ids).filter(([topic]) => (subscription.topics as ReadonlySet<string>).has(topic))),
          }
      subscription.held = merge(subscription.held, mine)
      if (subscription.timer === undefined) subscription.timer = schedule(() => flush(subscription), subscription.debounceMs)
    }
  }

  function armWatchdog() {
    if (watchdog !== undefined) cancel(watchdog)
    // Two missed heartbeats and a margin: the network is gone or something is buffering the stream.
    watchdog = schedule(() => { watchdog = undefined; lost() }, heartbeatMs * 2 + 5_000)
  }

  function closeSource() {
    if (watchdog !== undefined) { cancel(watchdog); watchdog = undefined }
    const current = source
    source = null
    if (current) {
      current.onerror = null
      try { current.close() } catch { /* already closed */ }
    }
  }

  function lost() {
    closeSource()
    if (!wanted) return
    interrupted = true
    setStatus('down')
    const base = Math.min(BACKOFF_MAX_MS, BACKOFF_START_MS * 2 ** attempts)
    attempts += 1
    const delay = Math.round(base * (0.5 + random() / 2))
    if (retryTimer !== undefined) cancel(retryTimer)
    retryTimer = schedule(() => { retryTimer = undefined; open() }, delay)
  }

  function open() {
    if (!wanted || source) return
    setStatus(state === 'down' ? 'down' : 'connecting')
    let opened: EventSourceLike
    try {
      opened = options.createSource(options.url)
    } catch {
      lost()
      return
    }
    source = opened
    opened.onerror = () => { if (source === opened) lost() }
    opened.addEventListener('hello', (event) => {
      if (source !== opened) return
      const hello = parse(event.data)
      if (typeof hello?.heartbeatMs === 'number' && hello.heartbeatMs > 0) heartbeatMs = hello.heartbeatMs
      attempts = 0
      armWatchdog()
      const recovered = interrupted
      interrupted = false
      setStatus('up')
      // Whatever was announced while the stream was down never arrived.
      if (recovered) dispatch({ topics: ['*'], ids: {}, at: null, resync: true })
    })
    opened.addEventListener('ping', () => { if (source === opened) armWatchdog() })
    opened.addEventListener('change', (event) => {
      if (source !== opened) return
      armWatchdog()
      const raw = parse(event.data)
      if (raw) dispatch(toChange(raw))
    })
    opened.addEventListener('bye', () => {
      if (source !== opened) return
      // Signed out elsewhere: let every screen refetch so the 401 reaches the session logic, then back off.
      dispatch({ topics: ['*'], ids: {}, at: null, resync: true })
      lost()
    })
  }

  return {
    start() {
      if (wanted) return
      wanted = true
      attempts = 0
      interrupted = false
      open()
    },
    stop() {
      wanted = false
      if (retryTimer !== undefined) { cancel(retryTimer); retryTimer = undefined }
      closeSource()
      for (const subscription of subscriptions) {
        if (subscription.timer !== undefined) cancel(subscription.timer)
        subscription.timer = undefined
        subscription.held = null
      }
      setStatus('off')
    },
    status: () => state,
    isUp: () => state === 'up',
    onStatus(listener) {
      statusListeners.add(listener)
      return () => { statusListeners.delete(listener) }
    },
    subscribe(topics, handler, subscribeOptions = {}) {
      const subscription: Subscription = {
        topics: topics === '*' ? '*' : new Set(topics),
        handler,
        debounceMs: subscribeOptions.debounceMs ?? DEFAULT_DEBOUNCE_MS,
        held: null,
        timer: undefined,
      }
      subscriptions.add(subscription)
      return () => {
        subscriptions.delete(subscription)
        if (subscription.timer !== undefined) cancel(subscription.timer)
        subscription.timer = undefined
        subscription.held = null
      }
    },
    flushHeld() {
      if (!isVisible()) return
      for (const subscription of [...subscriptions]) {
        if (subscription.held) {
          if (subscription.timer !== undefined) cancel(subscription.timer)
          flush(subscription)
        }
      }
    },
  }
}
