import { Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Bell,
  Briefcase,
  Building2,
  Check,
  ChevronDown,
  ChevronsUpDown,
  Clock3,
  ContactRound,
  Database,
  Factory,
  FileBarChart,
  GitBranch,
  Inbox,
  LayoutDashboard,
  Layers2,
  ListChecks,
  LogOut,
  Mail,
  Menu,
  MessageCircle,
  Moon,
  Search,
  SquareCheckBig,
  Sun,
  UserPlus,
  Users,
  X,
} from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { ago, initials, slaState } from '@/shared/lib/format'
import { PALETTES, useTheme } from '@/shared/lib/theme'
import { useStore } from '@/shared/state/crm-store'
import { taskClockStart } from '@/shared/lib/task-time'
import { agency } from '@/app/config/agency'
import { Dropdown, MenuItem, Toast } from '@/shared/ui/overlay'
import { PageLoading } from '@/shared/ui/page-loading'
import type { Capability } from '@/shared/state/crm-store'
import { AttendanceProvider } from '@/features/attendance/attendance-context'
import { AttendanceClock } from '@/features/attendance/components/attendance-clock'
import { CommandSearch } from './command-search'
import '@/styles/shell.css'
import '@/styles/surface.css'

interface NavItem {
  to: string
  label: string
  icon: typeof LayoutDashboard
  /** Colour of the icon tile; the sidebar gives every destination its own soft hue. */
  tone: string
  capability?: Capability
  supervisorsOnly?: boolean
  signal?: 'tasks' | 'inbox'
  /** Pages beyond the PRD's ten-item sidebar stay mounted and searchable, but are not listed in the menu. */
  extension?: boolean
  group: 'main' | 'Recruitment' | 'Workspace'
}

const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, tone: '#4f46e5', group: 'main' },
  { to: '/inbox', label: 'Inbox', icon: Inbox, tone: '#0891b2', signal: 'inbox', group: 'main' },
  { to: '/whatsapp', label: 'WhatsApp', icon: MessageCircle, tone: '#16a34a', group: 'main', extension: true },
  { to: '/clients', label: 'Clients', icon: Building2, tone: '#2563eb', group: 'Recruitment' },
  { to: '/companies', label: 'Companies', icon: Factory, tone: '#0d9488', group: 'Recruitment', extension: true },
  { to: '/jobs', label: 'Jobs / Positions', icon: Briefcase, tone: '#d97706', group: 'Recruitment' },
  { to: '/add-candidate', label: 'Add Candidate', icon: UserPlus, tone: '#9333ea', group: 'Recruitment' },
  { to: '/data-bank', label: 'Data Bank', icon: Database, tone: '#0d9488', group: 'Recruitment' },
  { to: '/mapped', label: 'Mapped Candidates', icon: GitBranch, tone: '#0891b2', group: 'Recruitment', extension: true },
  { to: '/tasks', label: 'Tasks', icon: ListChecks, tone: '#db2777', signal: 'tasks', group: 'Workspace' },
  { to: '/todo', label: 'To-do List', icon: SquareCheckBig, tone: '#16a34a', capability: 'todo_list', group: 'Workspace' },
  { to: '/reports', label: 'Reports', icon: FileBarChart, tone: '#7c3aed', group: 'Workspace' },
  { to: '/users', label: 'User Management', icon: Users, tone: '#ca8a04', capability: 'user_management', group: 'Workspace' },
  { to: '/staff-directory', label: 'Staff Directory', icon: ContactRound, tone: '#059669', supervisorsOnly: true, group: 'Workspace', extension: true },
  { to: '/attendance', label: 'Attendance', icon: Clock3, tone: '#7c3aed', group: 'Workspace', extension: true },
]

const GROUPS: { id: NavItem['group']; label?: string }[] = [
  { id: 'main' },
  { id: 'Recruitment', label: 'Recruitment' },
  { id: 'Workspace', label: 'Workspace' },
]

const COLLAPSE_KEY = 'crm-rail-collapsed'

