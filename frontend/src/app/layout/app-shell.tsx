import { Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Bell,
  Briefcase,
  Building2,
  Factory,
  ChevronDown,
  Clock3,
  Database,
  GitBranch,
  FileBarChart,
  Inbox,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Layers2,
  Mail,
  MessageCircle,
  Menu,
  SquareCheckBig,
  Search,
  UserPlus,
  Users,
  ContactRound,
  X,
} from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { ago, initials, slaState } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { taskClockStart } from '@/shared/lib/task-time'
import { dataBankAttention } from '@/features/dashboard/attention'
import { agency } from '@/app/config/agency'
import { Dropdown, MenuItem, Toast } from '@/shared/ui/overlay'
import { IconButton } from '@/shared/ui/primitives'
import { PageLoading } from '@/shared/ui/page-loading'
import type { Capability } from '@/shared/state/crm-store'
import { AttendanceProvider } from '@/features/attendance/attendance-context'
import { AttendanceClock } from '@/features/attendance/components/attendance-clock'

interface NavItem {
  to: string
  label: string
  icon: typeof LayoutDashboard
  capability?: Capability
  supervisorsOnly?: boolean
  count?: 'tasks' | 'dataBank'
  /** Pages beyond the PRD's ten-item sidebar stay mounted and searchable, but are not listed in the menu. */
  extension?: boolean
  group: 'Overview' | 'Recruitment' | 'Workspace'
}

const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, group: 'Overview' },
  { to: '/inbox', label: 'Inbox', icon: Inbox, group: 'Overview' },
  { to: '/whatsapp', label: 'WhatsApp', icon: MessageCircle, group: 'Overview', extension: true },
  { to: '/clients', label: 'Clients', icon: Building2, group: 'Recruitment' },
  { to: '/companies', label: 'Companies', icon: Factory, group: 'Recruitment', extension: true },
  { to: '/jobs', label: 'Jobs / Positions', icon: Briefcase, group: 'Recruitment' },
  { to: '/add-candidate', label: 'Add Candidate', icon: UserPlus, group: 'Recruitment' },
  { to: '/data-bank', label: 'Data Bank', icon: Database, count: 'dataBank', group: 'Recruitment' },
  { to: '/mapped', label: 'Mapped Candidates', icon: GitBranch, group: 'Recruitment', extension: true },
  { to: '/tasks', label: 'Tasks', icon: ListChecks, count: 'tasks', group: 'Workspace' },
  { to: '/staff-directory', label: 'Staff Directory', icon: ContactRound, supervisorsOnly: true, group: 'Workspace', extension: true },
  { to: '/attendance', label: 'Attendance', icon: Clock3, group: 'Workspace', extension: true },
  { to: '/todo', label: 'To-do List', icon: SquareCheckBig, capability: 'todo_list', group: 'Workspace' },
  { to: '/reports', label: 'Reports', icon: FileBarChart, group: 'Workspace' },
  { to: '/users', label: 'User Management', icon: Users, capability: 'user_management', group: 'Workspace' },
]

const NAV_GROUPS = ['Overview', 'Recruitment', 'Workspace'] as const

