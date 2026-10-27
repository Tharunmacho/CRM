import { useState } from 'react'
import { Sparkles } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import type { User } from '@/shared/model/types'
import { Button, Field, Input } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { PasswordOnce, PasswordPair, UserAvatar, failureText, generatePassword } from './user-ui'

export function SecurityDialog({ user, kind, onClose }: { user: User; kind: 'password' | 'logout'; onClose: () => void }) {
  const store = useStore()
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [generated, setGenerated] = useState('')
  const [done, setDone] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const own = user.id === store.me.id
  const generate = () => { const next = generatePassword(); setGenerated(next); setPassword(next); setConfirmation(next); setError('') }
  const submit = async () => {
    if (busy) return
    if (kind === 'password' && (!password || password !== confirmation)) { setError(password ? 'The two passwords are not the same.' : 'Enter a new password.'); return }
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
        <PasswordPair label="New password" confirmLabel="Confirm new password" autoFocus disabled={busy} password={password} confirmation={confirmation} generated={generated}
          onPassword={setPassword} onConfirmation={setConfirmation} onGenerate={generate} />
      </fieldset>}
      {own && <p className="uv-warn">This is your account. You will be signed out after this change.</p>}
      {error && <p role="alert" className="uv-error">{error}</p>}
    </form>
  </Dialog>
}
