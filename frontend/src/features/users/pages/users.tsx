import { useState } from 'react'
import { CalendarDays, KeyRound, LogOut, Plus, RotateCw, ScrollText, UserCheck, UserCog, Users } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateTimeLabel, smartTime } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { auditLog } from '@/mocks/seed'
import type { Role, User } from '@/shared/model/types'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { Button, Field, Input, Select, Switch } from '@/shared/ui/primitives'
import { DataTable, type Column } from '@/shared/ui/table'
import { Dialog, Dropdown, MenuItem, Sheet } from '@/shared/ui/overlay'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'

export default function UsersPage() {
  const store = useStore()
  const [editing, setEditing] = useState<User | null>(null)
  const [adding, setAdding] = useState(false)
  const [deactivating, setDeactivating] = useState<User | null>(null)
  const [auditFor, setAuditFor] = useState<User | null>(null)

  const eligible = store.users.filter(
    (u) => u.role === 'staff' && u.active && u.receivesAutoAssign && !u.onLeave,
  )

  const userColumns: Column<User>[] = [
    {
      key: 'name',
      header: 'Name',
      sortable: true,
      width: '24%',
      value: (u) => u.name,
      render: (u) => (
        <RecordIdentity name={u.name} detail={u.email} />
      ),
    },
    {
      key: 'role',
      header: 'Role',
      sortable: true,
      filterable: true,
      value: (u) => u.role,
      render: (u) => (
        <Badge tone={u.role === 'admin' ? 'ink' : u.role === 'manager' ? 'navy' : 'neutral'}>
          <span className="capitalize">{u.role}</span>
        </Badge>
      ),
    },
    {
      key: 'phone',
      header: 'Phone',
      className: 'whitespace-nowrap',
      value: (u) => u.phone,
      render: (u) => <span className="ident text-ink-600">{u.phone}</span>,
    },
    {
      key: 'autoAssign',
      header: 'Auto-assign',
      align: 'center',
      filterable: true,
      value: (u) => (u.receivesAutoAssign ? 'On' : 'Off'),
      render: (u) => (
        <Switch
          checked={u.receivesAutoAssign}
          disabled={u.role !== 'staff' || !u.active}
          onChange={(v) =>
            store.notify('User management is not connected to the server yet', 'warn')
          }
        />
      ),
    },
    {
      key: 'onLeave',
      header: 'On leave',
      align: 'center',
      filterable: true,
      value: (u) => (u.onLeave ? 'Yes' : 'No'),
      render: (u) => (
        <Switch
          checked={u.onLeave}
          disabled={!u.active}
          onChange={(v) => store.notify('User management is not connected to the server yet', 'warn')}
        />
      ),
    },
    {
      key: 'candidates',
      header: 'Candidates',
      align: 'right',
      sortable: true,
      value: (u) => store.candidates.filter((c) => c.ownerId === u.id).length,
      render: (u) => (
        <span className="tnum">{store.candidates.filter((c) => c.ownerId === u.id).length}</span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      filterable: true,
      value: (u) => (u.active ? 'Active' : 'Deactivated'),
      render: (u) => (
        <Badge tone={u.active ? 'navy' : 'neutral'} dot>
          {u.active ? 'Active' : 'Deactivated'}
        </Badge>
      ),
    },
    {
      key: 'action',
      header: '',
      align: 'right',
      render: (u) => (
        <Dropdown
          trigger={({ toggle }) => (
            <button
              onClick={toggle}
              aria-label={`Actions for ${u.name}`}
              className="rounded-control p-1.5 text-ink-400 hover:bg-field hover:text-ink"
            >
              <UserCog className="h-4 w-4" />
            </button>
          )}
        >
          {(close) => (
            <>
              <MenuItem
                icon={<UserCog className="h-3.5 w-3.5" />}
                onClick={() => {
                  setEditing(u)
                  close()
                }}
              >
                Edit user
              </MenuItem>
              <MenuItem
                icon={<ScrollText className="h-3.5 w-3.5" />}
                onClick={() => {
                  setAuditFor(u)
                  close()
                }}
              >
                View audit log
              </MenuItem>
              <MenuItem
                icon={<KeyRound className="h-3.5 w-3.5" />}
                onClick={() => {
                  store.notify(`Password reset is not connected yet`, 'warn')
                  close()
                }}
              >
                Reset password
              </MenuItem>
              <MenuItem
                icon={<LogOut className="h-3.5 w-3.5" />}
                onClick={() => {
                  store.notify('Force logout is not connected yet', 'warn')
                  close()
                }}
              >
                Force logout
              </MenuItem>
              {u.active && (
                <MenuItem
                  tone="danger"
                  onClick={() => {
                    setDeactivating(u)
                    close()
                  }}
                >
                  Deactivate
                </MenuItem>
              )}
            </>
          )}
        </Dropdown>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="User Management"
        eyebrow="People & permissions"
        description="A well-connected team works better. Manage access, availability, and candidate assignment."
        actions={
          <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setAdding(true)}>
            Add user
          </Button>
        }
      />

      <SummaryCards
        items={[
          {
            label: 'Team members',
            value: store.users.length,
            detail: 'People in your workspace',
            icon: Users,
          },
          {
            label: 'Active accounts',
            value: store.users.filter((user) => user.active).length,
            detail: 'With access to the platform',
            icon: UserCheck,
            tone: 'green',
          },
          {
            label: 'In rotation',
            value: eligible.length,
            detail: 'Receiving new candidates',
            icon: RotateCw,
            tone: 'violet',
          },
          {
            label: 'On leave',
            value: store.users.filter((user) => user.active && user.onLeave).length,
            detail: 'Paused from auto-assignment',
            icon: CalendarDays,
            tone: 'amber',
          },
        ]}
      />

      <Panel className="rotation-panel mb-4">
        <div className="rotation-heading"><span className="summary-icon tone-blue"><RotateCw size={18} /></span><h2>Auto-assignment rotation</h2><Badge tone="navy">Round-robin</Badge></div>
        <p className="mt-1 text-xs text-ink-500">
          New candidates go to each of these staff in turn — strict round-robin, no load balancing.
          Staff on leave are skipped.
        </p>
        <ol className="rotation-members mt-3 flex flex-wrap items-center gap-2">
          {eligible.map((u, i) => (
            <li key={u.id} className="flex items-center gap-1.5">
              <span className="text-micro text-ink-300 tnum">{i + 1}</span>
              <RecordIdentity name={u.name} detail="Available for assignment" />
            </li>
          ))}
          {eligible.length === 0 && (
            <li className="text-xs text-stamp-600">
              Nobody is eligible — new candidates cannot be assigned.
            </li>
          )}
        </ol>
      </Panel>

      <Panel flush>
        <PanelHeader title="Team directory" meta={`${store.users.length} accounts · Manage roles and availability`} />
        <DataTable
          rows={store.users}
          columns={userColumns}
          rowKey={(u) => u.id}
          pageSize={15}
          rowClassName={(u) => (!u.active ? 'opacity-60' : undefined)}
        />
      </Panel>

      <UserDialog
        open={adding || !!editing}
        user={editing}
        onClose={() => {
          setAdding(false)
          setEditing(null)
        }}
      />

      <DeactivateDialog user={deactivating} onClose={() => setDeactivating(null)} />

      <Sheet
        open={!!auditFor}
        onClose={() => setAuditFor(null)}
        title={`Audit log — ${auditFor?.name ?? ''}`}
        description="Every create, edit, stage change, reassignment and file upload, with user and timestamp."
      >
        <ul className="divide-y divide-rule-soft">
          {auditLog.map((a) => (
            <li key={a.id} className="px-5 py-3">
              <p className="text-sm text-ink">{a.action}</p>
              <p className="mt-0.5 text-xs text-ink-500">{a.target}</p>
              <p className="mt-1 text-micro text-ink-400">
                {store.user(a.actorId)?.name} · {dateTimeLabel(a.at)}
              </p>
            </li>
          ))}
        </ul>
      </Sheet>
    </>
  )
}

function UserDialog({
  open,
  user,
  onClose,
}: {
  open: boolean
  user: User | null
  onClose: () => void
}) {
  const store = useStore()
  const [form, setForm] = useState({ name: '', email: '', phone: '', role: 'staff' as Role })

  // Re-seed the form whenever a different user is opened.
  const [seeded, setSeeded] = useState<string | null>(null)
  if (open && seeded !== (user?.id ?? 'new')) {
    setSeeded(user?.id ?? 'new')
    setForm(
      user
        ? { name: user.name, email: user.email, phone: user.phone, role: user.role }
        : { name: '', email: '', phone: '', role: 'staff' },
    )
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={user ? `Edit ${user.name}` : 'Add user'}
      description={user ? 'Changes take effect immediately.' : 'They can sign in with this email address.'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!form.name.trim() || !form.email.trim()}
            onClick={() => {
              if (user) {
                store.notify('User management is not connected to the server yet', 'warn')
              } else {
                store.notify('Adding users is not connected to the server yet', 'warn')
              }
              setSeeded(null)
              onClose()
            }}
          >
            {user ? 'Save changes' : 'Add user'}
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" required className="sm:col-span-2">
          <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        </Field>
        <Field label="Email" required>
          <Input type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} />
        </Field>
        <Field label="Phone">
          <Input ident value={form.phone} onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))} />
        </Field>
        <Field
          label="Role"
          hint="Only staff receive auto-assigned candidates."
          className="sm:col-span-2"
        >
          <Select value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value as Role }))}>
            <option value="staff">Staff</option>
            <option value="manager">Manager</option>
            <option value="admin">Admin</option>
          </Select>
        </Field>
      </div>
    </Dialog>
  )
}

