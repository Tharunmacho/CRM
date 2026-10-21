import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useStore, type Capability } from '@/shared/state/crm-store'
import { EmptyState } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { PageBoundary, PageLoading } from '@/shared/ui/page-loading'

import LoginPage from '@/features/auth/pages/login'

const AppShell = lazy(() =>
  import('@/app/layout/app-shell').then((module) => ({ default: module.AppShell })),
)
const DashboardPage = lazy(() => import('@/features/dashboard/pages/dashboard'))
const InboxPage = lazy(() => import('@/features/inbox/pages/inbox'))
const WhatsAppPage = lazy(() => import('@/features/inbox/pages/whatsapp'))
const ClientsPage = lazy(() => import('@/features/clients/pages/clients'))
const ClientDetailPage = lazy(() => import('@/features/clients/pages/client-detail'))
const CompaniesPage = lazy(() => import('@/features/companies/pages/companies'))
const JobsPage = lazy(() => import('@/features/jobs/pages/jobs'))
const OrderDetailPage = lazy(() => import('@/features/jobs/pages/order-detail'))
const AddCandidatePage = lazy(() => import('@/features/candidates/pages/add-candidate'))
const DataBankPage = lazy(() => import('@/features/candidates/pages/data-bank'))
const MappedCandidatesPage = lazy(() => import('@/features/workflow/pages/mapped-candidates'))
const CandidateProfilePage = lazy(() => import('@/features/candidates/pages/candidate-profile'))
const TasksPage = lazy(() => import('@/features/tasks/pages/tasks'))
const AttendancePage = lazy(() => import('@/features/attendance/pages/attendance'))
const TodoPage = lazy(() => import('@/features/todos/pages/todo'))
const ReportsPage = lazy(() => import('@/features/reports/pages/reports'))
const UsersPage = lazy(() => import('@/features/users/pages/users'))

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
  if (!ready) return <PageLoading fullScreen />
  if (!authenticated) return <Navigate to="/login" replace />
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
            <Route
              path="*"
              element={
                <EmptyState
                  title="That page does not exist"
                  description="Check the address, or pick a section from the sidebar."
                />
              }
            />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </PageBoundary>
  )
}