export function AppShell() {
  const store = useStore()
  const { can, toast, dismissToast } = store
  const [mobileOpen, setMobileOpen] = useState(false)
  const [searching, setSearching] = useState(false)
  // Remembered choice wins; otherwise narrower windows start with the icon rail so tables keep their width.
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const stored = window.localStorage.getItem(COLLAPSE_KEY)
      if (stored === '1' || stored === '0') return stored === '1'
    } catch { /* optional */ }
    return window.innerWidth < 1366 && window.innerWidth >= 1024
  })
  const location = useLocation()
  const railRef = useRef<HTMLElement>(null)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const searchingRef = useRef(false)
  searchingRef.current = searching

  // The phone drawer behaves like a dialog: the page behind it does not scroll, Escape and the dark backdrop close it,
  // focus moves into it and comes back to the menu button, and Tab stays inside. It also closes on any page change.
  useEffect(() => {
    if (!mobileOpen) return undefined
    const rail = railRef.current
    const opener = menuButtonRef.current
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const frame = requestAnimationFrame(() => rail?.querySelector<HTMLElement>('.rail-mobile-close')?.focus())
    const wide = window.matchMedia('(min-width: 1024px)')
    const onWide = () => { if (wide.matches) setMobileOpen(false) }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setMobileOpen(false); return }
      if (event.key !== 'Tab' || !rail) return
      const items = Array.from(rail.querySelectorAll<HTMLElement>('a[href], button:not([disabled])')).filter((item) => item.getClientRects().length > 0)
      const first = items[0]
      const last = items[items.length - 1]
      if (!first || !last) return
      if (event.shiftKey && (document.activeElement === first || !rail.contains(document.activeElement))) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && (document.activeElement === last || !rail.contains(document.activeElement))) { event.preventDefault(); first.focus() }
    }
    wide.addEventListener('change', onWide)
    document.addEventListener('keydown', onKey)
    return () => {
      cancelAnimationFrame(frame)
      wide.removeEventListener('change', onWide)
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previousOverflow
      if (opener?.isConnected && !searchingRef.current) opener.focus()
    }
  }, [mobileOpen])
  useEffect(() => { setMobileOpen(false) }, [location.pathname])

  const overdue = useMemo(
    () => store.visibleTasks.filter((t) => !t.completedAt && !t.cancelledAt && slaState(taskClockStart(t)).overdue).length,
    [store.visibleTasks],
  )

  // Reading mail happens inside the inbox; refresh the badge as soon as you leave it.
  const refreshInboxUnread = store.refreshInboxUnread
  const previousPath = useRef(location.pathname)
  useEffect(() => {
    const left = previousPath.current === '/inbox' && location.pathname !== '/inbox'
    previousPath.current = location.pathname
    if (left) void refreshInboxUnread()
  }, [location.pathname, refreshInboxUnread])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setSearching((value) => !value) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const allowed = (item: NavItem) => (!item.capability || can(item.capability)) && (!item.supervisorsOnly || store.me.role === 'admin' || store.me.role === 'manager')
  const items = NAV.filter((item) => !item.extension && allowed(item))
  const searchable = NAV.filter(allowed).map((item) => ({ label: item.label, to: item.to, icon: item.icon }))
  const toggleCollapse = () => setCollapsed((value) => { try { window.localStorage.setItem(COLLAPSE_KEY, value ? '0' : '1') } catch { /* optional */ } return !value })

  return (
    <AttendanceProvider key={store.me.id}>
      <div className={cn('app-shell', collapsed && 'is-collapsed')}>
        <div className={cn('rail-backdrop', mobileOpen && 'is-open')} onClick={() => setMobileOpen(false)} aria-hidden="true" />
        <nav ref={railRef} className={cn('rail', collapsed && 'is-collapsed', mobileOpen && 'is-open')} aria-label="Main navigation">
          <div className="rail-brand">
            <NavLink to="/" className="rail-logo" aria-label={`${agency.name} dashboard`} onClick={() => setMobileOpen(false)}>
              <span className="rail-logo-mark"><Layers2 size={22} strokeWidth={2.4} /></span>
              <span className="rail-logo-text"><strong>Care India</strong><small>Recruitment workspace</small></span>
            </NavLink>
            <button type="button" className="rail-toggle rail-desktop-toggle" onClick={toggleCollapse} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} aria-expanded={!collapsed}><ChevronsUpDown size={14} /></button>
            <button type="button" className="rail-toggle rail-mobile-close" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><X size={17} /></button>
          </div>

          <button type="button" className="rail-search" aria-label="Search workspace" onClick={() => { searchingRef.current = true; setMobileOpen(false); setSearching(true) }}>
            <Search size={14} strokeWidth={1.6} /><span>Search</span><kbd>Ctrl K</kbd>
          </button>

          <div className="rail-scroll scrollbar-thin">
            {GROUPS.map((group, index) => {
              const list = items.filter((item) => item.group === group.id)
              if (!list.length) return null
              return (
                <div key={group.id} className={cn('rail-group', index === 0 && 'is-main')} role="group" aria-label={group.label ?? 'Overview'}>
                  {group.label && <p className="rail-section-label">{group.label}</p>}
                  {list.map((item) => {
                    const dot = (item.signal === 'tasks' && overdue > 0) || (item.signal === 'inbox' && store.inboxUnread > 0)
                    return (
                      <NavLink
                        key={item.to}
                        to={item.to}
                        end={item.to === '/'}
                        title={collapsed ? item.label : undefined}
                        onClick={() => setMobileOpen(false)}
                        className={({ isActive }) => cn('rail-item', isActive && 'is-active')}
                      >
                        <span className="rail-nav-icon" style={{ '--nav-icon-color': item.tone } as React.CSSProperties}><item.icon size={16} strokeWidth={1.7} /></span>
                        <span className="rail-item-label">{item.label}</span>
                        {dot && <i className={cn('rail-dot', item.signal === 'tasks' && 'is-alert')} aria-label={item.signal === 'tasks' ? `${overdue} overdue` : 'Unread messages'} />}
                      </NavLink>
                    )
                  })}
                </div>
              )
            })}
          </div>

          <RailFoot collapsed={collapsed} />
        </nav>

        <div className="shell-body">
          <Topbar menuRef={menuButtonRef} menuAlert={overdue > 0 || store.inboxUnread > 0} onMenu={() => setMobileOpen(true)} onSearch={() => setSearching(true)} />
          <main className="workspace">
            <div className={cn('workspace-inner', location.pathname !== '/' && 'workspace-page')}>
              <Suspense fallback={<PageLoading />}>
                <Outlet />
              </Suspense>
            </div>
          </main>
        </div>

        {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={dismissToast} />}
        <CommandSearch open={searching} onClose={() => setSearching(false)} pages={searchable} />
      </div>
    </AttendanceProvider>
  )
}