export function AppShell() {
  const store = useStore()
  const { can, toast, dismissToast } = store
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()

  const dataBank = useMemo(() => dataBankAttention(store.candidates), [store.candidates])
  const counts = useMemo(() => {
    const mine = store.visibleTasks.filter((t) => !t.completedAt && !t.cancelledAt)
    const overdue = mine.filter((t) => slaState(taskClockStart(t)).overdue).length
    return {
      tasks: mine.length,
      dataBank,
      overdue,
    }
  }, [store.visibleTasks, dataBank])

  // Reading mail happens inside the inbox; refresh the badge as soon as you leave it.
  const refreshInboxUnread = store.refreshInboxUnread
  const previousPath = useRef(location.pathname)
  useEffect(() => {
    const left = previousPath.current === '/inbox' && location.pathname !== '/inbox'
    previousPath.current = location.pathname
    if (left) void refreshInboxUnread()
  }, [location.pathname, refreshInboxUnread])

  const items = NAV.filter((n) => !n.extension && (!n.capability || can(n.capability)) && (!n.supervisorsOnly || store.me.role === 'admin' || store.me.role === 'manager'))

  return (
    <AttendanceProvider key={store.me.id}><div className={cn('app-shell flex min-h-screen bg-canvas', location.pathname === '/' && 'is-dashboard-view')}>
      {/* Sidebar */}
      <aside
        className={cn(
          'shell-sidebar fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-rule bg-white',
          'lg:sticky lg:bottom-auto lg:top-0 lg:h-screen lg:translate-x-0',
          'transition-transform duration-200',
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="shell-brand flex shrink-0 items-center justify-between gap-2">
          <NavLink to="/" className="shell-wordmark" aria-label={`${agency.name} dashboard`} onClick={() => setMobileOpen(false)}>
            <Layers2 size={25} strokeWidth={2.6} />
            <span>Care India<span className="shell-brand-caption">Recruitment workspace</span></span>
          </NavLink>
          <button
            className="shell-menu-close text-ink-400 hover:text-ink lg:hidden"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <nav className="shell-nav scrollbar-thin flex-1 overflow-y-auto" aria-label="Main navigation">
          {NAV_GROUPS.map((group) => (
            <div className="shell-nav-group" key={group}>
              <p className="shell-nav-group-label">{group}</p>
              {items.filter((item) => item.group === group).map((item) => {
                const badge =
                  item.count === 'tasks'
                      ? counts.tasks
                      : item.count === 'dataBank'
                        ? counts.dataBank
                        : undefined
                const danger = item.count === 'tasks' && counts.overdue > 0
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === '/'}
                    onClick={() => setMobileOpen(false)}
                    className={({ isActive }) =>
                      cn(
                        'shell-nav-item relative flex items-center transition-colors',
                        isActive ? 'is-active' : 'text-ink-600',
                      )
                    }
                  >
                    <item.icon className="h-4 w-4 shrink-0" />
                    <span className="shell-nav-label flex-1">{item.label}</span>
                    {badge != null && badge > 0 && (
                      <span
                        className={cn('shell-nav-count shrink-0 tnum', danger && 'has-overdue')}
                        title={item.count === 'dataBank' ? `${badge} candidate${badge === 1 ? '' : 's'} need review or a recruiter` : undefined}
                      >
                        {danger ? `${counts.overdue} late` : badge}
                      </span>
                    )}
                  </NavLink>
                )
              })}
            </div>
          ))}
        </nav>

        <footer className="shell-sidebar-footer">
          <AccountMenu />
          <button className="shell-logout-button" onClick={() => void store.signOut()} aria-label="Log out" title="Log out">
            <LogOut size={16} />
          </button>
        </footer>
      </aside>

      {mobileOpen && (
        <div
          className="fixed inset-0 z-30 bg-ink-900/50 lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden
        />
      )}

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onMenu={() => setMobileOpen(true)} />
        <main className="shell-main flex-1">
          <div className={cn('mx-auto max-w-[1600px]', location.pathname !== '/' && 'workspace-page')}>
            <Suspense fallback={<PageLoading />}>
              <Outlet />
            </Suspense>
          </div>
        </main>
      </div>

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={dismissToast} />}
    </div></AttendanceProvider>
  )
}

function AccountMenu({ compact = false }: { compact?: boolean }) {
  const { me, signOut } = useStore()
  return (
    <div className={compact ? 'shell-topbar-account' : 'shell-sidebar-account'}>
      <Dropdown
        align={compact ? 'right' : 'left'}
        width="w-[min(19rem,calc(100vw-2rem))]"
        trigger={({ open, toggle }) => (
          <button className={cn('shell-account-trigger', compact && 'is-compact')} onClick={toggle} aria-label={`Account menu for ${me.name}`} aria-expanded={open}>
            <span className="shell-profile-avatar">{initials(me.name)}</span>
            {!compact && <>
              <span>
                <strong>{me.name}</strong>
                <span>{me.role}</span>
              </span>
              <ChevronDown size={13} />
            </>}
          </button>
        )}
      >
        {(close) => (
          <>
            <div className="border-b border-rule-soft px-3 py-2">
              <p className="text-sm font-medium text-ink">{me.name}</p>
              <p className="truncate text-micro text-ink-400">{me.email}</p>
            </div>
            <MenuItem
              onClick={() => {
                close()
                void signOut()
              }}
              tone="danger"
            >
              Sign out
            </MenuItem>
          </>
        )}
      </Dropdown>
    </div>
  )
}

