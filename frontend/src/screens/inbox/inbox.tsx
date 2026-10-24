import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Archive, ArrowLeft, ChevronLeft, ChevronRight, Download, Eye, FileText, FolderPlus, Info, Mail, MailOpen, MessageCircle, MoreVertical, Paperclip, Pencil, Plus, RefreshCw, Reply, ReplyAll, Send, Settings2, SlidersHorizontal, Star, Trash2, Undo2, UserCheck } from 'lucide-react'
import { api, fileHref, type InboxAccount, type InboxSnapshot, type InboxJob, type MailProvider } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { smartTime } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { Badge, EmptyState, PageHeader, Panel } from '@/shared/ui/display'
import { Button, Field, Input, SearchInput, Select, Switch } from '@/shared/ui/primitives'
import { Dialog, Tabs } from '@/shared/ui/overlay'
import { useInbox } from '@/features/inbox/use-inbox'
import { IntakeAcknowledgements } from '@/features/intake/intake-acknowledgements'
import { DocumentBundleReview } from '@/features/candidates/components/document-bundle-review'
import { JobReport } from '@/features/inbox/intake-status'
import { PROVIDER_LABEL, intakeUnavailable, isLiveAccount } from '@/features/inbox/inbox-status'
import { HistoryImport } from '@/features/inbox/history-import'
import { ConnectMailbox } from '@/features/inbox/connect-mailbox'
import { AttachmentPreview, type PreviewFile } from '@/features/inbox/attachment-preview'
import { MailCompose } from '@/features/inbox/mail-compose'
import { AccountRail } from '@/features/inbox/account-strip'
import { CandidateChip, FileIcon, ProviderMark, SenderAvatar, fileSize } from '@/features/inbox/mail-parts'
import type { DraftMode, MailDraft, MailFolder, MailboxMessage, MailboxResult } from '@/features/inbox/mailbox-types'
import '@/features/inbox/mailbox.css'


