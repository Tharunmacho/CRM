import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Archive, ArrowLeft, ChevronLeft, ChevronRight, Eye, FileText, FolderPlus, Mail, MailOpen, MoreVertical, Paperclip, Pencil, Plus, RefreshCw, Reply, ReplyAll, Send, Settings2, SlidersHorizontal, Star, Trash2, Undo2, UserCheck } from 'lucide-react'
import { api, fileHref, type InboxSnapshot, type InboxJob, type MailProvider } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { initials, smartTime } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { Badge, EmptyState, PageHeader, Panel } from '@/shared/ui/display'
import { Button, Field, Input, SearchInput, Select, Switch } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { useInbox } from '@/features/inbox/use-inbox'
import { IntakeAcknowledgements } from '@/features/intake/intake-acknowledgements'
import { DocumentBundleReview } from '@/features/candidates/components/document-bundle-review'
import { JobReport } from '@/features/inbox/intake-status'
import { PROVIDER_LABEL, intakeUnavailable, isLiveAccount } from '@/features/inbox/inbox-status'
import { HistoryImport } from '@/features/inbox/history-import'
import { ConnectMailbox } from '@/features/inbox/connect-mailbox'
import { AttachmentPreview, type PreviewFile } from '@/features/inbox/attachment-preview'
import { MailCompose } from '@/features/inbox/mail-compose'
import type { DraftMode, MailDraft, MailFolder, MailboxMessage, MailboxResult } from '@/features/inbox/mailbox-types'
import '@/features/inbox/mailbox.css'

const PAGE_SIZE = 25
type InboxTab = 'all' | 'unread' | 'starred' | 'archived'
const TABS: [InboxTab, string][] = [['all', 'All'], ['unread', 'Unread'], ['starred', 'Starred'], ['archived', 'Archived']]
const errorText = (cause: unknown) => cause instanceof Error ? cause.message : 'The mailbox could not be loaded.'

