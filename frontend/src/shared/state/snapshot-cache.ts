/**
 * Stale-while-revalidate memory for the workspace and the dashboard.
 *
 * A reload used to show a blank screen until the server had read the whole workspace (hundreds of
 * milliseconds per database round trip). The last snapshot is kept in this tab's sessionStorage and painted at
 * once; the normal request still runs and replaces it a moment later.
 *
 * Safety rules, all enforced here:
 *  - An entry is used only when the readable `crm_uid` cookie (set by the server at sign-in, removed at
 *    sign-out) names the same user the entry was saved for. A different person signing in from another tab
 *    can never be shown the previous person's data, even for the moment before the session request returns.
 *    Without the cookie (an older server) nothing is ever restored.
 *  - sessionStorage belongs to one tab and disappears with it. Nothing goes to localStorage.
 *  - Only the workspace/report payloads the server already returned are stored; no tokens, no cookies.
 *  - Sign-in, sign-out and an anonymous session response wipe everything.
 *  - Storage that is full, blocked or corrupt just means no cache.
 */

const PREFIX = 'crm-swr:v1:'
const MAX_CHARS = 3_500_000
const USER_HINT_COOKIE = 'crm_uid'

interface Entry<T> { userId: string; savedAt: number; value: T }

function store(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

/** Who the browser's session cookie belongs to, as last announced by the server. */
export function signedInUserHint(): string | null {
  try {
    if (typeof document === 'undefined') return null
    for (const part of document.cookie.split(';')) {
      const [name, ...rest] = part.trim().split('=')
      if (name === USER_HINT_COOKIE) return decodeURIComponent(rest.join('=')) || null
    }
  } catch {
    // Cookies may be blocked.
  }
  return null
}

export function readPersisted<T>(name: string, userId: string | null): { value: T; savedAt: number } | null {
  const storage = store()
  if (!storage || !userId || signedInUserHint() !== userId) return null
  try {
    const raw = storage.getItem(PREFIX + name)
    if (!raw) return null
    const entry = JSON.parse(raw) as Entry<T>
    if (entry.userId !== userId || typeof entry.savedAt !== 'number' || entry.value == null) return null
    return { value: entry.value, savedAt: entry.savedAt }
  } catch {
    return null
  }
}

export function writePersisted<T>(name: string, userId: string, value: T): void {
  const storage = store()
  if (!storage) return
  try {
    const raw = JSON.stringify({ userId, savedAt: Date.now(), value } satisfies Entry<T>)
    if (raw.length > MAX_CHARS) {
      storage.removeItem(PREFIX + name)
      return
    }
    storage.setItem(PREFIX + name, raw)
  } catch {
    try { storage.removeItem(PREFIX + name) } catch { /* nothing more to do */ }
  }
}

/** Forget every remembered snapshot: sign-in, sign-out, anonymous or rejected session. */
export function forgetSnapshots(): void {
  reports.clear()
  clearTimeout(pending)
  const storage = store()
  if (!storage) return
  try {
    for (const key of Object.keys(storage)) if (key.startsWith(PREFIX)) storage.removeItem(key)
  } catch {
    // Nothing to clear if storage is unavailable.
  }
}

let pending: ReturnType<typeof setTimeout> | undefined

/**
 * Save a snapshot shortly after it is applied, when the browser is idle, so serialising a large workspace
 * never competes with rendering it. Later calls replace the pending one.
 */
export function persistSoon(name: string, userId: string, value: () => unknown): void {
  clearTimeout(pending)
  pending = setTimeout(() => {
    const write = () => writePersisted(name, userId, value())
    const idle = (window as Window & { requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number }).requestIdleCallback
    if (idle) idle.call(window, write, { timeout: 4_000 })
    else write()
  }, 1_500)
}

/** Report results (dashboard, reports): newest answer per user and query, in memory for this page's lifetime. */
const reports = new Map<string, { data: unknown; at: number }>()
const MAX_REPORTS = 24
const PERSISTED_REPORT = /^\/dashboard(\?|$)/

export function peekReport<T>(sessionId: string, path: string, revision: string): T | null {
  const key = `${sessionId}:${path}:${revision}`
  const hit = reports.get(key)
  if (hit) return hit.data as T
  if (!PERSISTED_REPORT.test(path)) return null
  const stored = readPersisted<T>(`report:${path}`, sessionId)
  if (!stored) return null
  reports.set(key, { data: stored.value, at: stored.savedAt })
  return stored.value
}

export function rememberReport(sessionId: string, path: string, revision: string, data: unknown): void {
  const key = `${sessionId}:${path}:${revision}`
  reports.delete(key)
  reports.set(key, { data, at: Date.now() })
  while (reports.size > MAX_REPORTS) reports.delete(reports.keys().next().value as string)
  if (PERSISTED_REPORT.test(path)) writePersisted(`report:${path}`, sessionId, data)
}
