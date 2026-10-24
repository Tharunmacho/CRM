import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { AlertTriangle, ChevronLeft, ChevronRight, Clock3, Download, ExternalLink, FileText, MessageCircle, Phone, RefreshCw, UserCheck, Zap } from 'lucide-react'
import { absoluteApiHref, api, fileHref } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Select, SearchInput, Switch, Textarea } from '@/shared/ui/primitives'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader, type Tone } from '@/shared/ui/display'
import { Dialog, Tabs } from '@/shared/ui/overlay'
import { SummaryCards, RecordIdentity } from '@/shared/ui/workspace'
import { DataTable, type Column } from '@/shared/ui/table'
import { dateTimeLabel, smartTime } from '@/shared/lib/format'
import { cn } from '@/shared/lib/cn'
import '@/features/inbox/mailbox.css'
import '@/styles/inbox-v2.css'
import { WhatsAppChat } from '@/features/whatsapp/whatsapp-chat'
import { conversationFromSearch } from '@/features/whatsapp/chat-types'
import { CandidateChip, FileIcon } from '@/features/inbox/mail-parts'
import { IntakeAcknowledgements } from '@/features/intake/intake-acknowledgements'
import { DocumentBundleReview } from '@/features/candidates/components/document-bundle-review'

type Message = { documentBundle?: {id: string; status: string; candidateIds: string[]} | null; id: string; phoneNumberId: string; senderNumber: string; senderName?: string; type: string; body?: string; fileName?: string; hasFile: boolean; receivedAt: string; status: string; attempts: number; lastError?: string; candidateId?: string }
type Inbox = { configured: boolean; configuredPhoneNumberId?: string; enabled: boolean; missingSettings: string[]; webhookPath: string; total: number; page: number; pageSize: number; messages: Message[]; channels: { id: string; displayNumber?: string; enabled: boolean; autoIngest: boolean; lastReceivedAt?: string; workerSeenAt?: string; lastError?: string }[] }
type WaTab = 'chat' | 'intake' | 'settings'
const TABS: { key: WaTab; label: string }[] = [{ key: 'chat', label: 'Conversations' }, { key: 'intake', label: 'Candidate documents' }, { key: 'settings', label: 'Number settings' }]
const STATUSES = ['received', 'pending', 'processing', 'retry', 'needs_review', 'completed', 'ignored', 'failed']
const PENDING = ['pending', 'processing', 'retry']
const statusTone = (status: string): Tone => status === 'completed' ? 'green' : status === 'needs_review' ? 'amber' : status === 'failed' ? 'stamp' : PENDING.includes(status) ? 'navy' : 'neutral'
const STATUS_WORDS: Record<string, string> = { received: 'Received', pending: 'Waiting', processing: 'Being read', retry: 'Will try again', needs_review: 'Needs review', completed: 'Done', ignored: 'Skipped', failed: 'Did not work' }
const statusLabel = (status: string) => STATUS_WORDS[status] ?? status.replaceAll('_', ' ')