function RailFoot({ collapsed }: { collapsed: boolean }) {
  const { theme, palette, setTheme, setPalette } = useTheme()
  const current = PALETTES.find((option) => option.id === palette)
  return (
    <div className="rail-foot">
      <div className="rail-workspace-note"><span className="rail-workspace-dot" /><span className="rail-workspace-name">Care India CRM</span><small>Workspace</small></div>
      <div className="palette-picker" role="group" aria-label="Colour palette">
        <div className="palette-picker-label"><span>Colour</span><strong>{current?.label}</strong></div>
        <div className="palette-swatches">
          {PALETTES.map(({ id, label, swatch }) => (
            <button key={id} type="button" className={cn('palette-swatch', palette === id && 'is-on')} data-palette-id={id} onClick={() => setPalette(id)} aria-label={`${label} palette`} aria-pressed={palette === id} title={label}>
              <span className="palette-dot" style={{ background: swatch }}>{palette === id && <Check size={12} strokeWidth={2.8} />}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="theme-switch" role="group" aria-label="Colour theme">
        {([{ id: 'light', label: 'Light', icon: Sun }, { id: 'dark', label: 'Dark', icon: Moon }] as const).map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" className={cn('theme-switch-btn', theme === id && 'is-on')} onClick={() => setTheme(id)} aria-pressed={theme === id} title={`${label} theme`}>
            <Icon size={13} /><span className="rail-item-label">{label}</span>
          </button>
        ))}
      </div>
      {collapsed && <button type="button" className="rail-theme-mini" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label="Switch colour theme">{theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}</button>}
    </div>
  )
}

function ProfileMenu() {
  const { me, signOut } = useStore()
  return (
    <Dropdown
      align="right"
      width="w-[min(19rem,calc(100vw-2rem))]"
      trigger={({ open, toggle }) => (
        <button type="button" className="topbar-profile-trigger" onClick={toggle} aria-label={`Account menu for ${me.name}`} aria-expanded={open}>
          <span className="topbar-profile-avatar">{initials(me.name)}</span>
          <span className="topbar-profile-copy"><strong>{me.name}</strong><small>{me.email}</small></span>
          <ChevronDown size={12} aria-hidden="true" />
        </button>
      )}
    >
      {(close) => (
        <>
          <div className="border-b border-rule-soft px-3 py-2.5">
            <p className="text-sm font-medium text-ink">{me.name}</p>
            <p className="truncate text-micro text-ink-400">{me.email} · <span className="capitalize">{me.role}</span></p>
          </div>
          <MenuItem onClick={() => { close(); void signOut() }} tone="danger"><span className="inline-flex items-center gap-2"><LogOut size={14} />Sign out</span></MenuItem>
        </>
      )}
    </Dropdown>
  )
}

function Topbar({ onMenu, onSearch, menuRef, menuAlert }: { onMenu: () => void; onSearch: () => void; menuRef: React.RefObject<HTMLButtonElement>; menuAlert: boolean }) {
  const { me, notifications, inboxUnread, notify, markNotificationRead } = useStore()
  const navigate = useNavigate()
  const unread = notifications.filter((n) => !n.read).length
  const firstName = me.name.split(' ')[0]

  return (
    <header className="topbar">
      <button ref={menuRef} type="button" className="topbar-menu" onClick={onMenu} aria-label={menuAlert ? 'Open menu. Something needs your attention.' : 'Open menu'}>
        <Menu size={19} />{menuAlert && <i className="topbar-menu-dot" aria-hidden="true" />}
      </button>
      <h2 className="topbar-welcome" title={`Welcome back, ${me.name}`}><span className="topbar-welcome-lead">Welcome back</span><span className="topbar-welcome-comma">, </span><span className="topbar-welcome-name">{firstName}</span></h2>
      <div className="topbar-actions">
        <button type="button" className="topbar-icon-btn topbar-search-btn" onClick={onSearch} aria-label="Search workspace"><Search size={16} strokeWidth={1.7} /></button>
        <AttendanceClock compact />
        <button type="button" className="topbar-icon-btn topbar-mail-btn" aria-label={inboxUnread > 0 ? `Open messages, ${inboxUnread} unread` : 'Open messages'} onClick={() => navigate('/inbox')}>
          <Mail size={16} strokeWidth={1.7} />
          {inboxUnread > 0 && <span className="topbar-badge">{inboxUnread > 9 ? '9+' : inboxUnread}</span>}
        </button>
        <Dropdown
          width="w-[min(22rem,calc(100vw-2rem))] max-sm:fixed max-sm:left-4 max-sm:right-4 max-sm:top-[72px] max-sm:w-auto"
          label="Notifications"
          trigger={({ toggle }) => (
            <button type="button" className="topbar-icon-btn" onClick={toggle} aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}>
              <Bell size={16} strokeWidth={1.7} />
              {unread > 0 && <span className="topbar-badge">{unread > 9 ? '9+' : unread}</span>}
            </button>
          )}
        >
          {(close) => (
            <>
              <div className="flex items-center justify-between border-b border-rule-soft px-3 py-2.5">
                <span className="text-xs font-medium text-ink">Notifications</span>
                {unread > 0 && (
                  <button onClick={() => void markNotificationRead().catch((e) => notify(e.message, 'warn'))} className="text-micro text-ink-500 hover:text-ink">
                    Mark all as read
                  </button>
                )}
              </div>
              <div className="scrollbar-thin max-h-[min(22rem,calc(100dvh-9rem))] overflow-y-auto">
                {notifications.length === 0 && <p className="px-3 py-6 text-center text-xs text-ink-400">You have no notifications.</p>}
                {notifications.map((n) => (
                  <button
                    key={n.id}
                    onClick={() => {
                      void markNotificationRead(n.id).catch((e) => notify(e.message, 'warn'))
                      if (n.linkTo) navigate(n.linkTo)
                      close()
                    }}
                    className={cn('notif-row flex w-full gap-2.5 border-b border-rule-soft px-3 py-2.5 text-left last:border-0 hover:bg-field', !n.read && 'bg-field/60')}
                  >
                    <span className={cn('mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full', n.kind === 'escalation' ? 'bg-stamp-600' : n.kind === 'reminder' ? 'bg-amber-600' : n.read ? 'bg-ink-300' : 'bg-navy-600')} />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline justify-between gap-2">
                        <span className="notif-title text-xs font-medium text-ink">{n.title}</span>
                        <span className="shrink-0 text-micro text-ink-400">{ago(n.createdAt)}</span>
                      </span>
                      <span className="mt-0.5 block text-xs leading-snug text-ink-500">{n.detail}</span>
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
        </Dropdown>
        <ProfileMenu />
      </div>
    </header>
  )
}