function DeactivateDialog({ user, onClose }: { user: User | null; onClose: () => void }) {
  const store = useStore()
  const [reassignTo, setReassignTo] = useState('')

  if (!user) return null
  const owned = store.candidates.filter((c) => c.ownerId === user.id)
  const openTasks = store.tasks.filter((t) => t.ownerId === user.id && !t.completedAt && !t.cancelledAt)

  return (
    <Dialog
      open={!!user}
      onClose={onClose}
      title={`Deactivate ${user.name}?`}
      description="They will be signed out and will no longer receive candidates."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={owned.length > 0 && !reassignTo}
            onClick={() => {
              if (reassignTo) {
                void store.reassign(owned.map((c) => c.id), reassignTo)
              }
              store.notify('User management is not connected to the server yet', 'warn')
              setReassignTo('')
              onClose()
            }}
          >
            Deactivate
          </Button>
        </>
      }
    >
      {owned.length > 0 ? (
        <>
          <p className="mb-3 rounded-control border border-amber-100 bg-amber-50 px-3 py-2.5 text-xs text-amber-700">
            {user.name} owns {owned.length} candidates and {openTasks.length} open tasks. Choose who
            takes them over — the 48-hour clocks keep running.
          </p>
          <Field label="Reassign everything to" required>
            <Select value={reassignTo} onChange={(e) => setReassignTo(e.target.value)}>
              <option value="">Choose a staff member</option>
              {store.users
                .filter((u) => u.id !== user.id && u.active && u.role === 'staff')
                .map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}
                    {u.onLeave ? ' — on leave' : ''}
                  </option>
                ))}
            </Select>
          </Field>
        </>
      ) : (
        <p className="text-sm text-ink-600">
          They own no candidates, so nothing needs reassigning.
        </p>
      )}
    </Dialog>
  )
}
