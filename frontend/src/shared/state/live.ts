/**
 * The app's single live-update connection and the small helpers screens use to follow it.
 *
 * `live` is opened by the store after sign-in and closed at sign-out (crm-store.tsx). Screens never open their
 * own stream; they call `liveInterval` (replacing a `setInterval` poll) or `useLiveStatus`.
 */
import { useSyncExternalStore } from 'react'
import { apiIsCrossOrigin, dropSharedReads, eventsHref } from '@/shared/api/client'
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
  beforeDispatch: (_change: LiveChange) => {
    dropSharedReads() // a refetch must not join a read that began before the commit
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
 */
export function liveInterval(
  run: () => void,
  intervalMs: number,
  topics: readonly string[] | '*',
  options: { debounceMs?: number; safetyMs?: number; onChange?: () => void } = {},
): () => void {
  let timer: ReturnType<typeof setInterval> | undefined
  const arm = () => {
    if (timer !== undefined) clearInterval(timer)
    timer = setInterval(run, live.isUp() ? Math.max(options.safetyMs ?? LIVE_SAFETY_POLL_MS, intervalMs) : intervalMs)
  }
  const stopStatus = live.onStatus(arm)
  const stopTopics = live.subscribe(topics, () => (options.onChange ?? run)(), { debounceMs: options.debounceMs })
  arm()
  return () => {
    stopTopics()
    stopStatus()
    if (timer !== undefined) clearInterval(timer)
  }
}

const subscribeStatus = (listener: () => void) => live.onStatus(listener)

/** Re-renders when the stream goes up or down. */
export function useLiveStatus(): LiveStatus {
  return useSyncExternalStore(subscribeStatus, live.status, live.status)
}
