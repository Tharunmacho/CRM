import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { ApiError, api, takeEarlyRead, type InboxUnreadCount } from '@/shared/api/client'
import { createLatestRequest } from '@/shared/api/latest-request'
import { createIngestionRefresh } from '@/shared/api/ingestion-refresh'
import { agencyDay } from '@/shared/lib/task-time'
import { EMPTY_SETTINGS } from '@/shared/lib/agency'
import { indexWorkspace } from './workspace-index'
import { keepIfSame, reconcile } from './reconcile'
import { deskUnread } from './desk-unread'
import { dropDeleted, markCandidateDeleted, removeCandidate, restoreCandidate, unmarkCandidateDeleted } from './candidate-removal'
import { forgetSnapshots, persistSoon, readPersisted, signedInUserHint } from './snapshot-cache'
import { live, liveInterval } from './live'
import { removeOrder } from './order-removal'
import { orderDeletePath } from '@/features/jobs/order-delete'
import { matchDoneMessage } from '@/features/candidates/initial-call'
import { unassignMessage, type UnassignResult } from '@/features/candidates/data-bank-pools'
import type {
  AgencySettings,
  Application,
  Candidate,
  Client,
  Company,
  Designation,
  Notification,
  Order,
  Remark,
  Role,
  Task,
  TodoItem,
  User,
} from '@/shared/model/types'

/**
 * The store is now backed by the API. Every collection comes from one bootstrap call,
 * and a mutation posts then reloads, so what the screen shows is always what the
 * database holds — a refresh no longer loses anything.
 *
 * The shape of `useStore()` is unchanged, so the pages and components did not have to
 * move with it.
 */

interface Data {
  agencyTimezone?: string
  /** The agency's name, default currencies, calling code and currency list, from the database. */
  settings: AgencySettings
  ingestionRevision?: string
  users: User[]
  clients: Client[]
  companies: Company[]
  designations: Designation[]
  orders: Order[]
  candidates: Candidate[]
  applications: Application[]
  tasks: Task[]
  remarks: Remark[]
  todos: TodoItem[]
  notifications: Notification[]
}

/** `inboxUnread` rides along with the desk (the sidebar badge); older servers omit it and the first pulse supplies it. */
type Bootstrap = Data & { me: User; inboxUnread?: InboxUnreadCount }

const EMPTY: Data = {
  settings: EMPTY_SETTINGS,
  users: [],
  clients: [],
  companies: [],
  designations: [],
  orders: [],
  candidates: [],
  applications: [],
  tasks: [],
  remarks: [],
  todos: [],
  notifications: [],
}

export type Capability =
  | 'connect_sources'
  | 'manage_records'
  | 'delete_records'
  | 'map_candidates'
  | 'work_any_candidate'
  | 'reassign'
  | 'reopen_stage'
  | 'todo_list'
  | 'user_management'
  | 'export'
  | 'all_reports'
  | 'staff_report'
  | 'view_invoices'

/** Straight from the client specification's capability table. */
const MATRIX: Record<Capability, Role[]> = {
  connect_sources: ['admin'],
  manage_records: ['admin', 'manager', 'staff'],
  // Deleting cascades down client → company → order, so staff do not get it.
  delete_records: ['admin', 'manager'],
  map_candidates: ['admin', 'manager', 'staff'],
  work_any_candidate: ['admin', 'manager'],
  // Only the admin assigns, reassigns and unassigns candidates; managers work the candidates assigned to them.
  reassign: ['admin'],
  reopen_stage: ['admin'],
  todo_list: ['admin', 'manager'],
  user_management: ['admin'],
  export: ['admin'],
  all_reports: ['admin', 'manager'],
  // The weekly per-person performance report (hours, tasks, candidate progress) is the admin's.
  staff_report: ['admin'],
  // The agreed service fee, CGST/SGST, invoices, receipts and the Fee collections report are the admin's alone: recruiters and managers
  // never see them (the server answers 403 and leaves them out of every payload; this only keeps the screens in step).
  view_invoices: ['admin'],
}

type Toast = { id: number; message: string; tone: 'ok' | 'warn' } | null

