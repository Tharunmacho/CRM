interface IngestionRefreshOptions {
  currentRevision: () => string | undefined
  readRevision: (signal: AbortSignal) => Promise<{ revision?: string }>
  refresh: () => Promise<void>
  isCurrentSession: () => boolean
}

/** Compare against the revision loaded with the workspace, never a poll baseline. */
export function createIngestionRefresh(options: IngestionRefreshOptions) {
  let active = true
  let pending: Promise<void> | null = null
  let controller: AbortController | null = null

  function check(): Promise<void> {
    if (!active || !options.isCurrentSession()) return Promise.resolve()
    if (pending) return pending
    const request = new AbortController()
    controller = request
    const task = Promise.resolve()
      .then(() => options.readRevision(request.signal))
      .then(async (snapshot) => {
        if (!active || request.signal.aborted || !options.isCurrentSession()) return
        const current = options.currentRevision()
        // Older deployments may not supply either token. Do not repeatedly
        // reload their whole workspace or turn that compatibility gap into logout.
        if (typeof current !== 'string' || typeof snapshot?.revision !== 'string') return
        if (snapshot.revision === current) return
        // Keep the guard through the refresh. The applied bootstrap supplies the
        // next token; errors/aborted reads never mark a revision as handled.
        await options.refresh()
      })
      .finally(() => {
        if (pending === task) {
          pending = null
          controller = null
        }
      })
    pending = task
    return task
  }

  return {
    check,
    dispose() {
      active = false
      controller?.abort()
    },
  }
}
