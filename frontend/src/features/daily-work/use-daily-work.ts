import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { api } from '@/shared/api/client'
import { createFeed, type Feed, type FeedState } from '@/shared/lib/feed'
import { liveInterval } from '@/shared/state/live'
import { useStore } from '@/shared/state/crm-store'
import { listRequest, type DailyView, type DailyWorkResponse, type FollowUpResponse } from './daily-work-model'

interface Options {
  /** Supervisors only: one recruiter id, or "all". Recruiters always get their own list. */
  staff?: string
  /** The status view of `/daily-work` (Declined, Rejected, Travelled, All); the current process is the default and sends nothing. */
  view?: DailyView
  /** Skip the request (for example for a role that has no list). */
  enabled?: boolean
  /** Kept so callers still compile; all watchers of one list share one live subscription with a single window. */
  debounceMs?: number
}

/**
 * The Daily work list. Loads on mount and whenever the server announces a task, candidate or application change, plus a
 * safety poll. There are no deadlines, so there is nothing to re-read at a given moment.
 *
 * One list is read once however many components show it (the sidebar badge, the tab strip and the screen all watch
 * the same feed): one request per page load, one live subscription, and one refetch per change. The answer is kept
 * when the screen closes, so coming back shows it at once and re-reads it quietly.
 */
const CACHE = { ttlMs: 3_000, tags: ['tasks', 'users', 'candidates', 'applications'] } as const
const TOPICS = ['tasks', 'users', 'candidates', 'applications'] as const
/** A burst of changes becomes one read, this long after the first. */
const LIVE_WINDOW_MS = 1_500

/** Today's candidates (`/daily-work`). */
export const useDailyWork = (options: Options = {}) => useRecruiterList<DailyWorkResponse>('/daily-work', options)

/** Follow-up (`/follow-up`): the same scoping, live topics and caching. A stage finished or an application closed moves a candidate between the two lists. */
export const useFollowUp = (options: Options = {}) => useRecruiterList<FollowUpResponse>('/follow-up', options)

const made = new Map<string, Feed<unknown>>()

/** One feed per signed-in person and request, so a list is never shown to anyone but the person it was read for. */
function feedFor<T>(sessionId: string, request: string): Feed<T> {
  const key = `${sessionId}|${request}`
  let feed = made.get(key)
  if (!feed) {
    feed = createFeed<unknown>({
      failure: 'Your candidates could not be loaded.',
      load: (signal, fresh) => api.get<unknown>(request, { signal, cache: CACHE, fresh }),
      attach: (reload) => liveInterval(
        () => reload(),
        15_000,
        TOPICS,
        { debounceMs: LIVE_WINDOW_MS, safetyMs: 20_000, onChange: () => { void reload({ afterChange: true }) } },
      ),
    })
    made.set(key, feed)
  }
  return feed as Feed<T>
}

const IDLE: FeedState<never> = { data: null, error: '', loading: false }
const noSubscribe = () => () => {}
const idle = () => IDLE

function useRecruiterList<T>(path: string, { staff, view, enabled = true }: Options) {
  const sessionId = useStore().me?.id ?? ''
  const request = listRequest(path, staff, view)
  const feed = enabled ? feedFor<T>(sessionId, request) : null
  const state = useSyncExternalStore(feed ? feed.subscribe : noSubscribe, feed ? feed.getState : (idle as () => FeedState<T>), feed ? feed.getState : (idle as () => FeedState<T>))
  const refresh = useCallback(() => (feed ? feed.reload({ fresh: true }) : Promise.resolve()), [feed])
  return { data: state.data, error: state.error, loading: enabled && state.loading, refresh }
}

/** A clock that ticks every `intervalMs` while the tab is visible (the Lead sheet's banner counts down with it). */
export function useTicker(intervalMs = 1000, enabled = true) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!enabled) return
    const tick = () => { if (document.visibilityState !== 'hidden') setNow(Date.now()) }
    tick()
    const timer = window.setInterval(tick, intervalMs)
    document.addEventListener('visibilitychange', tick)
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', tick) }
  }, [intervalMs, enabled])
  return now
}
