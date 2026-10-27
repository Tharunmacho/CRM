import { useState } from 'react'
import { ArrowRightLeft, KeyRound, RotateCw, ShieldCheck, Sparkles, UserRound } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import type { Role, User } from '@/shared/model/types'
import { Button, Field, Input, Select, Switch } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { useStaffAvailability } from '@/features/staff/use-staff-availability'
import { canReceiveManualAssignment, manualAssignmentLabel } from '@/features/staff/availability-model'
import { availabilityWords } from '@/features/staff-directory/model'
import { FormSection, PasswordOnce, PasswordPair, RolePill, UserAvatar, failureText, generatePassword, roleBlurbs } from './user-ui'

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function UserDialog({ user, deactivate, onClose, onSaved }: {
  user: User | null; deactivate?: boolean; onClose: () => void; onSaved: (message: string) => Promise<void>
}) {
  const store = useStore()
  const [form, setForm] = useState({
    name: user?.name || '', email: user?.email || '', phone: user?.phone || '',
    role: user?.role || 'staff' as Role, active: deactivate ? false : user?.active ?? true,
    receivesAutoAssign: user?.receivesAutoAssign ?? true, onLeave: user?.onLeave ?? false,
    password: '', confirmation: '', reassignToUserId: '',
  })
  const [generated, setGenerated] = useState('')
  const [created, setCreated] = useState<{ name: string; email: string; password: string } | null>(null)
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
  const blocked = transfers && (availability.loading || !!availability.error || !replacementValid)
  const isStaff = form.role === 'staff'
  const close = () => { if (!busy) onClose() }

  const useGenerated = () => {
    const next = generatePassword()
    setGenerated(next); setError(''); setForm((current) => ({ ...current, password: next, confirmation: next }))
  }
  const save = async () => {
    if (busy) return
    if (!form.name.trim() || !EMAIL.test(form.email.trim())) {
      setError('Enter a name and a valid email address.'); return
    }
    if (!user && (!form.password || form.password !== form.confirmation)) {
      setError(form.password ? 'The two passwords are not the same.' : 'Enter a password for this person.'); return
    }
    if (blocked) { setError('Choose a staff member who is free to take over the candidates and open tasks.'); return }
    setBusy(true); setError('')
    const { confirmation, password, reassignToUserId, ...fields } = form
    const payload = { ...fields, name: form.name.trim(), email: form.email.trim().toLowerCase(), phone: form.phone.trim(),
      receivesAutoAssign: isStaff && form.active && form.receivesAutoAssign,
      onLeave: isStaff && form.onLeave }
    try {
      if (user) await api.patch('/users/' + user.id, { ...payload, ...(transfers ? { reassignToUserId } : {}) })
      else await api.post('/users', { ...payload, password })
      if (own && payload.email !== user?.email) { onClose(); await store.signOut(); return }
      await onSaved(user ? (deactivate ? 'Account deactivated' : 'Account updated') : 'User created')
      if (!user && password === generated) setCreated({ name: payload.name, email: payload.email, password })
      else onClose()
    } catch (err) { setError(failureText(err)); if (transfers) availability.refresh() }
    finally { setBusy(false) }
  }

  if (created) return <Dialog open onClose={onClose} width="md" title="Account created" description="Give the temporary password to the new user."
    footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
    <PasswordOnce created {...created} />
  </Dialog>

  const title = deactivate ? 'Deactivate ' + user?.name : user ? 'Edit account' : 'Add a team member'
  const description = deactivate ? 'This person loses access, and their work is moved to someone else.'
    : user ? user.email : 'Enter their details, choose a role, and choose if they get new candidates.'
  return <Dialog open onClose={close} width="lg" title={title} description={description}
    footer={<><Button disabled={busy} variant="ghost" onClick={close}>Cancel</Button>
      <Button type="submit" form="user-form" disabled={busy || blocked} variant={form.active ? 'primary' : 'danger'}>
        {busy ? 'Saving…' : !form.active && user ? 'Deactivate account' : user ? 'Save changes' : 'Create user'}</Button></>}>
    <form id="user-form" noValidate className="form-stack" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <fieldset disabled={busy} className="form-stack">
        {deactivate && user && <div className="uv-target"><UserAvatar name={user.name} /><div><p>{user.name}</p><span>{user.email}</span></div><RolePill role={user.role} /></div>}
        {!deactivate && <>
          <FormSection icon={<UserRound size={18} />} title="Profile" hint="Who this person is and how to reach them">
            <Field label="Full name" required className="sm:col-span-2"><Input autoFocus maxLength={160} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Priya Nair" /></Field>
            <Field label="Sign-in email" required><Input type="email" maxLength={255} value={form.email} onChange={(e) => set('email', e.target.value)} placeholder="name@company.com" /></Field>
            <Field label="Phone"><Input type="tel" maxLength={40} value={form.phone} onChange={(e) => set('phone', e.target.value)} placeholder="+971 50 000 0000" /></Field>
          </FormSection>
          <FormSection icon={<ShieldCheck size={18} />} title="Access" hint="The role decides what this person can see and do">
            <Field label="Role" hint={own ? 'Your own Admin role cannot be removed.' : roleBlurbs[form.role]} className="sm:col-span-2">
              <Select value={form.role} disabled={busy || own} onChange={(e) => set('role', e.target.value as Role)}><option value="staff">Staff</option><option value="manager">Manager</option><option value="admin">Admin</option></Select>
            </Field>
            {!user && <>
              <PasswordPair className="sm:col-span-2" label="Initial password" disabled={busy} password={form.password} confirmation={form.confirmation} generated={generated}
                onPassword={(value) => set('password', value)} onConfirmation={(value) => set('confirmation', value)} onGenerate={useGenerated} />
            </>}
            <div className="uv-toggle sm:col-span-2">
              <Switch label="Account active" description="Inactive accounts cannot sign in." checked={form.active} disabled={busy || own} onChange={(value) => set('active', value)} />
            </div>
          </FormSection>
          <FormSection icon={<RotateCw size={18} />} title="New candidates" hint="Sharing new candidates one after another" columns={false}>
            {isStaff ? <div className="uv-toggle">
              <Switch label="Gets new candidates" description="Include this person when new candidates are shared out." checked={form.receivesAutoAssign} disabled={busy || !form.active} onChange={(value) => set('receivesAutoAssign', value)} />
              <Switch label="On leave" description="Stops new candidates being given, but keeps the work they have. Approved leave in Attendance also stops them." checked={form.onLeave} disabled={busy || !form.active} onChange={(value) => set('onLeave', value)} />
            </div> : <p className="uv-note">Only Staff get new candidates automatically. {form.role === 'admin' ? 'Admins' : 'Managers'} never do, so these options do not apply.</p>}
            {isStaff && currentAvailability && <p className="uv-note">Right now: <strong>{availabilityWords(currentAvailability.status, currentAvailability.label)}</strong>. Your changes start when you save.</p>}
          </FormSection>
        </>}
        {(transfers || deactivate) && <FormSection icon={<ArrowRightLeft size={18} />} title="Move work" hint={deactivate ? 'Needed before access is removed' : 'This person is no longer a recruiter'} columns={false}>
          {transfers ? <>
            <div className="uv-transfer-counts">
              <div><strong>{owned}</strong><span>{owned === 1 ? 'candidate' : 'candidates'}</span></div>
              <div><strong>{tasks}</strong><span>open {tasks === 1 ? 'task' : 'tasks'}</span></div>
            </div>
            <p className="uv-note">Everything moves to one staff member. Existing due times and history are kept.</p>
            {availability.loading && <p role="status" className="uv-note">Checking who is free to take over…</p>}
            {availability.error && <p role="alert" className="uv-error">{availability.error}</p>}
            <Field label="Move the work to" required><Select value={replacementValid ? form.reassignToUserId : ''} disabled={busy || availability.loading || !!availability.error || !replacementOptions.length} onChange={(e) => set('reassignToUserId', e.target.value)}>
              <option value="">Choose an available staff member</option>{replacementOptions.map((candidate) => <option key={candidate.id} value={candidate.id}>{manualAssignmentLabel(candidate)}</option>)}
            </Select></Field>
            {availability.data && !replacementOptions.length && <p role="status" className="uv-warn">No one is free to take over. Check who is available before you deactivate this account.</p>}
            <div><Button size="sm" disabled={busy || availability.loading} onClick={availability.refresh}>Refresh</Button></div>
          </> : <p className="uv-note">This person has no candidates or open tasks, so nothing needs to be moved.</p>}
        </FormSection>}
        {user && !deactivate && <p className="uv-note"><KeyRound className="inline h-3.5 w-3.5" aria-hidden="true" /> If you change the role or email, this person is signed out everywhere. Deactivating removes access straight away.</p>}
        {error && <p role="alert" className="uv-error">{error}</p>}
      </fieldset>
    </form>
  </Dialog>
}