const PAGE_SIZE = 25
type InboxTab = 'all' | 'unread' | 'starred' | 'archived'
const TABS: [InboxTab, string][] = [['all', 'All'], ['unread', 'Unread'], ['starred', 'Starred'], ['archived', 'Archived']]
const errorText = (cause: unknown) => cause instanceof Error ? cause.message : 'We could not load the mailbox. Check your internet and try again.'

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
  const openCandidateIds = open ? (open.candidateId ? [open.candidateId] : open.documentBundle?.candidateIds ?? []) : []
  const bump = () => setRevision((value) => value + 1)
  // Opening an unread message reads it, as in any mail client. "Mark unread" still brings the dot back
  // because each message is auto-read at most once per visit.
  const autoRead = useRef('')
  useEffect(() => {
    if (!open?.unread || !canOperate || autoRead.current === open.id) return
    autoRead.current = open.id
    const id = open.id
    void api.patch<MailboxMessage>(`/inbox/messages/${id}/flags`, { unread: false }).then((updated) => {
      if (!store.isCurrentSession()) return
      setMailbox((current) => current ? { ...current, messages: current.messages.map((item) => item.id === updated.id ? updated : item) } : current)
      void inbox.refresh().catch(() => {})
    }).catch(() => { autoRead.current = '' })
  }, [open?.id, open?.unread, canOperate])
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
    if (updated.remoteState === 'unknown') store.notify('We are not sure this change was saved in the mailbox. Open the message and press Check mailbox change.', 'warn')
    else store.notify(values.unread === undefined ? (values.starred ? 'Message starred.' : 'Star removed.') : values.unread ? 'Marked unread.' : 'Marked read.')
  }, '')
  const move = (message: MailboxMessage, action: 'archive' | 'trash' | 'restore' | string, custom = false) => void perform(`move:${message.id}`, async () => {
    const updated = await api.post<MailboxMessage>(`/inbox/messages/${message.id}/${custom ? 'move' : 'action'}`, custom ? { folder: action } : { action })
    if (updated.remoteState === 'moving' || updated.remoteState === 'unknown') {
      setMailbox((current) => current ? { ...current, messages: current.messages.map((item) => item.id === message.id ? updated : item) } : current)
      store.notify('We are not sure the move worked. Press Check mailbox change before you move it again.', 'warn')
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
    else if (added) { selectAccount(added); store.notify('Mailbox connected. New mail will show up shortly.') }
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
  const folderSelect = <Select aria-label="Folder" className="mb-folder-select" value={view === 'drafts' ? '__saved_drafts' : folder} onChange={(event) => event.target.value === '__saved_drafts' ? setView('drafts') : selectFolder(event.target.value)}>{folderChoices.map((item) => <option key={item.name} value={item.name}>{item.role === 'drafts' ? 'Mailbox drafts' : item.name}</option>)}<option value="__saved_drafts">My saved drafts</option></Select>

  const toggleAuto = (target: InboxAccount, enabled: boolean) => void perform(`auto:${target.id}`, () => api.inbox.setAutoIngest(target.id, enabled), `Auto-import turned ${enabled ? 'on' : 'off'} for ${target.address}.`)
  const checkingJobs = openJobs.some((job) => ['pending', 'processing', 'retry'].includes(job.status))
  const noCandidateYet = !open?.candidateId && !openJobs.length && !open?.documentBundle?.candidateIds.length
  // PRD: a manual "Create candidate from attachment" on any email the automatic import skipped or could not read, for every role.
  const canCreateFromAttachment = !open?.candidateId && !open?.documentBundle?.candidateIds.length && (openJobs.length === 0 || openJobs.every((job) => ['ignored', 'failed'].includes(job.status)))

  return <div className={cn('inbox-v2 mb-page', open && view === 'mail' && 'is-reading')}>
    <PageHeader title="Inbox" eyebrow="Mail" description="All your mailboxes in one list. Read mail, reply, and turn resumes into candidates."
      actions={<>
        <Button icon={<RefreshCw size={15} />} disabled={loading || busy} onClick={refresh}>Refresh</Button>
        {admin && <Button icon={<Settings2 size={15} />} disabled={busy} onClick={() => setManaging(true)}>Mailboxes</Button>}
        {store.me.role === 'admin' && <Button icon={<MessageCircle size={15} />} onClick={() => navigate('/whatsapp')}>WhatsApp</Button>}
        {liveMailbox && <Button icon={<FolderPlus size={15} />} disabled={busy} onClick={() => { setFolderName(''); setNewFolder(true) }}>New folder</Button>}
        <Button variant="primary" icon={<Pencil size={15} />} disabled={busy || !composeAccount || composeAccount.connectionStatus === 'disconnected'} title={composeAccount ? undefined : 'Connect a mailbox to compose'} onClick={() => createDraft('compose')}>Compose</Button>
      </>} />
    {(inbox.error || inbox.actionError || mailError) && <div role="alert" className="mb-alert is-error"><AlertTriangle size={18} /><div><p className="break-words">{inbox.actionError || mailError || inbox.error}</p>{mailError && mailbox && <p className="mb-alert-sub">Showing the last mail we loaded. Press Refresh to try again.</p>}</div><Button size="sm" onClick={refresh}>Try again</Button></div>}
    {inbox.notice && <p role="status" className="mb-alert is-info"><Info size={18} /><span>{inbox.notice}</span></p>}
    {!inbox.data && !inbox.error && <div role="status" aria-label="Loading mailbox" className="mb-skeleton">
      <div className="mb-card mb-skel-rail">{[0, 1, 2].map((row) => <i key={row} />)}</div>
      <div className="mb-card mb-skel-list"><div className="mb-skel-bar" />{[0, 1, 2, 3, 4, 5].map((row) => <div key={row} className="mb-skel-row"><span /><div><i /><i /></div></div>)}</div>
      <div className="mb-card mb-skel-reader" />
    </div>}
    {focusMessage && <p className="mb-focus-note">Showing a candidate source message. <button type="button" onClick={() => { setFocusMessage(null); setOpenId(null); window.history.replaceState(null, '', '/inbox') }}>Return to mailbox</button></p>}
    {inbox.data && accounts.length === 0 ? <Panel className="mb-empty-panel"><EmptyState icon={<Mail size={24} />} title="No mailbox connected yet" description={admin ? 'Connect Gmail, Microsoft 365, Zoho or any other mailbox. Add as many as you need and read them all here.' : 'An admin can connect the recruitment mailboxes.'} action={admin ? <Button variant="primary" icon={<Plus size={15} />} onClick={() => setConnect({ address: '' })}>Connect mailbox</Button> : undefined} /></Panel> : inbox.data && <>
      {account && (account.lastError || !liveMailbox) && <div className="mb-alert is-warn">
        <AlertTriangle size={18} />
        <div className="min-w-0 flex-1">
          {!liveMailbox && <p><Badge tone="neutral">{PROVIDER_LABEL[account.provider]} · {account.connectionStatus !== 'disconnected' ? 'old mailbox, read only' : 'disconnected, saved mail only'}</Badge></p>}
          {account.lastError && <div role="status"><strong>{/storage volume/i.test(account.lastError) ? 'Another copy of the CRM is using this mailbox' : 'Mailbox needs attention'}</strong><p>{/storage volume/i.test(account.lastError) ? 'Another copy of the CRM is already reading this mailbox, so this copy has paused to keep your documents safe. Ask your tech team to stop the email reader on the other copy, then press Check for new mail in Mailboxes.' : account.lastError}</p></div>}
        </div>
        {admin && account.connectionStatus === 'disconnected' && <Button size="sm" disabled={busy} onClick={() => setConnect({ address: account.address, provider: account.provider })}>Reconnect mailbox</Button>}
      </div>}
      <div className="mb"><div className={cn('mb-shell', open && view === 'mail' && 'is-reading', view === 'drafts' && 'is-drafts')}>
        <AccountRail accounts={accounts} selectedId={accountId} admin={admin} busy={busy} onSelect={selectAccount} onToggleAuto={toggleAuto} onConnect={() => setConnect({ address: '' })} />
        <div className="mb-panes">
          {view === 'drafts' ? <section className="mb-card mb-draft-list" aria-label="My drafts">
            <header className="mb-card-head"><div><h2>My saved drafts</h2><p>Drafts saved in the CRM, from all your mailboxes.</p></div><div className="mb-draft-controls">{folderSelect}</div></header>
            {draftError && <p role="alert" className="mb-note is-error">{draftError}</p>}
            {draftLoading && <p role="status" className="mb-note">Loading saved drafts…</p>}
            <div className="mb-pager"><Button size="sm" aria-label="Previous saved drafts" disabled={draftOffset === 0 || draftLoading} onClick={() => setDraftOffset(Math.max(0, draftOffset - PAGE_SIZE))}><ChevronLeft size={15} /></Button><span>Page {Math.floor(draftOffset / PAGE_SIZE) + 1} · {draftTotal} results</span><Button size="sm" aria-label="Next saved drafts" disabled={!draftHasMore || draftLoading} onClick={() => setDraftOffset(draftOffset + PAGE_SIZE)}><ChevronRight size={15} /></Button></div>
            <ul className="mb-scroll">{drafts.map((item) => <li key={item.id}><button type="button" className="mb-draft-row" disabled={busy || draftLoading} onClick={() => openDraft(item)}>
              <span className="mb-draft-icon"><FileText size={17} /></span>
              <span className="mb-draft-text"><strong title={item.subject || '(No subject)'}>{item.subject || '(No subject)'}</strong><span className="mb-draft-to">To {item.to.join(', ') || 'no one added yet'} · {smartTime(item.updatedAt)}</span><span className="mb-draft-body">{item.body || 'Empty draft'}</span></span>
              <Badge tone={item.status === 'unknown' || item.status === 'failed' ? 'amber' : 'neutral'} variant="dot">{item.status === 'unknown' ? 'Not confirmed as sent' : item.status}</Badge>
            </button></li>)}</ul>
            {!drafts.length && !draftError && !draftLoading && <EmptyState icon={<FileText size={22} />} title={search ? 'No matching drafts' : 'No saved drafts'} description={search ? 'Try another subject, message or recipient.' : 'Write a message and save it to keep your text and files here. Drafts saved to the mailbox can also be seen by others who use that mailbox.'} />}
          </section> : <>
            <section className="mb-card mb-list" aria-label="Messages">
              <div className="mb-list-head">
                <div className="mb-list-row">{folderSelect}
                <SearchInput value={query} onChange={setQuery} placeholder="Search mail" className="mb-search" />
                  {!focusMessage && <div className="mb-filter-wrap">
                    <button type="button" className={cn('mb-icon-btn is-boxed', (attachments !== '' || (liveMailbox && !remoteView)) && 'is-active')} aria-label="Filters" aria-expanded={filtersOpen} title="Filters" onClick={() => setFiltersOpen((value) => !value)}><SlidersHorizontal size={16} /></button>
                    {filtersOpen && <div className="mb-filter-pop" role="group" aria-label="Message filters">
                      <Select aria-label="Message source" title={remoteView ? 'Live folder from the mail server' : 'Mail already saved in the CRM'} value={remoteView ? 'live' : 'stored'} onChange={(event) => { setHistoryMode(event.target.value === 'stored'); setAttachments(''); setOpenId(null); setOffset(0) }}>{liveMailbox && <option value="live">Live {providerName} folder</option>}<option value="stored">Saved mail</option></Select>
                      <Select aria-label="Attachment filter" title={remoteView ? 'Attachment filters work on saved mail only' : undefined} value={attachments} disabled={!!remoteView} onChange={(event) => { setAttachments(event.target.value); setOpenId(null); setOffset(0) }}><option value="">Any attachments</option><option value="true">Has attachments</option><option value="false">No attachments</option></Select>
                    </div>}
                  </div>}
                </div>
                <div className="mb-tabrow">
                  {!focusMessage && <Tabs className="mb-tabs" tabs={TABS.map(([key, label]) => ({ key, label }))} active={tab} onChange={(key) => chooseTab(key as InboxTab)} />}
                  <div className="mb-pager"><Button size="sm" aria-label="Previous messages" disabled={offset === 0 || loading} onClick={() => { setOpenId(null); setOffset(Math.max(0, offset - PAGE_SIZE)) }}><ChevronLeft size={15} /></Button><span>Page {Math.floor(offset / PAGE_SIZE) + 1}</span><Button size="sm" aria-label="Next messages" disabled={!hasMore || loading} onClick={() => { setOpenId(null); setOffset(offset + PAGE_SIZE) }}><ChevronRight size={15} /></Button></div>
                </div>
              </div>
              {loading && <p role="status" className="mb-note">Refreshing from {providerName}…</p>}
              <ul className="mb-scroll mb-rows">{shown.map((message) => {
                const candidateIds = message.candidateId ? [message.candidateId] : message.documentBundle?.candidateIds ?? []
                const owner = accounts.find((item) => item.id === message.accountId)
                const sender = message.fromName || message.fromAddress
                return <li key={message.id} className={cn('mb-row', open?.id === message.id && 'is-selected', message.unread && 'is-unread', candidateIds.length > 0 && 'has-chip')}>
                  <button type="button" className="mb-row-open" aria-pressed={open?.id === message.id} onClick={() => setOpenId(message.id)}>
                    <SenderAvatar name={sender} />
                    <span className="mb-row-body">
                      <span className="mb-row-line">
                        {message.unread && <span className="mb-dot" role="img" aria-label="Unread" />}
                        <span className={cn('mb-row-name', message.unread && 'is-unread')} title={sender}>{sender}</span>
                      </span>
                      <span className={cn('mb-row-subject', message.unread && 'is-unread')} title={message.subject || '(No subject)'}>{message.subject || '(No subject)'}</span>
                      <span className="mb-row-line is-meta">
                        {!accountId && owner && <ProviderMark provider={owner.provider} size="sm" title={owner.address} />}
                        <span className="mb-row-preview" title={message.preview || undefined}>{message.preview || 'No preview'}</span>
                      </span>
                    </span>
                  </button>
                  <div className="mb-row-side">
                    <time className="mb-row-time" dateTime={String(message.receivedAt)}>{smartTime(message.receivedAt)}</time>
                    <span className="mb-row-icons">
                      <span className="mb-clip" title={message.attachments.length ? `${message.attachments.length} attachment${message.attachments.length === 1 ? '' : 's'}` : undefined}>{message.attachments.length > 0 && <Paperclip size={14} aria-label="Has attachments" />}</span>
                      {isLiveAccount(owner) && <button type="button" className={cn('mb-star', message.starred && 'is-on')} aria-label={`${message.starred ? 'Unstar' : 'Star'} ${message.subject || 'message'}`} title={message.starred ? 'Remove star' : 'Star'} disabled={busy || ['moving', 'unknown'].includes(message.remoteState ?? '') || owner?.connectionStatus === 'disconnected'} onClick={() => flags(message, { starred: !message.starred })}><Star size={16} /></button>}
                    </span>
                  </div>
                  {candidateIds.length > 0 && <div className="mb-row-chip"><CandidateChip label={candidateIds.length > 1 ? `${candidateIds.length} candidates created` : 'Candidate created'} disabled={openingCandidate} title={candidateIds.length > 1 ? 'This email produced several candidates. Open it to see each one.' : 'Open the candidate created from this email'} ariaLabel={candidateIds.length > 1 ? `${candidateIds.length} candidates from ${message.subject || 'this message'}` : `Open candidate from ${message.subject || 'this message'}`} onClick={() => candidateIds.length > 1 ? setOpenId(message.id) : void openCandidate(candidateIds[0])} /></div>}
                </li>
              })}</ul>
              {!shown.length && !loading && !mailError && <EmptyState icon={<Mail size={22} />} title={tab === 'starred' ? 'No starred messages' : tab === 'archived' ? 'No archived messages' : tab === 'unread' ? 'No unread messages' : 'No messages in this view'} description={search ? 'Try another search.' : tab === 'starred' ? 'Star a message to keep it here. Only the messages on this page are checked.' : 'Mail will show here once this folder loads.'} />}
            </section>
            <section className="mb-card mb-reader" aria-label="Message reader">{open ? <>
              <div className="mb-reader-bar">
                <button type="button" className="mb-icon-btn is-boxed mb-back" aria-label="Back to messages" title="Back to messages" onClick={() => setOpenId(null)}><ArrowLeft size={16} /></button>
                <span className="mb-reader-where" title={openAccount?.address}>{openAccount && <ProviderMark provider={openAccount.provider} size="sm" />}<span>{currentFolder?.name ?? open.folder ?? 'INBOX'}</span></span>
                <span className="mb-spacer" />
                {isLiveAccount(openAccount) && <button type="button" className={cn('mb-icon-btn', open.starred && 'is-star-on')} aria-label={open.starred ? 'Unstar message' : 'Star message'} title={open.starred ? 'Remove star' : 'Star'} disabled={busy || moveUnconfirmed} onClick={() => flags(open, { starred: !open.starred })}><Star size={17} /></button>}
                <button type="button" className="mb-icon-btn" aria-label="Archive" title="Archive" disabled={busy || !canOperate} onClick={() => move(open, 'archive')}><Archive size={17} /></button>
                {currentRole === 'trash' || currentRole === 'archive' ? <button type="button" className="mb-icon-btn" aria-label="Restore" title="Restore" disabled={busy || !canOperate} onClick={() => move(open, 'restore')}><Undo2 size={17} /></button> : <button type="button" className="mb-icon-btn" aria-label="Trash" title="Move to Trash" disabled={busy || !canOperate} onClick={() => move(open, 'trash')}><Trash2 size={17} /></button>}
                <button type="button" className="mb-icon-btn" aria-label={open.unread ? 'Mark read' : 'Mark unread'} title={open.unread ? 'Mark read' : 'Mark unread'} disabled={busy || !canOperate} onClick={() => flags(open, { unread: !open.unread })}>{open.unread ? <MailOpen size={17} /> : <Mail size={17} />}</button>
                {liveMailbox && <details className="mb-more"><summary className="mb-icon-btn" aria-label="More actions" title="More"><MoreVertical size={17} /></summary><div className="mb-more-pop"><Select aria-label="Move message to folder" value="" disabled={busy || !canOperate} onChange={(event) => { if (event.target.value) move(open, event.target.value, true) }}><option value="">Move to folder…</option>{folders.filter((item) => item.name !== folder).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</Select></div></details>}
              </div>
              <div className="mb-reader-scroll">
                <h2 className="mb-subject">{open.subject || '(No subject)'}</h2>
                <div className="mb-sender">
                  <SenderAvatar name={open.fromName || open.fromAddress} className="is-lg" />
                  <div className="min-w-0 flex-1">
                    <p className="mb-sender-name">{open.fromName || open.fromAddress}</p>
                    {open.fromName && <p className="mb-sender-line">{open.fromAddress}</p>}
                    <p className="mb-sender-line">to {(open.toAddresses?.length ? open.toAddresses : [openAccount?.address || '']).join(', ')}{open.ccAddresses?.length ? ` · Cc ${open.ccAddresses.join(', ')}` : ''}</p>
                  </div>
                  <time className="mb-row-time" dateTime={String(open.receivedAt)}>{smartTime(open.receivedAt)}</time>
                </div>
                <div className="mb-provenance">
                  {openAccount && <span className="mb-chip" title={`Received by ${openAccount.address}`}><ProviderMark provider={openAccount.provider} size="sm" /><span>{openAccount.address}</span></span>}
                  {openAccount && <Badge tone={openAccount.autoIngest ? 'green' : 'neutral'} variant="dot">Auto-import {openAccount.autoIngest ? 'on' : 'off'}</Badge>}
                  {openCandidateIds.map((id) => <CandidateChip key={id} inline label={store.candidates.find((candidate) => candidate.id === id)?.name ?? 'Open candidate'} disabled={openingCandidate} title="Open the candidate created from this email" onClick={() => void openCandidate(id)} />)}
                </div>
                {moveUnconfirmed && <div className="mb-inline-alert is-warn"><p>We are not sure the last change worked. Check it before you move this message again.</p><Button size="sm" disabled={busy} onClick={() => void perform(`reconcile:${open.id}`, async () => { const updated = await api.post<MailboxMessage>(`/inbox/messages/${open.id}/reconcile`); setMailbox((current) => current ? { ...current, messages: current.messages.map((item) => item.id === updated.id ? updated : item) } : current) }, 'Mailbox change checked.')}>Check mailbox change</Button></div>}
                {open.remoteError && <p role="alert" className="mb-inline-alert is-warn">{open.remoteError}</p>}
                <div className="mb-body">{open.body || open.preview || '(No message text)'}</div>
                {open.attachments.length > 0 && <div className="mb-attachments"><p className="mb-label">Attachments</p><ul>{open.attachments.map((file) => <li key={file.id} className="mb-attachment">
                  <FileIcon name={file.fileName} />
                  <span className="mb-attachment-text"><span className="mb-attachment-name" title={file.fileName}>{file.fileName}</span><span className="mb-attachment-size">{fileSize(file.sizeKb)}</span></span>
                  {file.url && <span className="mb-attachment-actions"><button type="button" className="mb-link" title={`Preview ${file.fileName}`} onClick={() => setPreview({ name: file.fileName, url: file.url! })}><Eye size={14} />Preview</button><a className="mb-icon-btn" aria-label={`Download ${file.fileName}`} title="Download" href={fileHref(`${file.url}${file.url.includes('?') ? '&' : '?'}download=true`)}><Download size={15} /></a></span>}
                </li>)}</ul></div>}
                <section className="mb-intake" aria-label="Candidate intake">
                  <header><span className="mb-intake-icon"><UserCheck size={16} /></span><h3>Candidate documents</h3></header>
                  <div className="mb-intake-actions">
                    {admin && open.attachments.length > 0 && <Button size="sm" disabled={busy || jobLoading || checkingJobs} onClick={() => setBundleSource(open.id)}>Review people in documents</Button>}
                    {open.documentBundle?.candidateIds.map((id) => <Button key={id} size="sm" disabled={openingCandidate} onClick={() => void openCandidate(id)}>{store.candidates.find((candidate) => candidate.id === id)?.name || 'Open candidate'}</Button>)}
                    {open.candidateId && !open.documentBundle && <Button size="sm" disabled={openingCandidate} onClick={() => void openCandidate(open.candidateId!)}>Open candidate</Button>}
                    {canCreateFromAttachment && <Button size="sm" variant="primary" icon={<UserCheck size={14} />} disabled={busy || jobLoading || !!jobError || !!intakeIssue || open.attachments.length === 0} title={open.attachments.length === 0 ? 'This email has no attachment to read' : intakeIssue ?? (openJobs.length ? 'Try again: read the attachments and save the candidate' : 'Read the attachments and save the candidate')} onClick={() => void perform(`ingest:${open.id}`, () => api.inbox.ingest(open.id), 'We started reading the documents.')}>Create candidate from attachment</Button>}
                  </div>
                  {intakeIssue && <div className="mb-inline-alert is-warn"><p role="status">{intakeIssue}</p>{admin && openAccount && (openAccount.connectionStatus === 'disconnected' || openAccount.disconnectedAt) && <Button size="sm" disabled={busy} onClick={() => setConnect({ address: openAccount.address, provider: openAccount.provider })}>Reconnect mailbox</Button>}</div>}
                  {jobError && <p role="alert" className="mb-inline-alert is-warn">{jobError}</p>}
                  {openJobs.map((job) => <JobReport key={job.id} job={job} busy={busy} canRetry={admin} retryUnavailable={intakeIssue} opening={openingCandidate} onOpen={openCandidate} onRetry={() => perform(`retry:${job.id}`, () => api.inbox.retry(job.id), 'We will try reading the documents again.')} onReview={(body) => perform(`review:${job.id}`, () => api.post(`/inbox/jobs/${job.id}/review`, body), 'Review saved. The original email stays in history.')} />)}
                  {(noCandidateYet || canCreateFromAttachment) && <p className="mb-intake-hint">{open.attachments.length ? (openAccount && !openAccount.autoIngest ? 'Auto-import is off for this mailbox, so this email was not read automatically. Use Create candidate from attachment to read it now.' : 'No candidate has been made from this email yet. New attachments are read automatically, or you can create the candidate now.') : 'This email has no attachment, so there is nothing to turn into a candidate.'}</p>}
                </section>
              </div>
              <footer className="mb-reply-bar">{currentRole === 'drafts' ? <Button disabled={busy || !canOperate} icon={<Pencil size={14} />} onClick={() => createDraft('edit_draft', open)}>Edit draft</Button> : <><Button disabled={busy || !canOperate} icon={<Reply size={14} />} onClick={() => createDraft('reply', open)}>Reply</Button><Button disabled={busy || !canOperate} icon={<ReplyAll size={14} />} onClick={() => createDraft('reply_all', open)}>Reply all</Button><Button disabled={busy || !canOperate} icon={<Send size={14} />} onClick={() => createDraft('forward', open)}>Forward</Button></>}</footer>
            </> : <EmptyState icon={<MailOpen size={22} />} title="Choose a conversation" description="Open a message to read, reply or review its candidate documents." />}</section>
          </>}
        </div>
      </div></div>
    </>}
    {bundleSource && <DocumentBundleReview sourceType="email" sourceId={bundleSource} onClose={() => setBundleSource(null)} onCommitted={() => { refresh(); void store.refresh({ keepCurrent: true }) }} />}
    {connect !== null && <ConnectMailbox address={connect.address} provider={connect.provider} providers={inbox.data?.providers} close={() => setConnect(null)} connected={(next) => { setConnect(null); setManaging(false); selectAccount(next.id); store.notify(`${PROVIDER_LABEL[next.provider]} mailbox connected.`); refresh() }} />}
    {preview && <AttachmentPreview file={preview} onClose={() => setPreview(null)} />}
    {compose && <MailCompose key={compose.id} initial={compose} address={accounts.find((item) => item.id === compose.accountId)?.address ?? 'Mailbox'} close={() => { setCompose(null); bump() }} changed={() => {}} />}
    <Dialog open={newFolder} onClose={() => { if (!busy) setNewFolder(false) }} title="New folder" description={`Create a folder in ${account?.address ?? 'this mailbox'}. Everyone who uses this mailbox will see it.`} width="sm" footer={<><Button disabled={busy} onClick={() => setNewFolder(false)}>Cancel</Button><Button variant="primary" disabled={busy || !folderName.trim() || !liveMailbox} onClick={() => void perform('new-folder', async () => { const result = await api.post<{ folders: MailFolder[] }>(`/inbox/accounts/${accountId}/folders`, { name: folderName.trim() }); setFolders(result.folders); selectFolder(folderName.trim()); setNewFolder(false) }, 'Folder is ready.')}>{busy ? 'Creating…' : 'Create folder'}</Button></>}>
      <Field label="Folder name" required><Input value={folderName} maxLength={255} onChange={(event) => setFolderName(event.target.value)} autoFocus /></Field>{inbox.actionError && <p role="alert" className="mt-3 text-sm text-stamp-700">{inbox.actionError}</p>}
    </Dialog>
    <Dialog open={managing && admin} onClose={() => { if (!busy) setManaging(false) }} title="Your mailboxes" description="Connect as many mailboxes as you need, and choose how each one handles candidate documents." width="lg" footer={<><Button icon={<Plus size={15} />} disabled={busy} onClick={() => setConnect({ address: '' })}>Connect mailbox</Button><Button variant="primary" disabled={busy} onClick={() => setManaging(false)}>Done</Button></>}>
      <div className="mb-acct-dialog">{accounts.map((item) => {
        const live = isLiveAccount({ ...item, connectionStatus: 'connected' })
        const state = item.connectionStatus.replaceAll('_', ' ')
        return <section key={item.id} className="mb-acct-card">
          <header>
            <ProviderMark provider={item.provider} off={item.connectionStatus === 'disconnected'} />
            <div className="min-w-0 flex-1"><h3 title={item.address}>{item.address}</h3><p>{PROVIDER_LABEL[item.provider]}</p></div>
            <Badge tone={item.configured && item.connectionStatus !== 'disconnected' ? 'green' : 'neutral'} variant="dot">{item.configured ? 'Ready' : 'Setup needed'}</Badge>
          </header>
          <dl className="mb-kv">
            <div><dt>Status</dt><dd className="capitalize">{state}</dd></div>
            <div><dt>Messages</dt><dd>{item.messageCount ?? 0}</dd></div>
            <div><dt>Unread</dt><dd>{item.unreadCount ?? 0}</dd></div>
            <div><dt>Last checked</dt><dd>{item.lastSyncAt ? smartTime(item.lastSyncAt) : 'Not yet'}</dd></div>
          </dl>
          {item.lastError && <p role="status" className="mb-inline-alert is-warn">{item.lastError}</p>}
          {live ? <>
            <div className="mb-acct-switch"><Switch checked={item.autoIngest} disabled={busy || item.connectionStatus === 'disconnected'} label={`Auto-import for ${item.address}`} description="Create candidates from new attachments automatically. You can still read and send mail when this is off." onChange={(enabled) => void perform(`auto:${item.id}`, () => api.inbox.setAutoIngest(item.id, enabled), `Auto-import turned ${enabled ? 'on' : 'off'}.`)} /></div>
            <IntakeAcknowledgements sourceKind="email" channelId={item.id} channelLabel={item.address} canManage={admin} />
            <HistoryImport accountId={item.id} disabled={busy || item.connectionStatus === 'disconnected'} />
            <div className="mb-acct-actions">{item.connectionStatus !== 'disconnected'
              ? <><Button size="sm" disabled={busy} onClick={() => void perform(`sync:${item.id}`, () => api.post(`/inbox/accounts/${item.id}/sync`), 'Checking for new mail.')}>Check for new mail</Button><Button size="sm" variant="danger" disabled={busy} onClick={() => void perform(`disconnect:${item.id}`, () => api.post(`/inbox/accounts/${item.id}/disconnect`), 'Mailbox disconnected. Messages, drafts and candidate documents are kept.')}>Disconnect</Button></>
              : <Button size="sm" disabled={busy} onClick={() => setConnect({ address: item.address, provider: item.provider })}>Reconnect</Button>}</div>
          </> : <p className="mb-acct-note">Old messages and candidate documents can still be read. Connect this mailbox again to get new mail.</p>}
        </section>
      })}{!accounts.length && <p className="text-sm text-ink-600">No mailboxes connected yet.</p>}{inbox.actionError && <p role="alert" className="text-sm text-stamp-700">{inbox.actionError}</p>}</div>
    </Dialog>
  </div>
}
