import { lazy, Suspense, useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useStore, type Capability } from '@/shared/state/crm-store'
import { EmptyState } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { PageBoundary, PageLoading } from '@/shared/ui/page-loading'

import LoginPage from '@/screens/auth/login'
import NotFoundPage from '@/screens/not-found/not-found'

const loaders = {
  'inbox/inbox': () => import('@/screens/inbox/inbox'),
  'clients/clients': () => import('@/screens/clients/clients'),
  'clients/client-detail': () => import('@/screens/clients/client-detail'),
  'companies/companies': () => import('@/screens/companies/companies'),
  'jobs/jobs': () => import('@/screens/jobs/jobs'),
  'jobs/order-detail': () => import('@/screens/jobs/order-detail'),
  'candidates/add-candidate': () => import('@/screens/candidates/add-candidate'),
  'candidates/data-bank': () => import('@/screens/candidates/data-bank'),
  'candidates/candidate-profile': () => import('@/screens/candidates/candidate-profile'),
  'tasks/tasks': () => import('@/screens/tasks/tasks'),
  'dashboard/dashboard': () => import('@/screens/dashboard/dashboard'),
}

const loadShell = () => import('@/app/layout/app-shell').then((module) => ({ default: module.AppShell }))

/** The screen chunk each top-level URL needs first. */
const FIRST_SCREEN: [RegExp, keyof typeof loaders][] = [
  [/^\/?$/, 'dashboard/dashboard'],
  [/^\/inbox\/?$/, 'inbox/inbox'],
  [/^\/clients\/?$/, 'clients/clients'],
  [/^\/clients\/[^/]+\/?$/, 'clients/client-detail'],
  [/^\/companies\/?$/, 'companies/companies'],
  [/^\/jobs\/?$/, 'jobs/jobs'],
  [/^\/orders\/[^/]+\/?$/, 'jobs/order-detail'],
  [/^\/add-candidate\/?$/, 'candidates/add-candidate'],
  [/^\/data-bank\/?$/, 'candidates/data-bank'],
  [/^\/candidates\/[^/]+\/?$/, 'candidates/candidate-profile'],
  [/^\/tasks\/?$/, 'tasks/tasks'],
]

// Start downloading the shell and the screen for the URL being opened right now, in parallel with the
// session request, instead of waiting for the session answer and then for the shell and then for the screen
// (the screen is rendered inside the shell, so it was requested last).
if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
  void loadShell().catch(() => {})
  const first = FIRST_SCREEN.find(([pattern]) => pattern.test(window.location.pathname))
  if (first) void loaders[first[1]]().catch(() => {})
}

/**
 * Download the remaining screens once the shell is idle, one at a time and only when the browser has
 * nothing better to do, so they never compete with the data the user is waiting for.
 */
