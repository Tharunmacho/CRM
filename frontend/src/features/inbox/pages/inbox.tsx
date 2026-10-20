import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ChevronDown, CornerUpLeft, FileText, Image as ImageIcon, Mail, MailOpen, Paperclip, Plus, Settings2, Sparkles, UserPlus } from 'lucide-react'
import { api, fileHref, type InboxAccount, type InboxJob, type InboxSnapshot } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { initials, smartTime } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { Button, CheckboxInput, Field, SearchInput, Select, Switch } from '@/shared/ui/primitives'
import { Dialog, Dropdown, Tabs } from '@/shared/ui/overlay'
import { canRetryJob, isJobPending, JOB_LABEL, workerHealth } from '../inbox-status'
import { useInbox } from '../use-inbox'

const PROVIDER_LABEL = { gmail: 'Gmail', microsoft: 'Microsoft 365', imap: 'IMAP' }
const synced = (account: InboxAccount) => account.lastSyncAt ? `Last sync ${smartTime(account.lastSyncAt)}` : 'No completed sync yet'

export default function InboxPage() {
  const store = useStore()
  const navigate = useNavigate()
  const inbox = useInbox(store.me.id, store.isCurrentSession)
  const accounts = inbox.data?.accounts ?? []
  const allMessages = inbox.data?.messages ?? []
  const jobs = inbox.data?.jobs ?? []
  const admin = store.can('connect_sources')
  const [selectedIds, setSelected] = useState<string[] | null>(null)
  const selected = selectedIds ?? accounts.map((account) => account.id)
  const [query, setQuery] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [managing, setManaging] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [connectAuto, setConnectAuto] = useState(true)
  const [connectAddress, setConnectAddress] = useState('')
  const [mailFilter, setMailFilter] = useState('all')
  const [openingCandidate, setOpeningCandidate] = useState<string | null>(null)
  const availableAccounts = inbox.data?.availableAccounts ?? []
  const messages = useMemo(() => {
    const q = query.trim().toLowerCase()
    return allMessages
      .filter((message) => selected.includes(message.accountId))
      .filter((message) => mailFilter !== 'unread' || message.unread)
      .filter((message) => !q || [message.subject, message.fromName, message.fromAddress, message.body].some((text) => text.toLowerCase().includes(q)))
      .sort((a, b) => +new Date(b.receivedAt) - +new Date(a.receivedAt))
  }, [allMessages, selected, query, mailFilter])
  const open = messages.find((message) => message.id === openId) ?? messages[0]
  const openJobs = jobs.filter((job) => job.messageId === open?.id)
  const worker = inbox.data ? workerHealth(inbox.data.worker) : null
  const busy = inbox.busy !== null
  const openCandidate = async (id: string) => {
    if (openingCandidate) return
    setOpeningCandidate(id)
    try {
      // The worker can create a candidate after the CRM bootstrap was loaded.
      await store.refresh()
      if (store.isCurrentSession()) navigate(`/candidates/${id}`)
    } catch { store.notify('The candidate could not be loaded. Please try again.', 'warn') }
    finally { setOpeningCandidate(null) }
  }

  return <>
    <PageHeader title="Inbox" eyebrow="Connected conversations"
      description="Read incoming mail and follow each attachment through document inspection and candidate creation."
      actions={<>
        <AccountPicker accounts={accounts} messages={allMessages} selected={selected}
          onChange={(ids) => setSelected(ids.length === accounts.length ? null : ids)} onManage={admin ? () => setManaging(true) : undefined} />
        {admin && <Button variant="accent" icon={<Plus className="h-4 w-4" />} disabled={busy} onClick={() => setConnecting(true)}>Connect an account</Button>}
      </>} />
    {(inbox.error || inbox.actionError) && <div role="alert" className="mb-4 rounded-control border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <p>{inbox.actionError || inbox.error}</p>
      {inbox.error && <p className="mt-1 text-xs">{inbox.data ? 'Showing the last loaded inbox. Updates are temporarily unavailable.' : 'The inbox could not be loaded.'}</p>}
      <Button size="sm" className="mt-2" disabled={inbox.refreshing || busy} onClick={() => void inbox.refresh().catch(() => {})}>Try again</Button>
    </div>}
    {inbox.notice && <p role="status" className="mb-4 rounded-control border border-navy-100 bg-navy-50 px-4 py-3 text-sm text-navy-700">{inbox.notice}</p>}
    {!inbox.data && !inbox.error && <p role="status" className="py-8 text-center text-sm text-ink-500">Loading inbox…</p>}
    {inbox.data && <>
      <SummaryCards items={[
        { label: 'Total messages', value: allMessages.length, detail: 'Loaded from connected mailboxes', icon: Mail },
        { label: 'Unread', value: allMessages.filter((message) => message.unread).length, detail: 'Mailbox unread status', icon: MailOpen, tone: 'violet' },
        { label: 'Processing', value: jobs.filter(isJobPending).length, detail: 'Queued, reading or awaiting retry', icon: Sparkles, tone: 'green' },
        { label: 'Needs attention', value: jobs.filter((job) => ['needs_review', 'failed'].includes(job.status)).length, detail: 'Review the saved processing details', icon: AlertTriangle, tone: 'amber' },
      ]} />
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-ink-500" role="status">
        <Badge tone={worker?.healthy ? 'navy' : 'amber'}>{worker?.label}</Badge>
        {inbox.data.worker.lastHeartbeat && <span>Last check-in {smartTime(inbox.data.worker.lastHeartbeat)}</span>}
        <span>Mail sync runs automatically on the server, every 2 minutes by default.</span>
        <span>Status refreshes every 5 seconds while this page is visible.</span>
      </div>
      {accounts.length === 0 ? <Panel><EmptyState title="No mailbox connected"
        description={admin ? 'Connect the mailbox configured on the server to start receiving new email. Passwords stay on the server.' : 'Ask an administrator to connect the recruitment mailbox.'} /></Panel> : <>
        <div className="mb-4 grid gap-3">{accounts.filter((account) => selected.includes(account.id)).map((account) => <Panel key={account.id} className="flex flex-col items-start gap-3 sm:flex-row sm:flex-wrap sm:items-center">
          <div className="w-full min-w-0 sm:w-auto sm:flex-1 sm:basis-64"><p className="break-words text-sm font-medium text-ink">{account.address}</p>
            <p className="mt-1 text-xs text-ink-500">{PROVIDER_LABEL[account.provider] ?? account.provider} · {synced(account)}</p>
            {account.lastError && <p className="mt-1 text-xs text-amber-900">{account.lastError}</p>}</div>
          <div className="flex max-w-full flex-wrap items-center gap-2 sm:shrink-0">
            <Badge>{account.connectionStatus.replaceAll('_', ' ')}</Badge>
            <Badge tone={account.autoIngest ? 'navy' : 'neutral'}>{account.autoIngest ? 'Auto-ingest on' : 'Auto-ingest off'}</Badge>
          </div>
        </Panel>)}</div>
        <div className="workspace-split inbox-layout">
          <Panel flush className="inbox-message-list h-fit min-w-0">
            <PanelHeader title="Messages" meta={`${messages.length} in this view`} />
            <Tabs tabs={[{ key: 'all', label: 'All messages' }, { key: 'unread', label: 'Unread' }]} active={mailFilter} onChange={setMailFilter} />
            <div className="border-b border-rule-soft p-3"><SearchInput value={query} onChange={setQuery} placeholder="Search mail" /></div>
            <ul className="scrollbar-thin max-h-[36rem] overflow-y-auto">
              {messages.map((message) => {
                const messageJobs = jobs.filter((job) => job.messageId === message.id)
                return <li key={message.id}><button onClick={() => setOpenId(message.id)} aria-pressed={open?.id === message.id}
                  className={cn('mail-list-item w-full border-b border-rule-soft px-3 py-2.5 text-left transition-colors last:border-0', open?.id === message.id ? 'bg-navy-50' : 'hover:bg-field/70')}>
                  <span className="mail-sender-avatar" aria-hidden>{initials(message.fromName || message.fromAddress)}</span>
                  <span className="mail-list-copy"><span className="flex items-baseline gap-2">
                    {message.unread && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-navy-600" />}
                    <span className={cn('min-w-0 flex-1 truncate text-sm', message.unread ? 'font-semibold text-ink' : 'text-ink-700')}>{message.fromName || message.fromAddress}</span>
                    <span className="shrink-0 text-micro text-ink-400">{smartTime(message.receivedAt)}</span></span>
                    <span className="mt-0.5 block truncate text-xs text-ink-600">{message.subject || '(No subject)'}</span>
                    <span className="mt-0.5 block truncate text-micro text-ink-400">{message.preview}</span>
                    <span className="mt-1 flex flex-wrap items-center gap-1.5">
                      {message.attachments.length > 0 && <span className="inline-flex items-center gap-0.5 text-micro text-ink-400"><Paperclip className="h-3 w-3" />{message.attachments.length}</span>}
                      {(message.candidateId || messageJobs.some((job) => job.candidateId)) && <Badge tone="navy">Candidate linked</Badge>}
                      {messageJobs.slice(0, 1).map((job) => <Badge key={job.id} tone={job.status === 'completed' ? 'navy' : 'amber'}>{JOB_LABEL[job.status] ?? job.status}</Badge>)}
                    </span>
                  </span>
                </button></li>
              })}
              {messages.length === 0 && <EmptyState title={selected.length === 0 ? 'No accounts selected' : 'No messages in this view'} description={selected.length === 0 ? 'Select a mailbox above.' : query || mailFilter !== 'all' ? 'Try another search or filter.' : 'New mail received after connection will appear automatically when the worker next checks the mailbox.'} />}
            </ul>
          </Panel>
          <Panel flush className="inbox-reader min-w-0">
            {open ? <>
              <div className="inbox-reader-header border-b border-rule-soft px-4 py-3.5"><span className="workspace-eyebrow">Conversation</span><h2 className="text-h3 font-semibold text-ink">{open.subject || '(No subject)'}</h2>
                <div className="mail-sender-detail mt-2 flex flex-wrap items-center gap-2.5"><span className="mail-sender-avatar" aria-hidden>{initials(open.fromName || open.fromAddress)}</span>
                  <div className="min-w-0"><p className="truncate text-sm text-ink">{open.fromName}</p><p className="truncate text-micro text-ink-400">{open.fromAddress}</p></div>
                  <span className="ml-auto text-micro text-ink-400">{smartTime(open.receivedAt)} · to {accounts.find((account) => account.id === open.accountId)?.address}</span></div>
                {open.candidateId && <Button size="sm" className="mt-3" disabled={!!openingCandidate} onClick={() => void openCandidate(open.candidateId!)}>Open candidate</Button>}
              </div>
              <div className="inbox-reader-body px-4 py-4"><p className="max-w-[70ch] text-sm leading-relaxed text-ink-700">{open.body}</p>
                {open.attachments.length > 0 && <div className="mt-4"><p className="mb-2 text-xs font-medium text-ink-600">{open.attachments.length} attachment{open.attachments.length > 1 ? 's' : ''}</p>
                  <ul className="space-y-1.5">{open.attachments.map((attachment, index) => {
                    const Icon = attachment.kind === 'image' ? ImageIcon : FileText
                    return <li key={attachment.id ?? `${attachment.fileName}-${index}`} className="mail-attachment flex items-center gap-2.5 rounded-control border border-rule px-3 py-2"><Icon className="h-4 w-4 shrink-0 text-ink-400" />
                      <span className="min-w-0 flex-1 truncate text-sm text-ink">{attachment.fileName}</span><span className="shrink-0 text-micro text-ink-400">{attachment.sizeKb} KB</span>
                      {attachment.url && <a href={fileHref(attachment.url)} target="_blank" rel="noreferrer" className="text-xs text-navy-700 underline">View original</a>}</li>
                  })}</ul>
                  {openJobs.length === 0 && !open.candidateId && <div className="mt-3"><Button size="sm" disabled={busy} icon={<UserPlus className="h-3.5 w-3.5" />}
                    onClick={() => void inbox.act(`ingest:${open.id}`, () => api.inbox.ingest(open.id), 'Attachment processing queued. Progress and candidate links will appear below.')}>
                    {inbox.busy === `ingest:${open.id}` ? 'Requesting…' : 'Create candidate from attachments'}</Button>
                    <p className="mt-1 text-xs text-ink-400">Reads the message's attachments together so related identity pages stay with the same candidate.</p></div>}
                </div>}
                {openJobs.length > 0 && <div className="mt-5 space-y-3" aria-label="Attachment processing">{openJobs.map((job) => <JobReport key={job.id} job={job} busy={busy} canRetry={admin} opening={!!openingCandidate} onOpen={openCandidate}
                  onRetry={() => inbox.act(`retry:${job.id}`, () => api.inbox.retry(job.id), 'Retry queued. Follow the updated processing status here.')} />)}</div>}
              </div>
              <div className="inbox-reply border-t border-rule-soft bg-field/40 px-4 py-3"><p className="mb-2 flex items-center gap-1.5 text-xs text-ink-500"><CornerUpLeft className="h-3.5 w-3.5" />Reply sending is not connected yet. Use your mailbox to reply.</p><Button disabled title="Outbound email is not implemented yet">Send reply</Button></div>
            </> : <EmptyState title="Pick a message" description="Choose a message to read its attachments and processing status." />}
          </Panel>
        </div>
      </>}
    </>}
    {admin && <>
      <Dialog open={connecting} onClose={() => !busy && setConnecting(false)} title="Connect the configured mailbox" width="md" description="Mailbox credentials are configured securely on the server."
        footer={<><Button variant="ghost" disabled={busy} onClick={() => setConnecting(false)}>Cancel</Button><Button variant="primary" disabled={busy} onClick={async () => {
          if (await inbox.act('connect', () => api.inbox.connect(connectAuto, connectAddress || availableAccounts[0]), 'Mailbox connected. New email will sync automatically while the worker is running.')) setConnecting(false)
        }}>{inbox.busy === 'connect' ? 'Connecting…' : 'Connect mailbox'}</Button></>}>
        <p className="mb-4 text-sm text-ink-600">Connect an IMAP mailbox configured for this CRM once. New mail then syncs automatically; existing mailbox history is not imported.</p>
        {availableAccounts.length > 0 && <Field label="Mailbox" className="mb-4"><Select value={connectAddress || availableAccounts[0]} onChange={(event) => setConnectAddress(event.target.value)} disabled={busy}>
          {availableAccounts.map((address) => <option key={address} value={address}>{address}</option>)}
        </Select></Field>}
        <Switch checked={connectAuto} onChange={setConnectAuto} disabled={busy} label="Auto-ingest new attachments" description="Queue new documents for inspection and candidate processing." />
        {inbox.actionError && <p role="alert" className="mt-4 text-sm text-stamp-600">{inbox.actionError}</p>}
      </Dialog>
      <Dialog open={managing} onClose={() => !busy && setManaging(false)} title="Connected accounts" width="md" description="Manage automatic processing for the mailbox configured on the server."
        footer={<Button variant="primary" disabled={busy} onClick={() => setManaging(false)}>Done</Button>}>
        <ul className="divide-y divide-rule-soft">{accounts.map((account) => <li key={account.id} className="space-y-3 py-3 first:pt-0">
          <div><p className="break-all text-sm font-medium text-ink">{account.address}</p><p className="mt-1 text-xs text-ink-400">{synced(account)}</p></div>
          <Switch checked={account.autoIngest} disabled={busy || !account.configured} label={`Auto-ingest for ${account.address}`} description="Automatically queue newly received attachments."
            onChange={(enabled) => void inbox.act(`auto:${account.id}`, () => api.inbox.setAutoIngest(account.id, enabled), `Auto-ingest ${enabled ? 'enabled' : 'disabled'}.`)} />
          {!account.configured && <p className="text-xs text-amber-900">This mailbox no longer matches the server configuration. Ask the server administrator to update it.</p>}
          {account.lastError && <p className="text-xs text-amber-900">{account.lastError}</p>}
        </li>)}</ul>
        {inbox.actionError && <p role="alert" className="mt-3 text-sm text-stamp-600">{inbox.actionError}</p>}
        <p className="mt-4 border-t border-rule-soft pt-3 text-xs text-ink-500">Mail continues to sync automatically when auto-ingest is off. This toggle controls attachment processing only. Open a message and choose Create candidate from attachments to queue it manually.</p>
      </Dialog>
    </>}
  </>
}

function JobReport({ job, busy, canRetry, opening, onOpen, onRetry }: {
  job: InboxJob; busy: boolean; canRetry: boolean; opening: boolean
  onOpen: (id: string) => Promise<void>; onRetry: () => Promise<boolean>
}) {
  const pages = (numbers: number[]) => numbers?.length ? numbers.join(', ') : 'None'
  return <section className="rounded-control border border-rule bg-field/40 p-3 text-xs" aria-label="Processing result">
    <div className="flex flex-wrap items-center gap-2"><Badge tone={job.status === 'completed' ? 'navy' : 'amber'}>{JOB_LABEL[job.status] ?? job.status}</Badge><span className="text-ink-500">Attempts: {job.attempts} · Updated {smartTime(job.updatedAt)}</span></div>
    {job.lastError && <p className="mt-2 whitespace-pre-wrap break-words text-amber-900">{job.lastError}</p>}
    {job.inspection?.map((report, index) => <details key={report.attachmentId ?? index} className="mt-3 rounded border border-rule bg-white p-2">
      <summary className="cursor-pointer break-words font-medium text-ink">{report.fileName} · {report.pageCount} pages · {report.status.replaceAll('_', ' ')}</summary>
      <dl className="mt-2 grid gap-2 text-ink-600 sm:grid-cols-2">
        <div><dt>Resume pages</dt><dd>{pages(report.resumePages)}</dd></div><div><dt>Passport pages</dt><dd>{pages(report.passportPages)}</dd></div><div><dt>Aadhaar pages</dt><dd>{pages(report.aadhaarPages)}</dd></div>
        <div><dt>Unread pages</dt><dd>{pages(report.unreadPages)}</dd></div><div><dt>Uncertain pages</dt><dd>{pages(report.unknownPages)}</dd></div>
      </dl>
    </details>)}
    <div className="mt-3 flex flex-wrap gap-2">{job.candidateId && <Button size="sm" disabled={opening} onClick={() => void onOpen(job.candidateId!)}>Open candidate</Button>}{canRetry && canRetryJob(job) && <Button size="sm" disabled={busy} onClick={() => void onRetry()}>Retry processing</Button>}</div>
  </section>
}

function AccountPicker({ accounts, messages, selected, onChange, onManage }: {
  accounts: InboxAccount[]; messages: InboxSnapshot['messages']; selected: string[]
  onChange: (ids: string[]) => void; onManage?: () => void
}) {
  const countFor = (id: string) => messages.filter((message) => message.accountId === id).length
  const allOn = accounts.length > 0 && selected.length === accounts.length
  const some = selected.length > 0 && !allOn
  const label = allOn ? 'All mail' : selected.length === 0 ? 'Select accounts' : selected.length === 1 ? accounts.find((account) => account.id === selected[0])?.address ?? 'Mailbox' : `${selected.length} accounts`
  return <Dropdown width="w-[min(22rem,calc(100vw-2rem))]" trigger={({ open, toggle }) => <button onClick={toggle} aria-expanded={open}
    className="inbox-account-trigger flex h-10 w-[min(22rem,calc(100vw-2rem))] items-center gap-2 rounded-control border border-rule bg-white px-2.5 text-sm"><Mail className="h-4 w-4 shrink-0 text-ink-400" /><span className="min-w-0 flex-1 truncate text-left">{label}</span><ChevronDown className="h-4 w-4 shrink-0" /></button>}>
    {(close) => <div aria-label="Mailbox filters"><label className="flex items-center gap-2.5 border-b border-rule-soft px-3 py-2.5"><CheckboxInput checked={allOn} indeterminate={some} disabled={accounts.length === 0} onChange={(on) => onChange(on ? accounts.map((account) => account.id) : [])} /><span className="flex-1 text-sm font-medium">All mail</span><span className="text-xs text-ink-400">{messages.length}</span></label>
      {accounts.map((account) => <label key={account.id} className="flex items-start gap-2.5 px-3 py-2.5"><CheckboxInput checked={selected.includes(account.id)} onChange={(on) => onChange(on ? [...selected, account.id] : selected.filter((id) => id !== account.id))} /><span className="min-w-0 flex-1"><span className="block truncate text-sm">{account.address}</span><span className="text-xs text-ink-400">{synced(account)}</span></span><span className="text-xs text-ink-400">{countFor(account.id)}</span></label>)}
      {onManage && <button onClick={() => { close(); onManage() }} className="flex w-full items-center gap-2 border-t border-rule px-3 py-2.5 text-left text-sm text-ink-600 hover:bg-field"><Settings2 className="h-4 w-4" />Manage accounts and auto-ingest</button>}
    </div>}
  </Dropdown>
}
