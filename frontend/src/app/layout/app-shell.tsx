import { Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Bell,
  Briefcase,
  Building2,
  Check,
  ChevronDown,
  Clock3,
  ContactRound,
  Database,
  Factory,
  FileBarChart,
  GitBranch,
  Inbox,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Mail,
  Menu,
  MessageCircle,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  SquareCheckBig,
  Sun,
  UserPlus,
  Users,
  X,
} from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { ago, initials, slaState } from '@/shared/lib/format'
import { useTheme } from '@/shared/lib/theme'
import { useStore } from '@/shared/state/crm-store'
import { taskClockStart } from '@/shared/lib/task-time'
import { useAgency } from '@/shared/state/use-agency'
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
  capability?: Capability
  supervisorsOnly?: boolean
  signal?: 'tasks' | 'inbox'
  /** Pages beyond the PRD's ten-item sidebar stay mounted and searchable, but are not listed in the menu. */
  extension?: boolean
  group: 'main' | 'Recruitment' | 'Workspace'
}

const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, group: 'main' },
  { to: '/inbox', label: 'Inbox', icon: Inbox, signal: 'inbox', group: 'main' },
  { to: '/whatsapp', label: 'WhatsApp', icon: MessageCircle, group: 'main', extension: true },
  { to: '/clients', label: 'Clients', icon: Building2, group: 'Recruitment' },
  { to: '/companies', label: 'Companies', icon: Factory, group: 'Recruitment', extension: true },
  { to: '/jobs', label: 'Jobs / Positions', icon: Briefcase, group: 'Recruitment' },
  { to: '/add-candidate', label: 'Add Candidate', icon: UserPlus, group: 'Recruitment' },
  { to: '/data-bank', label: 'Data Bank', icon: Database, group: 'Recruitment' },
  { to: '/mapped', label: 'Matched Candidates', icon: GitBranch, group: 'Recruitment', extension: true },
  { to: '/tasks', label: 'Tasks', icon: ListChecks, signal: 'tasks', group: 'Workspace' },
  { to: '/todo', label: 'To-do List', icon: SquareCheckBig, capability: 'todo_list', group: 'Workspace' },
  { to: '/reports', label: 'Reports', icon: FileBarChart, group: 'Workspace' },
  { to: '/users', label: 'User Management', icon: Users, capability: 'user_management', group: 'Workspace' },
  { to: '/staff-directory', label: 'Staff Directory', icon: ContactRound, supervisorsOnly: true, group: 'Workspace', extension: true },
  { to: '/attendance', label: 'Attendance', icon: Clock3, group: 'Workspace', extension: true },
]

const GROUPS: { id: NavItem['group']; label?: string }[] = [
  { id: 'main' },
  { id: 'Recruitment', label: 'Recruitment' },
  { id: 'Workspace', label: 'Workspace' },
]

const COLLAPSE_KEY = 'crm-rail-collapsed'

/**
 * The Care India Overseas logo for the attached header box: the full logo (figure, name, "Overseas Pvt.Ltd") and, for
 * the 72px icon rail, the figure alone. Light and dark variants are both in the markup; CSS shows the one for the theme.
 */
export function BrandLogo() {
  return (
    <>
      <img className="rail-logo-full is-light-only" src="/brand/care-india-logo-tight.png" alt="" width={768} height={400} decoding="async" draggable={false} />
      <img className="rail-logo-full is-dark-only" src="/brand/care-india-logo-tight-dark.png" alt="" width={768} height={400} decoding="async" draggable={false} />
      <img className="rail-logo-mark is-light-only" src="/brand/care-india-mark-fit.png" alt="" width={116} height={233} decoding="async" draggable={false} />
      <img className="rail-logo-mark is-dark-only" src="/brand/care-india-mark-dark-fit.png" alt="" width={116} height={233} decoding="async" draggable={false} />
    </>
  )
}

