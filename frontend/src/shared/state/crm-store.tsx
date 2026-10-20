import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { ApiError, api } from '@/shared/api/client'
import { isSameDay } from '@/shared/lib/format'
import type {
  Application,
  Candidate,
  Client,
  Company,
  Designation,
  MailAccount,
  MailMessage,
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
  mailAccounts: MailAccount[]
  mailMessages: MailMessage[]
  notifications: Notification[]
}

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
  mailAccounts: [],
  mailMessages: [],
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
  toast: Toast

  signIn: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  refresh: () => Promise<void>
  notify: (message: string, tone?: 'ok' | 'warn') => void
  dismissToast: () => void

  can: (capability: Capability) => boolean

  candidate: (id: string) => Candidate | undefined
  client: (id: string) => Client | undefined
  company: (id: string) => Company | undefined
  order: (id: string) => Order | undefined
  designation: (id: string) => Designation | undefined
  user: (id: string) => User | undefined
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
  mapCandidate: (candidateId: string, orderId: string) => Promise<void>
  unmap: (applicationId: string) => Promise<void>
  addRemark: (taskId: string, body: string) => Promise<void>
  recordCall: (candidateId: string, interested: string, remarks: string) => Promise<void>
  addCandidate: (payload: Record<string, unknown>) => Promise<{ id: string } | null>
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
  reassign: (candidateIds: string[], toUserId: string) => Promise<void>
}

const Ctx = createContext<StoreValue | null>(null)

export function StoreProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<Data>(EMPTY)
  const [me, setMe] = useState<User | null>(null)
  const [ready, setReady] = useState(false)
  const [toast, setToast] = useState<Toast>(null)

  const notify = useCallback((message: string, tone: 'ok' | 'warn' = 'ok') => {
    setToast({ id: Date.now(), message, tone })
  }, [])

  const refresh = useCallback(async () => {
    const payload = await api.get<Data & { me: User }>('/bootstrap')
    const { me: who, ...rest } = payload
    setMe(who)
    setData({ ...EMPTY, ...rest })
  }, [])

  // Restore the session on load, so a refresh does not sign you out.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        await refresh()
      } catch {
        if (!cancelled) setMe(null)
      } finally {
        if (!cancelled) setReady(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [refresh])

  /** Run a write, reload, and turn a failure into something the user can read. */
  const run = useCallback(
    async <T,>(work: () => Promise<T>, success?: string): Promise<T | null> => {
      try {
        const result = await work()
        await refresh()
        if (success) notify(success)
        return result
      } catch (err) {
        const e = err as ApiError
        notify(e.message || 'Something went wrong', 'warn')
        throw e
      }
    },
    [refresh, notify],
  )

  const signIn = useCallback(
    async (email: string, password: string) => {
      await api.post('/auth/login', { email, password })
      await refresh()
    },
    [refresh],
  )

  const signOut = useCallback(async () => {
    try {
      await api.post('/auth/logout')
    } catch {
      // The cookie may already be gone; clearing local state still signs you out here.
    } finally {
      setMe(null)
      setData(EMPTY)
      setToast(null)
    }
  }, [])

  const value = useMemo<StoreValue>(() => {
    const openTasks = data.tasks.filter((t) => !t.completedAt && !t.cancelledAt)
    const can = (c: Capability) => (me ? MATRIX[c].includes(me.role) : false)

    return {
      ...data,
      me: me as User,
      ready,
      authenticated: me !== null,
      toast,
      signIn,
      signOut,
      refresh,
      notify,
      dismissToast: () => setToast(null),
      can,

      candidate: (id) => data.candidates.find((c) => c.id === id),
      client: (id) => data.clients.find((c) => c.id === id),
      company: (id) => data.companies.find((c) => c.id === id),
      order: (id) => data.orders.find((o) => o.id === id),
      designation: (id) => data.designations.find((d) => d.id === id),
      user: (id) => data.users.find((u) => u.id === id),
      orderLabel: (id) => {
        const o = data.orders.find((x) => x.id === id)
        if (!o) return '—'
        const des = data.designations.find((d) => d.id === o.designationId)
        const co = data.companies.find((c) => c.id === o.companyId)
        return `${des?.title ?? 'Role'} · ${co?.name ?? 'Company'}`
      },
      openTasks,
      myOpenTasks: openTasks.filter((t) => t.ownerId === me?.id),
      // Staff see their own work; admin and managers see the whole desk.
      visibleTasks: me?.role === 'staff' ? data.tasks.filter((t) => t.ownerId === me.id) : data.tasks,
      activeApplicationFor: (candidateId) =>
        data.applications.find((a) => a.candidateId === candidateId && a.status === 'active'),
      applicationsFor: (candidateId) => data.applications.filter((a) => a.candidateId === candidateId),
      remarksFor: (taskId) =>
        data.remarks
          .filter((r) => r.taskId === taskId)
          .sort((a, b) => +new Date(b.createdAt) - +new Date(a.createdAt)),
      updatedToday: (taskId) =>
        data.remarks.some((r) => r.taskId === taskId && isSameDay(r.createdAt, new Date())),

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
        await run(
          () => api.post('/applications', { candidate_id: candidateId, order_id: orderId }),
          'Mapped. Stage 1 task raised.',
        )
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
      recordCall: async (candidateId, interested, remarks) => {
        await run(
          () => api.post(`/candidates/${candidateId}/call`, { called: true, interested, remarks }),
          interested === 'no'
            ? 'Marked not interested. The candidate stays in the Data Bank.'
            : 'Call outcome recorded',
        )
      },
      addCandidate: (payload) =>
        run(() => api.post<{ id: string }>('/candidates', payload), 'Candidate saved and assigned'),
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
        await run(() => api.patch(`/todos/${id}`))
      },
      deleteTodo: async (id) => {
        await run(() => api.del(`/todos/${id}`))
      },
      reassign: async (candidateIds, toUserId) => {
        await run(() => api.post('/candidates/reassign', { candidateIds, toUserId }), 'Reassigned')
      },
    }
  }, [data, me, ready, toast, signIn, signOut, refresh, notify, run])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useStore() {
  const v = useContext(Ctx)
  if (!v) throw new Error('useStore must be used inside StoreProvider')
  return v
}
