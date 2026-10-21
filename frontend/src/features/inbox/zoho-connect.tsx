import { useState } from 'react'
import { api, type InboxAccount } from '@/shared/api/client'
import { Button, Field, Input, Select, Switch } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'

export function ZohoConnect({ address = '', close, connected }: { address?: string; close: () => void; connected: (account: InboxAccount) => void }) {
  const [email, setEmail] = useState(address)
  const [password, setPassword] = useState('')
  const [region, setRegion] = useState('in')
  const [accountType, setAccountType] = useState('organization')
  const [imapHost, setImapHost] = useState('')
  const [smtpHost, setSmtpHost] = useState('')
  const [auto, setAuto] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async () => {
    if (busy) return
    setBusy(true); setError('')
    try {
      const account = await api.post<InboxAccount>('/inbox/accounts/zoho', { address: email.trim(), password, region, account_type: accountType, auto_ingest: auto,
        ...(imapHost.trim() ? { imap_host: imapHost.trim() } : {}), ...(smtpHost.trim() ? { smtp_host: smtpHost.trim() } : {}) })
      setPassword(''); connected(account)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'The mailbox could not be connected.') }
    finally { setBusy(false) }
  }
  return <Dialog open onClose={() => { if (!busy) { setPassword(''); close() } }} title={address ? 'Reconnect Zoho Mail' : 'Connect Zoho Mail'} width="md"
    description="Use your Zoho mailbox and app password to bring mail into the CRM."
    footer={<><Button disabled={busy} onClick={() => { setPassword(''); close() }}>Cancel</Button><Button variant="primary" type="submit" form="zoho-connect" disabled={busy || !email.trim() || !password}>{busy ? 'Connecting…' : 'Connect mailbox'}</Button></>}>
    <form id="zoho-connect" onSubmit={(event) => { event.preventDefault(); void submit() }} className="space-y-4">
      <Field label="Email address" required><Input type="email" autoComplete="username" required maxLength={255} value={email} disabled={busy} onChange={(event) => setEmail(event.target.value)} /></Field>
      <Field label="Zoho app password" required hint="Use an app password when multi-factor authentication is enabled. It is encrypted on the server and is never saved in this browser."><Input type="password" autoComplete="new-password" required value={password} disabled={busy} onChange={(event) => setPassword(event.target.value)} /></Field>
      <div className="grid gap-4 sm:grid-cols-2"><Field label="Zoho data region"><Select value={region} disabled={busy} onChange={(event) => setRegion(event.target.value)}>{[['in', 'India'], ['com', 'United States'], ['eu', 'Europe'], ['com.au', 'Australia'], ['jp', 'Japan'], ['ca', 'Canada'], ['com.cn', 'China'], ['sa', 'Saudi Arabia']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></Field>
        <Field label="Mailbox type"><Select value={accountType} disabled={busy} onChange={(event) => setAccountType(event.target.value)}><option value="organization">Organization / custom domain</option><option value="personal">Personal Zoho mailbox</option></Select></Field></div>
      <Switch checked={auto} disabled={busy} onChange={setAuto} label="Create candidates from new attachments" description="Queue incoming resumes for document inspection and assignment." />
      <details className="rounded-control border border-rule p-3"><summary className="cursor-pointer text-sm font-medium text-ink-700">Server settings</summary><p className="my-3 text-xs text-ink-500">Leave these empty to use the selected Zoho region and mailbox type. Only verified Zoho mail servers are accepted.</p><div className="grid gap-3 sm:grid-cols-2"><Field label="IMAP host override"><Input value={imapHost} disabled={busy} pattern="[A-Za-z0-9.-]+" onChange={(event) => setImapHost(event.target.value)} placeholder="Automatic for selected region" /></Field><Field label="SMTP host override"><Input value={smtpHost} disabled={busy} pattern="[A-Za-z0-9.-]+" onChange={(event) => setSmtpHost(event.target.value)} placeholder="Automatic for selected region" /></Field></div></details>
      <p className="text-xs leading-relaxed text-ink-500">IMAP access must be enabled for this Zoho account. Existing mail is available through folders; candidate intake from older messages remains an explicit action.</p>
      {error && <p role="alert" className="rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    </form>
  </Dialog>
}
