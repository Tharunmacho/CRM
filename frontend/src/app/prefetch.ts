/**
 * Warm the next screen while the pointer is still on its link.
 *
 * One delegated listener (not a handler per link) watches pointer-over and focus on every in-app link. After a short
 * hover (a pointer passing through the sidebar fetches nothing) it downloads that route's code and, for screens that
 * start with the shared form lists, reads them through the API cache. Each route is warmed once per page load, only
 * when the browser is idle, and never on a data-saver or very slow connection.
 */
import { createCoalescer } from '@/shared/lib/coalesce'

type Warm = () => Promise<unknown>

/** Same module specifiers as app/routes.tsx: the bundler gives one chunk per module, so this warms the real chunk. */
const code = {
  dashboard: () => import('@/screens/dashboard/dashboard'),
  inbox: () => import('@/screens/inbox/inbox'),
  whatsapp: () => import('@/screens/inbox/whatsapp'),
  clients: () => import('@/screens/clients/clients'),
  clientDetail: () => import('@/screens/clients/client-detail'),
  jobs: () => import('@/screens/jobs/jobs'),
  orderDetail: () => import('@/screens/jobs/order-detail'),
  addCandidate: () => import('@/screens/candidates/add-candidate'),
  dataBank: () => import('@/screens/candidates/data-bank'),
  leads: () => import('@/screens/leads/leads'),
  profile: () => import('@/screens/candidates/candidate-profile'),
  applicationForm: () => import('@/screens/candidates/application-form'),
  tasks: () => import('@/screens/tasks/tasks'),
  attendance: () => import('@/screens/attendance/attendance'),
  todo: () => import('@/screens/todos/todo'),
  reports: () => import('@/screens/reports/reports'),
  users: () => import('@/screens/users/users'),
  staffDirectory: () => import('@/screens/staff/staff-directory'),
}

/** Reads that the screen asks for first and that the API layer keeps for minutes (form-options.ts owns the cache rules). */
const formLists: Warm = () => import('@/features/form-options/use-form-options').then((module) => module.refreshFormOptions())
const suggestions: Warm = () => import('@/features/form-options/use-form-options').then((module) => module.refreshFormSuggestions())

const ROUTES: [RegExp, Warm[]][] = [
  [/^\/?$/, [code.dashboard]],
  [/^\/inbox\/?$/, [code.inbox]],
  [/^\/whatsapp\/?$/, [code.whatsapp]],
  [/^\/clients\/?$/, [code.clients]],
  [/^\/clients\/[^/]+\/?$/, [code.clientDetail]],
  [/^\/jobs\/?$/, [code.jobs]],
  [/^\/orders\/[^/]+\/?$/, [code.orderDetail]],
  [/^\/add-candidate\/?$/, [code.addCandidate, formLists, suggestions]],
  [/^\/data-bank\/?$/, [code.dataBank, formLists]],
  [/^\/leads(\/import)?\/?$/, [code.leads]],
  [/^\/candidates\/[^/]+\/?$/, [code.profile]],
  [/^\/candidates\/[^/]+\/application\/?$/, [code.applicationForm, formLists]],
  [/^\/tasks\/?$/, [code.tasks]],
  [/^\/attendance\/?$/, [code.attendance]],
  [/^\/todo\/?$/, [code.todo]],
  [/^\/(reports|staff-report)\/?$/, [code.reports]],
  [/^\/users\/?$/, [code.users]],
  [/^\/staff-directory\/?$/, [code.staffDirectory]],
]

export function warmablesFor(pathname: string): Warm[] {
  return ROUTES.find(([pattern]) => pattern.test(pathname))?.[1] ?? []
}

function slowConnection() {
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection
  return Boolean(connection?.saveData) || connection?.effectiveType === 'slow-2g' || connection?.effectiveType === '2g'
}

function whenIdle(work: () => void) {
  const idle = (window as Window & { requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number }).requestIdleCallback
  if (idle) idle.call(window, work, { timeout: 800 })
  else window.setTimeout(work, 0)
}

/** Start listening; returns the stop function. Safe to call once from the app providers. */
export function installPrefetch(): () => void {
  if (typeof document === 'undefined' || slowConnection()) return () => {}
  const warmed = new Set<string>()
  let target = ''
  const run = createCoalescer(() => {
    const path = target
    if (!path || warmed.has(path)) return
    warmed.add(path)
    whenIdle(() => { for (const warm of warmablesFor(path)) void warm().catch(() => { /* the screen asks again when it opens */ }) })
  }, { waitMs: 65 })

  const onEnter = (event: Event) => {
    const link = (event.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null
    if (!link || link.target === '_blank' || link.hasAttribute('download')) return
    let url: URL
    try { url = new URL(link.href, window.location.href) } catch { return }
    if (url.origin !== window.location.origin || url.pathname.startsWith('/api/')) return
    if (url.pathname === window.location.pathname) return
    target = url.pathname
    run()
  }
  const onLeave = () => { target = ''; run.cancel() }

  // The application dialog opens from most screens: fetch it once the page has settled.
  const dialogTimer = window.setTimeout(() => whenIdle(() => { void import('@/features/workflow/components/stage-modal-body').catch(() => {}) }), 4_000)

  document.addEventListener('pointerover', onEnter, { passive: true })
  document.addEventListener('focusin', onEnter, { passive: true })
  document.addEventListener('pointerout', onLeave, { passive: true })
  return () => {
    document.removeEventListener('pointerover', onEnter)
    document.removeEventListener('focusin', onEnter)
    document.removeEventListener('pointerout', onLeave)
    run.cancel()
    window.clearTimeout(dialogTimer)
  }
}
