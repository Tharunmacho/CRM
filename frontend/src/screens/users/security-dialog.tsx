import { useState } from 'react'
import { Sparkles } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import type { User } from '@/shared/model/types'
import { Button, Field, Input } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { PasswordOnce, PasswordReveal, UserAvatar, failureText, generatePassword } from './user-ui'

export function SecurityDialog({ user, kind, onClose }: { user: User; kind: 'password' | 'logout'; onClose: () => void }) {
  const store = useStore()
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [generated, setGenerated] = useState('')
  const [done, setDone] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const own = user.id === store.me.id
  const shown = generated !== '' && generated === password && password === confirmation
  const generate = () => { const next = generatePassword(); setGenerated(next); setPassword(next); setConfirmation(next); setError('') }
  const submit = async () => {
    if (busy) return
    if (kind === 'password' && (password.length < 12 || password !== confirmation)) { setError('Use at least 12 characters and matching passwords.'); return }
    setBusy(true); setError('')
    try {
      const result = await api.post<{ revokedSessions?: number } | undefined>('/users/' + user.id + (kind === 'password' ? '/password' : '/force-logout'), kind === 'password' ? { password } : undefined)
      if (own) { onClose(); await store.signOut(); return }
      const count = result?.revokedSessions
      if (kind === 'password' && password === generated) { setDone(password); return }
      store.notify(kind === 'password' ? 'Password changed. This person was signed out everywhere.'
        : typeof count === 'number' ? `${count} ${count === 1 ? 'session was' : 'sessions were'} signed out.` : 'All sessions were signed out.')
      onClose()
    } catch (err) { setError(failureText(err)) }
    finally { setBusy(false) }
  }
  const close = () => { if (!busy) onClose() }
  if (done) return <Dialog open onClose={onClose} width="md" title="Password reset" description="This person was signed out everywhere."
    footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
    <PasswordOnce name={user.name} email={user.email} password={done} />
  </Dialog>
  return <Dialog open onClose={close} width="md" title={kind === 'password' ? 'Reset password' : 'Sign out everywhere'}
    description="Account security"
    footer={<><Button disabled={busy} variant="ghost" onClick={close}>Cancel</Button><Button variant="primary" type="submit" form="security-form" disabled={busy}>{busy ? 'Saving…' : kind === 'password' ? 'Reset password' : 'Sign out everywhere'}</Button></>}>
    <form id="security-form" noValidate className="space-y-4" onSubmit={(event) => { event.preventDefault(); void submit() }}>
      <div className="uv-target"><UserAvatar name={user.name} /><div><p>{user.name}</p><span>{user.email}</span></div></div>
      <p className="uv-note">{kind === 'password'
        ? 'Set a new password. This person will be signed out everywhere. Their account stays active or inactive as it is now. The password is not written in the history.'
        : 'This person will have to sign in again. Their account and work stay as they are.'}</p>
      {kind === 'password' && <fieldset disabled={busy} className="form-stack">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="New password" required hint="At least 12 characters."><Input type={shown ? 'text' : 'password'} autoComplete="new-password" autoFocus maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          <Field label="Confirm new password" required><Input type={shown ? 'text' : 'password'} autoComplete="new-password" maxLength={128} value={confirmation} onChange={(e) => setConfirmation(e.target.value)} /></Field>
        </div>
        <div className="uv-generate">
          <Button size="sm" icon={<Sparkles className="h-4 w-4" />} onClick={generate}>Create a strong password</Button>
          {shown ? <PasswordReveal value={password} defaultVisible /> : <span>A password we create is shown once, after you save.</span>}
        </div>
      </fieldset>}
      {own && <p className="uv-warn">This is your account. You will be signed out after this change.</p>}
      {error && <p role="alert" className="uv-error">{error}</p>}
    </form>
  </Dialog>
}