interface StoreValue extends Data {
  /**
   * Always set for anything rendered inside the app shell — app/routes.tsx will not mount a
   * page without a session. The login screen reads `authenticated` instead.
   */
  me: User
  ready: boolean
  authenticated: boolean
  /** The agency name the sign-in page shows before anyone is signed in (null until the server answers). */
  publicAgency: { name: string; shortName: string | null } | null
  loadError: string | null
  retryStartup: () => Promise<void>
  /** Whether a pending UI operation still belongs to this signed-in account. */
  isCurrentSession: () => boolean
  /** Unread INBOX messages across connected mailboxes; the mail itself is never loaded into the store. */
  inboxUnread: number
  /** Re-read the unread count now, e.g. after leaving the inbox where messages were read. */
  refreshInboxUnread: () => Promise<void>
  toast: Toast

  signIn: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  refresh: (options?: { keepCurrent?: boolean }) => Promise<void>
  /** Route-entry read: share current reads and reuse a snapshot loaded in the last five seconds. */
  ensureFresh: () => Promise<void>
  /** Milliseconds since the workspace snapshot was last read, or Infinity when there is none. */
  snapshotAge: () => number
  notify: (message: string, tone?: 'ok' | 'warn') => void
  dismissToast: () => void

  can: (capability: Capability) => boolean

  candidate: (id: string) => Candidate | undefined
  client: (id: string) => Client | undefined
  company: (id: string) => Company | undefined
  order: (id: string) => Order | undefined
  designation: (id: string) => Designation | undefined
  user: (id: string | null | undefined) => User | undefined
  orderLabel: (id: string) => string
  openTasks: Task[]
  myOpenTasks: Task[]
  visibleTasks: Task[]
  activeApplicationFor: (candidateId: string) => Application | undefined
  applicationsFor: (candidateId: string) => Application[]
  remarksFor: (taskId: string) => Remark[]
  updatedToday: (taskId: string) => boolean

  // Writes. Each posts and then reloads, so nothing is left only in the browser.
  saveDraft: (applicationId: string, data: Record<string, unknown>, lockVersion: number) => Promise<void>
  completeStage: (applicationId: string, lockVersion: number) => Promise<void>
  mapCandidate: (candidateId: string, orderId: string) => Promise<{ refreshed: boolean; closedInitialCall: boolean } | null>
  unmap: (applicationId: string) => Promise<void>
  addRemark: (taskId: string, body: string) => Promise<void>
  recordCall: (candidateId: string, interested: string, remarks: string, called?: boolean) => Promise<void>
  markNotificationRead: (id?: string) => Promise<void>
  addCandidate: (payload: Record<string, unknown>, options?: { deferRefresh?: boolean }) => Promise<{ id: string; ownerName?: string | null } | null>
  addClient: (payload: Record<string, unknown>) => Promise<{ id: string } | null>
  addCompany: (payload: Record<string, unknown>) => Promise<{ id: string } | null>
  /** Throw when the server refuses (a candidate still active or deployed below). */
  deleteClient: (id: string) => Promise<void>
  deleteCompany: (id: string) => Promise<void>
  /** Admin only. Throws the server's plain message. `withdrawApplications` is the explicit confirmation to remove the candidates on the job with it. */
  deleteOrder: (id: string, options?: { withdrawApplications?: boolean }) => Promise<void>
  /** Admin only. Throws the server's plain message (e.g. still on an order). */
  deleteCandidate: (id: string, options?: { withdrawFromOrders?: boolean }) => Promise<void>
  addDesignation: (payload: Record<string, unknown>) => Promise<void>
  addOrder: (payload: Record<string, unknown>) => Promise<void>
  addTodo: (payload: Record<string, unknown>) => Promise<void>
  toggleTodo: (id: string) => Promise<void>
  deleteTodo: (id: string) => Promise<void>
  reassign: (candidateIds: string[], toUserId: string, reason: string, restartClockReason?: string) => Promise<void>
  /** Admin only: take candidates back from their recruiter (they wait in "Not assigned"). Toasts what happened; throws the server's refusal. */
  unassign: (candidateIds: string[]) => Promise<UnassignResult>
}

const Ctx = createContext<StoreValue | null>(null)

/**
 * How often the desk asks whether new mail/intake arrived (and re-reads the unread badge) while the live stream
 * is down. With the stream up it asks when mail/WhatsApp/document topics change, plus a slow safety poll.
 */