const GROUPS_KEY = 'crm-nav-groups'
/** Which menu groups a person folded away; remembered per user. Everything is open by default. */
function readClosedGroups(userId: string): string[] {
  try {
    const all = JSON.parse(window.localStorage.getItem(GROUPS_KEY) ?? '{}') as Record<string, unknown>
    const mine = all[userId]
    return Array.isArray(mine) ? mine.filter((id): id is string => typeof id === 'string') : []
  } catch { return [] }
}
function writeClosedGroups(userId: string, closed: string[]) {
  try {
    const all = JSON.parse(window.localStorage.getItem(GROUPS_KEY) ?? '{}') as Record<string, unknown>
    window.localStorage.setItem(GROUPS_KEY, JSON.stringify({ ...(all && typeof all === 'object' ? all : {}), [userId]: closed }))
  } catch { /* optional */ }
}

const badgeText = (count: number) => (count > 99 ? '99+' : String(count))

export function AppShell() {
  const store = useStore()
  const agency = useAgency()
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
  const [closedGroups, setClosedGroups] = useState<string[]>(() => readClosedGroups(store.me.id))
  const [peek, setPeek] = useState(false)
  const hoverRef = useRef(false)
  const peekTimer = useRef(0)
  const [tip, setTip] = useState<{ label: string; top: number; left: number } | null>(null)
  const [bar, setBar] = useState<{ top: number; height: number } | null>(null)
  const [barReady, setBarReady] = useState(false)
  const navRef = useRef<HTMLDivElement>(null)
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

  const toggleGroup = (id: string) => setClosedGroups((current) => {
    const next = current.includes(id) ? current.filter((value) => value !== id) : [...current, id]
    writeClosedGroups(store.me.id, next)
    return next
  })
  const showTip = useCallback((label: string, element: HTMLElement) => {
    const box = element.getBoundingClientRect()
    setTip({ label, top: box.top + box.height / 2, left: box.right + 10 })
  }, [])
  const hideTip = useCallback(() => setTip(null), [])


  // One bar in the logo green slides (150ms) to the active page; it hides when that page sits in a folded group.
  const measureBar = useCallback(() => {
    const nav = navRef.current
    const active = nav?.querySelector<HTMLElement>('.rail-item.is-active')
    if (!nav || !active || active.offsetParent === null || active.offsetHeight === 0) { setBar(null); return }
    const height = Math.round(active.offsetHeight * 0.64)
    const top = Math.round(active.offsetTop + (active.offsetHeight - height) / 2)
    setBar((current) => (current && current.top === top && current.height === height ? current : { top, height }))
  }, [])
  useLayoutEffect(() => { measureBar() }, [measureBar, location.pathname, closedGroups, collapsed, mobileOpen, store.me.role])
  useEffect(() => {
    const frame = requestAnimationFrame(() => setBarReady(true))
    const nav = navRef.current
    if (!nav || typeof ResizeObserver === 'undefined') return () => cancelAnimationFrame(frame)
    const observer = new ResizeObserver(() => measureBar())
    observer.observe(nav)
    window.addEventListener('resize', measureBar)
    return () => { cancelAnimationFrame(frame); observer.disconnect(); window.removeEventListener('resize', measureBar) }
  }, [measureBar])

  const allowed = (item: NavItem) => (!item.capability || can(item.capability)) && (!item.supervisorsOnly || store.me.role === 'admin' || store.me.role === 'manager')
  const items = NAV.filter((item) => !item.extension && allowed(item))
  const searchable = NAV.filter(allowed).map((item) => ({ label: item.label, to: item.to, icon: item.icon }))
  // The icon rail "peeks": hovering it (after a short intent delay) or tabbing into it opens the full panel over the page
  // without pushing it; leaving or Escape closes it again. The pin button keeps it open for good (and remembers that).
  const peeking = collapsed && peek
  const dock = collapsed && !peek
  const pinLabel = collapsed ? 'Pin sidebar' : 'Unpin sidebar'
  const closePeek = useCallback(() => { window.clearTimeout(peekTimer.current); setPeek(false) }, [])
  const canHover = () => window.innerWidth >= 1024 && window.matchMedia?.('(hover: hover) and (pointer: fine)').matches
  const onRailEnter = (event: React.PointerEvent) => {
    if (event.pointerType !== 'mouse' || !collapsed || !canHover()) return
    hoverRef.current = true
    window.clearTimeout(peekTimer.current)
    peekTimer.current = window.setTimeout(() => setPeek(true), 180)
  }
  const onRailLeave = () => {
    hoverRef.current = false
    window.clearTimeout(peekTimer.current)
    if (!railRef.current?.contains(document.activeElement) || !(document.activeElement as HTMLElement | null)?.matches?.(':focus-visible')) setPeek(false)
  }
  const onRailFocus = (event: React.FocusEvent) => {
    if (!collapsed || !canHover()) return
    if ((event.target as HTMLElement).matches?.(':focus-visible')) { window.clearTimeout(peekTimer.current); setPeek(true) }
  }
  const onRailBlur = (event: React.FocusEvent) => {
    if (railRef.current?.contains(event.relatedTarget as Node | null)) return
    if (!hoverRef.current) setPeek(false)
  }
  const onRailKey = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape' && peek) { event.stopPropagation(); closePeek() }
  }
  useEffect(() => () => window.clearTimeout(peekTimer.current), [])
  useEffect(() => { if (!collapsed) closePeek() }, [collapsed, closePeek])
  useEffect(() => { if (!dock) setTip(null) }, [dock])
  const order = useMemo(() => Object.fromEntries(items.map((item, index) => [item.to, index])) as Record<string, number>, [items])

  const toggleCollapse = () => { hideTip(); setPeek(false); hoverRef.current = false; setCollapsed((value) => { try { window.localStorage.setItem(COLLAPSE_KEY, value ? '0' : '1') } catch { /* optional */ } return !value }) }

  return (
    <AttendanceProvider key={store.me.id}>
      <div className={cn('app-shell', collapsed && 'is-collapsed')}>
        <div className={cn('rail-backdrop', mobileOpen && 'is-open')} onClick={() => setMobileOpen(false)} aria-hidden="true" />
        <nav
          ref={railRef}
          className={cn('rail', collapsed && 'is-collapsed', dock && 'is-dock', peeking && 'is-peek', mobileOpen && 'is-open')}
          aria-label="Main navigation"
          onPointerEnter={onRailEnter}
          onPointerLeave={onRailLeave}
          onFocusCapture={onRailFocus}
          onBlurCapture={onRailBlur}
          onKeyDown={onRailKey}
        >
          <div className="rail-panel">
            <div className="rail-brand">
              <NavLink to="/" className="rail-logo" aria-label={`${agency.name} dashboard`} title={agency.name} onClick={() => { setMobileOpen(false); closePeek() }}>
                <BrandLogo />
              </NavLink>
              <button type="button" className="rail-toggle rail-desktop-toggle" onClick={toggleCollapse} aria-label={pinLabel} aria-pressed={!collapsed} title={pinLabel} tabIndex={dock ? -1 : undefined}>
                {collapsed ? <PanelLeftOpen size={16} strokeWidth={1.75} /> : <PanelLeftClose size={16} strokeWidth={1.75} />}
              </button>
              <button type="button" className="rail-toggle rail-mobile-close" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><X size={17} /></button>
            </div>

            <button
              type="button"
              className="rail-search"
              aria-label="Search workspace"
              aria-keyshortcuts="Control+K"
              onClick={() => { searchingRef.current = true; setMobileOpen(false); closePeek(); hideTip(); setSearching(true) }}
              onMouseEnter={(event) => dock && showTip('Search, Ctrl K', event.currentTarget)}
              onMouseLeave={hideTip}
              onFocus={(event) => dock && showTip('Search, Ctrl K', event.currentTarget)}
              onBlur={hideTip}
            >
              <Search size={16} strokeWidth={1.75} /><span>Search</span><kbd>Ctrl K</kbd>
            </button>

            <div className="rail-scroll scrollbar-thin">
              <div className="rail-nav" ref={navRef}>
                <span className={cn('rail-indicator', barReady && 'is-ready')} aria-hidden="true" style={bar ? { transform: `translateY(${bar.top}px)`, height: bar.height, opacity: 1 } : { opacity: 0 }} />
                {GROUPS.map((group, index) => {
                  const list = items.filter((item) => item.group === group.id)
                  if (!list.length) return null
                  const closed = Boolean(group.label) && closedGroups.includes(group.id) && !collapsed
                  const signals = list.some((item) => (item.signal === 'tasks' && overdue > 0) || (item.signal === 'inbox' && store.inboxUnread > 0))
                  return (
                    <div key={group.id} className={cn('rail-group', index === 0 && 'is-main', closed && 'is-closed')} role="group" aria-label={group.label ?? 'Overview'}>
                      {group.label && (
                        <button type="button" className="rail-section-label" aria-expanded={!closed} aria-controls={`rail-list-${group.id}`} onClick={() => toggleGroup(group.id)} title={closed ? `Show ${group.label}` : `Hide ${group.label}`} tabIndex={dock ? -1 : undefined}>
                          <span>{group.label}</span>
                          {closed && signals && <i className="rail-section-dot" aria-label="Something needs your attention" />}
                          <ChevronDown size={14} strokeWidth={2} aria-hidden="true" className="rail-section-chevron" />
                        </button>
                      )}
                      <div className="rail-list-wrap" id={`rail-list-${group.id}`}>
                        <div className="rail-list">
                          {list.map((item) => {
                            const count = item.signal === 'tasks' ? overdue : item.signal === 'inbox' ? store.inboxUnread : 0
                            return (
                              <NavLink
                                key={item.to}
                                to={item.to}
                                end={item.to === '/'}
                                tabIndex={closed ? -1 : undefined}
                                style={{ '--i': order[item.to] } as React.CSSProperties}
                                onClick={() => { setMobileOpen(false); hideTip(); closePeek() }}
                                onMouseEnter={(event) => dock && showTip(item.label, event.currentTarget)}
                                onMouseLeave={hideTip}
                                onFocus={(event) => dock && showTip(item.label, event.currentTarget)}
                                onBlur={hideTip}
                                className={({ isActive }) => cn('rail-item', isActive && 'is-active')}
                              >
                                <span className="rail-nav-icon" aria-hidden="true"><item.icon size={20} strokeWidth={1.75} /></span>
                                <span className="rail-item-label">{item.label}</span>
                                {count > 0 && <span className={cn('rail-badge', item.signal === 'tasks' && 'is-alert')} role="img" aria-label={item.signal === 'tasks' ? `${count} overdue` : `${count} unread`}>{badgeText(count)}</span>}
                              </NavLink>
                            )
                          })}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            <RailFoot dock={dock} pinLabel={pinLabel} onToggle={toggleCollapse} showTip={showTip} hideTip={hideTip} />
          </div>
          {tip && dock && <span className="rail-tip" role="tooltip" style={{ top: tip.top, left: tip.left }}>{tip.label}</span>}
        </nav>

        <div className="shell-body">
          <Topbar menuRef={menuButtonRef} menuAlert={overdue > 0 || store.inboxUnread > 0} onMenu={() => setMobileOpen(true)} />
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

function RailFoot({ dock, pinLabel, onToggle, showTip, hideTip }: { dock: boolean; pinLabel: string; onToggle: () => void; showTip: (label: string, element: HTMLElement) => void; hideTip: () => void }) {
  const { theme, setTheme } = useTheme()
  return (
    <div className="rail-foot">
      <div className="theme-switch" role="group" aria-label="Colour theme">
        {([{ id: 'light', label: 'Light', icon: Sun }, { id: 'dark', label: 'Dark', icon: Moon }] as const).map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" className={cn('theme-switch-btn', theme === id && 'is-on')} onClick={() => setTheme(id)} aria-pressed={theme === id} title={`${label} theme`} tabIndex={dock ? -1 : undefined}>
            <Icon size={15} aria-hidden="true" /><span>{label}</span>
          </button>
        ))}
      </div>
      {/* Only the icon rail shows this: the pin button that opens the sidebar for good. */}
      <div className="rail-dock-tools">
        <button type="button" className="rail-dock-btn" onClick={onToggle} aria-label={pinLabel} tabIndex={dock ? undefined : -1}
          onMouseEnter={(event) => dock && showTip(pinLabel, event.currentTarget)} onMouseLeave={hideTip} onFocus={(event) => dock && showTip(pinLabel, event.currentTarget)} onBlur={hideTip}>
          <PanelLeftOpen size={17} strokeWidth={1.75} />
        </button>
      </div>
    </div>
  )
}

const ROLE_LABEL: Record<string, string> = { admin: 'Administrator', manager: 'Manager', staff: 'Staff' }

function ProfileMenu() {
  const { me, signOut } = useStore()
  return (
    <Dropdown
      align="right"
      width="w-[min(19rem,calc(100vw-2rem))]"
      trigger={({ open, toggle }) => (
        <button type="button" className="topbar-profile-trigger" onClick={toggle} aria-label={`Account menu for ${me.name}`} aria-expanded={open}>
          <span className="topbar-profile-avatar" aria-hidden="true">{initials(me.name)}</span>
          <span className="topbar-profile-copy"><strong>{me.name}</strong><small>{ROLE_LABEL[me.role] ?? me.role}</small></span>
          <ChevronDown size={14} aria-hidden="true" />
        </button>
      )}
    >
      {(close) => (
        <>
          <div className="border-b border-rule-soft px-3 py-2.5">
            <p className="text-sm font-medium text-ink">{me.name}</p>
            <p className="truncate text-micro text-ink-400">{me.email} · {ROLE_LABEL[me.role] ?? me.role}</p>
          </div>
          <MenuItem onClick={() => { close(); void signOut() }} tone="danger"><span className="inline-flex items-center gap-2"><LogOut size={14} />Sign out</span></MenuItem>
        </>
      )}
    </Dropdown>
  )
}

function Topbar({ onMenu, menuRef, menuAlert }: { onMenu: () => void; menuRef: React.RefObject<HTMLButtonElement>; menuAlert: boolean }) {
  const { me, notifications, inboxUnread, notify, markNotificationRead } = useStore()
  const navigate = useNavigate()
  const unread = notifications.filter((n) => !n.read).length
  const firstName = me.name.trim().split(/\s+/)[0] || me.name
  const roleLabel = ROLE_LABEL[me.role] ?? me.role

  return (
    <header className="topbar">
      <div className="topbar-left">
        <button ref={menuRef} type="button" className="topbar-menu" onClick={onMenu} aria-label={menuAlert ? 'Open menu. Something needs your attention.' : 'Open menu'}>
          <Menu size={20} />{menuAlert && <i className="topbar-menu-dot" aria-hidden="true" />}
        </button>
        <div className="topbar-hello">
          <h2 className="topbar-welcome" title={`Welcome back, ${me.name}`}>
            <span className="welcome-long">Welcome back</span><span className="welcome-short">Hi</span>, {firstName}
          </h2>
          {roleLabel.toLowerCase() !== firstName.toLowerCase() && <p className="topbar-role">{roleLabel}</p>}
        </div>
      </div>
      <div className="topbar-actions">
        <AttendanceClock compact />
        <button type="button" className="topbar-icon-btn topbar-mail-btn" aria-label={inboxUnread > 0 ? `Open messages, ${inboxUnread} unread` : 'Open messages'} onClick={() => navigate('/inbox')}>
          <Mail size={18} strokeWidth={1.75} />
          {inboxUnread > 0 && <span className="topbar-badge">{inboxUnread > 9 ? '9+' : inboxUnread}</span>}
        </button>
        <Dropdown
          width="w-[min(22rem,calc(100vw-2rem))] max-sm:fixed max-sm:left-4 max-sm:right-4 max-sm:top-[72px] max-sm:w-auto"
          label="Notifications"
          trigger={({ toggle }) => (
            <button type="button" className="topbar-icon-btn" onClick={toggle} aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}>
              <Bell size={18} strokeWidth={1.75} />
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
