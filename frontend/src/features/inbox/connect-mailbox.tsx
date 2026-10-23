import { useEffect, useRef, useState } from 'react'
import { Eye, EyeOff, Mail, ShieldCheck } from 'lucide-react'
import { api, type InboxAccount, type InboxSnapshot, type MailProvider } from '@/shared/api/client'
import { Button, Field, Input, Select, Switch } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { cn } from '@/shared/lib/cn'
import { PROVIDER_LABEL } from './inbox-status'

type Readiness = NonNullable<InboxSnapshot['providers']>
const CHOICES: { provider: MailProvider; hint: string }[] = [
  { provider: 'gmail', hint: 'Sign in with Google' },
  { provider: 'microsoft', hint: 'Sign in with Microsoft' },
  { provider: 'zoho', hint: 'App password' },
  { provider: 'imap', hint: 'Any other mail server' },
]
const ZOHO_REGIONS = [['in', 'India'], ['com', 'United States'], ['eu', 'Europe'], ['com.au', 'Australia'], ['jp', 'Japan'], ['ca', 'Canada'], ['com.cn', 'China'], ['sa', 'Saudi Arabia']]

/** What usually fixes a rejected sign-in, per provider; the server message stays as the headline. */
const signInHint = (message: string, provider: MailProvider) => !/sign-in failed|credentials/i.test(message) ? '' : provider === 'zoho'
  ? 'Use a Zoho app password (Zoho Account → Security → App Passwords), not your normal password. Make sure IMAP is enabled in Zoho Mail settings and the data region matches your account.'
  : provider === 'imap' ? 'Check the username and password, that IMAP access is enabled for the mailbox, and use an app password if the provider requires one.' : ''

