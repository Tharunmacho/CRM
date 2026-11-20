/**
 * One shared copy of a list that several components show (the daily list in the sidebar badge, the tab strip and
 * the screen). Each `useX()` call used to own its own fetch, its own live subscription and its own timer, so one
 * page load asked three times and one change on the desk caused three refetches at three different moments.
 *
 * A feed holds the newest answer, loads it once however many components watch it, keeps it after the last one has
 * gone (so coming back to the page shows it at once and re-reads quietly: `loading` is only true while there is
 * nothing to show), and tells the live layer to attach only while somebody watches.
 *
 * Pure: loading and the live attachment are injected, so node tests it with a fake `load`.
 */
export interface FeedState<T> {
  data: T | null
  error: string
  /** True only until the first answer or failure arrives; a re-read of data already on screen is silent. */
  loading: boolean
}

export interface FeedOptions<T> {
  load: (signal: AbortSignal, fresh: boolean) => Promise<T>
  /**
   * Called when the first watcher arrives; returns the cleanup for when the last one leaves. `reload` re-reads now:
   * pass `{ afterChange: true }` for a live change (a read already running began before the change, so one more follows).
   */
  attach?: (reload: (options?: { afterChange?: boolean }) => Promise<void>) => () => void
  failure?: string
}

export interface Feed<T> {
  getState(): FeedState<T>
  subscribe(listener: () => void): () => void
  /** Re-read now. `fresh` skips the API client's short copies (a Refresh button). Never rejects. */
  reload(options?: { afterChange?: boolean; fresh?: boolean }): Promise<void>
  watchers(): number
}

export function createFeed<T>(options: FeedOptions<T>): Feed<T> {
  let state: FeedState<T> = { data: null, error: '', loading: true }
  const listeners = new Set<() => void>()
  let detach: (() => void) | null = null
  let running: Promise<void> | null = null
  let controller: AbortController | null = null
  let again = false
  let againFresh = false

  const set = (next: FeedState<T>) => {
    if (next.data === state.data && next.error === state.error && next.loading === state.loading) return
    state = next
    for (const listener of [...listeners]) listener()
  }

  async function once(fresh: boolean) {
    const mine = new AbortController()
    controller = mine
    try {
      const data = await options.load(mine.signal, fresh)
      if (mine.signal.aborted) return
      set({ data, error: '', loading: false })
    } catch (caught) {
      if (mine.signal.aborted) return
      set({ data: state.data, error: caught instanceof Error ? caught.message : options.failure ?? 'This list could not be loaded.', loading: false })
    } finally {
      if (controller === mine) controller = null
    }
  }

  function reload(request: { afterChange?: boolean; fresh?: boolean } = {}) {
    if (running) {
      if (request.afterChange || request.fresh) { again = true; againFresh = againFresh || !!request.fresh }
      return running
    }
    const first = once(!!request.fresh)
    const loop: Promise<void> = first.then(async () => {
      while (again && listeners.size > 0) {
        const fresh = againFresh
        again = false; againFresh = false
        await once(fresh)
      }
      again = false; againFresh = false
    }).finally(() => { if (running === loop) running = null })
    running = loop
    return loop
  }

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener)
      if (listeners.size === 1) {
        detach = options.attach?.(reload) ?? null
        // Data already held is shown as it is and re-read quietly; with none, the first read starts now.
        void reload()
      }
      return () => {
        if (!listeners.delete(listener)) return
        if (listeners.size === 0) {
          detach?.(); detach = null
          controller?.abort() // nobody is waiting for it; the next watcher starts again
          controller = null; running = null; again = false; againFresh = false
        }
      }
    },
    reload,
    watchers: () => listeners.size,
  }
}
