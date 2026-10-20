import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/app/layout/app-shell'
import { useStore, type Capability } from '@/shared/state/crm-store'
import { EmptyState } from '@/shared/ui/display'

import LoginPage from '@/features/auth/pages/login'
import DashboardPage from '@/features/dashboard/pages/dashboard'
import InboxPage from '@/features/inbox/pages/inbox'
import ClientsPage from '@/features/clients/pages/clients'
import ClientDetailPage from '@/features/clients/pages/client-detail'
import CompaniesPage from '@/features/companies/pages/companies'
import JobsPage from '@/features/jobs/pages/jobs'
import OrderDetailPage from '@/features/jobs/pages/order-detail'
import AddCandidatePage from '@/features/candidates/pages/add-candidate'
import DataBankPage from '@/features/candidates/pages/data-bank'
import MappedCandidatesPage from '@/features/workflow/pages/mapped-candidates'
import CandidateProfilePage from '@/features/candidates/pages/candidate-profile'
import TasksPage from '@/features/tasks/pages/tasks'
import TodoPage from '@/features/todos/pages/todo'
import ReportsPage from '@/features/reports/pages/reports'
import UsersPage from '@/features/users/pages/users'

/**
 * Nothing renders until the session has been checked, so a refresh does not flash the
 * login screen before landing you back where you were.
 */
function RequireAuth() {
  const { ready, authenticated } = useStore()

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas">
        <p className="text-sm text-ink-400">Loading…</p>
      </div>
    )
  }
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
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route index element={<DashboardPage />} />
        <Route path="inbox" element={<InboxPage />} />
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
  )
}
