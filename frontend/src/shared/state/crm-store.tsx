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
import { indexWorkspace } from './workspace-index'
import { forgetSnapshots, persistSoon, readPersisted, signedInUserHint } from './snapshot-cache'
import type {
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

type Bootstrap = Data & { me: User }

const EMPTY: Data = {
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

/** Straight from the client specification's capability table. */
const MATRIX: Record<Capability, Role[]> = {
  connect_sources: ['admin'],
  manage_records: ['admin', 'manager', 'staff'],
  // Deleting cascades down client → company → order, so staff do not get it.
  delete_records: ['admin', 'manager'],
  map_candidates: ['admin', 'manager', 'staff'],
  work_any_candidate: ['admin', 'manager'],
  reassign: ['admin', 'manager'],
  reopen_stage: ['admin'],
  todo_list: ['admin', 'manager'],
  user_management: ['admin'],
  export: ['admin'],
  all_reports: ['admin', 'manager'],
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
  mapCandidate: (candidateId: string, orderId: string) => Promise<{ refreshed: boolean } | null>
  unmap: (applicationId: string) => Promise<void>
  addRemark: (taskId: string, body: string) => Promise<void>
  recordCall: (candidateId: string, interested: string, remarks: string, called?: boolean) => Promise<void>
  markNotificationRead: (id?: string) => Promise<void>
  addCandidate: (payload: Record<string, unknown>) => Promise<{ id: string; ownerName?: string | null } | null>
  addClient: (payload: Record<string, unknown>) => Promise<{ id: string } | null>
  addCompany: (payload: Record<string, unknown>) => Promise<{ id: string } | null>
  /** Throw when the server refuses (a candidate still active or deployed below). */
  deleteClient: (id: string) => Promise<void>
  deleteCompany: (id: string) => Promise<void>
  deleteOrder: (id: string) => Promise<void>
  addDesignation: (payload: Record<string, unknown>) => Promise<void>
  addOrder: (payload: Record<string, unknown>) => Promise<void>
  addTodo: (payload: Record<string, unknown>) => Promise<void>
  toggleTodo: (id: string) => Promise<void>
  deleteTodo: (id: string) => Promise<void>
  reassign: (candidateIds: string[], toUserId: string, reason: string, restartClockReason?: string) => Promise<void>
}

const Ctx = createContext<StoreValue | null>(null)

/** How often the desk asks whether new mail/intake arrived (and re-reads the unread badge). */
const PULSE_MS = 20_000

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
    const { me: _who, ...rest } = hydrated.payload
    return { ...EMPTY, ...rest }
  })
  const [me, setMe] = useState<User | null>(hydrated?.payload.me ?? null)
  const [ready, setReady] = useState(Boolean(hydrated))
  const [loadError, setLoadError] = useState<string | null>(null)
  const [toast, setToast] = useState<Toast>(null)
  const [inboxUnread, setInboxUnread] = useState(0)
  const readInboxUnread = useRef<() => Promise<void>>(() => Promise.resolve())
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
          setMe(payload?.me ?? null)
          if (payload) {
            const { me: _who, ...rest } = payload
            setData({ ...EMPTY, ...rest })
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
    if (lastLoadedAt.current > 0 && Date.now() - lastLoadedAt.current < 5_000) return Promise.resolve()
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
      if (!me) setInboxUnread(0)
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
    const read = () => {
      if (document.visibilityState === 'visible') {
        // Retain usable data and retry next time if the probe/refresh fails.
        void observer.check().catch(() => {})
      }
    }
    readInboxUnread.current = async () => {
      if (document.visibilityState !== 'visible') return
      try { await observer.check() } catch { /* Keep the last known count; the next poll retries. */ }
    }
    const first = window.setTimeout(read, 1_200)
    const timer = window.setInterval(read, PULSE_MS)
    document.addEventListener('visibilitychange', read)
    return () => {
      observer.dispose()
      window.clearTimeout(first)
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', read)
      readInboxUnread.current = () => Promise.resolve()
    }
  }, [me?.id, ready, loadError, loadWorkspace])

  useEffect(() => {
    if (!me || !ready) return
    const version = authVersion.current
    const controller = new AbortController()
    let running = false
    const check = async () => {
      if (running || document.visibilityState !== 'visible') return
      running = true
      try {
        const items = await api.get<Notification[]>('/notifications', { signal: controller.signal })
        if (!controller.signal.aborted && version === authVersion.current) {
          const knownIds = new Set(currentData.current.notifications.map(item => item.id))
          const hasNew = items.some(item => !knownIds.has(item.id))
          setData((current) => {
            const readIds = new Set(current.notifications.filter(item => item.read).map(item => item.id))
            return { ...current, notifications: items.map(item => ({ ...item, read: item.read || readIds.has(item.id) })) }
          })
          if (hasNew) await loadWorkspace(false, false, true)
        }
      } catch (error) {
        if (!controller.signal.aborted && error instanceof ApiError && error.isUnauthorised) void loadWorkspace(false, false, true).catch(() => {})
      } finally { running = false }
    }
    const timer = window.setInterval(() => void check(), 30_000)
    return () => { controller.abort(); window.clearInterval(timer) }
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
        try { await api.post('/applications', { candidate_id: candidateId, order_id: orderId }) }
        catch (error) {
          if (isCurrentSession()) notify(error instanceof Error ? error.message : 'Mapping failed', 'warn')
          throw error
        }
        if (!isCurrentSession()) return null
        try {
          await refresh({ keepCurrent: true })
          if (!isCurrentSession()) return null
          notify('Mapped. Stage 1 task raised.')
          return { refreshed: true }
        } catch {
          if (!isCurrentSession()) return null
          notify('Candidate mapped. The directory could not refresh; refresh the results to see the change.', 'warn')
          return { refreshed: false }
        }
      },
      unmap: async (applicationId) => {
        await run(
          () => api.post(`/applications/${applicationId}/unmap`),
          'Unmapped. Open tasks cancelled; the candidate is back in the Data Bank.',
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
      addCandidate: async (payload) => {
        const created = await api.post<{ id: string; ownerName?: string | null }>('/candidates', payload)
        if (!isCurrentSession()) return null
        // A refresh failure must not discard the saved ID and create a duplicate on retry.
        try { await refresh({ keepCurrent: true }) }
        catch { if (isCurrentSession()) notify('Candidate saved. The directory could not refresh; reopen it to try again.', 'warn') }
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
      deleteOrder: async (id) => {
        await run(() => api.del(`/orders/${id}`), 'Order deleted')
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
    }
  }, [data, index, me, ready, loadError, retryStartup, inboxUnread, toast, signIn, signOut, refresh, ensureFresh, notify, run])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useStore() {
  const v = useContext(Ctx)
  if (!v) throw new Error('useStore must be used inside StoreProvider')
  return v
}