const PULSE_MS = 20_000
const NOTIFICATIONS_POLL_MS = 30_000

/** Everything the workspace snapshot (/bootstrap) is built from, except notifications, which have their own cheap read. */
const WORKSPACE_TOPICS = ['candidates', 'applications', 'tasks', 'clients', 'companies', 'orders', 'designations', 'users', 'todos', 'settings'] as const
/** Changes arriving within this window are fetched together; kept short because this is the visible latency. */
const WORKSPACE_LIVE_WINDOW_MS = 200
/**
 * Route-entry revalidation window: short when polling is all we have. While the live stream is up every workspace change
 * announces itself and marks the snapshot stale at once (below), so an unchanged snapshot can be trusted much longer; the
 * remaining risk is a missed event, which a reconnect (resync) or this window running out at the next route entry covers.
 */
const FRESH_MS = 5_000
const FRESH_WHILE_LIVE_MS = 5 * 60_000

/**
 * The last workspace this tab saw for the person the browser says is signed in, if any. It is painted
 * straight away and replaced by the server's answer a moment later (see snapshot-cache.ts for the rules).
 */
function remembered(): { payload: Bootstrap; savedAt: number } | null {
  const userId = signedInUserHint()
  const found = readPersisted<Bootstrap>('bootstrap', userId)
  const value = found?.value
  if (!found || !value || value.me?.id !== userId || !Array.isArray(value.candidates) || !Array.isArray(value.orders)) return null
  return { payload: value, savedAt: found.savedAt }
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [hydrated] = useState(remembered)
  const [data, setData] = useState<Data>(() => {
    if (!hydrated) return EMPTY
    const { me: _who, inboxUnread: _unread, ...rest } = hydrated.payload
    return { ...EMPTY, ...rest }
  })
  const [me, setMe] = useState<User | null>(hydrated?.payload.me ?? null)
  const [ready, setReady] = useState(Boolean(hydrated))
  const [loadError, setLoadError] = useState<string | null>(null)
  const [toast, setToast] = useState<Toast>(null)
  const [publicAgency, setPublicAgency] = useState<StoreValue['publicAgency']>(null)
  const [inboxUnread, setInboxUnread] = useState(0)
  const readInboxUnread = useRef<() => Promise<void>>(() => Promise.resolve())
  /** The desk response already told us the unread count, so the first /inbox/pulse after load is not needed. */
  const unreadSeeded = useRef(false)
  const requests = useRef(createLatestRequest<Bootstrap | null>()).current
  const mounted = useRef(false)
  const authVersion = useRef(0)
  const authQueue = useRef<Promise<void>>(Promise.resolve())
  const pendingAuthWrites = useRef(0)
  const currentData = useRef(data)
  currentData.current = data
  const lastLoadedAt = useRef(hydrated?.savedAt ?? 0)
  const index = useMemo(() => indexWorkspace(data), [data.candidates, data.clients, data.companies,
    data.designations, data.orders, data.users, data.applications, data.tasks, data.remarks])

  const notify = useCallback((message: string, tone: 'ok' | 'warn' = 'ok') => {
    setToast({ id: Date.now(), message, tone })
  }, [])

  const loadWorkspace = useCallback(
    (checkSession: boolean, replace = false, background = false) =>
      (replace ? requests.replace : requests.run)(
        async (signal) => {
          // One round trip for an authenticated startup; anonymous visits still
          // receive only { me: null } and never load CRM collections.
          if (checkSession) {
            const sessionPath = '/auth/session?workspace=true&compact=true'
            // index.html has usually started this request already, before this code loaded.
            const session = (await takeEarlyRead<Bootstrap | { me: null }>('session', sessionPath))
              ?? (await api.get<Bootstrap | { me: null }>(sessionPath, { signal }))
            if (session?.me === null) return null
            if (!session?.me?.id || !('candidates' in session)) {
              throw new ApiError(502, 'The server returned an invalid session response.')
            }
            return session
          }
          const payload = await api.get<Bootstrap>('/bootstrap?compact=true', { signal })
          if (!payload?.me?.id) {
            throw new ApiError(502, 'The server returned an invalid workspace response.')
          }
          return payload
        },
        (payload) => {
          lastLoadedAt.current = payload ? Date.now() : 0
          setMe((current) => (payload ? keepIfSame(current, payload.me) : null))
          if (payload) {
            const { me: _who, inboxUnread: unread, ...rest } = payload
            const seeded = deskUnread({ inboxUnread: unread })
            if (seeded !== null) { unreadSeeded.current = true; setInboxUnread(seeded) }
            // Unchanged records keep their identity, so a refresh that changed nothing renders nothing.
            // A snapshot read before a delete finished must not bring the deleted candidate back.
            setData((current) => reconcile(current, dropDeleted({ ...EMPTY, ...rest })))
            persistSoon('bootstrap', payload.me.id, () => payload)
          } else {
            setData(EMPTY)
            forgetSnapshots()
          }
          setLoadError(null)
          setReady(true)
        },
        (error) => {
          lastLoadedAt.current = 0
          if (error instanceof ApiError && error.isUnauthorised) {
            setMe(null)
            setData(EMPTY)
            setLoadError(null)
            forgetSnapshots()
          } else if (!background) {
            setLoadError(
              error instanceof Error ? error.message : 'The workspace could not be loaded.',
            )
          }
          setReady(true)
        },
      ),
    [requests],
  )

  const refresh = useCallback((options: { keepCurrent?: boolean } = {}) => {
    // This can follow a write outside the store too (for example a file upload).
    // A read that started before that write must not supply the refreshed snapshot.
    lastLoadedAt.current = 0
    return loadWorkspace(false, true, Boolean(options.keepCurrent))
  }, [loadWorkspace])

  const ensureFresh = useCallback(() => {
    if (lastLoadedAt.current > 0 && Date.now() - lastLoadedAt.current < (live.isUp() ? FRESH_WHILE_LIVE_MS : FRESH_MS)) return Promise.resolve()
    return loadWorkspace(false, false, true)
  }, [loadWorkspace])

  const retryStartup = useCallback(async () => {
    requests.invalidate()
    lastLoadedAt.current = 0
    setReady(false)
    setLoadError(null)
    try {
      await loadWorkspace(true)
    } catch {
      // loadWorkspace renders the failure and leaves retry available.
    }
  }, [loadWorkspace, requests])

  // Signed out: the sign-in page still names the agency. The session probe stays free of database work, so the
  // name comes from its own tiny public read, and only here, never for a signed-in visit.
  useEffect(() => {
    if (!ready || me || publicAgency) return
    const controller = new AbortController()
    api.get<{ name?: string; shortName?: string | null }>('/settings/public', { signal: controller.signal })
      .then((found) => { if (found?.name) setPublicAgency({ name: found.name, shortName: found.shortName ?? null }) })
      .catch(() => { /* the page falls back to a neutral label */ })
    return () => controller.abort()
  }, [ready, me, publicAgency])

  // StrictMode replays this effect in development. Both setups share one request.
  useEffect(() => {
    mounted.current = true
    // With a remembered workspace on screen, a failed check keeps it instead of replacing it with an error.
    void loadWorkspace(true, false, Boolean(hydrated)).catch(() => {
      // The failure is displayed by the route/login screen.
    })
    return () => {
      mounted.current = false
      queueMicrotask(() => {
        if (!mounted.current) {
          requests.invalidate()
          authVersion.current += 1
        }
      })
    }
  }, [loadWorkspace, requests, hydrated])

  // Email intake can finish while any authenticated screen is open. One small request answers both "did
  // intake change?" (the revision, compared with the one loaded with the workspace) and "how many unread?"
  // (the sidebar badge), so the full snapshot is only downloaded when something really changed. The bootstrap
  // just delivered the current revision, so there is no immediate read: the first one waits a moment.
  useEffect(() => {
    if (!ready || !me || loadError) {
      if (!me) { setInboxUnread(0); unreadSeeded.current = false }
      return
    }
    const version = authVersion.current
    let legacy = false
    const applyUnread = (unread: InboxUnreadCount | null | undefined, signal: AbortSignal) => {
      if (!signal.aborted && version === authVersion.current && typeof unread?.total === 'number') setInboxUnread(unread.total)
    }
    const readPulse = async (signal: AbortSignal): Promise<{ revision?: string }> => {
      if (!legacy) {
        try {
          const result = await api.inbox.pulse({ signal })
          applyUnread(result?.unread, signal)
          return { revision: result?.revision }
        } catch (error) {
          // A server without /inbox/pulse answers 404/405, or serves the app shell for unknown paths (502 here).
          if (!(error instanceof ApiError) || ![404, 405, 502].includes(error.status)) throw error
          legacy = true
        }
      }
      const [revision, unread] = await Promise.all([
        api.inbox.revision({ signal }),
        api.inbox.unreadCount({ signal }).catch(() => null),
      ])
      applyUnread(unread, signal)
      return revision
    }
    const observer = createIngestionRefresh({
      currentRevision: () => currentData.current.ingestionRevision,
      readRevision: readPulse,
      refresh: () => loadWorkspace(false, false, true),
      isCurrentSession: () =>
        mounted.current && version === authVersion.current && pendingAuthWrites.current === 0,
    })
    // Retain usable data and retry next time if the probe/refresh fails. The promise goes back to the timer so a failing
    // server is asked less often instead of every 20 seconds (the timer backs off and pauses while the tab is hidden).
    const read = () => (document.visibilityState === 'visible' ? observer.check() : undefined)
    const readOnce = () => { void read()?.catch(() => {}) }
    readInboxUnread.current = async () => {
      if (document.visibilityState !== 'visible') return
      try { await observer.check() } catch { /* Keep the last known count; the next poll retries. */ }
    }
    // The desk response carried the unread count: wait for the first scheduled poll instead of asking 1.2 s after load.
    const first = unreadSeeded.current ? undefined : window.setTimeout(readOnce, 1_200)
    const stopLive = liveInterval(read, PULSE_MS, ['mail', 'whatsapp', 'documents'], { onChange: readOnce, debounceMs: 2_500 }) // events cannot make the badge poll more than once per 2.5 s (the server memo lasts 3-10 s)
    document.addEventListener('visibilitychange', readOnce)
    return () => {
      observer.dispose()
      if (first !== undefined) window.clearTimeout(first)
      stopLive()
      document.removeEventListener('visibilitychange', readOnce)
      readInboxUnread.current = () => Promise.resolve()
    }
  }, [me?.id, ready, loadError, loadWorkspace])

  useEffect(() => {
    if (!me || !ready) return
    const version = authVersion.current
    const controller = new AbortController()
    let running = false
    let again = false
    const check = async () => {
      if (document.visibilityState !== 'visible') return
      // A notice that lands while a read is in flight (an allocation request sent just now) must not be dropped:
      // that read may have started before the commit, so ask once more when it finishes.
      if (running) { again = true; return }
      running = true
      try {
        const items = await api.get<Notification[]>('/notifications', { signal: controller.signal })
        if (!controller.signal.aborted && version === authVersion.current) {
          const knownIds = new Set(currentData.current.notifications.map(item => item.id))
          const hasNew = items.some(item => !knownIds.has(item.id))
          setData((current) => {
            const readIds = new Set(current.notifications.filter(item => item.read).map(item => item.id))
            // Most polls find nothing new: keep the same objects so nothing on screen re-renders.
            return reconcile(current, { ...current, notifications: items.map(item => ({ ...item, read: item.read || readIds.has(item.id) })) })
          })
          if (hasNew) await loadWorkspace(false, false, true)
        }
      } catch (error) {
        if (!controller.signal.aborted && error instanceof ApiError && error.isUnauthorised) void loadWorkspace(false, false, true).catch(() => {})
      } finally {
        running = false
        if (again && !controller.signal.aborted) { again = false; void check() }
      }
    }
    const stopLive = liveInterval(() => void check(), NOTIFICATIONS_POLL_MS, ['notifications'])
    return () => { controller.abort(); stopLive() }
  }, [me?.id, ready, loadWorkspace])

  // One live stream for the whole signed-in session (see live-events.ts): open once we know who is signed in.
  useEffect(() => {
    if (!me?.id) return
    live.start()
    return () => live.stop()
  }, [me?.id])

  // The server announced a committed change to something the workspace shows: re-read it now instead of waiting for
  // the next poll. `replace` abandons a read that began before the change, whose snapshot may not contain it.
  useEffect(() => {
    if (!me?.id || !ready) return
    const version = authVersion.current
    // At most one live-triggered read runs at a time. Events that arrive while it is in flight (a bulk change, a document
    // being read page by page) are remembered and answered by ONE follow-up read, instead of each event aborting the read
    // before it and starting another: against a slow database that loop never delivers a snapshot at all.
    let running = false
    let again = false
    const reload = () => {
      if (version !== authVersion.current || pendingAuthWrites.current > 0) return
      if (running) { again = true; return }
      running = true
      lastLoadedAt.current = 0
      void loadWorkspace(false, true, true).catch(() => {}).finally(() => {
        running = false
        if (again) { again = false; reload() }
      })
    }
    // The snapshot is stale the moment the server says so, not 200 ms later when the read starts: a route entered in that gap must reload.
    const stopStale = live.subscribe(WORKSPACE_TOPICS, () => { lastLoadedAt.current = 0 }, { debounceMs: 0 })
    const stopReload = live.subscribe(WORKSPACE_TOPICS, reload, { debounceMs: WORKSPACE_LIVE_WINDOW_MS })
    return () => { stopStale(); stopReload() }
  }, [me?.id, ready, loadWorkspace])

  const queueAuthWrite = useCallback((write: () => Promise<unknown>) => {
    pendingAuthWrites.current += 1
    const result = authQueue.current.then(write).finally(() => {
      pendingAuthWrites.current -= 1
    })
    // Serialize cookie changes, including a sign-in started while logout is pending.
    authQueue.current = result.then(() => undefined, () => undefined)
    return result
  }, [])

  /** Run a write, reload, and turn a failure into something the user can read. */
  const run = useCallback(
    async <T,>(work: () => Promise<T>, success?: string): Promise<T | null> => {
      const version = authVersion.current
      try {
        const result = await work()
        if (version !== authVersion.current) return null
        try { await refresh({ keepCurrent: true }) }
        catch {
          if (version === authVersion.current) notify('Saved. The workspace could not refresh; reload to see the latest records.', 'warn')
          return version === authVersion.current ? result : null
        }
        if (version !== authVersion.current) return null
        if (success) notify(success)
        return result
      } catch (err) {
        const e = err as ApiError
        if (version === authVersion.current) notify(e.message || 'Something went wrong', 'warn')
        throw e
      }
    },
    [refresh, notify],
  )

  const signIn = useCallback(
    async (email: string, password: string) => {
      const version = ++authVersion.current
      requests.invalidate()
      forgetSnapshots()
      lastLoadedAt.current = 0
      setMe(null)
      setData(EMPTY)
      setLoadError(null)
      setReady(false)
      try {
        await queueAuthWrite(() => api.post('/auth/login', { email, password }))
        if (version === authVersion.current) await refresh()
      } catch (error) {
        if (version === authVersion.current) setReady(true)
        throw error
      }
    },
    [queueAuthWrite, refresh, requests],
  )

  const signOut = useCallback(async () => {
    const version = ++authVersion.current
    requests.invalidate()
    forgetSnapshots()
    lastLoadedAt.current = 0
    setReady(false)
    setLoadError(null)
    try {
      await queueAuthWrite(() => api.post('/auth/logout'))
    } catch {
      // The cookie may already be gone; clearing local state still signs you out here.
    } finally {
      if (version === authVersion.current) {
        setMe(null)
        setData(EMPTY)
        setToast(null)
        setReady(true)
      }
    }
  }, [queueAuthWrite, requests])

  const value = useMemo<StoreValue>(() => {
    const version = authVersion.current
    const isCurrentSession = () =>
      me !== null && version === authVersion.current && pendingAuthWrites.current === 0
    const openTasks = data.tasks.filter((t) => !t.completedAt && !t.cancelledAt)
    const can = (c: Capability) => (me ? MATRIX[c].includes(me.role) : false)

    return {
      ...data,
      me: me as User,
      ready,
      authenticated: me !== null,
      publicAgency,
      loadError,
      retryStartup,
      isCurrentSession,
      inboxUnread,
      refreshInboxUnread: () => readInboxUnread.current(),
      toast,
      signIn,
      signOut,
      // Callbacks retained by an upload/dialog from an old session must not
      // refresh or show notifications in the account that signed in afterward.
      refresh: (options) => (isCurrentSession() ? refresh(options) : Promise.resolve()),
      ensureFresh: () => (isCurrentSession() ? ensureFresh() : Promise.resolve()),
      snapshotAge: () => (lastLoadedAt.current > 0 ? Date.now() - lastLoadedAt.current : Infinity),
      notify: (message, tone) => {
        if (version === authVersion.current) notify(message, tone)
      },
      dismissToast: () => setToast(null),
      can,

      candidate: (id) => index.candidates.get(id),
      client: (id) => index.clients.get(id),
      company: (id) => index.companies.get(id),
      order: (id) => index.orders.get(id),
      designation: (id) => index.designations.get(id),
      user: (id) => (id ? index.users.get(id) : undefined),
      orderLabel: (id) => {
        const o = index.orders.get(id)
        if (!o) return '—'
        const des = index.designations.get(o.designationId)
        const co = index.companies.get(o.companyId)
        return `${des?.title ?? 'Role'} · ${co?.name ?? 'Company'}`
      },
      openTasks,
      myOpenTasks: openTasks.filter((t) => t.ownerId === me?.id),
      // Staff see their own work; admin and managers see the whole desk.
      visibleTasks: me?.role === 'staff' ? data.tasks.filter((t) => t.ownerId === me.id) : data.tasks,
      activeApplicationFor: (candidateId) =>
        index.activeApplications.get(candidateId),
      applicationsFor: (candidateId) => index.applications.get(candidateId) ?? [],
      remarksFor: (taskId) => index.remarks.get(taskId) ?? [],
      updatedToday: (taskId) =>
        (index.remarks.get(taskId) ?? []).some((r) => r.authorId === index.tasks.get(taskId)?.ownerId && agencyDay(r.createdAt, data.agencyTimezone) === agencyDay(new Date(), data.agencyTimezone)),

      saveDraft: async (applicationId, draft, lockVersion) => {
        await run(
          () => api.put(`/applications/${applicationId}/draft`, { data: draft, lock_version: lockVersion }),
          'Saved',
        )
      },
      completeStage: async (applicationId, lockVersion) => {
        await run(
          () => api.post(`/applications/${applicationId}/complete`, { lock_version: lockVersion }),
          'Stage completed',
        )
      },
      mapCandidate: async (candidateId, orderId) => {
        let closedInitialCall = false
        try {
          const created = await api.post<{ closed_initial_call?: boolean } | null>('/applications', { candidate_id: candidateId, order_id: orderId })
          closedInitialCall = created?.closed_initial_call === true
        }
        catch (error) {
          if (isCurrentSession()) notify(error instanceof Error ? error.message : 'Mapping failed', 'warn')
          throw error
        }
        if (!isCurrentSession()) return null
        try {
          await refresh({ keepCurrent: true })
          if (!isCurrentSession()) return null
          notify(matchDoneMessage(closedInitialCall))
          return { refreshed: true, closedInitialCall }
        } catch {
          if (!isCurrentSession()) return null
          notify('Candidate mapped. The directory could not refresh; refresh the results to see the change.', 'warn')
          return { refreshed: false, closedInitialCall }
        }
      },
      unmap: async (applicationId) => {
        await run(
          () => api.post(`/applications/${applicationId}/unmap`),
          'Removed from the order. Open tasks were cancelled and the candidate is back in the Data Bank.',
        )
      },
      addRemark: async (taskId, body) => {
        await run(() => api.post(`/tasks/${taskId}/remarks`, { body }), 'Remark added')
      },
      recordCall: async (candidateId, interested, remarks, called = true) => {
        await run(
          () => api.post(`/candidates/${candidateId}/call`, { called, interested, remarks }),
          interested === 'no'
            ? 'Marked not interested. The candidate stays in the Data Bank.'
            : 'Call outcome recorded',
        )
      },
      markNotificationRead: async (id) => {
        await api.post(id ? `/notifications/${id}/read` : '/notifications/read-all')
        if (isCurrentSession()) setData((current) => ({ ...current, notifications: current.notifications.map((n) => !id || n.id === id ? { ...n, read: true } : n) }))
      },
      addCandidate: async (payload, options) => {
        const created = await api.post<{ id: string; ownerName?: string | null }>('/candidates', payload)
        if (!isCurrentSession()) return null
        // A refresh failure must not discard the saved ID and create a duplicate on retry.
        // `deferRefresh`: the caller starts its own refresh once it has finished its follow-up writes (see Add candidate), so the
        // person is not kept waiting for a whole workspace download here and the workspace is not downloaded twice.
        if (!options?.deferRefresh) {
          try { await refresh({ keepCurrent: true }) }
          catch { if (isCurrentSession()) notify('Candidate saved. The directory could not refresh; reopen it to try again.', 'warn') }
        }
        return isCurrentSession() ? created : null
      },
      addClient: (payload) => run(() => api.post<{ id: string }>('/clients', payload), 'Client saved'),
      addCompany: (payload) => run(() => api.post<{ id: string }>('/companies', payload), 'Company saved'),
      deleteClient: async (id) => {
        await run(() => api.del(`/clients/${id}`), 'Client deleted')
      },
      deleteCompany: async (id) => {
        await run(() => api.del(`/companies/${id}`), 'Company deleted')
      },
      deleteOrder: async (id, options) => {
        // The dialog shows the server's reason when it refuses, so a refusal is only thrown here. Once the server has
        // confirmed, the job and its candidates' applications leave every list at once (counts follow), then a quiet
        // refresh brings anything else that changed (a candidate back in the Data Bank).
        const version = authVersion.current
        await api.del(orderDeletePath(id, options?.withdrawApplications))
        if (version !== authVersion.current) return
        setData((current) => removeOrder(current, id))
        notify('Job deleted')
        void refresh({ keepCurrent: true }).catch(() => undefined)
      },
      deleteCandidate: async (id, options) => {
        // Optimistic: the candidate leaves every list now (the call below only has to confirm it), and comes
        // back with the server's reason if it refuses. The server's answer already says what changed, so there is
        // no full workspace reload; the live feed refreshes other desks and the lists that are fetched by search.
        const version = authVersion.current
        const { removed } = removeCandidate(currentData.current, id)
        markCandidateDeleted(id)
        setData((current) => removeCandidate(current, id).data)
        notify('Candidate deleted')
        try {
          await api.del(`/candidates/${id}${options?.withdrawFromOrders ? '?withdrawFromOrders=true' : ''}`)
        } catch (err) {
          unmarkCandidateDeleted(id)
          if (version === authVersion.current) {
            setData((current) => restoreCandidate(current, removed))
            notify((err as ApiError).message || 'We could not delete this candidate. Please try again.', 'warn')
            void refresh({ keepCurrent: true }).catch(() => undefined)
          }
          throw err
        }
      },
      addDesignation: async (payload) => {
        await run(() => api.post('/designations', payload), 'Designation saved')
      },
      addOrder: async (payload) => {
        await run(() => api.post('/orders', payload), 'Order created')
      },
      addTodo: async (payload) => {
        await run(() => api.post('/todos', payload), 'To-do added')
      },
      toggleTodo: async (id) => {
        const item = currentData.current.todos.find(todo => todo.id === id)
        if (item) await run(() => api.patch(`/todos/${id}`, { done: !item.done }))
      },
      deleteTodo: async (id) => {
        await run(() => api.del(`/todos/${id}`))
      },
      reassign: async (candidateIds, toUserId, reason, restartClockReason) => {
        await run(() => api.post('/candidates/reassign', { candidateIds, toUserId, reason, ...(restartClockReason ? { restartClock: true, restartReason: restartClockReason } : {}) }), 'Reassigned')
      },
      unassign: async (candidateIds) => {
        const result = await run(() => api.post<UnassignResult>('/candidates/unassign', { candidateIds }))
        const answer = result ?? { unassigned: 0, alreadyUnassigned: 0, refused: [] }
        if (result) { const message = unassignMessage(answer); notify(message.text, message.tone) }
        return answer
      },
    }
  }, [data, index, me, ready, loadError, publicAgency, retryStartup, inboxUnread, toast, signIn, signOut, refresh, ensureFresh, notify, run])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useStore() {
  const v = useContext(Ctx)
  if (!v) throw new Error('useStore must be used inside StoreProvider')
  return v
}
