import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Archive, ArrowLeft, ChevronLeft, ChevronRight, FileText, Folder, FolderPlus, Inbox, Mail, MailOpen, Paperclip, Pencil, Plus, RefreshCw, Reply, ReplyAll, Send, Settings2, Star, Trash2, Undo2 } from 'lucide-react'
import { api, fileHref, type InboxSnapshot, type InboxJob } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { initials, smartTime } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { Badge, EmptyState, PageHeader, Panel } from '@/shared/ui/display'
import { Button, Field, Input, SearchInput, Select, Switch } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { useInbox } from './use-inbox'
import { JobReport } from './intake-status'
import { HistoryImport } from './history-import'
import { ZohoConnect } from './zoho-connect'
import { MailCompose } from './mail-compose'
import type { DraftMode, MailDraft, MailFolder, MailboxMessage, MailboxResult } from './mailbox-types'
import './mailbox.css'

const PAGE_SIZE = 25
const roleIcon = { inbox: Inbox, sent: Send, drafts: FileText, trash: Trash2, archive: Archive, spam: Mail, custom: Folder }
const errorText = (cause: unknown) => cause instanceof Error ? cause.message : 'The mailbox could not be loaded.'

export default function ZohoMailbox() {
  const store = useStore()
  const navigate = useNavigate()
  const admin = store.can('connect_sources')
  const [accountId, setAccountId] = useState('')
  const [folder, setFolder] = useState('INBOX')
  const [view, setView] = useState<'mail' | 'drafts'>('mail')
  const [historyMode, setHistoryMode] = useState(false)
  const [unread, setUnread] = useState('')
  const [attachments, setAttachments] = useState('')
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [openId, setOpenId] = useState<string | null>(() => new URLSearchParams(window.location.search).get('message'))
  const [focusMessage, setFocusMessage] = useState(() => new URLSearchParams(window.location.search).get('message'))
  const [revision, setRevision] = useState(0)
  const [folders, setFolders] = useState<MailFolder[]>([])
  const [mailbox, setMailbox] = useState<MailboxResult | null>(null)
  const [mailError, setMailError] = useState('')
  const [folderError, setFolderError] = useState('')
  const [loading, setLoading] = useState(false)
  const [drafts, setDrafts] = useState<MailDraft[]>([])
  const [draftOffset, setDraftOffset] = useState(0)
  const [draftTotal, setDraftTotal] = useState(0)
  const [draftHasMore, setDraftHasMore] = useState(false)
  const [draftLoading, setDraftLoading] = useState(false)
  const [draftError, setDraftError] = useState('')
  const [newFolder, setNewFolder] = useState(false)
  const [folderName, setFolderName] = useState('')
  const [compose, setCompose] = useState<MailDraft | null>(null)
  const [connect, setConnect] = useState<string | null>(null)
  const [managing, setManaging] = useState(false)
  const [openingCandidate, setOpeningCandidate] = useState(false)
  const [selectedJobs, setSelectedJobs] = useState<InboxJob[]>([])
  const [jobLoading, setJobLoading] = useState(false)
  const [jobError, setJobError] = useState('')
  useEffect(() => { const timer = window.setTimeout(() => { setSearch(query); setOffset(0); setDraftOffset(0) }, 300); return () => window.clearTimeout(timer) }, [query])
  const summaryParams = new URLSearchParams({ scope: 'stored', limit: String(PAGE_SIZE), offset: String(offset), q: search })
  if (accountId) summaryParams.set('account_ids', accountId)
  if (unread) summaryParams.set('unread', unread)
  if (attachments) summaryParams.set('has_attachments', attachments)
  if (focusMessage) summaryParams.set('message_id', focusMessage)
  const inbox = useInbox(store.me.id, store.isCurrentSession, summaryParams.toString())
  const accounts = inbox.data?.accounts ?? []
  const account = accounts.find((item) => item.id === accountId)
  const liveMailbox = account?.provider === 'zoho' && account.connectionStatus !== 'disconnected'
  const remoteView = liveMailbox && !historyMode && !focusMessage
  const composeAccount = account?.provider === 'zoho' ? account : accounts.find((item) => item.provider === 'zoho' && item.connectionStatus !== 'disconnected')
  const busy = inbox.busy !== null
  const currentFolder = folders.find((item) => item.name === folder)
  const currentRole = currentFolder?.role ?? 'inbox'
  const summaryMessages = inbox.data?.messages ?? []
  const messages: MailboxMessage[] = remoteView ? mailbox?.messages ?? [] : summaryMessages
  const total = remoteView ? mailbox?.total ?? 0 : inbox.data?.total ?? messages.length
  const hasMore = remoteView ? !!mailbox?.hasMore : !!inbox.data?.hasMore
  const open = messages.find((item) => item.id === openId)
  const openAccount = accounts.find((item) => item.id === open?.accountId)
  const moveUnconfirmed = open?.remoteState === 'moving' || open?.remoteState === 'unknown'
  const canOperate = openAccount?.provider === 'zoho' && openAccount.connectionStatus !== 'disconnected' && !moveUnconfirmed
  const openJobs = selectedJobs.filter((item) => item.messageId === open?.id)
  const bump = () => setRevision((value) => value + 1)
  const perform = async (key: string, work: () => Promise<unknown>, notice: string) => {
    const done = await inbox.act(key, work, notice)
    if (done) bump()
    return done
  }
  useEffect(() => {
    setSelectedJobs([]); setJobError('')
    if (!openId) { setJobLoading(false); return }
    const controller = new AbortController()
    let pending = false
    const read = async () => {
      if (pending || document.visibilityState !== 'visible') return
      pending = true; setJobLoading(true)
      try {
        const result = await api.get<InboxSnapshot>(`/inbox?${new URLSearchParams({ message_id: openId, limit: '1' })}`, { signal: controller.signal })
        if (!controller.signal.aborted && store.isCurrentSession()) { setSelectedJobs(result.jobs); setJobError('') }
      } catch (cause) { if (!controller.signal.aborted) setJobError(errorText(cause)) }
      finally { pending = false; if (!controller.signal.aborted) setJobLoading(false) }
    }
    void read()
    const timer = window.setInterval(() => void read(), 5000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [openId, revision, store.me.id])
  useEffect(() => {
    if (!liveMailbox || !accountId) { setFolders([]); setFolderError(''); return }
    const controller = new AbortController()
    void api.get<{ folders: MailFolder[] }>(`/inbox/accounts/${accountId}/folders`, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted && store.isCurrentSession()) { setFolders(result.folders); setFolderError('') }
    }).catch((cause) => { if (!controller.signal.aborted) setFolderError(errorText(cause)) })
    return () => controller.abort()
  }, [accountId, liveMailbox, revision, store.me.id])
  useEffect(() => {
    setMailbox(null); setMailError('')
    if (!remoteView || !accountId || view !== 'mail') { setLoading(false); return }
    const controller = new AbortController()
    let pending = false
    const read = async () => {
      if (pending || document.visibilityState !== 'visible') return
      pending = true; setLoading(true)
      try {
        const params = new URLSearchParams({ folder, search, offset: String(offset), limit: String(PAGE_SIZE) })
        if (unread) params.set('unread', unread)
        const result = await api.get<MailboxResult>(`/inbox/accounts/${accountId}/mailbox?${params}`, { signal: controller.signal })
        if (!controller.signal.aborted && store.isCurrentSession()) { setMailbox(result); setMailError('') }
      } catch (cause) { if (!controller.signal.aborted) setMailError(errorText(cause)) }
      finally { pending = false; if (!controller.signal.aborted) setLoading(false) }
    }
    void read()
    const timer = window.setInterval(() => void read(), 60000)
    const visible = () => { if (document.visibilityState === 'visible') void read() }
    document.addEventListener('visibilitychange', visible)
    return () => { controller.abort(); window.clearInterval(timer); document.removeEventListener('visibilitychange', visible) }
  }, [accountId, remoteView, view, folder, search, unread, offset, revision, store.me.id])
  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams({ offset: String(draftOffset), limit: String(PAGE_SIZE), search })
    if (accountId) params.set('account_id', accountId)
    setDraftLoading(true)
    void api.get<{ drafts: MailDraft[]; total: number; hasMore: boolean }>(`/inbox/drafts?${params}`, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted && store.isCurrentSession()) { setDrafts(result.drafts); setDraftTotal(result.total); setDraftHasMore(result.hasMore); setDraftError('') }
    }).catch((cause) => { if (!controller.signal.aborted) setDraftError(errorText(cause)) })
      .finally(() => { if (!controller.signal.aborted) setDraftLoading(false) })
    return () => controller.abort()
  }, [accountId, draftOffset, search, revision, store.me.id])
  const selectAccount = (id: string) => { setAccountId(id); setFolder('INBOX'); setView('mail'); setHistoryMode(false); setAttachments(''); setUnread(''); setOpenId(null); setOffset(0); setDraftOffset(0); setDrafts([]); setFocusMessage(null); setQuery(''); setFolders([]) }
  const selectFolder = (name: string) => { setFolder(name); setView('mail'); setHistoryMode(false); setAttachments(''); setOpenId(null); setOffset(0); setFocusMessage(null) }
  const createDraft = (mode: DraftMode, message?: MailboxMessage) => {
    const sourceAccount = message ? accounts.find((item) => item.id === message.accountId) : composeAccount
    if (!sourceAccount || sourceAccount.provider !== 'zoho') return
    void perform('compose', async () => {
      const draft = await api.post<MailDraft>('/inbox/drafts', { account_id: sourceAccount.id, mode, ...(message ? { source_message_id: message.id } : {}) })
      setCompose(draft)
    }, '')
  }
  const openDraft = (draft: MailDraft) => void perform('draft', async () => setCompose(await api.get<MailDraft>(`/inbox/drafts/${draft.id}`)), '')
  const flags = (message: MailboxMessage, values: { unread?: boolean; starred?: boolean }) => void perform(`flags:${message.id}`, async () => {
    const updated = await api.patch<MailboxMessage>(`/inbox/messages/${message.id}/flags`, values)
    setMailbox((current) => current ? { ...current, messages: current.messages.map((item) => item.id === updated.id ? updated : item) } : current)
    if (updated.remoteState === 'unknown') store.notify('The mailbox change is not confirmed. Open the message and check its mailbox operation.', 'warn')
    else store.notify(values.unread === undefined ? (values.starred ? 'Message starred.' : 'Star removed.') : values.unread ? 'Marked unread in Zoho.' : 'Marked read in Zoho.')
  }, '')
  const move = (message: MailboxMessage, action: 'archive' | 'trash' | 'restore' | string, custom = false) => void perform(`move:${message.id}`, async () => {
    const updated = await api.post<MailboxMessage>(`/inbox/messages/${message.id}/${custom ? 'move' : 'action'}`, custom ? { folder: action } : { action })
    if (updated.remoteState === 'moving' || updated.remoteState === 'unknown') {
      setMailbox((current) => current ? { ...current, messages: current.messages.map((item) => item.id === message.id ? updated : item) } : current)
      store.notify('The mailbox move is not confirmed. Use Check mailbox operation before trying another move.', 'warn')
    } else {
      setOpenId(null)
      store.notify(custom ? 'Message moved.' : action === 'trash' ? 'Moved to Trash. You can restore it from that folder.' : action === 'restore' ? 'Message restored.' : 'Message archived.')
    }
  }, '')
  const openCandidate = async (id: string) => { setOpeningCandidate(true); try { await store.refresh({ keepCurrent: true }); if (store.isCurrentSession()) navigate(`/candidates/${id}`) } catch (cause) { store.notify(errorText(cause), 'warn') } finally { setOpeningCandidate(false) } }
  const folderChoices = useMemo(() => folders.length ? folders : [{ name: 'INBOX', role: 'inbox' as const, total, unread: 0 }], [folders, total])
  const refresh = () => { bump(); void inbox.refresh().catch(() => {}) }

  return <div className="zoho-workspace">
    <PageHeader title="Inbox" eyebrow="Zoho Mail workspace" description="Read, write and organize mail, with candidate documents connected to every conversation."
      actions={<><Button icon={<RefreshCw size={15} />} disabled={loading || busy} onClick={refresh}>Refresh</Button>{admin && <Button icon={<Settings2 size={15} />} disabled={busy} onClick={() => setManaging(true)}>Accounts</Button>}<Button icon={<FolderPlus size={15} />} disabled={busy || !liveMailbox} onClick={() => { setFolderName(''); setNewFolder(true) }}>New folder</Button><Button variant="primary" icon={<Pencil size={15} />} disabled={busy || !composeAccount || composeAccount.connectionStatus === 'disconnected'} onClick={() => createDraft('compose')}>Compose</Button></>} />
    {(inbox.error || inbox.actionError || mailError) && <div role="alert" className="mb-4 rounded-control border border-stamp-100 bg-stamp-50 p-3 text-sm text-stamp-700">{inbox.actionError || mailError || inbox.error}{mailError && mailbox && <p className="mt-1 text-xs">Showing the last loaded folder. Refresh to try the provider again.</p>}<Button size="sm" className="mt-2" onClick={refresh}>Try again</Button></div>}
    {inbox.notice && <p role="status" className="mb-4 rounded-control bg-navy-50 p-3 text-sm text-navy-800">{inbox.notice}</p>}
    {!inbox.data && !inbox.error && <p role="status" className="py-8 text-sm text-ink-500">Loading mailbox connections…</p>}
    {focusMessage && <p className="mb-4 text-sm text-ink-500">Showing a candidate source message. <button className="font-medium text-navy-700 underline" onClick={() => { setFocusMessage(null); setOpenId(null); window.history.replaceState(null, '', '/inbox') }}>Return to mailbox</button></p>}
    {inbox.data && accounts.length === 0 ? <Panel><EmptyState title="Your mailbox, connected" description={admin ? 'Connect your Zoho account to read mail, manage folders and send messages from this workspace.' : 'An administrator can connect the recruitment Zoho mailbox.'} action={admin ? <Button variant="primary" icon={<Plus size={15} />} onClick={() => setConnect('')}>Connect Zoho Mail</Button> : undefined} /></Panel> : inbox.data && <>
      <div className="zoho-controls"><Field label="Mailbox" className="min-w-0"><Select aria-label="Mailbox" value={accountId} onChange={(event) => selectAccount(event.target.value)}><option value="">All accounts · message history</option>{accounts.map((item) => <option key={item.id} value={item.id}>{item.address}{item.provider !== 'zoho' ? ' · archived connection' : item.connectionStatus === 'disconnected' ? ' · disconnected' : ''}</option>)}</Select></Field>
        <div className="zoho-folder-mobile"><Field label="Folder"><Select value={view === 'drafts' ? '__saved_drafts' : folder} onChange={(event) => event.target.value === '__saved_drafts' ? setView('drafts') : selectFolder(event.target.value)}>{folderChoices.map((item) => <option key={item.name} value={item.name}>{item.role === 'drafts' ? 'Zoho drafts' : item.name}</option>)}<option value="__saved_drafts">My saved drafts and send history</option></Select></Field></div>
        <div className="zoho-search"><Field label="Search"><SearchInput value={query} onChange={setQuery} placeholder={view === 'drafts' ? 'Search saved drafts' : 'Search this mailbox'} /></Field></div>
      </div>
      {view === 'mail' && !focusMessage && <><div className="mb-3 flex flex-wrap items-end gap-3"><Field label="Message source" className="min-w-40 flex-1"><Select aria-label="Message source" value={remoteView ? 'live' : 'stored'} onChange={(event) => { setHistoryMode(event.target.value === 'stored'); setAttachments(''); setOpenId(null); setOffset(0) }}>{liveMailbox && <option value="live">Live Zoho folder</option>}<option value="stored">Stored message history</option></Select></Field><Field label="Read status" className="min-w-36 flex-1"><Select aria-label="Read status" value={unread} onChange={(event) => { setUnread(event.target.value); setOpenId(null); setOffset(0) }}><option value="">All messages</option><option value="true">Unread</option><option value="false">Read</option></Select></Field><Field label="Attachments" className="min-w-40 flex-1"><Select aria-label="Attachment filter" value={attachments} disabled={!!remoteView} onChange={(event) => { setAttachments(event.target.value); setOpenId(null); setOffset(0) }}><option value="">Any attachment state</option><option value="true">Has attachments</option><option value="false">No attachments</option></Select></Field></div><p className="mb-3 text-xs text-ink-400">{remoteView ? 'Read status filters the live Zoho folder. To filter attachments, choose Stored message history.' : 'Stored history includes messages received, imported or viewed in CRM. Attachment filters apply to that stored history, not unopened remote mail.'}</p></>}
      {account && <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-500"><Badge tone={liveMailbox ? 'navy' : 'neutral'}>{account.provider !== 'zoho' ? 'Archived connection · read only' : account.connectionStatus.replaceAll('_', ' ')}</Badge><span>{account.lastSyncAt ? `Last incoming sync ${smartTime(account.lastSyncAt)}` : 'Incoming sync has not completed yet'}</span><span>{account.autoIngest ? 'Candidate intake on' : 'Candidate intake off'}</span>{account.lastError && <span className="text-amber-900">{account.lastError}</span>}</div>}
      <div className={cn('zoho-mail-layout', open && view === 'mail' && 'is-reading')}>
        <aside className="zoho-folders" aria-label="Mail folders"><p className="px-3 pb-3 text-xs font-semibold uppercase tracking-wide text-ink-400">Folders</p>{folderChoices.map((item) => { const Icon = roleIcon[item.role] ?? Folder; return <button key={item.name} className={cn('zoho-folder', view === 'mail' && folder === item.name && 'is-active')} onClick={() => selectFolder(item.name)}><Icon size={16} /><span className="min-w-0 flex-1 truncate text-left">{item.role === 'drafts' ? 'Zoho drafts' : item.name}</span>{item.unread > 0 && <span className="zoho-unread-count">{item.unread}</span>}</button> })}
          {folderError && <p role="alert" className="px-3 py-2 text-xs text-amber-900">{folderError}</p>}
          <button className={cn('zoho-folder mt-3 border-t border-rule pt-4', view === 'drafts' && 'is-active')} onClick={() => setView('drafts')}><FileText size={16} /><span className="flex-1 text-left">My saved drafts</span></button>
          <p className="px-3 pt-4 text-xs leading-relaxed text-ink-400">Your CRM draft list and send recovery are private. Synced Zoho drafts and other mailbox folders are shared with your team.</p>{admin && <Button className="mt-5 w-full" size="sm" icon={<Plus size={14} />} onClick={() => setConnect('')}>Connect Zoho</Button>}
        </aside>
        {view === 'drafts' ? <section className="zoho-draft-list" aria-label="My drafts"><div className="zoho-section-header"><div><h2 className="text-sm font-semibold">My saved drafts and send history</h2><p className="mt-1 text-xs text-ink-500">Continue composing or check an earlier delivery.</p></div><Button size="sm" onClick={refresh}>Refresh</Button></div>{draftError && <p role="alert" className="p-4 text-sm text-stamp-700">{draftError}</p>}
          {draftLoading && <p role="status" className="px-4 py-2 text-xs text-ink-500">Loading saved drafts…</p>}
          {drafts.map((item) => <button key={item.id} className="zoho-draft-row" disabled={busy || draftLoading} onClick={() => openDraft(item)}><FileText size={18} className="shrink-0 text-ink-400" /><span className="min-w-0 flex-1 text-left"><strong className="block truncate text-sm">{item.subject || '(No subject)'}</strong><span className="mt-1 block truncate text-xs text-ink-500">To {item.to.join(', ') || 'No direct recipients'} · {smartTime(item.updatedAt)}</span><span className="mt-1 block truncate text-xs text-ink-400">{item.body || 'Empty draft'}</span></span><Badge tone={item.status === 'unknown' || item.status === 'failed' ? 'amber' : 'neutral'}>{item.status === 'unknown' ? 'Delivery unconfirmed' : item.status}</Badge></button>)}
          {!drafts.length && !draftError && !draftLoading && <EmptyState title={search ? 'No matching drafts' : 'No saved drafts'} description={search ? 'Try another subject, message or recipient.' : 'Compose a message to save your text and attachments here. Drafts synchronized to Zoho are also visible to other mailbox users.'} />}
          <div className="zoho-pagination"><Button size="sm" aria-label="Previous saved drafts" disabled={draftOffset === 0 || draftLoading} onClick={() => setDraftOffset(Math.max(0, draftOffset - PAGE_SIZE))}><ChevronLeft size={15} /></Button><span className="text-xs text-ink-500">Page {Math.floor(draftOffset / PAGE_SIZE) + 1} · {draftTotal} results</span><Button size="sm" aria-label="Next saved drafts" disabled={!draftHasMore || draftLoading} onClick={() => setDraftOffset(draftOffset + PAGE_SIZE)}><ChevronRight size={15} /></Button></div>
        </section> : <>
          <section className="zoho-message-list" aria-label="Messages"><div className="zoho-section-header"><h2 className="text-sm font-semibold">{remoteView ? currentRole === 'drafts' ? 'Zoho drafts' : folder : 'Stored message history'}</h2><span className="text-xs text-ink-500">{total} messages</span></div>
            {loading && <p role="status" className="px-4 py-2 text-xs text-ink-500">Refreshing from Zoho…</p>}
            <ul className="zoho-message-scroll">{messages.map((message) => <li key={message.id} className={cn('zoho-message-row', open?.id === message.id && 'is-selected')}><button className="zoho-open-message" aria-pressed={open?.id === message.id} onClick={() => setOpenId(message.id)}><span className="mail-sender-avatar shrink-0" aria-hidden>{initials(message.fromName || message.fromAddress)}</span><span className="min-w-0 flex-1"><span className="flex items-center gap-2"><span className={cn('min-w-0 flex-1 truncate text-sm', message.unread ? 'font-semibold text-ink' : 'text-ink-600')}>{message.fromName || message.fromAddress}</span>{message.unread && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-navy-600" aria-label="Unread" />}</span><span className={cn('mt-1 block truncate text-xs', message.unread ? 'font-medium text-ink-700' : 'text-ink-500')}>{message.subject || '(No subject)'}</span><span className="mt-1 block truncate text-xs text-ink-400">{message.preview}</span><span className="mt-2 flex flex-wrap items-center gap-2 text-micro text-ink-400"><span>{smartTime(message.receivedAt)}</span>{message.attachments.length > 0 && <span className="inline-flex items-center gap-1"><Paperclip size={11} />{message.attachments.length}</span>}{message.candidateId && <span className="text-navy-700">Candidate linked</span>}</span></span></button>
              {accounts.find((item) => item.id === message.accountId)?.provider === 'zoho' && <button className="zoho-star" aria-label={`${message.starred ? 'Unstar' : 'Star'} ${message.subject || 'message'}`} disabled={busy || ['moving', 'unknown'].includes(message.remoteState ?? '') || accounts.find((item) => item.id === message.accountId)?.connectionStatus === 'disconnected'} onClick={() => flags(message, { starred: !message.starred })}><Star size={14} className={message.starred ? 'fill-amber-400 text-amber-500' : 'text-ink-300'} /></button>}</li>)}</ul>
            {!messages.length && !loading && !mailError && <EmptyState title="No messages in this view" description={search ? 'Try another search.' : 'Messages will appear here when this folder is refreshed.'} />}
            <div className="zoho-pagination"><Button size="sm" aria-label="Previous messages" disabled={offset === 0 || loading} onClick={() => { setOpenId(null); setOffset(Math.max(0, offset - PAGE_SIZE)) }}><ChevronLeft size={15} /></Button><span className="text-xs text-ink-500">Page {Math.floor(offset / PAGE_SIZE) + 1}</span><Button size="sm" aria-label="Next messages" disabled={!hasMore || loading} onClick={() => { setOpenId(null); setOffset(offset + PAGE_SIZE) }}><ChevronRight size={15} /></Button></div>
          </section>
          <section className="zoho-reader" aria-label="Message reader">{open ? <><div className="zoho-reader-toolbar"><Button size="sm" className="zoho-back" icon={<ArrowLeft size={14} />} onClick={() => setOpenId(null)}>Messages</Button><Button size="sm" disabled={busy || !canOperate} icon={open.unread ? <MailOpen size={14} /> : <Mail size={14} />} onClick={() => flags(open, { unread: !open.unread })}>{open.unread ? 'Mark read' : 'Mark unread'}</Button><Button size="sm" disabled={busy || !canOperate} icon={<Archive size={14} />} onClick={() => move(open, 'archive')}>Archive</Button>{currentRole === 'trash' || currentRole === 'archive' ? <Button size="sm" disabled={busy || !canOperate} icon={<Undo2 size={14} />} onClick={() => move(open, 'restore')}>Restore</Button> : <Button size="sm" disabled={busy || !canOperate} icon={<Trash2 size={14} />} onClick={() => move(open, 'trash')}>Trash</Button>}
            {liveMailbox && <Select aria-label="Move message to folder" className="max-w-44" value="" disabled={busy || !canOperate} onChange={(event) => { if (event.target.value) move(open, event.target.value, true) }}><option value="">Move to folder…</option>{folders.filter((item) => item.name !== folder).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</Select>}</div>
            <div className="zoho-reader-scroll"><div className="zoho-reader-heading"><h2 className="break-words text-lg font-semibold text-ink">{open.subject || '(No subject)'}</h2><div className="mt-4 flex items-start gap-3"><span className="mail-sender-avatar shrink-0" aria-hidden>{initials(open.fromName || open.fromAddress)}</span><div className="min-w-0 flex-1"><p className="break-words text-sm font-medium text-ink">{open.fromName || open.fromAddress}</p><p className="mt-1 break-all text-xs text-ink-500">{open.fromAddress}</p><p className="mt-1 break-words text-xs text-ink-400">To {(open.toAddresses?.length ? open.toAddresses : [openAccount?.address || '']).join(', ')}{open.ccAddresses?.length ? ` · Cc ${open.ccAddresses.join(', ')}` : ''}</p><p className="mt-1 text-xs text-ink-400">{smartTime(open.receivedAt)}</p></div></div></div>
              {moveUnconfirmed && <div className="mx-5 mb-4 rounded-control border border-amber-100 bg-amber-50 p-3"><p className="text-sm text-amber-900">The last mailbox operation is not confirmed. Check its status before moving this message again.</p><Button size="sm" className="mt-2" disabled={busy} onClick={() => void perform(`reconcile:${open.id}`, async () => { const updated = await api.post<MailboxMessage>(`/inbox/messages/${open.id}/reconcile`); setMailbox((current) => current ? { ...current, messages: current.messages.map((item) => item.id === updated.id ? updated : item) } : current) }, 'Mailbox operation checked.')}>Check mailbox operation</Button></div>}
              {open.remoteError && <p role="alert" className="mx-5 rounded-control bg-amber-50 p-3 text-sm text-amber-900">{open.remoteError}</p>}
              <div className="zoho-message-body">{open.body || open.preview || '(No message text)'}</div>
              {open.attachments.length > 0 && <div className="px-5 pb-5"><p className="mb-3 text-xs font-semibold text-ink-500">Attachments · {open.attachments.length}</p><ul className="space-y-2">{open.attachments.map((file) => <li key={file.id} className="flex min-w-0 flex-wrap items-center gap-2 rounded-control border border-rule px-3 py-2.5"><Paperclip size={15} className="shrink-0 text-ink-400" /><span className="min-w-0 flex-1 break-all text-sm">{file.fileName}</span><span className="text-xs text-ink-400">{file.sizeKb} KB</span>{file.url && <><a className="text-xs font-medium text-navy-700 underline" href={fileHref(file.url)} target="_blank" rel="noreferrer">View</a><a className="text-xs font-medium text-navy-700 underline" href={fileHref(`${file.url}${file.url.includes('?') ? '&' : '?'}download=true`)}>Download</a></>}</li>)}</ul></div>}
              <div className="flex flex-wrap gap-2 border-y border-rule bg-field/50 px-5 py-4">{currentRole === 'drafts' ? <Button disabled={busy || !canOperate} icon={<Pencil size={14} />} onClick={() => createDraft('edit_draft', open)}>Edit draft</Button> : <><Button disabled={busy || !canOperate} icon={<Reply size={14} />} onClick={() => createDraft('reply', open)}>Reply</Button><Button disabled={busy || !canOperate} icon={<ReplyAll size={14} />} onClick={() => createDraft('reply_all', open)}>Reply all</Button><Button disabled={busy || !canOperate} icon={<Send size={14} />} onClick={() => createDraft('forward', open)}>Forward</Button></>}</div>
              <section className="space-y-3 px-5 py-5" aria-label="Candidate intake"><div className="flex flex-wrap items-center gap-2"><h3 className="mr-auto text-xs font-semibold uppercase tracking-wide text-ink-400">Candidate documents</h3>{open.candidateId && <Button size="sm" disabled={openingCandidate} onClick={() => void openCandidate(open.candidateId!)}>Open candidate</Button>}{open.attachments.length > 0 && !open.candidateId && !openJobs.length && <Button size="sm" disabled={busy || jobLoading || !!jobError} onClick={() => void perform(`ingest:${open.id}`, () => api.inbox.ingest(open.id), 'Document processing queued.')}>Create candidate from attachments</Button>}</div>
                {jobError && <p role="alert" className="text-xs text-amber-900">{jobError}</p>}
                {openJobs.map((job) => <JobReport key={job.id} job={job} busy={busy} canRetry={admin} opening={openingCandidate} onOpen={openCandidate} onRetry={() => perform(`retry:${job.id}`, () => api.inbox.retry(job.id), 'Document retry queued.')} onReview={(body) => perform(`review:${job.id}`, () => api.post(`/inbox/jobs/${job.id}/review`, body), 'Review saved. Source evidence remains in history.')} />)}
                {!openJobs.length && !open.candidateId && <p className="text-xs text-ink-500">{open.attachments.length ? 'This conversation has not been processed into a candidate.' : 'This message has no candidate attachments.'}</p>}
              </section>
            </div></> : <EmptyState title="Choose a conversation" description="Open a message to read, reply or review its candidate documents." icon={<Mail size={28} />} />}</section>
        </>}
      </div>
    </>}
    {connect !== null && <ZohoConnect address={connect} close={() => setConnect(null)} connected={(next) => { setConnect(null); setManaging(false); selectAccount(next.id); store.notify('Zoho mailbox connected.'); refresh() }} />}
    {compose && <MailCompose key={compose.id} initial={compose} address={accounts.find((item) => item.id === compose.accountId)?.address ?? 'Zoho mailbox'} close={() => { setCompose(null); bump() }} changed={() => {}} />}
    <Dialog open={newFolder} onClose={() => { if (!busy) setNewFolder(false) }} title="New Zoho folder" description={`Create a shared folder in ${account?.address ?? 'this mailbox'}.`} width="sm" footer={<><Button disabled={busy} onClick={() => setNewFolder(false)}>Cancel</Button><Button variant="primary" disabled={busy || !folderName.trim() || !liveMailbox} onClick={() => void perform('new-folder', async () => { const result = await api.post<{ folders: MailFolder[] }>(`/inbox/accounts/${accountId}/folders`, { name: folderName.trim() }); setFolders(result.folders); selectFolder(folderName.trim()); setNewFolder(false) }, 'Folder is ready.')}>{busy ? 'Creating…' : 'Create folder'}</Button></>}>
      <Field label="Folder name" required><Input value={folderName} maxLength={255} onChange={(event) => setFolderName(event.target.value)} autoFocus /></Field>{inbox.actionError && <p role="alert" className="mt-3 text-sm text-stamp-700">{inbox.actionError}</p>}
    </Dialog>
    <Dialog open={managing && admin} onClose={() => { if (!busy) setManaging(false) }} title="Mailbox accounts" description="Connect Zoho accounts and manage incoming candidate documents." width="lg" footer={<><Button disabled={busy} onClick={() => setConnect('')}>Connect Zoho Mail</Button><Button variant="primary" disabled={busy} onClick={() => setManaging(false)}>Done</Button></>}>
      <div className="space-y-5">{accounts.map((item) => <section key={item.id} className="space-y-3 rounded-control border border-rule p-4"><div className="flex flex-wrap items-start gap-2"><div className="min-w-0 flex-1"><h3 className="break-all text-sm font-semibold">{item.address}</h3><p className="mt-1 text-xs text-ink-500">{item.provider === 'zoho' ? 'Zoho Mail' : 'Archived connection'} · {item.connectionStatus.replaceAll('_', ' ')}</p></div><Badge tone={item.configured && item.connectionStatus !== 'disconnected' ? 'navy' : 'neutral'}>{item.configured ? 'Configured' : 'Setup required'}</Badge></div>
        {item.lastError && <p role="status" className="break-words text-xs text-amber-900">{item.lastError}</p>}
        {item.provider === 'zoho' ? <><Switch checked={item.autoIngest} disabled={busy || item.connectionStatus === 'disconnected'} label={`Auto-ingest for ${item.address}`} description="Create candidates from newly received attachments. Reading and sending mail remain available when this is off." onChange={(enabled) => void perform(`auto:${item.id}`, () => api.inbox.setAutoIngest(item.id, enabled), `Automatic candidate intake ${enabled ? 'enabled' : 'paused'}.`)} />
          <HistoryImport accountId={item.id} disabled={busy || item.connectionStatus === 'disconnected'} /><div className="flex flex-wrap gap-2">{item.connectionStatus !== 'disconnected' ? <><Button size="sm" disabled={busy} onClick={() => void perform(`sync:${item.id}`, () => api.post(`/inbox/accounts/${item.id}/sync`), 'Incoming sync requested.')}>Sync incoming mail</Button><Button size="sm" variant="danger" disabled={busy} onClick={() => void perform(`disconnect:${item.id}`, () => api.post(`/inbox/accounts/${item.id}/disconnect`), 'Mailbox disconnected. Messages, drafts and candidate documents are retained.')}>Disconnect</Button></> : <Button size="sm" disabled={busy} onClick={() => setConnect(item.address)}>Reconnect Zoho</Button>}</div></> : <p className="text-xs leading-relaxed text-ink-500">Historical messages and candidate evidence remain readable. New mailbox connections and live actions use Zoho only.</p>}
      </section>)}{!accounts.length && <p className="text-sm text-ink-500">No accounts connected yet.</p>}{inbox.actionError && <p role="alert" className="text-sm text-stamp-700">{inbox.actionError}</p>}</div>
    </Dialog>
  </div>
}
