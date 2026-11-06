/**
 * The app's single live-update connection and the small helpers screens use to follow it.
 *
 * `live` is opened by the store after sign-in and closed at sign-out (crm-store.tsx). Screens never open their
 * own stream; they call `liveInterval` (replacing a `setInterval` poll) or `useLiveStatus`.
 */
import { useSyncExternalStore } from 'react'
import { apiIsCrossOrigin, eventsHref, invalidateReads } from '@/shared/api/client'
import { createPoller } from '@/shared/lib/poll'
import { createLiveEvents, type EventSourceLike, type LiveChange, type LiveEvents, type LiveStatus } from './live-events'
import { forgetSnapshots } from './snapshot-cache'

/**
 * While the stream is up, every screen's poll slows to this safety net (a missed event, or a change made by
 * something that does not announce itself, is then caught within two minutes). While it is down, each screen
 * polls at its own, original interval.
 */
export const LIVE_SAFETY_POLL_MS = 120_000

export const live: LiveEvents = createLiveEvents({
  url: eventsHref(),
  createSource: (url) => new EventSource(url, { withCredentials: apiIsCrossOrigin }) as unknown as EventSourceLike,
  beforeDispatch: (change: LiveChange) => {
    // A refetch must not join a read that began before the commit, nor be answered from a cached copy of what changed.
    invalidateReads(change.resync ? undefined : change.topics)
    forgetSnapshots() // the remembered workspace/dashboard is stale; it is saved again after the refetch
  },
})

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') live.flushHeld() })
}

/**
 * Keep a screen current: `run` whenever one of `topics` changes (coalesced; or `onChange` instead, for a screen whose
 * timer tick has its own rate limit that a change must bypass), and on a timer that is `intervalMs` while the stream
 * is down and LIVE_SAFETY_POLL_MS while it is up. Returns the cleanup. Drop-in for `setInterval(run, ms)`.
 *
 * The timer pauses while the tab is hidden and catches up once when it is shown again, and backs off (up to 8x)
 * while `run` keeps returning a rejected promise, so a server that is down is not asked every few seconds by every
 * open widget. Live events are not paused here: the stream itself holds them back while the tab is hidden.
 */
export function liveInterval(
  run: () => void | Promise<unknown>,
  intervalMs: number | (() => number),
  topics: readonly string[] | '*',
  options: { debounceMs?: number; safetyMs?: number; onChange?: (change: LiveChange) => void } = {},
): () => void {
  const poller = createPoller({
    run,
    intervalMs: () => {
      const own = typeof intervalMs === 'function' ? intervalMs() : intervalMs
      return live.isUp() ? Math.max(options.safetyMs ?? LIVE_SAFETY_POLL_MS, own) : own
    },
  })
  const stopStatus = live.onStatus(() => poller.rearm())
  const stopTopics = live.subscribe(topics, (change) => {
    if (options.onChange) return options.onChange(change)
    const result = run()
    if (result && typeof (result as Promise<unknown>).then === 'function') (result as Promise<unknown>).then(undefined, () => { /* the screen shows its own failure */ })
  }, { debounceMs: options.debounceMs })
  const onVisible = () => { if (document.visibilityState === 'visible') poller.shown() }
  document.addEventListener('visibilitychange', onVisible)
  poller.start()
  return () => {
    stopTopics()
    stopStatus()
    document.removeEventListener('visibilitychange', onVisible)
    poller.stop()
  }
}

const subscribeStatus = (listener: () => void) => live.onStatus(listener)

/** Re-renders when the stream goes up or down. */
export function useLiveStatus(): LiveStatus {
  return useSyncExternalStore(subscribeStatus, live.status, live.status)
}
