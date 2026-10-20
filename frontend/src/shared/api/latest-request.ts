/**
 * Share an in-flight read and discard responses from an older session or refresh.
 * Call invalidate before a write changes which snapshot is safe to display.
 */
export function createLatestRequest<T>() {
  let version = 0
  let scope = 0
  let pending: { controller: AbortController; promise: Promise<void> } | null = null
  let latest: Promise<void> | null = null

  function supersede() {
    version += 1
    pending?.controller.abort()
    pending = null
  }

  function run(
    read: (signal: AbortSignal) => Promise<T>,
    apply: (value: T) => void,
    fail: (error: unknown) => void,
  ): Promise<void> {
    if (pending) return pending.promise

    const currentVersion = ++version
    const controller = new AbortController()
    const promise = Promise.resolve()
      .then(() => read(controller.signal))
      .then(
        (value) => {
          if (currentVersion === version) apply(value)
        },
        (error: unknown) => {
          if (currentVersion !== version) return
          fail(error)
          throw error
        },
      )
      .finally(() => {
        if (currentVersion === version) pending = null
      })

    pending = { controller, promise }
    latest = promise
    return promise
  }

  return {
    run,

    async replace(
      read: (signal: AbortSignal) => Promise<T>,
      apply: (value: T) => void,
      fail: (error: unknown) => void,
    ): Promise<void> {
      const currentScope = scope
      supersede()
      let response = run(read, apply, fail)
      await response
      // A later write can replace our read. Its snapshot must be applied before
      // either write reports that its refresh is complete.
      while (currentScope === scope && latest && latest !== response) {
        response = latest
        await response
      }
    },

    invalidate() {
      scope += 1
      supersede()
      latest = null
    },
  }
}
