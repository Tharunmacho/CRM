import { useEffect, useState } from 'react'
import { CalendarDays, KeyRound, LogOut, Plus, RotateCw, ScrollText, UserCheck, UserCog, Users } from 'lucide-react'
import { dateTimeLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { api } from '@/shared/api/client'
import type { Role, User } from '@/shared/model/types'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { Button, Field, Input, SearchInput, Select, Switch } from '@/shared/ui/primitives'
import { DataTable, type Column } from '@/shared/ui/table'
import { Dialog, Dropdown, MenuItem, Sheet } from '@/shared/ui/overlay'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { useStaffAvailability } from '@/features/staff/use-staff-availability'
import { canReceiveManualAssignment, manualAssignmentLabel } from '@/features/staff/availability-model'
import { useWorkspaceRead } from '@/features/staff/use-workspace-read'

interface AuditRow {
  id: string; actorId: string | null; action: string; target: string | null
  entityType: string | null; entityId: string | null; at: string
  meta: { changes?: Record<string, { before: unknown; after: unknown }>; transfer?: { candidates: number; tasks: number }; revokedSessions?: number }
}
interface AuditPage { items: AuditRow[]; nextBefore: string | null }
const failureText = (error: unknown) => error instanceof Error ? error.message : 'The change could not be saved. Try again.'
const roleLabels = { admin: 'Admin', manager: 'Manager', staff: 'Staff' }

export default function UsersPage() {
  const store = useStore()
  const accountRead = useWorkspaceRead()
  const [editing, setEditing] = useState<{ user: User | null; deactivate?: boolean } | null>(null)
  const [security, setSecurity] = useState<{ user: User; kind: 'password' | 'logout' } | null>(null)
  const [auditFor, setAuditFor] = useState<User | null>(null)
  const [query, setQuery] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState('')
  const availability = useStaffAvailability({ revision: store.users.map(user => `${user.id}:${user.role}:${user.active}:${user.receivesAutoAssign}:${user.onLeave}`).join('|') })
  const eligible = (availability.data?.staff ?? []).filter(user => user.availability.eligible)
  const onLeave = (availability.data?.staff ?? []).filter(user => user.active && ['approved_leave', 'manual_leave'].includes(user.availability.status))
  const availabilityFor = (id: string) => availability.data?.staff.find(user => user.id === id)?.availability
  const rows = store.users.filter((u) => [u.name, u.email, u.phone || ''].join(' ').toLowerCase().includes(query.trim().toLowerCase()))

  const refreshSaved = async (message: string) => {
    try { await store.refresh(); store.notify(message) }
    catch { store.notify('The change was saved. Reload the directory to see the latest details.', 'warn') }
    availability.refresh()
  }
  const changeFlag = async (user: User, values: Record<string, boolean>) => {
    if (pending) return
    setPending(user.id); setError('')
    try { await api.patch('/users/' + user.id, values); await refreshSaved('Availability updated') }
    catch (err) { setError(failureText(err)) }
    finally { setPending(null) }
  }
  const columns: Column<User>[] = [
    { key: 'name', header: 'Team member', sortable: true, value: (u) => u.name,
      render: (u) => <RecordIdentity name={u.name + (u.id === store.me.id ? ' (you)' : '')} detail={u.email} /> },
    { key: 'role', header: 'Role', filterable: true, value: (u) => roleLabels[u.role],
      render: (u) => <Badge tone={u.role === 'admin' ? 'ink' : u.role === 'manager' ? 'navy' : 'neutral'}>{roleLabels[u.role]}</Badge> },
    { key: 'phone', header: 'Phone', value: (u) => u.phone || '—' },
    { key: 'auto', header: 'Auto-assign', filterable: true, value: (u) => u.receivesAutoAssign ? 'On' : 'Off',
      render: (u) => <Switch checked={u.receivesAutoAssign} disabled={!!pending || u.role !== 'staff' || !u.active}
        label={'Auto-assign ' + u.name} compact onChange={(on) => void changeFlag(u, { receivesAutoAssign: on })} /> },
    { key: 'leave', header: 'Manual leave', filterable: true, value: (u) => u.onLeave ? 'Yes' : 'No',
      render: (u) => <Switch checked={u.onLeave} disabled={!!pending || u.role !== 'staff' || !u.active}
        label={'Manual leave: ' + u.name} compact onChange={(on) => void changeFlag(u, { onLeave: on })} /> },
    { key: 'availability', header: 'Availability today', filterable: true, value: user => availabilityFor(user.id)?.label || 'Not checked',
      render: user => { const current = availabilityFor(user.id); return current ? <Badge tone={current.eligible ? 'navy' : ['approved_leave', 'manual_leave'].includes(current.status) ? 'amber' : 'neutral'}>{current.label}</Badge> : <span className="text-xs text-ink-400">{availability.loading ? 'Checking…' : 'Unavailable'}</span> } },
    { key: 'candidates', header: 'Candidates', sortable: true, align: 'right',
      value: (u) => store.candidates.filter((c) => c.ownerId === u.id).length },
    { key: 'status', header: 'Status', filterable: true, value: (u) => u.active ? 'Active' : 'Inactive',
      render: (u) => <Badge tone={u.active ? 'navy' : 'neutral'} dot>{u.active ? 'Active' : 'Inactive'}</Badge> },
    { key: 'actions', header: '', align: 'right', render: (u) => <Dropdown trigger={({ toggle }) => (
      <Button size="sm" variant="ghost" aria-label={'Actions for ' + u.name} onClick={toggle}><UserCog className="h-4 w-4" /></Button>
    )}>{(close) => <>
      <MenuItem onClick={() => { close(); setEditing({ user: u }) }}>Edit account</MenuItem>
      <MenuItem icon={<ScrollText className="h-4 w-4" />} onClick={() => { close(); setAuditFor(u) }}>View audit history</MenuItem>
      <MenuItem icon={<KeyRound className="h-4 w-4" />} onClick={() => { close(); setSecurity({ user: u, kind: 'password' }) }}>Reset password</MenuItem>
      <MenuItem icon={<LogOut className="h-4 w-4" />} onClick={() => { close(); setSecurity({ user: u, kind: 'logout' }) }}>Sign out all sessions</MenuItem>
      {u.active && u.id !== store.me.id && <MenuItem tone="danger" onClick={() => { close(); setEditing({ user: u, deactivate: true }) }}>Deactivate account</MenuItem>}
    </>}</Dropdown> },
  ]
  if (accountRead.loading || accountRead.error) return <>
    <PageHeader title="User Management" eyebrow="People & permissions" description="Manage your team, access and assignment availability." />
    <Panel>{accountRead.error ? <div className="space-y-3"><p role="alert" className="text-sm text-stamp-700">{accountRead.error}</p><p className="text-sm text-ink-500">Account controls are available after the current directory loads.</p><Button onClick={accountRead.refresh}>Retry directory</Button></div>
      : <p role="status" className="text-sm text-ink-500">Loading current accounts and assigned work…</p>}</Panel>
  </>
  return <>
    <PageHeader title="User Management" eyebrow="People & permissions"
      description="Manage your team, access and assignment availability."
      actions={<><Button onClick={() => { accountRead.refresh(); availability.refresh() }}>Refresh directory</Button><Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setEditing({ user: null })}>Add user</Button></>} />
    <SummaryCards items={[
      { label: 'Team members', value: store.users.length, detail: 'Accounts in your workspace', icon: Users },
      { label: 'Active accounts', value: store.users.filter((u) => u.active).length, detail: 'With access to the CRM', icon: UserCheck, tone: 'green' },
      { label: 'In rotation', value: availability.data ? eligible.length : '—', detail: 'Available for new candidates', icon: RotateCw, tone: 'violet' },
      { label: 'On leave', value: availability.data ? onLeave.length : '—', detail: 'Approved or manual leave today', icon: CalendarDays, tone: 'amber' },
    ]} />
    <Panel className="mb-5">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-h3 font-semibold">Candidate assignment</h2><div className="flex items-center gap-2"><Badge tone="navy">Round-robin</Badge><Button size="sm" disabled={availability.loading} onClick={availability.refresh}>Refresh availability</Button></div></div>
      <p className="mt-1 text-xs text-ink-500">New candidates rotate equally among active staff with auto-assignment enabled. Approved attendance leave and manual leave both exclude staff. Clearing manual leave does not cancel approved leave.</p>
      <div className="mt-3 flex flex-wrap gap-2">{availability.loading ? <p role="status" className="text-sm text-ink-500">Checking current availability…</p> : availability.error ? <p role="alert" className="text-sm text-stamp-700">{availability.error}</p> : eligible.length ? eligible.map((u) => <Badge key={u.id}>{u.name}</Badge>)
        : <p role="status" className="text-sm text-amber-700">No staff are currently in rotation. Check approved leave, manual leave and auto-assignment settings.</p>}</div>
      {availability.data && <p className="mt-3 text-xs text-ink-400">Availability for {availability.data.today} · {availability.data.timezone}</p>}
    </Panel>
    {error && <p role="alert" className="mb-4 rounded-control border border-stamp-100 bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    <Panel flush>
      <PanelHeader title="Team directory" meta={store.users.length + ' accounts'} />
      <div className="workspace-list-toolbar"><SearchInput value={query} onChange={setQuery} placeholder="Search name, email or phone" /></div>
      <DataTable rows={rows} columns={columns} rowKey={(u) => u.id} pageSize={15}
        empty={<EmptyState title="No team members match" description="Try a different name or email." />}
        rowClassName={(u) => u.active ? undefined : 'bg-field/40'} />
    </Panel>
    {editing && <UserDialog key={(editing.user?.id || 'new') + Boolean(editing.deactivate)} user={editing.user} deactivate={editing.deactivate}
      onClose={() => setEditing(null)} onSaved={refreshSaved} />}
    {security && <SecurityDialog key={security.user.id + security.kind} {...security} onClose={() => setSecurity(null)} />}
    {auditFor && <AuditHistory key={auditFor.id} user={auditFor} onClose={() => setAuditFor(null)} />}
  </>
}

function UserDialog({ user, deactivate, onClose, onSaved }: {
  user: User | null; deactivate?: boolean; onClose: () => void; onSaved: (message: string) => Promise<void>
}) {
  const store = useStore()
  const [form, setForm] = useState({
    name: user?.name || '', email: user?.email || '', phone: user?.phone || '',
    role: user?.role || 'staff' as Role, active: deactivate ? false : user?.active ?? true,
    receivesAutoAssign: user?.receivesAutoAssign ?? true, onLeave: user?.onLeave ?? false,
    password: '', confirmation: '', reassignToUserId: '',
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const availability = useStaffAvailability({ enabled: !!user })
  const replacementOptions = (availability.data?.staff ?? []).filter(candidate => candidate.id !== user?.id && canReceiveManualAssignment(candidate))
  const replacementValid = replacementOptions.some(candidate => candidate.id === form.reassignToUserId)
  const currentAvailability = availability.data?.staff.find(candidate => candidate.id === user?.id)?.availability
  const set = <K extends keyof typeof form>(key: K, value: typeof form[K]) => setForm((current) => ({ ...current, [key]: value }))
  const own = user?.id === store.me.id
  const owned = user ? store.candidates.filter((c) => c.ownerId === user.id).length : 0
  const tasks = user ? store.tasks.filter((t) => t.ownerId === user.id && !t.completedAt && !t.cancelledAt).length : 0
  const transfers = !!user && (!form.active || (user.role === 'staff' && form.role !== 'staff')) && (owned > 0 || tasks > 0)
  const save = async () => {
    if (busy) return
    if (!form.name.trim() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) {
      setError('Enter a name and a valid email address.'); return
    }
    if (!user && (form.password.length < 12 || form.password !== form.confirmation)) {
      setError('Use a password of at least 12 characters and enter the same password twice.'); return
    }
    if (transfers && (availability.loading || availability.error || !replacementValid)) { setError('Choose a currently available staff member to receive the candidates and open tasks.'); return }
    setBusy(true); setError('')
    const { confirmation, password, reassignToUserId, ...fields } = form
    const payload = { ...fields, name: form.name.trim(), email: form.email.trim().toLowerCase(),
      receivesAutoAssign: form.role === 'staff' && form.active && form.receivesAutoAssign,
      onLeave: form.role === 'staff' && form.onLeave }
    try {
      if (user) await api.patch('/users/' + user.id, { ...payload, ...(transfers ? { reassignToUserId } : {}) })
      else await api.post('/users', { ...payload, password })
      if (own && payload.email !== user?.email) { onClose(); await store.signOut(); return }
      await onSaved(user ? 'Account updated' : 'User created')
      onClose()
    } catch (err) { setError(failureText(err)); if (transfers) availability.refresh() }
    finally { setBusy(false) }
  }
  return <Dialog open onClose={() => { if (!busy) onClose() }} width="lg"
    title={deactivate ? 'Deactivate ' + user?.name : user ? 'Edit account' : 'Add a team member'}
    description={deactivate ? 'Access is removed and assigned work is transferred together.' : 'Set account details, role and candidate assignment availability.'}
    footer={<><Button disabled={busy} variant="ghost" onClick={onClose}>Cancel</Button><Button disabled={busy || (transfers && (availability.loading || !!availability.error || !replacementValid))} variant={form.active ? 'primary' : 'danger'} onClick={() => void save()}>
      {busy ? 'Saving…' : !form.active && user ? 'Deactivate account' : user ? 'Save changes' : 'Create user'}</Button></>}>
    <div className="grid gap-5 sm:grid-cols-2">
      <Field label="Full name" required><Input autoFocus maxLength={160} value={form.name} disabled={busy} onChange={(e) => set('name', e.target.value)} /></Field>
      <Field label="Sign-in email" required><Input type="email" maxLength={255} value={form.email} disabled={busy} onChange={(e) => set('email', e.target.value)} /></Field>
      <Field label="Phone"><Input type="tel" maxLength={40} value={form.phone} disabled={busy} onChange={(e) => set('phone', e.target.value)} /></Field>
      <Field label="Role" hint={own ? 'Your own Admin role cannot be removed.' : 'Staff receive candidates. Managers supervise. Admins manage access.'}>
        <Select value={form.role} disabled={busy || own} onChange={(e) => set('role', e.target.value as Role)}><option value="staff">Staff</option><option value="manager">Manager</option><option value="admin">Admin</option></Select>
      </Field>
      {!user && <><Field label="Initial password" required hint="At least 12 characters. Share securely with the new user.">
        <Input type="password" autoComplete="new-password" minLength={12} maxLength={128} value={form.password} disabled={busy} onChange={(e) => set('password', e.target.value)} /></Field>
        <Field label="Confirm password" required><Input type="password" autoComplete="new-password" maxLength={128} value={form.confirmation} disabled={busy} onChange={(e) => set('confirmation', e.target.value)} /></Field></>}
      <div className="space-y-4 rounded-panel border border-rule-soft bg-field/30 p-4 sm:col-span-2">
        <Switch label="Account active" description="Inactive accounts cannot sign in." checked={form.active} disabled={busy || own} onChange={(value) => set('active', value)} />
        {form.role === 'staff' && <><Switch label="Receives auto-assigned candidates" description="Include this recruiter in the round-robin rotation." checked={form.receivesAutoAssign} disabled={busy || !form.active} onChange={(value) => set('receivesAutoAssign', value)} />
          <Switch label="Manual leave" description="Prevent new assignments while keeping existing work. Approved leave is managed in Attendance and is also respected." checked={form.onLeave} disabled={busy || !form.active} onChange={(value) => set('onLeave', value)} /></>}
        {currentAvailability && <p className="text-xs text-ink-500">Current availability: {currentAvailability.label}. Changes take effect when you save.</p>}
      </div>
      {transfers && <div className="space-y-3 rounded-panel border border-amber-100 bg-amber-50 p-4 sm:col-span-2">
        <p className="text-sm text-amber-700">{owned} candidates and {tasks} open tasks need a new owner. Existing due times and history are kept.</p>
        {availability.loading && <p role="status" className="text-sm text-ink-500">Checking replacement availability…</p>}
        {availability.error && <p role="alert" className="text-sm text-stamp-700">{availability.error}</p>}
        <Field label="Reassign work to" required><Select value={replacementValid ? form.reassignToUserId : ''} disabled={busy || availability.loading || !!availability.error || !replacementOptions.length} onChange={(e) => set('reassignToUserId', e.target.value)}>
          <option value="">Choose an available staff member</option>{replacementOptions.map((candidate) => <option key={candidate.id} value={candidate.id}>{manualAssignmentLabel(candidate)}</option>)}
        </Select></Field>
        {availability.data && !replacementOptions.length && <p role="status" className="text-sm text-amber-700">No eligible replacement is available. Review staff availability before deactivating this account.</p>}
        <Button size="sm" disabled={busy || availability.loading} onClick={availability.refresh}>Refresh availability</Button>
      </div>}
      {user && <p className="text-xs text-ink-500 sm:col-span-2">Changing role or sign-in email signs out existing sessions. Deactivation removes access immediately.</p>}
      {error && <p role="alert" className="text-sm text-stamp-700 sm:col-span-2">{error}</p>}
    </div>
  </Dialog>
}

function SecurityDialog({ user, kind, onClose }: { user: User; kind: 'password' | 'logout'; onClose: () => void }) {
  const store = useStore()
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async () => {
    if (busy) return
    if (kind === 'password' && (password.length < 12 || password !== confirmation)) { setError('Use at least 12 characters and matching passwords.'); return }
    setBusy(true); setError('')
    try {
      await api.post('/users/' + user.id + (kind === 'password' ? '/password' : '/force-logout'), kind === 'password' ? { password } : undefined)
      if (user.id === store.me.id) { onClose(); await store.signOut(); return }
      store.notify(kind === 'password' ? 'Password changed. Existing sessions were signed out.' : 'All sessions were signed out.')
      onClose()
    } catch (err) { setError(failureText(err)) }
    finally { setBusy(false) }
  }
  return <Dialog open onClose={() => { if (!busy) onClose() }} title={kind === 'password' ? 'Reset password' : 'Sign out all sessions'}
    description={user.name + ' · ' + user.email} footer={<><Button disabled={busy} onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy} onClick={() => void submit()}>{busy ? 'Saving…' : kind === 'password' ? 'Reset password' : 'Sign out sessions'}</Button></>}>
    <div className="space-y-4">
      <p className="text-sm text-ink-500">{kind === 'password' ? 'Set a new password and revoke all existing sessions. The account keeps its current activation status.' : 'This user will need to sign in again. Their account and assigned work remain available.'}</p>
      {kind === 'password' && <><Field label="New password" required hint="At least 12 characters."><Input type="password" autoComplete="new-password" autoFocus maxLength={128} disabled={busy} value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        <Field label="Confirm new password" required><Input type="password" autoComplete="new-password" maxLength={128} disabled={busy} value={confirmation} onChange={(e) => setConfirmation(e.target.value)} /></Field></>}
      {user.id === store.me.id && <p className="text-sm text-amber-700">This is your account. You will be signed out after this change.</p>}
      {error && <p role="alert" className="text-sm text-stamp-700">{error}</p>}
    </div>
  </Dialog>
}

function AuditHistory({ user, onClose }: { user: User; onClose: () => void }) {
  const store = useStore()
  const [data, setData] = useState<AuditPage>({ items: [], nextBefore: null })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    api.get<AuditPage>('/users/' + user.id + '/audit', { signal: controller.signal }).then(setData)
      .catch((err) => { if (!controller.signal.aborted) setError(failureText(err)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [user.id, retry])
  const more = async () => {
    if (!data.nextBefore || loading) return
    setLoading(true); setError('')
    try {
      const next = await api.get<AuditPage>('/users/' + user.id + '/audit?before=' + encodeURIComponent(data.nextBefore))
      setData((current) => ({ items: [...current.items, ...next.items], nextBefore: next.nextBefore }))
    } catch (err) { setError(failureText(err)) }
    finally { setLoading(false) }
  }
  return <Sheet open onClose={onClose} title={'Audit history · ' + user.name} description="Account changes and actions recorded by this user." width="lg">
    <div className="space-y-4 p-5">
      {error && <div role="alert" className="rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}<Button size="sm" className="mt-2" onClick={() => setRetry((v) => v + 1)}>Reload history</Button></div>}
      {loading && data.items.length === 0 && <p role="status" className="text-sm text-ink-500">Loading recorded activity…</p>}
      {!loading && !error && data.items.length === 0 && <EmptyState title="No activity recorded yet" description="Future account changes and recorded CRM actions will appear here." />}
      <ol className="divide-y divide-rule-soft">{data.items.map((row) => <li key={row.id} className="py-4 first:pt-0">
        <p className="text-sm font-semibold text-ink">{row.action}</p>
        <p className="mt-1 break-words text-sm text-ink-600">{row.target}</p>
        {row.meta.changes && <dl className="mt-2 space-y-1 text-xs text-ink-500">{Object.entries(row.meta.changes).map(([key, change]) => <div key={key} className="flex flex-wrap gap-x-2"><dt className="font-medium capitalize">{key.replaceAll('_', ' ')}</dt><dd>{String(change.before ?? '—')} → {String(change.after ?? '—')}</dd></div>)}</dl>}
        {row.meta.transfer && <p className="mt-1 text-xs text-ink-500">Reassigned {row.meta.transfer.candidates} candidates and {row.meta.transfer.tasks} open tasks.</p>}
        <p className="mt-2 text-micro text-ink-400">{store.user(row.actorId || '')?.name || 'System'} · {dateTimeLabel(row.at)}</p>
      </li>)}</ol>
      {data.nextBefore && <Button disabled={loading} onClick={() => void more()}>{loading ? 'Loading…' : 'Load earlier activity'}</Button>}
    </div>
  </Sheet>
}