export default function WhatsAppPage() {
  const store = useStore()
  const navigate = useNavigate()
  const location = useLocation()
  const linkedConversation = conversationFromSearch(location.search)
  const admin = store.me.role === 'admin'
  const [view, setView] = useState<WaTab>('chat')
  const [bundleSource, setBundleSource] = useState<string | null>(null)
  const [snapshot, setSnapshot] = useState<{ key: string; data: Inbox } | null>(null)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const [busy, setBusy] = useState('')
  const [selected, setSelected] = useState<Message | null>(null)
  const [candidateId, setCandidateId] = useState('')
  const [reason, setReason] = useState('')
  const [dialogError, setDialogError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const busyRef = useRef(false)
  const readController = useRef<AbortController | null>(null)
  const key = `${store.me.id}:${query}:${status}:${page}`
  const data = snapshot?.key === key ? snapshot.data : null
  const configuredChannel = data?.channels.find((channel) => channel.id === data.configuredPhoneNumberId)
  useEffect(() => {
    const controller = new AbortController()
    readController.current = controller
    setError('')
    let pending = false
    const read = async () => {
      if (pending || busyRef.current || document.visibilityState !== 'visible') return
      pending = true
      setRefreshing(true)
      try {
        const params = new URLSearchParams({ q: query, page: String(page), page_size: '20', ...(status ? { status } : {}) })
        const result = await api.get<Inbox>(`/whatsapp?${params}`, { signal: controller.signal })
        if (!controller.signal.aborted) { setSnapshot({ key, data: result }); setError('') }
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'We could not load the messages. Check your internet and try again.') }
      finally { pending = false; if (!controller.signal.aborted) setRefreshing(false) }
    }
    const delay = window.setTimeout(() => void read(), 250)
    const timer = window.setInterval(() => void read(), 15_000)
    const visible = () => void read()
    document.addEventListener('visibilitychange', visible)
    window.addEventListener('focus', visible)
    return () => { controller.abort(); window.clearTimeout(delay); window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); window.removeEventListener('focus', visible) }
  }, [key, query, status, page, revision, store.me.id])
  async function act(id: string, action: () => Promise<unknown>) {
    if (busyRef.current) return
    busyRef.current = true
    readController.current?.abort()
    setBusy(id); setActionError('')
    try { await action() }
    catch (e) { if (store.isCurrentSession()) setActionError(e instanceof Error ? e.message : 'We could not save that. Try again.') }
    finally { busyRef.current = false; if (store.isCurrentSession()) { setBusy(''); setRevision((v) => v + 1) } }
  }
  const candidateName = (id: string) => store.candidates.find((candidate) => candidate.id === id)?.name || 'Open candidate'
  const columns: Column<Message>[] = [
    { key: 'sender', header: 'Sender', width: '19%', render: (row) => <RecordIdentity name={row.senderName || row.senderNumber} detail={row.senderNumber} icon={row.senderName ? undefined : Phone} /> },
    { key: 'message', header: 'Message or document', width: '27%', render: (row) => <div className="wa-doc">
      {row.fileName ? <FileIcon name={row.fileName} /> : <span className="mb-file is-doc" aria-hidden="true"><MessageCircle size={17} /></span>}
      <div className="min-w-0"><p className="wa-doc-name" title={row.fileName || row.type}>{row.fileName || row.type}</p><p className="wa-doc-caption" title={row.body || undefined}>{row.body || 'No caption'}</p>{row.lastError && <p className="wa-doc-error">{row.lastError}</p>}</div>
    </div> },
    { key: 'received', header: 'Received', width: '13%', render: (row) => <span className="wa-when" title={dateTimeLabel(row.receivedAt)}>{smartTime(row.receivedAt)}</span> },
    { key: 'status', header: 'Status', width: '15%', render: (row) => <div className="wa-state"><Badge tone={statusTone(row.status)} variant="dot">{statusLabel(row.status)}</Badge>
      {row.documentBundle?.candidateIds.map((id) => <CandidateChip key={id} label={candidateName(id)} title="Open the candidate" onClick={() => navigate(`/candidates/${id}`)} />)}
      {row.candidateId && !row.documentBundle && <CandidateChip label="Candidate" title="Open the candidate" onClick={() => navigate(`/candidates/${row.candidateId}`)} />}
    </div> },
    { key: 'actions', header: 'Actions', width: '26%', align: 'right', render: (row) => <div className="wa-doc-actions">
      {row.hasFile && <><a className="wa-act" href={fileHref(`/api/whatsapp/messages/${row.id}/file`)} target="_blank" rel="noreferrer" title={`View ${row.fileName ?? 'document'}`}><ExternalLink size={14} />View</a><a className="wa-act is-icon" aria-label={`Download ${row.fileName}`} title="Download" href={fileHref(`/api/whatsapp/messages/${row.id}/file?download=true`)}><Download size={14} /></a></>}
      {admin && row.hasFile && <Button size="sm" disabled={!!busy || PENDING.includes(row.status)} onClick={() => setBundleSource(row.id)}>Review people</Button>}
      {!row.candidateId && !row.documentBundle && admin && !PENDING.includes(row.status) && <>
        <Button size="sm" disabled={!!busy || !row.hasFile} onClick={() => { setSelected(row); setCandidateId(''); setReason(''); setDialogError('') }}>Link</Button>
        {['received', 'needs_review', 'failed'].includes(row.status) && <Button size="sm" disabled={!!busy || !data?.channels.some((channel) => channel.id === row.phoneNumberId && channel.enabled)} onClick={() => void act(row.id, () => api.post(`/whatsapp/messages/${row.id}/retry`))}>{busy === row.id ? 'Starting…' : 'Read now'}</Button>}
      </>}
    </div> },
  ]
  const channels = data?.channels ?? []
  const connectedCount = channels.filter((channel) => channel.enabled).length
  const lastReceived = channels.map((channel) => channel.lastReceivedAt).filter(Boolean).sort().at(-1)
  const totalPages = data ? Math.max(1, Math.ceil(data.total / 20)) : 1
  return <div className="wa-v2">
    <PageHeader title="WhatsApp" eyebrow="Business conversations" description="Chat with candidates and see the documents they send, all in one place." actions={<>{admin && data?.configured && !configuredChannel?.enabled && <Button variant="primary" disabled={!!busy} onClick={() => void act('connect', () => api.post('/whatsapp/channels/connect'))}>{busy === 'connect' ? 'Connecting…' : configuredChannel ? 'Reconnect WhatsApp' : 'Connect WhatsApp'}</Button>}<Button icon={<RefreshCw size={15} />} disabled={refreshing || !!busy} onClick={() => setRevision((v) => v + 1)}>{refreshing ? 'Refreshing…' : 'Refresh'}</Button></>} />
    <SummaryCards items={[
      { label: 'Business numbers', value: data ? String(channels.length) : '–', detail: data ? `${connectedCount} connected` : error ? 'Unavailable' : 'Loading', icon: Phone },
      { label: 'Documents received', value: data ? String(data.total) : '–', detail: query || status ? 'matching the current filters' : 'all time', icon: FileText, tone: 'green' },
      { label: 'Auto-import', value: !data ? '–' : !channels.length ? 'None' : channels.some((channel) => channel.enabled && channel.autoIngest) ? 'On' : 'Off', detail: admin ? 'Change it in Number settings' : 'Set by an admin', icon: Zap, tone: 'amber' },
      { label: 'Last message', value: lastReceived ? smartTime(lastReceived) : data ? 'None yet' : '–', detail: configuredChannel?.workerSeenAt ? `Reader active ${smartTime(configuredChannel.workerSeenAt)}` : 'Reader not active yet', icon: Clock3, tone: 'violet' },
    ]} />
    {(error || actionError) && <div role="alert" className="wa-alert"><AlertTriangle size={18} /><div><p>{actionError || error}</p>{error && data && <p className="wa-alert-sub">Showing the last loaded messages for these filters.</p>}</div><Button size="sm" disabled={refreshing || !!busy} onClick={() => { setActionError(''); setRevision((v) => v + 1) }}>Try again</Button></div>}
    {data && !data.configured && <Panel className="wa-setup"><span className="wa-setup-icon"><MessageCircle size={20} /></span><div><h2>Connect WhatsApp Business</h2><p>WhatsApp is not set up yet. It must be set up on the server before new messages can arrive.</p>{admin && <details><summary>Setup details</summary><p className="break-all">Callback: {absoluteApiHref(data.webhookPath)}</p><p>{!data.enabled && 'Ask your tech team to turn on WHATSAPP_ENABLED on the server. '}{data.missingSettings.length > 0 && `Settings still missing on the server: ${data.missingSettings.join(', ')}.`} Keep passwords and keys in the server secrets.</p></details>}</div></Panel>}
    <Tabs className="wa-tabs" tabs={TABS} active={view} onChange={(next) => setView(next as WaTab)} />
    {view === 'chat' && <WhatsAppChat channels={data?.channels ?? []} revision={revision} initialConversation={linkedConversation} />}
    {view === 'settings' && data && !data.channels.length && <Panel flush><EmptyState icon={<MessageCircle size={22} />} title="No WhatsApp number connected" description={admin ? 'Connect the WhatsApp Business number to receive candidate documents and chat from here.' : 'An administrator can connect the WhatsApp Business number.'} /></Panel>}
    {view === 'settings' && <div className="wa-channels">{data?.channels.map((channel) => <Panel key={channel.id} className="wa-channel">
      <header><span className="wa-channel-icon"><Phone size={18} /></span><div className="min-w-0 flex-1"><h2>{channel.displayNumber || channel.id}</h2><p>WhatsApp Business number</p></div><Badge tone={!channel.enabled ? 'neutral' : channel.autoIngest ? 'green' : 'amber'} variant="dot">{!channel.enabled ? 'Disconnected' : channel.autoIngest ? 'Auto-import on' : 'Check by hand'}</Badge></header>
      <dl className="mb-kv"><div><dt>Last message</dt><dd>{dateTimeLabel(channel.lastReceivedAt)}</dd></div><div><dt>Background reader last active</dt><dd>{dateTimeLabel(channel.workerSeenAt)}</dd></div><div><dt>Connection</dt><dd>{channel.enabled ? 'Connected' : 'Disconnected'}</dd></div><div><dt>Auto-import</dt><dd>{channel.autoIngest ? 'On' : 'Off'}</dd></div></dl>
      {admin && channel.enabled && <div className="wa-channel-actions"><Switch checked={channel.autoIngest} disabled={!!busy} label="Auto-import candidate documents" description={channel.autoIngest ? 'New documents are read and turned into candidates. Switch off to review each one by hand.' : 'New documents wait for manual review. Switch on to read them automatically.'} onChange={(enabled) => void act(channel.id, () => api.patch(`/whatsapp/channels/${channel.id}`, { autoIngest: enabled }))} /><Button variant="danger" disabled={!!busy} onClick={() => void act(channel.id, () => api.post(`/whatsapp/channels/${channel.id}/disconnect`))}>Disconnect</Button></div>}
      <p className="wa-channel-note">{channel.enabled ? 'Disconnecting stops new messages from coming in and pauses documents that are waiting to be read. Existing messages and documents stay.' : 'New messages are not saved and waiting documents are paused. Reconnect to carry on. Existing messages and documents stay.'}</p>
      {channel.lastError && <p className="wa-channel-error">{channel.lastError}</p>}
      <IntakeAcknowledgements sourceKind="whatsapp" channelId={channel.id} channelLabel={channel.displayNumber || channel.id} canManage={admin} />
    </Panel>)}</div>}
    {view === 'intake' && <Panel flush className="wa-intake"><PanelHeader title="Incoming candidate documents" meta={data ? `${data.total} messages found` : 'Messages for the chosen filters'} />
      <div className="wa-toolbar"><SearchInput placeholder="Search sender or message" value={query} onChange={(value) => { setQuery(value); setPage(1) }} /><Select aria-label="Processing status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}><option value="">All statuses</option>{STATUSES.map((value) => <option key={value} value={value}>{statusLabel(value)}</option>)}</Select></div>
      {!data ? <p className="wa-loading" role="status">Loading messages…</p> : <>
        <DataTable compact stackBelow={1280} rows={data.messages} columns={columns} rowKey={(row) => row.id} pageSize={20} empty={<EmptyState icon={<FileText size={22} />} title={query || status ? 'No messages match these filters' : 'No documents received yet'} description={query || status ? 'Clear the search or the status filter to see more.' : 'Documents candidates send to the business number appear here for review.'} />} />
        <footer className="wa-pager"><span>Page {page} of {totalPages}</span><div><Button size="sm" aria-label="Previous page" disabled={page <= 1} onClick={() => setPage((v) => v - 1)}><ChevronLeft size={15} /></Button><Button size="sm" aria-label="Next page" disabled={page * 20 >= data.total} onClick={() => setPage((v) => v + 1)}><ChevronRight size={15} /></Button></div></footer>
      </>}
    </Panel>}
    {bundleSource && <DocumentBundleReview sourceType="whatsapp" sourceId={bundleSource} onClose={() => setBundleSource(null)} onCommitted={() => { setRevision(v => v + 1); void store.refresh({ keepCurrent: true }) }} />}
    <Dialog open={!!selected} onClose={() => { if (!busy) setSelected(null) }} title="Check this message" description="Look at the original document, then link it to the right candidate. The candidate's details will not change." footer={<><Button variant="ghost" disabled={!!busy} onClick={() => setSelected(null)}>Cancel</Button><Button variant="primary" icon={<UserCheck size={15} />} disabled={!!busy || !candidateId || reason.trim().length < 3} onClick={async () => {
      if (!selected || busyRef.current) return
      busyRef.current = true
      readController.current?.abort()
      setBusy(selected.id); setDialogError('')
      try {
        await api.post(`/whatsapp/messages/${selected.id}/resolve`, { candidateId, reason })
        setSelected(null)
        try { await store.refresh({ keepCurrent: true }) } catch { store.notify('Message linked. Refresh the page to see the update.', 'warn') }
      }
      catch (e) { setDialogError(e instanceof Error ? e.message : 'We could not save this. Try again.') }
      finally { busyRef.current = false; setBusy(''); setRevision((v) => v + 1) }
    }}>{busy ? 'Saving…' : 'Link candidate'}</Button></>}>
      {dialogError && <p role="alert" className="mb-3 text-sm text-stamp-700">{dialogError}</p>}
      {selected?.hasFile && <a className="wa-open-original" href={fileHref(`/api/whatsapp/messages/${selected.id}/file`)} target="_blank" rel="noreferrer"><FileIcon name={selected.fileName || 'file'} size="sm" />Open original document<ExternalLink size={13} /></a>}
      <div className={cn('space-y-4')}><Field label="Candidate" required><Select value={candidateId} onChange={(e) => setCandidateId(e.target.value)}><option value="">Choose the matching candidate</option>{store.candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.ref} · {candidate.name}</option>)}</Select></Field><Field label="Why are you linking this?" required><Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={2000} /></Field><p className="text-xs text-ink-500">For a new person, add them in Add Candidate with WhatsApp as the source, then come back here to link this message.</p></div>
    </Dialog>
  </div>
}
