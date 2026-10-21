export interface PresencePulse {
  clientId: string
  sequence: number
  visible: boolean
  interactionAgeSeconds: number | null
}
export type PresenceLeave = Pick<PresencePulse, 'clientId' | 'sequence'>

/** Ordered page signals. The clock must be monotonic (performance.now in browsers). */
export function createPresenceReporter<T>(options: {
  clientId: string
  now: () => number
  send: (pulse: PresencePulse, signal: AbortSignal) => Promise<T>
  leave: (pulse: PresenceLeave) => void
  onResult: (result: T) => void
  onError: (error: unknown) => void
}) {
  let sequence = 0
  let lastInteraction: number | null = null
  let pending: PresencePulse | null = null
  let sending = false
  let stopped = false
  const controller = new AbortController()
  const age = () => lastInteraction === null ? null : Math.min(86_400, Math.max(0, (options.now() - lastInteraction) / 1000))

  const drain = async () => {
    if (stopped || sending || !pending) return
    // Queueing must not make a heartbeat claim more recent activity than it has.
    const pulse = { ...pending, interactionAgeSeconds: age() }
    pending = null; sending = true
    try {
      const result = await options.send(pulse, controller.signal)
      // A hide/show/leave after this request makes its response obsolete.
      if (!stopped && pulse.sequence === sequence) options.onResult(result)
    } catch (error) {
      if (!stopped && pulse.sequence === sequence) options.onError(error)
    } finally {
      sending = false
      if (!stopped && pending) void drain()
    }
  }
  const leave = () => {
    if (stopped) return
    pending = null
    options.leave({ clientId: options.clientId, sequence: ++sequence })
  }

  return {
    recordInteraction() {
      const elapsed = age()
      const wasIdle = elapsed === null || elapsed >= 300
      lastInteraction = options.now()
      return wasIdle
    },
    publish(visible: boolean) {
      if (stopped) return
      pending = { clientId: options.clientId, sequence: ++sequence, visible, interactionAgeSeconds: age() }
      void drain()
    },
    leave,
    dispose() {
      leave()
      stopped = true
      controller.abort()
    },
  }
}