export default function InboxScreen() {
  const store = useStore()
  const navigate = useNavigate()
  const admin = store.can('connect_sources')
  const [bundleSource, setBundleSource] = useState<string | null>(null)
  const [accountId, setAccountId] = useState('')
  const [folder, setFolder] = useState('INBOX')
  const [view, setView] = useState<'mail' | 'drafts'>('mail')
  const [historyMode, setHistoryMode] = useState(false)
  const [unread, setUnread] = useState('')
  const [tab, setTab] = useState<InboxTab>('all')
  const [filtersOpen, setFiltersOpen] = useState(false)
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
  const [connect, setConnect] = useState<{ address: string; provider?: MailProvider } | null>(null)
  const [preview, setPreview] = useState<PreviewFile | null>(null)
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
  const liveMailbox = isLiveAccount(account)
  const remoteView = liveMailbox && !historyMode && !focusMessage
  const composeAccount = isLiveAccount(account) ? account : accounts.find(isLiveAccount)
  const accountLabel = (id: string) => accounts.find((item) => item.id === id)?.address ?? ''
  const providerName = account ? PROVIDER_LABEL[account.provider] : 'mailbox'
  const busy = inbox.busy !== null
  const currentFolder = folders.find((item) => item.name === folder)
  const currentRole = currentFolder?.role ?? 'inbox'
  const summaryMessages = inbox.data?.messages ?? []
  const messages: MailboxMessage[] = remoteView ? mailbox?.messages ?? [] : summaryMessages
  const total = remoteView ? mailbox?.total ?? 0 : inbox.data?.total ?? messages.length
  const hasMore = remoteView ? !!mailbox?.hasMore : !!inbox.data?.hasMore
  const open = messages.find((item) => item.id === openId)
  const openAccount = accounts.find((item) => item.id === open?.accountId)
  const intakeIssue = intakeUnavailable(openAccount)
  const moveUnconfirmed = open?.remoteState === 'moving' || open?.remoteState === 'unknown'
  const canOperate = isLiveAccount(openAccount) && !moveUnconfirmed
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
    void api.get<{ folders: MailFolder[] }>(`/inbox/accounts/${accountId}/folders${revision ? '?refresh=true' : ''}`, { signal: controller.signal }).then((result) => {
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
  const selectAccount = (id: string) => { setAccountId(id); setFolder('INBOX'); setView('mail'); setHistoryMode(false); setAttachments(''); setUnread(''); setTab('all'); setFiltersOpen(false); setOpenId(null); setOffset(0); setDraftOffset(0); setDrafts([]); setFocusMessage(null); setQuery(''); setFolders([]) }
  const selectFolder = (name: string) => { const role = folders.find((item) => item.name === name)?.role; setFolder(name); setView('mail'); setHistoryMode(false); setAttachments(''); setOpenId(null); setOffset(0); setFocusMessage(null); setTab((current) => role === 'archive' ? 'archived' : current === 'archived' ? (unread === 'true' ? 'unread' : 'all') : current) }
  const createDraft = (mode: DraftMode, message?: MailboxMessage) => {
    const sourceAccount = message ? accounts.find((item) => item.id === message.accountId) : composeAccount
    if (!sourceAccount || !isLiveAccount(sourceAccount)) return
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
    else store.notify(values.unread === undefined ? (values.starred ? 'Message starred.' : 'Star removed.') : values.unread ? 'Marked unread.' : 'Marked read.')
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
  const openCandidate = async (id: string) => { setOpeningCandidate(true); try { if (!store.candidate(id)) await store.refresh({ keepCurrent: true }); if (store.isCurrentSession()) navigate(`/candidates/${id}`) } catch (cause) { store.notify(errorText(cause), 'warn') } finally { setOpeningCandidate(false) } }
  const folderChoices = useMemo(() => folders.length ? folders : [{ name: 'INBOX', role: 'inbox' as const, total, unread: 0 }], [folders, total])
  const refresh = () => { bump(); void inbox.refresh().catch(() => {}) }
  // Gmail and Microsoft sign-in return here with the outcome in the address.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const added = params.get('mail_connected')
    const failed = params.get('mail_error')
    if (!added && !failed) return
    window.history.replaceState(null, '', '/inbox')
    if (failed) store.notify(failed, 'warn')
    else if (added) { selectAccount(added); store.notify('Mailbox connected. Incoming mail will sync shortly.') }
  }, [])

  const archiveFolder = folders.find((item) => item.role === 'archive')
  const inboxFolder = folders.find((item) => item.role === 'inbox')?.name ?? 'INBOX'
  const shown = tab === 'starred' ? messages.filter((item) => item.starred)
    : tab === 'archived' && !(remoteView && currentRole === 'archive') ? messages.filter((item) => /archive/i.test(item.folder ?? ''))
    : messages
  const chooseTab = (next: InboxTab) => {
    setTab(next); setOpenId(null); setOffset(0)
    setUnread(next === 'unread' ? 'true' : '')
    if (next === 'archived' && remoteView && archiveFolder) setFolder(archiveFolder.name)
    else if (next !== 'archived' && remoteView && currentRole === 'archive') setFolder(inboxFolder)
  }
  const listControls = <div className="zoho-list-controls">
    <Select aria-label="Mailbox" className="zoho-tool-mailbox" value={accountId} onChange={(event) => selectAccount(event.target.value)}><option value="">All accounts</option>{accounts.map((item) => <option key={item.id} value={item.id}>{item.address} · {PROVIDER_LABEL[item.provider]}{!isLiveAccount(item) ? (item.connectionStatus === 'disconnected' ? ' · disconnected' : ' · archived') : ''}</option>)}</Select>
    <Select aria-label="Folder" className="zoho-tool-folder" value={view === 'drafts' ? '__saved_drafts' : folder} onChange={(event) => event.target.value === '__saved_drafts' ? setView('drafts') : selectFolder(event.target.value)}>{folderChoices.map((item) => <option key={item.name} value={item.name}>{item.role === 'drafts' ? 'Mailbox drafts' : item.name}</option>)}<option value="__saved_drafts">My saved drafts</option></Select>
  </div>

  return <div className="zoho-workspace">
    <PageHeader title="Inbox" description="All your conversations and messages in one place."
      actions={<><Button icon={<RefreshCw size={15} />} disabled={loading || busy} onClick={refresh}>Refresh</Button>{admin && <Button icon={<Settings2 size={15} />} disabled={busy} onClick={() => setManaging(true)}>Accounts</Button>}<Button icon={<FolderPlus size={15} />} disabled={busy || !liveMailbox} onClick={() => { setFolderName(''); setNewFolder(true) }}>New folder</Button><Button variant="primary" icon={<Pencil size={15} />} disabled={busy || !composeAccount || composeAccount.connectionStatus === 'disconnected'} onClick={() => createDraft('compose')}>Compose</Button></>} />
    {(inbox.error || inbox.actionError || mailError) && <div role="alert" className="mb-4 flex flex-col items-start gap-2 rounded-control border border-stamp-100 bg-white p-4 text-sm text-stamp-700 shadow-sm"><p className="break-words">{inbox.actionError || mailError || inbox.error}</p>{mailError && mailbox && <p className="text-xs text-ink-600">Showing the last loaded folder. Refresh to try the provider again.</p>}<Button size="sm" onClick={refresh}>Try again</Button></div>}
    {inbox.notice && <p role="status" className="mb-4 rounded-control bg-navy-50 p-3 text-sm text-navy-800">{inbox.notice}</p>}
    {!inbox.data && !inbox.error && <div role="status" aria-label="Loading mailbox" className="zoho-skeleton"><div className="zoho-skeleton-bar" />{[0, 1, 2, 3, 4, 5].map((row) => <div key={row} className="zoho-skeleton-row"><span /><div><i /><i /></div></div>)}</div>}
    {focusMessage && <p className="mb-4 text-sm text-ink-600">Showing a candidate source message. <button className="font-medium text-navy-700 underline" onClick={() => { setFocusMessage(null); setOpenId(null); window.history.replaceState(null, '', '/inbox') }}>Return to mailbox</button></p>}
    {inbox.data && accounts.length === 0 ? <Panel><EmptyState title="Your mailbox, connected" description={admin ? 'Connect Gmail, Microsoft 365, Zoho or any IMAP mailbox. Add as many as you need and read them all here.' : 'An administrator can connect the recruitment mailboxes.'} action={admin ? <Button variant="primary" icon={<Plus size={15} />} onClick={() => setConnect({ address: '' })}>Connect mailbox</Button> : undefined} /></Panel> : inbox.data && <>
      {account && <div className="zoho-status"><Badge tone={liveMailbox ? 'navy' : 'neutral'}>{PROVIDER_LABEL[account.provider]} · {!isLiveAccount(account) && account.connectionStatus !== 'disconnected' ? 'archived, read only' : account.connectionStatus.replaceAll('_', ' ')}</Badge><span>{account.lastSyncAt ? `Last incoming sync ${smartTime(account.lastSyncAt)}` : 'Incoming sync has not completed yet'}</span><span className={cn('zoho-chip', account.autoIngest && 'is-on')}>{account.autoIngest ? 'Candidate intake on' : 'Candidate intake off'}</span>{account.lastError && <div role="status" className="zoho-account-alert"><strong>{/storage volume/i.test(account.lastError) ? 'Another copy of the CRM is reading this mailbox' : 'Mailbox needs attention'}</strong>{/storage volume/i.test(account.lastError) ? 'This mailbox is saved against a different server\'s file storage, so this copy pauses to protect your documents. Pause the other copy\'s email worker (for example the deployed server), then choose Sync incoming mail in Accounts.' : account.lastError}</div>}</div>}
      <div className={cn('zoho-mail-layout', open && view === 'mail' && 'is-reading', view === 'drafts' && 'is-drafts')}>
        {view === 'drafts' ? <section className="zoho-draft-list" aria-label="My drafts"><div className="zoho-section-header"><h2>My saved drafts</h2><div className="zoho-draft-controls">{listControls}</div></div>{draftError && <p role="alert" className="p-4 text-sm text-stamp-700">{draftError}</p>}
          {draftLoading && <p role="status" className="px-4 py-2 text-xs text-ink-600">Loading saved drafts…</p>}
          {drafts.map((item) => <button key={item.id} className="zoho-draft-row" disabled={busy || draftLoading} onClick={() => openDraft(item)}><FileText size={18} className="shrink-0 text-ink-500" /><span className="min-w-0 flex-1 text-left"><strong className="block truncate text-sm">{item.subject || '(No subject)'}</strong><span className="mt-1 block truncate text-xs text-ink-600">To {item.to.join(', ') || 'No direct recipients'} · {smartTime(item.updatedAt)}</span><span className="mt-1 block truncate text-xs text-ink-500">{item.body || 'Empty draft'}</span></span><Badge tone={item.status === 'unknown' || item.status === 'failed' ? 'amber' : 'neutral'}>{item.status === 'unknown' ? 'Delivery unconfirmed' : item.status}</Badge></button>)}
          {!drafts.length && !draftError && !draftLoading && <EmptyState title={search ? 'No matching drafts' : 'No saved drafts'} description={search ? 'Try another subject, message or recipient.' : 'Compose a message to save your text and attachments here. Drafts synchronized to the mailbox are also visible to other mailbox users.'} />}
          <div className="zoho-pagination"><Button size="sm" aria-label="Previous saved drafts" disabled={draftOffset === 0 || draftLoading} onClick={() => setDraftOffset(Math.max(0, draftOffset - PAGE_SIZE))}><ChevronLeft size={15} /></Button><span className="text-xs text-ink-600">Page {Math.floor(draftOffset / PAGE_SIZE) + 1} · {draftTotal} results</span><Button size="sm" aria-label="Next saved drafts" disabled={!draftHasMore || draftLoading} onClick={() => setDraftOffset(draftOffset + PAGE_SIZE)}><ChevronRight size={15} /></Button></div>
        </section> : <>
          <section className="zoho-message-list" aria-label="Messages">
            <div className="zoho-list-head">
              {listControls}
              {!focusMessage && <div className="zoho-tabs" role="tablist" aria-label="Message views">{TABS.map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={tab === key} className={cn('zoho-tab', tab === key && 'is-active')} onClick={() => chooseTab(key)}>{label}</button>)}</div>}
              <div className="zoho-search-row">
                <div className="zoho-tool-search"><SearchInput value={query} onChange={setQuery} placeholder="Search in this mailbox" /></div>
                {!focusMessage && <div className="zoho-filter-wrap">
                  <button type="button" className={cn('zoho-icon-btn', (attachments !== '' || (liveMailbox && !remoteView)) && 'is-active')} aria-label="Filters" aria-expanded={filtersOpen} title="Filters" onClick={() => setFiltersOpen((value) => !value)}><SlidersHorizontal size={17} /></button>
                  {filtersOpen && <div className="zoho-filter-pop" role="group" aria-label="Message filters">
                    <Select aria-label="Message source" title={remoteView ? 'Live folder from the mail server' : 'Messages received, imported or viewed in CRM'} value={remoteView ? 'live' : 'stored'} onChange={(event) => { setHistoryMode(event.target.value === 'stored'); setAttachments(''); setOpenId(null); setOffset(0) }}>{liveMailbox && <option value="live">Live {providerName} folder</option>}<option value="stored">Stored history</option></Select>
                    <Select aria-label="Attachment filter" title={remoteView ? 'Attachment filters apply to stored history' : undefined} value={attachments} disabled={!!remoteView} onChange={(event) => { setAttachments(event.target.value); setOpenId(null); setOffset(0) }}><option value="">Any attachments</option><option value="true">Has attachments</option><option value="false">No attachments</option></Select>
                  </div>}
                </div>}
              </div>
            </div>
            {loading && <p role="status" className="zoho-list-note">Refreshing from {providerName}…</p>}
            <ul className="zoho-message-scroll">{shown.map((message) => {
              const candidateIds = message.candidateId ? [message.candidateId] : message.documentBundle?.candidateIds ?? []
              const owner = accounts.find((item) => item.id === message.accountId)
              return <li key={message.id} className={cn('zoho-message-row', open?.id === message.id && 'is-selected')}>
                <button className="zoho-open-message" aria-pressed={open?.id === message.id} onClick={() => setOpenId(message.id)}>
                  <span className="mail-sender-avatar shrink-0" aria-hidden>{initials(message.fromName || message.fromAddress)}</span>
                  <span className="zoho-row-body">
                    <span className="zoho-row-top">
                      {message.unread && <span className="zoho-unread-dot" aria-label="Unread" />}
                      <span className={cn('zoho-row-name', message.unread && 'is-unread')}>{message.fromName || message.fromAddress}</span>
                      <time className="zoho-row-time" dateTime={String(message.receivedAt)}>{smartTime(message.receivedAt)}</time>
                    </span>
                    <span className={cn('zoho-row-subject', message.unread && 'is-unread')}>{message.subject || '(No subject)'}</span>
                    {message.preview && <span className="zoho-row-preview">{message.preview}</span>}
                    {(message.attachments.length > 0 || !accountId) && <span className={cn('zoho-row-meta', candidateIds.length > 0 && 'has-badge')}>
                      {message.attachments.length > 0 && <span className="zoho-meta-chip" title="Has attachments"><Paperclip size={13} />{message.attachments.length}</span>}
                      {!accountId && <span className="zoho-meta-chip is-account" title={owner?.address}>{owner?.address}</span>}
                    </span>}
                  </span>
                </button>
                {isLiveAccount(owner) && <button className="zoho-star" aria-label={`${message.starred ? 'Unstar' : 'Star'} ${message.subject || 'message'}`} disabled={busy || ['moving', 'unknown'].includes(message.remoteState ?? '') || owner?.connectionStatus === 'disconnected'} onClick={() => flags(message, { starred: !message.starred })}><Star size={17} className={message.starred ? 'fill-amber-400 text-amber-500' : 'text-ink-400'} /></button>}
                {candidateIds.length > 0 && <button className="mail-candidate-badge" disabled={openingCandidate} title="Open the candidate created from this email" aria-label={`Open candidate from ${message.subject || 'this message'}`} onClick={() => void openCandidate(candidateIds[0])}><UserCheck size={14} />Candidate</button>}
              </li>
            })}</ul>
            {!shown.length && !loading && !mailError && <EmptyState title={tab === 'starred' ? 'No starred messages' : tab === 'archived' ? 'No archived messages' : tab === 'unread' ? 'No unread messages' : 'No messages in this view'} description={search ? 'Try another search.' : tab === 'starred' ? 'Star a message to keep it here. Only the messages on this page are checked.' : 'Messages will appear here when this folder is refreshed.'} />}
            <div className="zoho-pagination"><Button size="sm" aria-label="Previous messages" disabled={offset === 0 || loading} onClick={() => { setOpenId(null); setOffset(Math.max(0, offset - PAGE_SIZE)) }}><ChevronLeft size={15} /></Button><span className="text-xs text-ink-600">Page {Math.floor(offset / PAGE_SIZE) + 1}</span><Button size="sm" aria-label="Next messages" disabled={!hasMore || loading} onClick={() => { setOpenId(null); setOffset(offset + PAGE_SIZE) }}><ChevronRight size={15} /></Button></div>
          </section>
          <section className="zoho-reader" aria-label="Message reader">{open ? <>
            <div className="zoho-reader-toolbar">
              <button type="button" className="zoho-icon-btn zoho-back" aria-label="Back to messages" title="Back to messages" onClick={() => setOpenId(null)}><ArrowLeft size={17} /></button>
              <h2 className="zoho-reader-title" title={open.subject || '(No subject)'}>{open.subject || '(No subject)'}</h2>
              {isLiveAccount(openAccount) && <button type="button" className="zoho-icon-btn" aria-label={open.starred ? 'Unstar message' : 'Star message'} title={open.starred ? 'Remove star' : 'Star'} disabled={busy || moveUnconfirmed} onClick={() => flags(open, { starred: !open.starred })}><Star size={17} className={open.starred ? 'fill-amber-400 text-amber-500' : ''} /></button>}
              <span className="zoho-toolbar-gap" />
              <button type="button" className="zoho-icon-btn" aria-label="Reply" title="Reply" disabled={busy || !canOperate || currentRole === 'drafts'} onClick={() => createDraft('reply', open)}><Reply size={17} /></button>
              <button type="button" className="zoho-icon-btn" aria-label="Archive" title="Archive" disabled={busy || !canOperate} onClick={() => move(open, 'archive')}><Archive size={17} /></button>
              {currentRole === 'trash' || currentRole === 'archive' ? <button type="button" className="zoho-icon-btn" aria-label="Restore" title="Restore" disabled={busy || !canOperate} onClick={() => move(open, 'restore')}><Undo2 size={17} /></button> : <button type="button" className="zoho-icon-btn" aria-label="Trash" title="Move to Trash" disabled={busy || !canOperate} onClick={() => move(open, 'trash')}><Trash2 size={17} /></button>}
              <button type="button" className="zoho-icon-btn" aria-label={open.unread ? 'Mark read' : 'Mark unread'} title={open.unread ? 'Mark read' : 'Mark unread'} disabled={busy || !canOperate} onClick={() => flags(open, { unread: !open.unread })}>{open.unread ? <MailOpen size={17} /> : <Mail size={17} />}</button>
              {liveMailbox && <details className="zoho-more"><summary className="zoho-icon-btn" aria-label="More actions" title="More"><MoreVertical size={17} /></summary><div className="zoho-more-pop"><Select aria-label="Move message to folder" value="" disabled={busy || !canOperate} onChange={(event) => { if (event.target.value) move(open, event.target.value, true) }}><option value="">Move to folder…</option>{folders.filter((item) => item.name !== folder).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</Select></div></details>}
            </div>
            <div className="zoho-reader-scroll">
              <div className="zoho-reader-heading"><span className="mail-sender-avatar shrink-0" aria-hidden>{initials(open.fromName || open.fromAddress)}</span><div className="min-w-0 flex-1"><p className="zoho-sender-name">{open.fromName || open.fromAddress}</p>{open.fromName && <p className="zoho-sender-address">{open.fromAddress}</p>}<p className="zoho-sender-to">to {(open.toAddresses?.length ? open.toAddresses : [openAccount?.address || '']).join(', ')}{open.ccAddresses?.length ? ` · Cc ${open.ccAddresses.join(', ')}` : ''}</p></div><time className="zoho-row-time" dateTime={String(open.receivedAt)}>{smartTime(open.receivedAt)}</time></div>
              {moveUnconfirmed && <div className="mx-5 mb-4 rounded-control border border-amber-100 bg-amber-50 p-3"><p className="text-sm text-amber-900">The last mailbox operation is not confirmed. Check its status before moving this message again.</p><Button size="sm" className="mt-2" disabled={busy} onClick={() => void perform(`reconcile:${open.id}`, async () => { const updated = await api.post<MailboxMessage>(`/inbox/messages/${open.id}/reconcile`); setMailbox((current) => current ? { ...current, messages: current.messages.map((item) => item.id === updated.id ? updated : item) } : current) }, 'Mailbox operation checked.')}>Check mailbox operation</Button></div>}
              {open.remoteError && <p role="alert" className="mx-5 rounded-control bg-amber-50 p-3 text-sm text-amber-900">{open.remoteError}</p>}
              <div className="zoho-message-body">{open.body || open.preview || '(No message text)'}</div>
              {open.attachments.length > 0 && <div className="zoho-attachments"><p className="zoho-section-label">Attachments</p><ul>{open.attachments.map((file) => <li key={file.id} className="zoho-attachment"><Paperclip size={15} className="shrink-0 text-ink-500" /><span className="min-w-0 flex-1 truncate" title={file.fileName}>{file.fileName}</span><span className="zoho-attachment-size">{file.sizeKb} KB</span>{file.url && <><button type="button" className="zoho-link" onClick={() => setPreview({ name: file.fileName, url: file.url! })}><Eye size={12} />Preview</button><a className="zoho-link" href={fileHref(`${file.url}${file.url.includes('?') ? '&' : '?'}download=true`)}>Download</a></>}</li>)}</ul></div>}
              <div className="zoho-reply-bar">{currentRole === 'drafts' ? <Button disabled={busy || !canOperate} icon={<Pencil size={14} />} onClick={() => createDraft('edit_draft', open)}>Edit draft</Button> : <><Button disabled={busy || !canOperate} icon={<Reply size={14} />} onClick={() => createDraft('reply', open)}>Reply</Button><Button disabled={busy || !canOperate} icon={<ReplyAll size={14} />} onClick={() => createDraft('reply_all', open)}>Reply all</Button><Button disabled={busy || !canOperate} icon={<Send size={14} />} onClick={() => createDraft('forward', open)}>Forward</Button></>}</div>
              <section className="zoho-intake" aria-label="Candidate intake"><div className="flex flex-wrap items-center gap-2"><h3 className="zoho-section-label mr-auto">Candidate documents</h3>{admin && open.attachments.length > 0 && <Button size="sm" disabled={busy || jobLoading || openJobs.some(job => ['pending', 'processing', 'retry'].includes(job.status))} onClick={() => setBundleSource(open.id)}>Review people in documents</Button>}{open.documentBundle?.candidateIds.map(id => <Button key={id} size="sm" disabled={openingCandidate} onClick={() => void openCandidate(id)}>{store.candidates.find(candidate => candidate.id === id)?.name || "Open candidate"}</Button>)}{open.candidateId && !open.documentBundle && <Button size="sm" disabled={openingCandidate} onClick={() => void openCandidate(open.candidateId!)}>Open candidate</Button>}{open.attachments.length > 0 && !open.candidateId && !openJobs.length && <Button size="sm" disabled={busy || jobLoading || !!jobError || !!intakeIssue} title={intakeIssue ?? undefined} onClick={() => void perform(`ingest:${open.id}`, () => api.inbox.ingest(open.id), 'Document processing queued.')}>Create candidate from attachment</Button>}</div>
                {intakeIssue && <div className="rounded-control bg-amber-50 p-3 text-xs text-amber-900"><p role="status">{intakeIssue}</p>{admin && openAccount && (openAccount.connectionStatus === 'disconnected' || openAccount.disconnectedAt) && <Button size="sm" className="mt-2" disabled={busy} onClick={() => setConnect({ address: openAccount.address, provider: openAccount.provider })}>Reconnect mailbox</Button>}</div>}
                {jobError && <p role="alert" className="text-xs text-amber-900">{jobError}</p>}
                {openJobs.map((job) => <JobReport key={job.id} job={job} busy={busy} canRetry={admin} retryUnavailable={intakeIssue} opening={openingCandidate} onOpen={openCandidate} onRetry={() => perform(`retry:${job.id}`, () => api.inbox.retry(job.id), 'Document retry queued.')} onReview={(body) => perform(`review:${job.id}`, () => api.post(`/inbox/jobs/${job.id}/review`, body), 'Review saved. Source evidence remains in history.')} />)}
                {!openJobs.length && !open.candidateId && <p className="text-xs text-ink-600">{open.attachments.length ? 'This conversation has not been processed into a candidate.' : 'This message has no candidate attachments.'}</p>}
              </section>
            </div></> : <EmptyState title="Choose a conversation" description="Open a message to read, reply or review its candidate documents." icon={<Mail size={28} />} />}</section>
        </>}
      </div>
    </>}
    {bundleSource && <DocumentBundleReview sourceType="email" sourceId={bundleSource} onClose={() => setBundleSource(null)} onCommitted={() => { refresh(); void store.refresh({ keepCurrent: true }) }} />}
    {connect !== null && <ConnectMailbox address={connect.address} provider={connect.provider} providers={inbox.data?.providers} close={() => setConnect(null)} connected={(next) => { setConnect(null); setManaging(false); selectAccount(next.id); store.notify(`${PROVIDER_LABEL[next.provider]} mailbox connected.`); refresh() }} />}
    {preview && <AttachmentPreview file={preview} onClose={() => setPreview(null)} />}
    {compose && <MailCompose key={compose.id} initial={compose} address={accounts.find((item) => item.id === compose.accountId)?.address ?? 'Mailbox'} close={() => { setCompose(null); bump() }} changed={() => {}} />}
    <Dialog open={newFolder} onClose={() => { if (!busy) setNewFolder(false) }} title="New folder" description={`Create a shared folder in ${account?.address ?? 'this mailbox'}.`} width="sm" footer={<><Button disabled={busy} onClick={() => setNewFolder(false)}>Cancel</Button><Button variant="primary" disabled={busy || !folderName.trim() || !liveMailbox} onClick={() => void perform('new-folder', async () => { const result = await api.post<{ folders: MailFolder[] }>(`/inbox/accounts/${accountId}/folders`, { name: folderName.trim() }); setFolders(result.folders); selectFolder(folderName.trim()); setNewFolder(false) }, 'Folder is ready.')}>{busy ? 'Creating…' : 'Create folder'}</Button></>}>
      <Field label="Folder name" required><Input value={folderName} maxLength={255} onChange={(event) => setFolderName(event.target.value)} autoFocus /></Field>{inbox.actionError && <p role="alert" className="mt-3 text-sm text-stamp-700">{inbox.actionError}</p>}
    </Dialog>
    <Dialog open={managing && admin} onClose={() => { if (!busy) setManaging(false) }} title="Mailbox accounts" description="Connect any number of mailboxes and manage incoming candidate documents for each." width="lg" footer={<><Button icon={<Plus size={15} />} disabled={busy} onClick={() => setConnect({ address: '' })}>Connect mailbox</Button><Button variant="primary" disabled={busy} onClick={() => setManaging(false)}>Done</Button></>}>
      <div className="space-y-5">{accounts.map((item) => <section key={item.id} className="space-y-3 rounded-control border border-rule p-4"><div className="flex flex-wrap items-start gap-2"><div className="min-w-0 flex-1"><h3 className="break-all text-sm font-semibold">{item.address}</h3><p className="mt-1 text-xs text-ink-600">{PROVIDER_LABEL[item.provider]} · {item.connectionStatus.replaceAll('_', ' ')}{item.messageCount ? ` · ${item.messageCount} messages` : ''}</p></div><Badge tone={item.configured && item.connectionStatus !== 'disconnected' ? 'navy' : 'neutral'}>{item.configured ? 'Configured' : 'Setup required'}</Badge></div>
        {item.lastError && <p role="status" className="break-words text-xs text-amber-900">{item.lastError}</p>}
        {isLiveAccount({ ...item, connectionStatus: 'connected' }) ? <><Switch checked={item.autoIngest} disabled={busy || item.connectionStatus === 'disconnected'} label={`Auto-ingest for ${item.address}`} description="Create candidates from newly received attachments. Reading and sending mail remain available when this is off." onChange={(enabled) => void perform(`auto:${item.id}`, () => api.inbox.setAutoIngest(item.id, enabled), `Automatic candidate intake ${enabled ? 'enabled' : 'paused'}.`)} />
          <IntakeAcknowledgements sourceKind="email" channelId={item.id} channelLabel={item.address} canManage={admin} /><HistoryImport accountId={item.id} disabled={busy || item.connectionStatus === 'disconnected'} /><div className="flex flex-wrap gap-2">{item.connectionStatus !== 'disconnected' ? <><Button size="sm" disabled={busy} onClick={() => void perform(`sync:${item.id}`, () => api.post(`/inbox/accounts/${item.id}/sync`), 'Incoming sync requested.')}>Sync incoming mail</Button><Button size="sm" variant="danger" disabled={busy} onClick={() => void perform(`disconnect:${item.id}`, () => api.post(`/inbox/accounts/${item.id}/disconnect`), 'Mailbox disconnected. Messages, drafts and candidate documents are retained.')}>Disconnect</Button></> : <Button size="sm" disabled={busy} onClick={() => setConnect({ address: item.address, provider: item.provider })}>Reconnect</Button>}</div></> : <p className="text-xs leading-relaxed text-ink-600">Historical messages and candidate evidence remain readable. Connect this mailbox again to resume live access.</p>}
      </section>)}{!accounts.length && <p className="text-sm text-ink-600">No accounts connected yet.</p>}{inbox.actionError && <p role="alert" className="text-sm text-stamp-700">{inbox.actionError}</p>}</div>
    </Dialog>
  </div>
}
