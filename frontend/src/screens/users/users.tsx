import { useMemo, useState, type ReactNode } from 'react'
import { AlertTriangle, CalendarDays, Download, KeyRound, LogOut, MoreHorizontal, Pencil, Plus, RefreshCw, RotateCw, ScrollText, UserCheck, UserCog, UserX, Users } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import type { Role, User } from '@/shared/model/types'
import { EmptyState, PageHeader, Panel, StatusPill } from '@/shared/ui/display'
import { PageSkeleton } from '@/shared/ui/page-loading'
import { DataTable, type Column } from '@/shared/ui/table'
import { Button, SearchInput, Select, Switch } from '@/shared/ui/primitives'
import { Dropdown, MenuItem, Tabs } from '@/shared/ui/overlay'
import { SummaryCards } from '@/shared/ui/workspace'
import { cn } from '@/shared/lib/cn'
import { useStaffAvailability } from '@/features/staff/use-staff-availability'
import type { StaffAvailability } from '@/features/staff/availability-model'
import { useWorkspaceRead } from '@/features/staff/use-workspace-read'
import { availabilityWords } from '@/features/staff-directory/model'
import { AuditHistory } from './audit-sheet'
import { SecurityDialog } from './security-dialog'
import { UserDialog } from './user-dialog'
import { RolePill, UserAvatar, downloadRows, failureText, roleLabels } from './user-ui'
import '@/styles/users-v2.css'

const PAGE_SIZE = 10
type SortKey = 'name' | 'role' | 'work'
type StatusFilter = 'all' | 'active' | 'inactive' | 'in_rotation' | 'on_leave'
const isLeave = (availability?: StaffAvailability) => !!availability && ['approved_leave', 'manual_leave'].includes(availability.status)
const roleRank: Record<Role, number> = { admin: 0, manager: 1, staff: 2 }
const STATUS_TABS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: 'All' }, { key: 'active', label: 'Active' }, { key: 'inactive', label: 'Inactive' },
  { key: 'in_rotation', label: 'Getting candidates' }, { key: 'on_leave', label: 'On leave' },
]