function preloadScreens() {
  const saveData = Boolean((navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData)
  if (saveData) return () => {}
  let stopped = false
  let timer = 0
  const queue = Object.values(loaders)
  const idle = (work: () => void) => {
    const request = (window as Window & { requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number }).requestIdleCallback
    if (request) request.call(window, work, { timeout: 3_000 })
    else window.setTimeout(work, 0)
  }
  const next = () => {
    const load = queue.shift()
    if (stopped || !load) return
    load().catch(() => {}).finally(() => { if (!stopped) timer = window.setTimeout(() => idle(next), 100) })
  }
  timer = window.setTimeout(() => idle(next), 2_500)
  return () => { stopped = true; window.clearTimeout(timer) }
}

const AppShell = lazy(loadShell)
const DashboardPage = lazy(loaders['dashboard/dashboard'])
const InboxPage = lazy(loaders['inbox/inbox'])
const WhatsAppPage = lazy(() => import('@/screens/inbox/whatsapp'))
const ClientsPage = lazy(loaders['clients/clients'])
const ClientDetailPage = lazy(loaders['clients/client-detail'])
const CompaniesPage = lazy(loaders['companies/companies'])
const JobsPage = lazy(loaders['jobs/jobs'])
const OrderDetailPage = lazy(loaders['jobs/order-detail'])
const AddCandidatePage = lazy(loaders['candidates/add-candidate'])
const DataBankPage = lazy(loaders['candidates/data-bank'])
const MappedCandidatesPage = lazy(() => import('@/screens/workflow/mapped-candidates'))
const CandidateProfilePage = lazy(loaders['candidates/candidate-profile'])
const TasksPage = lazy(loaders['tasks/tasks'])
const AttendancePage = lazy(() => import('@/screens/attendance/attendance'))
const TodoPage = lazy(() => import('@/screens/todos/todo'))
const ReportsPage = lazy(() => import('@/screens/reports/reports'))
const UsersPage = lazy(() => import('@/screens/users/users'))
const StaffDirectoryPage = lazy(() => import('@/screens/staff/staff-directory'))
const StaffDetailPage = lazy(() => import('@/screens/staff/staff-detail'))

/**
 * Nothing renders until the session has been checked, so a refresh does not flash the
 * login screen before landing you back where you were.
 */
function RequireAuth() {
  const { ready, authenticated, loadError, retryStartup } = useStore()

  if (loadError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas px-6">
        <div role="alert" className="max-w-sm text-center">
          <h1 className="text-h3 font-semibold text-ink">Could not load your workspace</h1>
          <p className="mt-2 text-sm text-ink-500">{loadError}</p>
          <Button className="mt-4" variant="primary" onClick={() => void retryStartup()}>
            Try again
          </Button>
        </div>
      </div>
    )
  }
  if (!ready) return <PageLoading fullScreen onRetry={() => void retryStartup()} />
  if (!authenticated) return <Navigate to="/login" replace />
  return <PreloadedShell />
}

function PreloadedShell() {
  useEffect(preloadScreens, [])
  return <AppShell />
}

/** Role gate. The sidebar already hides these, so this catches direct URL entry. */
function Require({ capability, children }: { capability: Capability; children: JSX.Element }) {
  const { can, me } = useStore()
  if (can(capability)) return children
  return (
    <EmptyState
      title="This page is not available to your role"
      description={`You are signed in as ${me.name} (${me.role}). Ask an admin if you need access.`}
    />
  )
}

function RequireStaffSupervisor({ children }: { children: JSX.Element }) {
  const { me } = useStore()
  if (me.role === 'admin' || me.role === 'manager') return children
  return <EmptyState title="Staff directory is available to supervisors" description="Administrators and managers can review employee availability and team workload." />
}

export default function AppRoutes() {
  return (
    <PageBoundary>
      <Suspense fallback={<PageLoading fullScreen />}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route index element={<DashboardPage />} />
            <Route path="inbox" element={<InboxPage />} />
            <Route path="whatsapp" element={<WhatsAppPage />} />
            <Route path="clients" element={<ClientsPage />} />
            <Route path="clients/:id" element={<ClientDetailPage />} />
            <Route path="companies" element={<CompaniesPage />} />
            <Route path="jobs" element={<JobsPage />} />
            <Route path="orders/:id" element={<OrderDetailPage />} />
            <Route path="add-candidate" element={<AddCandidatePage />} />
            <Route path="data-bank" element={<DataBankPage />} />
            <Route path="mapped" element={<MappedCandidatesPage />} />
            <Route path="candidates/:id" element={<CandidateProfilePage />} />
            <Route path="tasks" element={<TasksPage />} />
            <Route path="attendance" element={<AttendancePage />} />
            <Route path="staff-directory" element={<RequireStaffSupervisor><StaffDirectoryPage /></RequireStaffSupervisor>} />
            <Route path="staff-directory/:id" element={<RequireStaffSupervisor><StaffDetailPage /></RequireStaffSupervisor>} />
            <Route
              path="todo"
              element={
                <Require capability="todo_list">
                  <TodoPage />
                </Require>
              }
            />
            <Route path="reports" element={<ReportsPage />} />
            <Route
              path="users"
              element={
                <Require capability="user_management">
                  <UsersPage />
                </Require>
              }
            />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </PageBoundary>
  )
}
