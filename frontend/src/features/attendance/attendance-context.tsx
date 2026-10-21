import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, fileHref } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { activeAttendanceSession, type AttendanceResponse, type PresenceSummary, type StaffAttendance } from './model'
import { createPresenceReporter } from './presence-reporter'

type ClockIntent = { kind: 'check-in' | 'check-out'; requestKey: string; sessionId?: string }
interface AttendanceContextValue {
  own: StaffAttendance | null
  loading: boolean
  error: string
  presenceError: string
  busy: boolean
  pendingKind?: ClockIntent['kind']
  refreshedAt: number
  revision: number
  refresh: () => Promise<void>
  clock: () => Promise<void>
}
const AttendanceContext = createContext<AttendanceContextValue | null>(null)

/** Presence reports foreground interaction only; it never changes the attendance clock. */
export function AttendanceProvider({ children }: { children: ReactNode }) {
  const { me } = useStore()
  const [own, setOwn] = useState<StaffAttendance | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [presenceError, setPresenceError] = useState('')
  const [busy, setBusy] = useState(false)
  const [pendingKind, setPendingKind] = useState<ClockIntent['kind']>()
  const [refreshedAt, setRefreshedAt] = useState(0)
  const [revision, setRevision] = useState(0)
  const intent = useRef<ClockIntent | null>(null)
  const busyRef = useRef(false)
  const active = useRef(true)
  const request = useRef<AbortController | null>(null)
  const mutationVersion = useRef(0)
  const lifecycleVersion = useRef(0)

  const refresh = useCallback(async () => {
    if (me.role === 'admin') { setLoading(false); return }
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    const version = mutationVersion.current
    try {
      const result = await api.get<AttendanceResponse>('/attendance?staffId=' + encodeURIComponent(me.id), { signal: controller.signal })
      if (!active.current || controller.signal.aborted || version !== mutationVersion.current) return
      setOwn(result.staff.find((row) => row.userId === me.id) ?? null)
      setRefreshedAt(performance.now()); if (!intent.current) setError('')
    } catch (err) {
      if (active.current && !controller.signal.aborted) setError(err instanceof Error ? err.message : 'Could not load attendance.')
    } finally { if (active.current && !controller.signal.aborted) setLoading(false) }
  }, [me.id, me.role])

  useEffect(() => {
    active.current = true
    lifecycleVersion.current += 1
    busyRef.current = false; setBusy(false); intent.current = null; setPendingKind(undefined)
    void refresh()
    const interval = window.setInterval(() => { if (document.visibilityState === 'visible' && !busyRef.current) void refresh() }, 30_000)
    return () => { active.current = false; lifecycleVersion.current += 1; request.current?.abort(); clearInterval(interval) }
  }, [refresh])

  useEffect(() => {
    if (me.role !== 'staff') return
    // A fresh page instance prevents duplicated tabs from sharing a heartbeat identity.
    const clientId = crypto.randomUUID()
    try { sessionStorage.setItem('crm-presence-client-id', clientId) } catch { /* Private browsing still has an in-memory identity. */ }
    const visible = () => document.visibilityState === 'visible' && document.hasFocus()
    const reporter = createPresenceReporter<PresenceSummary>({
      clientId,
      now: () => performance.now(),
      send: (pulse, signal) => api.post('/presence/heartbeat', pulse, { signal }),
      leave: (pulse) => {
        const body = JSON.stringify(pulse)
        const url = fileHref('/api/presence/leave')
        if (navigator.sendBeacon?.(url, new Blob([body], { type: 'application/json' }))) return
        void fetch(url, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body, keepalive: true }).catch(() => {})
      },
      onResult: (result) => { setPresenceError(''); setOwn((row) => row ? { ...row, presence: result } : row) },
      onError: (error) => setPresenceError(error instanceof Error ? error.message : 'Presence could not sync.'),
    })
    const interaction = (event: Event) => {
      if (!event.isTrusted || !visible()) return
      if (reporter.recordInteraction()) reporter.publish(true)
    }
    const visibility = () => reporter.publish(visible())
    const leave = () => reporter.leave()
    const events = ['pointerdown', 'pointermove', 'keydown', 'touchstart', 'scroll']
    for (const type of events) window.addEventListener(type, interaction, { passive: true })
    document.addEventListener('visibilitychange', visibility)
    window.addEventListener('focus', visibility)
    window.addEventListener('blur', visibility)
    window.addEventListener('pagehide', leave)
    window.addEventListener('pageshow', visibility)
    window.addEventListener('online', visibility)
    const interval = window.setInterval(() => { if (visible()) reporter.publish(true) }, 30_000)
    visibility()
    return () => {
      clearInterval(interval); reporter.dispose()
      for (const type of events) window.removeEventListener(type, interaction)
      document.removeEventListener('visibilitychange', visibility)
      window.removeEventListener('focus', visibility)
      window.removeEventListener('blur', visibility)
      window.removeEventListener('pagehide', leave)
      window.removeEventListener('pageshow', visibility)
      window.removeEventListener('online', visibility)
    }
  }, [me.id, me.role])

  const clock = async () => {
    if (busyRef.current || !own) return
    if (!intent.current) {
      const sessionId = activeAttendanceSession(own)
      if (own.attendance.status === 'checked_in' && !sessionId) { setError('Refresh attendance before checking out.'); return }
      intent.current = { kind: sessionId ? 'check-out' : 'check-in', requestKey: crypto.randomUUID(), ...(sessionId ? { sessionId } : {}) }
    }
    const pending = intent.current
    const lifecycle = lifecycleVersion.current
    busyRef.current = true; setBusy(true); setPendingKind(pending.kind); setError('')
    mutationVersion.current += 1; request.current?.abort()
    try {
      const saved = await api.post<StaffAttendance>('/attendance/' + pending.kind, { requestKey: pending.requestKey, ...(pending.sessionId ? { sessionId: pending.sessionId } : {}) })
      if (!active.current || lifecycle !== lifecycleVersion.current) return
      setOwn(saved); setRefreshedAt(performance.now()); setRevision((value) => value + 1)
      intent.current = null; setPendingKind(undefined)
    } catch (err) {
      if (active.current && lifecycle === lifecycleVersion.current) setError(err instanceof Error ? err.message : 'The attendance change could not be confirmed. Retry safely.')
    } finally { if (active.current && lifecycle === lifecycleVersion.current) { busyRef.current = false; setBusy(false); setLoading(false) } }
  }

  return <AttendanceContext.Provider value={{ own, loading, error, presenceError, busy, pendingKind, refreshedAt, revision, refresh, clock }}>{children}</AttendanceContext.Provider>
}

export function useAttendance() {
  const value = useContext(AttendanceContext)
  if (!value) throw new Error('Attendance must be inside the workspace provider')
  return value
}