export default function UsersPage() {
  const store = useStore()
  const accountRead = useWorkspaceRead()
  const [editing, setEditing] = useState<{ user: User | null; deactivate?: boolean } | null>(null)
  const [security, setSecurity] = useState<{ user: User; kind: 'password' | 'logout' } | null>(null)
  const [auditFor, setAuditFor] = useState<User | null>(null)
  const [query, setQuery] = useState('')
  const [roleFilter, setRoleFilter] = useState<'all' | Role>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [sort, setSort] = useState<SortKey>('name')
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState('')
  const availability = useStaffAvailability({ revision: store.users.map(user => `${user.id}:${user.role}:${user.active}:${user.receivesAutoAssign}:${user.onLeave}`).join('|') })
  const availabilityFor = (id: string) => availability.data?.staff.find(user => user.id === id)?.availability
  const eligible = (availability.data?.staff ?? []).filter(user => user.availability.eligible)
  const onLeave = (availability.data?.staff ?? []).filter(user => user.active && isLeave(user.availability))

  const work = useMemo(() => {
    const candidates = new Map<string, number>(), tasks = new Map<string, number>()
    for (const candidate of store.candidates) if (candidate.ownerId) candidates.set(candidate.ownerId, (candidates.get(candidate.ownerId) ?? 0) + 1)
    for (const task of store.tasks) if (!task.completedAt && !task.cancelledAt) tasks.set(task.ownerId, (tasks.get(task.ownerId) ?? 0) + 1)
    return (id: string) => ({ candidates: candidates.get(id) ?? 0, tasks: tasks.get(id) ?? 0 })
  }, [store.candidates, store.tasks])

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return store.users.filter((u) => {
      if (needle && ![u.name, u.email, u.phone || ''].join(' ').toLowerCase().includes(needle)) return false
      if (roleFilter !== 'all' && u.role !== roleFilter) return false
      const current = availability.data?.staff.find(s => s.id === u.id)?.availability
      if (statusFilter === 'active') return u.active
      if (statusFilter === 'inactive') return !u.active
      if (statusFilter === 'in_rotation') return !!current?.eligible
      if (statusFilter === 'on_leave') return u.active && (u.onLeave || isLeave(current))
      return true
    }).sort((a, b) => {
      if (sort === 'role') return roleRank[a.role] - roleRank[b.role] || a.name.localeCompare(b.name)
      if (sort === 'work') { const x = work(a.id), y = work(b.id); return (y.candidates + y.tasks) - (x.candidates + x.tasks) || a.name.localeCompare(b.name) }
      return a.name.localeCompare(b.name)
    })
  }, [store.users, query, roleFilter, statusFilter, availability.data, sort, work])
  const filtered = query.trim() !== '' || roleFilter !== 'all' || statusFilter !== 'all'
  const activeCount = store.users.filter((u) => u.active).length
  const clearFilters = () => { setQuery(''); setRoleFilter('all'); setStatusFilter('all') }

  const refreshSaved = async (message: string) => {
    try { await store.refresh(); store.notify(message) }
    catch { store.notify('Saved. Refresh the page to see the latest.', 'warn') }
    availability.refresh()
  }
  const changeFlag = async (user: User, values: Record<string, boolean>) => {
    if (pending) return
    setPending(user.id); setError('')
    try { await api.patch('/users/' + user.id, values); await refreshSaved('Saved') }
    catch (err) { setError(failureText(err)) }
    finally { setPending(null) }
  }
  const statusLine = (u: User) => {
    const current = availabilityFor(u.id)
    if (u.role !== 'staff') return { text: 'Does not get new candidates', tone: '' }
    if (!u.active) return { text: 'No access', tone: '' }
    if (current) return { text: availabilityWords(current.status, current.label), tone: current.eligible ? 'is-ok' : isLeave(current) ? 'is-leave' : '' }
    return { text: availability.loading ? 'Checking…' : 'Unavailable', tone: '' }
  }
  const exportCsv = () => downloadRows(
    `team-directory-${new Date().toISOString().slice(0, 10)}.csv`,
    ['Name', 'Email', 'Phone', 'Role', 'Account', 'Gets new candidates', 'On leave', 'Status', 'Open candidates', 'Open tasks'],
    rows.map((u) => [u.name, u.email, u.phone || '', roleLabels[u.role], u.active ? 'Active' : 'Inactive',
      u.role === 'staff' ? (u.receivesAutoAssign ? 'Yes' : 'No') : '', u.role === 'staff' ? (u.onLeave ? 'Yes' : 'No') : '',
      statusLine(u).text, work(u.id).candidates, work(u.id).tasks]),
  )

  const header = (actions?: boolean) => <PageHeader title="User Management" eyebrow="People & permissions"
    description="Add your team, choose what each person can do, and choose who gets new candidates."
    actions={actions ? <><Button icon={<RefreshCw className="h-4 w-4" />} onClick={() => { accountRead.refresh(); availability.refresh() }}>Refresh</Button>
      <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setEditing({ user: null })}>Add user</Button></> : undefined} />

  if (accountRead.loading) return <div className="uv"><PageSkeleton label="Loading team directory" /></div>
  if (accountRead.error) return <div className="uv">
    {header()}
    <Panel className="uv-state" >
      <div role="alert">
        <span className="uv-state-icon" aria-hidden="true"><AlertTriangle size={20} /></span>
        <h2>We could not load your team</h2>
        <p>{accountRead.error}</p>
        <Button variant="primary" onClick={accountRead.refresh}>Try again</Button>
      </div>
    </Panel>
  </div>

  const columns: Column<User>[] = [
    {
      key: 'name', header: 'Team member', width: '23%', sortable: true, value: (u) => u.name.toLowerCase(),
      render: (u) => <div className="uv-person">
        <UserAvatar name={u.name} muted={!u.active} />
        <div>
          <p className="uv-name"><span>{u.name}</span>{u.id === store.me.id && <em>You</em>}</p>
          <p className="uv-email" title={u.email}>{u.email}</p>
        </div>
      </div>,
    },
    { key: 'role', header: 'Role', width: '10%', sortable: true, value: (u) => roleRank[u.role], render: (u) => <RolePill role={u.role} /> },
    {
      key: 'phone', header: 'Phone', width: '12%',
      render: (u) => u.phone ? <div><a className="uv-phone" href={'tel:' + u.phone.replace(/\s+/g, '')} title={'Call ' + u.name}>{u.phone}</a></div> : <span className="uv-none">Not set</span>,
    },
    {
      key: 'status', header: 'Status', width: '16%',
      render: (u) => { const line = statusLine(u); return <div className="uv-status">
        <StatusPill status={u.active ? 'active' : 'inactive'} />
        <small className={line.tone}>{line.text}</small>
      </div> },
    },
    {
      key: 'assignment', header: 'New candidates', width: '16%',
      render: (u) => u.role === 'staff' && u.active ? <div className="uv-flags">
        <label><Switch checked={u.receivesAutoAssign} disabled={!!pending} label={'Gets new candidates: ' + u.name} compact onChange={(on) => void changeFlag(u, { receivesAutoAssign: on })} /><span>Auto-assign</span></label>
        <label><Switch checked={u.onLeave} disabled={!!pending} label={'On leave: ' + u.name} compact onChange={(on) => void changeFlag(u, { onLeave: on })} /><span>On leave</span></label>
      </div> : <span className="uv-none">{u.role !== 'staff' ? 'Staff only' : 'Account inactive'}</span>,
    },
    {
      key: 'work', header: 'Open work', width: '11%', sortable: true, value: (u) => work(u.id).candidates + work(u.id).tasks,
      render: (u) => { const counts = work(u.id); return <div className="uv-work">
        <span><b>{counts.candidates}</b> {counts.candidates === 1 ? 'candidate' : 'candidates'}</span>
        <span><b>{counts.tasks}</b> open {counts.tasks === 1 ? 'task' : 'tasks'}</span>
      </div> },
    },
    {
      key: 'actions', header: 'Actions', actions: true,
      render: (u) => <div className="uv-actions">
        <button type="button" className="uv-edit" title={'Edit ' + u.name} aria-label={'Edit ' + u.name} onClick={() => setEditing({ user: u })}><Pencil size={14} aria-hidden="true" />Edit</button>
        <Dropdown width="w-60" trigger={({ toggle, open }) => (
          <button type="button" className="uv-menu-btn" title={'More actions for ' + u.name} aria-label={'Actions for ' + u.name} aria-expanded={open} onClick={toggle}><MoreHorizontal className="h-4 w-4" /></button>
        )}>{(close) => <>
          <MenuItem icon={<UserCog className="h-4 w-4" />} onClick={() => { close(); setEditing({ user: u }) }}>Edit account</MenuItem>
          <MenuItem icon={<ScrollText className="h-4 w-4" />} onClick={() => { close(); setAuditFor(u) }}>See history of changes</MenuItem>
          <MenuItem icon={<KeyRound className="h-4 w-4" />} onClick={() => { close(); setSecurity({ user: u, kind: 'password' }) }}>Reset password</MenuItem>
          <MenuItem icon={<LogOut className="h-4 w-4" />} onClick={() => { close(); setSecurity({ user: u, kind: 'logout' }) }}>Sign out everywhere</MenuItem>
          {u.active && u.id !== store.me.id && <MenuItem tone="danger" icon={<UserX className="h-4 w-4" />} onClick={() => { close(); setEditing({ user: u, deactivate: true }) }}>Deactivate account</MenuItem>}
        </>}</Dropdown>
      </div>,
    },
  ]

  const toggleStatus = (key: StatusFilter) => setStatusFilter((current) => current === key ? 'all' : key)

  return <div className="uv">
    {header(true)}
    <SummaryCards items={[
      { label: 'Team members', value: store.users.length, detail: `${store.users.length - activeCount} inactive`, icon: Users },
      { label: 'Active accounts', value: activeCount, detail: 'With access to the CRM', icon: UserCheck, tone: 'green', onSelect: () => toggleStatus('active'), active: statusFilter === 'active' },
      { label: 'Getting new candidates', value: availability.data ? eligible.length : '—', detail: 'Can be given new candidates now', icon: RotateCw, onSelect: () => toggleStatus('in_rotation'), active: statusFilter === 'in_rotation' },
      { label: 'On leave', value: availability.data ? onLeave.length : '—', detail: 'On leave today', icon: CalendarDays, tone: 'amber', onSelect: () => toggleStatus('on_leave'), active: statusFilter === 'on_leave' },
    ]} />

    <Panel flush className="uv-panel uv-rotation">
      <PanelHead id="uv-rotation-title" title="Candidate assignment"
        description="New candidates are shared out equally, one after another, among active staff who get new candidates and are not on leave. Approved leave and the On leave switch both pause this."
        actions={<><span className="uv-chip-label"><RotateCw size={13} aria-hidden="true" />One by one</span><Button size="sm" icon={<RefreshCw className="h-3.5 w-3.5" />} disabled={availability.loading} onClick={availability.refresh}>Refresh</Button></>} />
      <div className="uv-rotation-body" role="group" aria-labelledby="uv-rotation-title">
        {availability.loading ? <p role="status" className="uv-note">Checking who is free…</p>
          : availability.error ? <p role="alert" className="uv-error">{availability.error}</p>
          : <>
            <div className="uv-rotation-group">
              <h3>Getting new candidates</h3>
              {eligible.length ? <div className="uv-rotation-list">{eligible.map((u) => <span key={u.id} className="uv-rotation-chip" title={u.name + ' · ' + work(u.id).candidates + ' candidates'}><UserAvatar name={u.name} size="sm" /><span>{u.name}</span><small>{work(u.id).candidates}</small></span>)}</div>
                : <p role="status" className="uv-warn">No staff can get new candidates right now. Check leave and the Auto-assign switches.</p>}
            </div>
            {onLeave.length > 0 && <div className="uv-rotation-group">
              <h3>Paused</h3>
              <div className="uv-rotation-list">{onLeave.map((u) => <span key={u.id} className="uv-rotation-chip is-paused" title={u.name + ' · ' + availabilityWords(u.availability.status, u.availability.label)}><UserAvatar name={u.name} size="sm" muted /><span>{u.name}</span><small>{availabilityWords(u.availability.status, u.availability.label)}</small></span>)}</div>
            </div>}
            {availability.data && <p className="uv-foot">Status for {availability.data.today} ({availability.data.timezone}). The number next to each name is how many candidates they already have.</p>}
          </>}
      </div>
    </Panel>

    {error && <p role="alert" className="uv-error uv-banner">{error}</p>}

    <Panel flush className="uv-panel uv-directory">
      <PanelHead id="uv-directory-title" title="Team directory" description={filtered ? `${rows.length} of ${store.users.length} accounts` : `${store.users.length} accounts`}
        actions={rows.length > 0 ? <Button size="sm" icon={<Download className="h-3.5 w-3.5" />} onClick={exportCsv} title="Download the people shown here as a spreadsheet (CSV)">Export CSV</Button> : undefined} />
      <Tabs tabs={STATUS_TABS} active={statusFilter} onChange={(key) => setStatusFilter(key as StatusFilter)} />
      <div className={cn('uv-toolbar', filtered && 'has-clear')}>
        <SearchInput value={query} onChange={setQuery} placeholder="Search name, email or phone" />
        <Select aria-label="Filter by role" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value as 'all' | Role)}><option value="all">All roles</option><option value="admin">Admin</option><option value="manager">Manager</option><option value="staff">Staff</option></Select>
        <div className="uv-sort-narrow"><Select aria-label="Sort team members" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}><option value="name">Sort: Name</option><option value="role">Sort: Role</option><option value="work">Sort: Open work</option></Select></div>
        {filtered && <Button variant="ghost" onClick={clearFilters}>Clear filters</Button>}
      </div>
      <DataTable compact stackBelow={1280} pageSize={PAGE_SIZE} rows={rows} columns={columns} rowKey={(u) => u.id}
        resetPageKey={`${query}|${roleFilter}|${statusFilter}`} rowClassName={(u) => cn(!u.active && 'uv-row-off')}
        empty={<EmptyState title="No team members match" description="Try a different name, email or filter."
          action={filtered ? <Button onClick={clearFilters}>Clear filters</Button> : undefined} />} />
    </Panel>
    {editing && <UserDialog key={(editing.user?.id || 'new') + Boolean(editing.deactivate)} user={editing.user} deactivate={editing.deactivate}
      onClose={() => setEditing(null)} onSaved={refreshSaved} />}
    {security && <SecurityDialog key={security.user.id + security.kind} {...security} onClose={() => setSecurity(null)} />}
    {auditFor && <AuditHistory key={auditFor.id} user={auditFor} onClose={() => setAuditFor(null)} />}
  </div>
}

/** Section header: a 17px title with a muted description on the left, the controls on the right, a hairline under it. */
function PanelHead({ id, title, description, actions }: { id: string; title: string; description: ReactNode; actions?: ReactNode }) {
  return <header className="uv-head">
    <div><h2 id={id}>{title}</h2><p>{description}</p></div>
    {actions && <div className="uv-head-actions">{actions}</div>}
  </header>
}