/** Connects any number of mailboxes: OAuth for Gmail/Microsoft, credentials for Zoho and IMAP. */
export function ConnectMailbox({ address = '', provider: initial, providers = [], close, connected }: {
  address?: string; provider?: MailProvider; providers?: Readiness; close: () => void; connected: (account: InboxAccount) => void
}) {
  const [provider, setProvider] = useState<MailProvider>(initial ?? 'gmail')
  const [email, setEmail] = useState(address)
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
  const [auto, setAuto] = useState(true)
  const [region, setRegion] = useState('in')
  const [accountType, setAccountType] = useState('organization')
  const [imapHost, setImapHost] = useState('')
  const [imapPort, setImapPort] = useState('993')
  const [smtpHost, setSmtpHost] = useState('')
  const [smtpPort, setSmtpPort] = useState('587')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const errorRef = useRef<HTMLDivElement>(null)
  const reconnecting = !!address
  const readiness = providers.find((item) => item.provider === provider)
  const unavailable = readiness && !readiness.ready ? readiness.message : ''
  const oauth = provider === 'gmail' || provider === 'microsoft'
  useEffect(() => {
    if (!error) return
    errorRef.current?.focus({ preventScroll: true })
    errorRef.current?.scrollIntoView({ block: 'nearest' })
  }, [error])
  const finish = () => { setPassword(''); setReveal(false); close() }
  const submit = async () => {
    if (busy) return
    setBusy(true); setError('')
    try {
      const base = { address: email.trim(), auto_ingest: auto }
      if (oauth) {
        const { authorizationUrl } = await api.post<{ authorizationUrl: string }>(`/inbox/oauth/${provider}/start`, base)
        window.location.assign(authorizationUrl)
        return // The browser leaves for the provider's consent screen; the busy state stays on.
      }
      const account = provider === 'zoho'
        ? await api.post<InboxAccount>('/inbox/accounts/zoho', { ...base, password, region, account_type: accountType })
        : await api.post<InboxAccount>('/inbox/accounts/imap', { ...base, password, imap_host: imapHost.trim(), imap_port: Number(imapPort), smtp_host: smtpHost.trim(), smtp_port: Number(smtpPort) })
      setPassword(''); connected(account)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'The mailbox could not be connected.') }
    setBusy(false)
  }
  const ready = !!email.trim() && !unavailable && (oauth || (!!password && (provider === 'zoho' || (!!imapHost.trim() && !!smtpHost.trim()))))
  return <Dialog open onClose={() => { if (!busy) finish() }} title={reconnecting ? 'Reconnect mailbox' : 'Connect a mailbox'} width="lg" scroll="inside" appearance="clean"
    description="Connect as many mailboxes as you need. Mail from every account appears together in one inbox."
    footer={<><Button className="flex-1 sm:flex-none" disabled={busy} onClick={finish}>Cancel</Button><Button className="flex-1 sm:flex-none" icon={<Mail size={17} />} variant="primary" type="submit" form="connect-mailbox" disabled={busy || !ready}>{busy ? (oauth ? 'Opening sign-in…' : 'Connecting…') : oauth ? `Continue with ${PROVIDER_LABEL[provider]}` : 'Connect mailbox'}</Button></>}>
    <form id="connect-mailbox" onSubmit={(event) => { event.preventDefault(); void submit() }} className="space-y-6">
      {error && <div ref={errorRef} role="alert" tabIndex={-1} className="rounded-control border border-stamp-100 bg-white p-4 text-sm text-stamp-700 shadow-sm"><p className="mb-1 font-semibold">{error.includes('MAIL_TOKEN_ENCRYPTION_KEY') ? 'Server setup required' : 'Mailbox could not connect'}</p><p className="break-words">{error}</p>{signInHint(error, provider) && <p className="mt-2 text-xs text-ink-500">{signInHint(error, provider)}</p>}</div>}
      <fieldset className="min-w-0" disabled={busy || reconnecting}>
        <legend className="mb-2 text-sm font-semibold text-ink">Mail provider</legend>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" role="radiogroup" aria-label="Mail provider">
          {CHOICES.map((choice) => <button key={choice.provider} type="button" role="radio" aria-checked={provider === choice.provider} onClick={() => { setProvider(choice.provider); setError('') }}
            className={cn('rounded-control border p-3 text-left transition-colors disabled:opacity-60', provider === choice.provider ? 'border-navy-600 bg-navy-50 text-navy-900' : 'border-rule hover:bg-field/60')}>
            <span className="block text-sm font-semibold">{PROVIDER_LABEL[choice.provider]}</span><span className="mt-1 block text-xs text-ink-500">{choice.hint}</span>
          </button>)}
        </div>
      </fieldset>
      {unavailable && <p role="status" className="rounded-control bg-amber-50 p-3 text-sm text-amber-900">{unavailable}</p>}
      <fieldset className="min-w-0 space-y-4" disabled={busy}>
        <legend className="text-sm font-semibold text-ink">Mailbox details</legend>
        <Field label="Email address" required><Input type="email" name="username" autoComplete="username" required maxLength={255} placeholder="jobs@yourcompany.com" value={email} readOnly={reconnecting} onChange={(event) => setEmail(event.target.value)} /></Field>
        {oauth
          ? <p className="flex items-start gap-2 text-xs text-ink-500"><ShieldCheck size={16} className="mt-0.5 shrink-0 text-navy-600" />You will sign in on {provider === 'gmail' ? "Google's" : "Microsoft's"} own page and approve access. Your password is never shared with the CRM, and the access grant is stored encrypted on the server.</p>
          : <>
            <Field label={provider === 'zoho' ? 'Zoho app password' : 'Mailbox password or app password'} required hint={provider === 'zoho' ? 'Paste the app password generated in your Zoho account.' : 'Use an app-specific password where your provider offers one.'}><div className="relative"><Input type={reveal ? 'text' : 'password'} name="password" className="pr-11" autoComplete="current-password" autoCapitalize="none" spellCheck={false} required value={password} onChange={(event) => setPassword(event.target.value)} /><button type="button" className="absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-r-control text-ink-400 hover:text-ink-700 focus-visible:text-navy-700" aria-label={reveal ? 'Hide password' : 'Show password'} aria-pressed={reveal} onClick={() => setReveal((value) => !value)}>{reveal ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></Field>
            <p className="flex items-start gap-2 text-xs text-ink-500"><ShieldCheck size={16} className="mt-0.5 shrink-0 text-navy-600" />The password is encrypted on the server. When your browser offers to save it, choose Save to fill it in automatically next time.</p>
          </>}
      </fieldset>
      {provider === 'zoho' && <fieldset className="min-w-0 border-t border-rule-soft pt-5" disabled={busy}>
        <legend className="pr-3 text-sm font-semibold text-ink">Connection settings</legend>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2"><Field className="min-w-0" label="Zoho data region"><Select value={region} onChange={(event) => setRegion(event.target.value)}>{ZOHO_REGIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></Field>
          <Field className="min-w-0" label="Mailbox type"><Select value={accountType} onChange={(event) => setAccountType(event.target.value)}><option value="organization">Organization / custom domain</option><option value="personal">Personal Zoho mailbox</option></Select></Field></div>
        <p className="mt-3 text-xs text-ink-500">Match the region to the server names shown in Zoho Mail settings. Enable IMAP access there before connecting.</p>
      </fieldset>}
      {provider === 'imap' && <fieldset className="min-w-0 border-t border-rule-soft pt-5" disabled={busy}>
        <legend className="pr-3 text-sm font-semibold text-ink">Mail servers</legend>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_9rem]"><Field className="min-w-0" label="Incoming (IMAP) server" required><Input value={imapHost} required placeholder="imap.example.com" pattern="[A-Za-z0-9.\-]+" onChange={(event) => setImapHost(event.target.value)} /></Field>
          <Field className="min-w-0" label="Port"><Select value={imapPort} onChange={(event) => setImapPort(event.target.value)}><option value="993">993 · SSL</option><option value="143">143 · STARTTLS</option></Select></Field>
          <Field className="min-w-0" label="Outgoing (SMTP) server" required><Input value={smtpHost} required placeholder="smtp.example.com" pattern="[A-Za-z0-9.\-]+" onChange={(event) => setSmtpHost(event.target.value)} /></Field>
          <Field className="min-w-0" label="Port"><Select value={smtpPort} onChange={(event) => setSmtpPort(event.target.value)}><option value="587">587 · STARTTLS</option><option value="465">465 · SSL</option></Select></Field></div>
        <p className="mt-3 text-xs text-ink-500">Connections always use encryption. Enable IMAP access in your mail provider's settings first.</p>
      </fieldset>}
      <div className="rounded-control border border-rule-soft bg-white p-4"><Switch checked={auto} disabled={busy} onChange={setAuto} label="Create candidates from new attachments" description="New resumes enter document review and assignment. You can change this per mailbox at any time." /></div>
    </form>
  </Dialog>
}