function Topbar({ onMenu }: { onMenu: () => void }) {
  const { notifications, inboxUnread, notify, markNotificationRead } = useStore()
  const navigate = useNavigate()
  const unread = notifications.filter((n) => !n.read).length
  const location = useLocation()
  const currentPage =
    NAV.find((item) => item.to === location.pathname)?.label ??
    (location.pathname.startsWith('/candidates/')
      ? 'Candidate profile'
      : location.pathname.startsWith('/clients/')
        ? 'Client details'
        : location.pathname.startsWith('/orders/')
          ? 'Order details'
          : location.pathname.startsWith('/staff-directory/')
            ? 'Employee record'
            : 'Workspace')

  return (
    <header
      className={cn(
        'shell-topbar sticky top-0 z-20 flex shrink-0 items-center gap-3 border-b border-rule bg-white',
        location.pathname !== '/' && 'is-workspace',
      )}
    >
      <button className="shell-menu-toggle text-ink-500 lg:hidden" onClick={onMenu} aria-label="Open navigation">
        <Menu className="h-5 w-5" />
      </button>

      <GlobalSearch />
      {location.pathname !== '/' && <div className="shell-page-heading">
        <p>
          <span>Workspace</span> / {currentPage}
        </p>
      </div>}

      <div className="shell-topbar-actions ml-auto flex items-center">
        <AttendanceClock compact />
        <button
          className="shell-icon-button"
          aria-label={inboxUnread > 0 ? `Open messages, ${inboxUnread} unread` : 'Open messages'}
          onClick={() => navigate('/inbox')}
        >
          <Mail size={18} strokeWidth={1.5} />
          {inboxUnread > 0 && <span className="shell-unread-dot" />}
        </button>
        <Dropdown
          width="w-[min(22rem,calc(100vw-2rem))] max-sm:fixed max-sm:left-4 max-sm:right-4 max-sm:top-[82px] max-sm:w-auto"
          trigger={({ toggle }) => (
            <IconButton
              className="shell-icon-button"
              label={`Notifications${unread ? `, ${unread} unread` : ''}`}
              onClick={toggle}
            >
              <span className="relative">
                <Bell className="h-[18px] w-[18px]" />
                {unread > 0 && (
                  <span className="shell-notification-count absolute -right-1 -top-1 flex h-3.5 min-w-[14px] items-center justify-center rounded-full px-[3px] text-[9px] font-semibold text-white tnum">
                    {unread}
                  </span>
                )}
              </span>
            </IconButton>
          )}
        >
          {(close) => (
            <>
              <div className="flex items-center justify-between border-b border-rule-soft px-3 py-2">
                <span className="text-xs font-medium text-ink">Notifications</span>
                {unread > 0 && (
                  <button
                    onClick={() => void markNotificationRead().catch((e) => notify(e.message, 'warn'))}
                    className="text-micro text-navy-700 hover:text-navy-600"
                  >
                    Mark all read
                  </button>
                )}
              </div>
              <div className="scrollbar-thin max-h-[22rem] overflow-y-auto">
                {notifications.length === 0 && (
                  <p className="px-3 py-6 text-center text-xs text-ink-400">
                    Nothing to catch up on.
                  </p>
                )}
                {notifications.map((n) => (
                  <button
                    key={n.id}
                    onClick={() => {
                      void markNotificationRead(n.id).catch((e) => notify(e.message, 'warn'))
                      if (n.linkTo) navigate(n.linkTo)
                      close()
                    }}
                    className={cn(
                      'flex w-full gap-2.5 border-b border-rule-soft px-3 py-2.5 text-left last:border-0 hover:bg-field',
                      !n.read && 'bg-navy-50/40',
                    )}
                  >
                    <span
                      className={cn(
                        'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
                        n.kind === 'escalation'
                          ? 'bg-stamp-600'
                          : n.kind === 'reminder'
                            ? 'bg-amber-600'
                            : n.read
                              ? 'bg-ink-300'
                              : 'bg-navy-600',
                      )}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline justify-between gap-2">
                        <span className="truncate text-xs font-medium text-ink">{n.title}</span>
                        <span className="shrink-0 text-micro text-ink-400">{ago(n.createdAt)}</span>
                      </span>
                      <span className="mt-0.5 block text-xs leading-snug text-ink-500">
                        {n.detail}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
        </Dropdown>
        <AccountMenu compact />
      </div>
    </header>
  )
}

function GlobalSearch() {
  const store = useStore()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const normalized = query.trim().toLowerCase()
  const matches = normalized
    ? [
        ...store.candidates
          .filter(
            (candidate) =>
              (store.can('work_any_candidate') || candidate.ownerId === store.me.id) &&
              `${candidate.name} ${candidate.ref} ${candidate.phone}`
                .toLowerCase()
                .includes(normalized),
          )
          .map((candidate) => ({
            label: candidate.name,
            detail: candidate.ref,
            to: `/candidates/${candidate.id}`,
          })),
        ...NAV.filter(
          (item) =>
            (!item.capability || store.can(item.capability)) &&
            (!item.supervisorsOnly || store.me.role === 'admin' || store.me.role === 'manager') &&
            item.label.toLowerCase().includes(normalized),
        ).map((item) => ({ label: item.label, detail: 'Go to page', to: item.to })),
      ].slice(0, 6)
    : []
  const choose = (to: string) => {
    navigate(to)
    setQuery('')
    setOpen(false)
  }
  return (
    <form
      className="shell-search"
      role="search"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false)
      }}
      onSubmit={(event) => {
        event.preventDefault()
        if (matches[0]) choose(matches[0].to)
      }}
    >
      <Search size={16} aria-hidden="true" />
      <input
        aria-label="Search candidates and pages"
        placeholder="Search anything..."
        value={query}
        onChange={(event) => {
          setQuery(event.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setOpen(false)
        }}
        autoComplete="off"
      />
      {open && normalized && (
        <div className="shell-search-results">
          {matches.length ? (
            matches.map((match) => (
              <button key={match.to} type="button" onClick={() => choose(match.to)}>
                <strong>{match.label}</strong>
                <span>{match.detail}</span>
              </button>
            ))
          ) : (
            <p>No results for “{query}”</p>
          )}
        </div>
      )}
    </form>
  )
}
