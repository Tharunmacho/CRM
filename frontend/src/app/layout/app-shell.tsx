import { useMemo, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Bell,
  Briefcase,
  Building2,
  Factory,
  ChevronDown,
  Database,
  GitBranch,
  FileBarChart,
  Inbox,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Layers2,
  Mail,
  Menu,
  SquareCheckBig,
  Search,
  UserPlus,
  Users,
  X,
} from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { ago, initials, slaState } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { agency } from '@/app/config/agency'
import { Dropdown, MenuItem, Toast } from '@/shared/ui/overlay'
import { IconButton } from '@/shared/ui/primitives'
import type { Capability } from '@/shared/state/crm-store'

interface NavItem {
  to: string
  label: string
  icon: typeof LayoutDashboard
  capability?: Capability
  count?: 'inbox' | 'tasks'
}

/** The client's ten items, in the order they specified. */
const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/inbox', label: 'Inbox', icon: Inbox, count: 'inbox' },
  { to: '/clients', label: 'Clients', icon: Building2 },
  { to: '/companies', label: 'Companies', icon: Factory },
  { to: '/jobs', label: 'Jobs / Positions', icon: Briefcase },
  { to: '/add-candidate', label: 'Add Candidate', icon: UserPlus },
  { to: '/data-bank', label: 'Data Bank', icon: Database },
  { to: '/mapped', label: 'Mapped Candidates', icon: GitBranch },
  { to: '/tasks', label: 'Tasks', icon: ListChecks, count: 'tasks' },
  { to: '/todo', label: 'To-do List', icon: SquareCheckBig, capability: 'todo_list' },
  { to: '/reports', label: 'Reports', icon: FileBarChart },
  { to: '/users', label: 'User Management', icon: Users, capability: 'user_management' },
]

export function AppShell() {
  const store = useStore()
  const { can, toast, dismissToast } = store
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()

  const counts = useMemo(() => {
    const mine = store.visibleTasks.filter((t) => !t.completedAt && !t.cancelledAt)
    const overdue = mine.filter((t) => slaState(t.createdAt).overdue).length
    return {
      inbox: store.mailMessages.filter((m) => m.unread).length,
      tasks: mine.length,
      overdue,
    }
  }, [store.visibleTasks, store.mailMessages])

  const items = NAV.filter((n) => !n.capability || can(n.capability))

  return (
    <div className="app-shell flex min-h-screen bg-canvas">
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
          <div className="min-w-0" aria-label={agency.name}>
            <p className="shell-wordmark">
              <Layers2 size={25} strokeWidth={3} /> CARE INDIA
            </p>
          </div>
          <button
            className="text-ink-400 hover:text-ink lg:hidden"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <SidebarAccount />

        <nav className="shell-nav scrollbar-thin flex-1 overflow-y-auto">
          {items.map((item) => {
            const badge =
              item.count === 'inbox'
                ? counts.inbox
                : item.count === 'tasks'
                  ? counts.tasks
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
                    'shell-nav-item relative mb-0.5 flex items-center gap-2.5 rounded-control px-2.5 py-2 text-sm transition-colors',
                    isActive ? 'is-active' : 'text-ink-600',
                  )
                }
              >
                {() => (
                  <>
                    <item.icon className="h-4 w-4 shrink-0" />
                    <span className="flex-1 truncate">{item.label}</span>
                    {badge != null && badge > 0 && (
                      <span
                        className={cn('shell-nav-count shrink-0 tnum', danger && 'has-overdue')}
                      >
                        {danger ? `${counts.overdue} late` : badge}
                      </span>
                    )}
                  </>
                )}
              </NavLink>
            )
          })}
        </nav>

        <footer className="shell-sidebar-footer">
          <button className="shell-logout-button" onClick={() => void store.signOut()}>
            <LogOut size={16} /> Log out
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
          <div className={cn('mx-auto max-w-[1440px]', location.pathname !== '/' && 'workspace-page')}>
            <Outlet />
          </div>
        </main>
      </div>

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={dismissToast} />}
    </div>
  )
}

function SidebarAccount() {
  const { me, signOut } = useStore()
  return (
    <div className="shell-sidebar-account">
      <Dropdown
        align="left"
        width="w-[min(19rem,calc(100vw-2rem))]"
        trigger={({ toggle }) => (
          <button className="shell-account-trigger" onClick={toggle} aria-label="Account menu">
            <span className="shell-profile-avatar">{initials(me.name)}</span>
            <span>
              <strong>{me.name}</strong>
              <span>{me.role}</span>
            </span>
            <ChevronDown size={13} />
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
  const { notifications, mailMessages, notify } = useStore()
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
          : 'Workspace')

  return (
    <header
      className={cn(
        'shell-topbar sticky top-0 z-20 flex shrink-0 items-center gap-3 border-b border-rule bg-white',
        location.pathname !== '/' && 'is-workspace',
      )}
    >
      <button className="text-ink-500 lg:hidden" onClick={onMenu} aria-label="Open navigation">
        <Menu className="h-5 w-5" />
      </button>

      <div className="shell-page-heading">
        {location.pathname === '/' && <h1>{currentPage}</h1>}
        <p>
          <span>Workspace</span> / {location.pathname === '/' ? 'Overview' : currentPage}
        </p>
      </div>

      <div className="shell-topbar-actions ml-auto flex items-center">
        <GlobalSearch />
        <button
          className="shell-icon-button"
          aria-label="Open messages"
          onClick={() => navigate('/inbox')}
        >
          <Mail size={18} strokeWidth={1.5} />
          {mailMessages.some((message) => message.unread) && <span className="shell-unread-dot" />}
        </button>
        <Dropdown
          width="w-[min(22rem,calc(100vw-2rem))] max-sm:fixed max-sm:left-4 max-sm:right-4 max-sm:top-[61px] max-sm:w-auto"
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
                    onClick={() => notify('Notifications are not stored yet', 'warn')}
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
                          : n.kind === 'missed_update'
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
