/** The 24-hour customer reply window that WhatsApp Business gives after a customer writes (logic only, no styling). */
export const WINDOW_MS = 24 * 60 * 60 * 1000

export function windowLeftMs(expiresAt?: string | null, now = Date.now()) {
  if (!expiresAt) return 0
  return Math.max(0, new Date(expiresAt).getTime() - now)
}

/** "5 h 20 min left", "45 min left", "Under 1 min left". */
export function leftLabel(ms: number) {
  const minutes = Math.floor(ms / 60_000)
  if (minutes < 1) return 'Under 1 min left'
  if (minutes < 60) return `${minutes} min left`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} h ${rest} min left` : `${hours} h left`
}

/** Colour of the time-left text: calm while there is time, warning below 6 hours, danger below 2. */
export function windowTone(ms: number): 'ok' | 'warn' | 'danger' | 'over' {
  if (ms <= 0) return 'over'
  if (ms < 2 * 3600_000) return 'danger'
  if (ms < 6 * 3600_000) return 'warn'
  return 'ok'
}
