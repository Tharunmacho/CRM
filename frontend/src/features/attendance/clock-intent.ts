export interface ClockIntent {
  kind: 'check-in' | 'check-out'
  requestKey: string
  sessionId?: string
}

type StoragePort = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
const key = (userId: string) => 'crm-attendance-pending:' + userId

/** Only an explicit retry may dispatch a recovered action. No credentials are stored. */
export function readClockIntent(storage: StoragePort, userId: string, context: string | null): ClockIntent | null {
  try {
    const saved = JSON.parse(storage.getItem(key(userId)) ?? 'null')
    if (!saved) return null
    const intent = saved.intent as ClockIntent | undefined
    if (!context || saved.context !== context || !intent ||
      !['check-in', 'check-out'].includes(intent.kind) ||
      typeof intent.requestKey !== 'string' || !/^[0-9a-f-]{36}$/i.test(intent.requestKey) ||
      (intent.kind === 'check-out' && (typeof intent.sessionId !== 'string' || !intent.sessionId))) {
      storage.removeItem(key(userId))
      return null
    }
    return intent
  } catch { return null }
}

export function writeClockIntent(storage: StoragePort, userId: string, context: string | null, intent: ClockIntent | null) {
  try {
    if (intent && context) storage.setItem(key(userId), JSON.stringify({ context, intent }))
    else storage.removeItem(key(userId))
  } catch { /* The in-memory retry still works when browser storage is unavailable. */ }
}
