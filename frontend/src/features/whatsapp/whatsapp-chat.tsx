import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Check, CheckCheck, Clock3, MessageCircle, Paperclip, Plus, RefreshCw, Send, User, UserCheck, X } from 'lucide-react'
import { api, ApiError, fileHref } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { liveInterval } from '@/shared/state/live'
import { Button, Field, Input, SearchInput, Select, Textarea } from '@/shared/ui/primitives'
import { Badge } from '@/shared/ui/display'
import { Dialog } from '@/shared/ui/overlay'
import { dateTimeLabel, initials, smartTime } from '@/shared/lib/format'
import { FileIcon, hueOf } from '@/features/inbox/mail-parts'
import { canLinkCandidate, mergeChatMessages, openConversationBody, templatePreview, type ChatConversation, type ChatTemplate, type ConversationPage, type MessagePage } from './chat-types'
import { classifySendFailure } from './send-recovery'
import { leftLabel, windowLeftMs, windowTone } from './reply-window'
import './whatsapp-chat.css'

type Channel = { id: string; displayNumber?: string; enabled: boolean }
type Draft = { body: string; mode: 'text' | 'template'; templateId: string; parameters: string[] }
function WaAvatar({ name, large }: { name: string; large?: boolean }) { return <span className={`record-avatar wa-avatar avatar-hue-${hueOf(name)}${large ? ' is-lg' : ''}`} aria-hidden="true">{/^[+\d]/.test(name.trim()) ? <User size={large ? 18 : 16} strokeWidth={1.9} /> : initials(name) || '?'}</span> }
const emptyDraft = (): Draft => ({ body: '', mode: 'text', templateId: '', parameters: [] })
const messageOf = (error: unknown) => error instanceof Error ? error.message : 'That did not work. Try again.'

export function WhatsAppChat({ channels, revision = 0, initialConversation = '' }: { channels: Channel[]; revision?: number; initialConversation?: string }) {
  const store = useStore()
  return <WhatsAppChatSession key={store.me.id} channels={channels} revision={revision} initialConversation={initialConversation} />
}

function WhatsAppChatSession({ channels, revision, initialConversation }: { channels: Channel[]; revision: number; initialConversation: string }) {
  const store = useStore()
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const current = () => alive.current && store.isCurrentSession()
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [list, setList] = useState<{ key: string; data: ConversationPage } | null>(null)
  const [selected, setSelected] = useState(initialConversation)
  // A deep link (?conversation=ID) opens that conversation, also while this page is already showing.
  useEffect(() => { if (initialConversation) setSelected(initialConversation) }, [initialConversation])
  const [thread, setThread] = useState<{ id: string; data: MessagePage } | null>(null)
  const [version, setVersion] = useState(0)
  const [error, setError] = useState('')
  const [threadError, setThreadError] = useState('')
  const [opening, setOpening] = useState(false)
  const [linking, setLinking] = useState(false)
  const [newPhone, setNewPhone] = useState('')
  const [peer, setPeer] = useState('')
  const [candidateId, setCandidateId] = useState('')
  const [linkReason, setLinkReason] = useState('')
  const [actionError, setActionError] = useState('')
  const [busy, setBusy] = useState(false)
  const [olderBusy, setOlderBusy] = useState(false)
  const [templates, setTemplates] = useState<{ phone: string; rows: ChatTemplate[] } | null>(null)
  const [templateError, setTemplateError] = useState('')
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [files, setFiles] = useState<Record<string, File | undefined>>({})
  const [uncertain, setUncertain] = useState<Record<string, boolean>>({})
  const writeLock = useRef(false)
  const activeId = useRef(selected)
  activeId.current = selected
  const scroll = useRef<HTMLDivElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const listKey = `${store.me.id}:${query}:${page}`
  const visibleList = list?.key === listKey ? list.data : null
  const data = thread?.id === selected ? thread.data : null
  const conversation = data?.conversation
  const draft = drafts[selected] ?? emptyDraft()
  const file = files[selected]
  const availableTemplates = templates?.phone === conversation?.phoneNumberId ? templates?.rows ?? [] : []
  const chosenTemplate = availableTemplates.find(t => t.id === draft.templateId)
  const supervisor = canLinkCandidate(store.me.role)
  const updateDraft = (patch: Partial<Draft>) => setDrafts(value => ({ ...value, [selected]: { ...(value[selected] ?? emptyDraft()), ...patch } }))
  const intentSlot = (id: string) => `crm:whatsapp:send:${store.me.id}:${id}`
  const windowOpen = !!conversation?.serviceWindowOpen && !!conversation?.serviceWindowExpiresAt && new Date(conversation.serviceWindowExpiresAt).getTime() > Date.now()
  const canCompose = !!conversation?.canSend && !busy

  useEffect(() => {
    const controller = new AbortController()
    let reading = false
    let again = false // a change arrived while a read was running: that read may predate it
    setError('')
    const read = async () => {
      if (reading || document.visibilityState !== 'visible') return
      reading = true
      try {
        const result = await api.get<ConversationPage>(`/whatsapp/conversations?${new URLSearchParams({ q: query, page: String(page), page_size: '20' })}`, { signal: controller.signal })
        if (!controller.signal.aborted && current()) { setList({ key: listKey, data: result }); setError('') }
      } catch (e) { if (!controller.signal.aborted && current()) setError(messageOf(e)) }
      finally { reading = false; if (again && !controller.signal.aborted) { again = false; void read() } }
    }
    const delay = setTimeout(() => void read(), 250)
    const stopLive = liveInterval(() => void read(), 15_000, ['whatsapp', 'candidates'], { onChange: () => { if (reading) again = true; else void read() } })
    window.addEventListener('focus', read)
    return () => { controller.abort(); clearTimeout(delay); stopLive(); window.removeEventListener('focus', read) }
  }, [listKey, page, query, revision, version])

  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    let reading = false
    let again = false // a change arrived while a read was running: that read may predate it
    setThreadError('')
    const read = async () => {
      if (reading || writeLock.current || document.visibilityState !== 'visible') return
      reading = true
      try {
        const result = await api.get<MessagePage>(`/whatsapp/conversations/${selected}/messages`, { signal: controller.signal })
        if (!controller.signal.aborted && current()) {
          setThread(previous => ({ id: selected, data: { ...result, messages: mergeChatMessages(previous?.id === selected ? previous.data.messages : [], result.messages), nextBefore: previous?.id === selected ? previous.data.nextBefore : result.nextBefore } }))
          setThreadError('')
        }
      } catch (e) { if (!controller.signal.aborted && current()) setThreadError(messageOf(e)) }
      finally { reading = false; if (again && !controller.signal.aborted) { again = false; void read() } }
    }
    void read()
    const stopLive = liveInterval(() => void read(), 8_000, ['whatsapp'], { onChange: () => { if (reading) again = true; else void read() } })
    window.addEventListener('focus', read)
    return () => { controller.abort(); stopLive(); window.removeEventListener('focus', read) }
  }, [selected, revision, version])

  useEffect(() => {
    if (!conversation) return
    const controller = new AbortController()
    const phone = conversation.phoneNumberId
    setTemplateError('')
    void api.get<{ templates: ChatTemplate[] }>(`/whatsapp/templates?phone_number_id=${encodeURIComponent(phone)}`, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted && current()) setTemplates({ phone, rows: result.templates }) })
      .catch(e => { if (!controller.signal.aborted && current()) setTemplateError(messageOf(e)) })
    return () => controller.abort()
  }, [conversation?.phoneNumberId, version])

  useEffect(() => {
    // A refreshed browser can check its original durable intent without generating a new send.
    if (!selected) return
    try {
      const saved = JSON.parse(sessionStorage.getItem(intentSlot(selected)) || 'null')
      if (saved?.draft) {
        setDrafts(value => ({ ...value, [selected]: saved.draft }))
        setUncertain(value => ({ ...value, [selected]: true }))
      }
    } catch { /* An invalid local draft never authorizes a send. */ }
  }, [selected, store.me.id])

  useEffect(() => {
    if (!scroll.current || olderBusy) return
    scroll.current.scrollTop = scroll.current.scrollHeight
  }, [selected, data?.messages.at(-1)?.id])

  async function loadOlder() {
    if (!selected || !data?.nextBefore || olderBusy) return
    const id = selected
    const oldHeight = scroll.current?.scrollHeight ?? 0
    setOlderBusy(true)
    try {
      const result = await api.get<MessagePage>(`/whatsapp/conversations/${id}/messages?before=${encodeURIComponent(data.nextBefore)}`)
      if (current() && activeId.current === id) {
        setThread(previous => previous?.id === id ? { id, data: { ...previous.data, nextBefore: result.nextBefore, messages: mergeChatMessages(result.messages, previous.data.messages) } } : previous)
        requestAnimationFrame(() => { if (current() && activeId.current === id && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight - oldHeight })
      }
    } catch (e) { if (current() && activeId.current === id) setThreadError(messageOf(e)) }
    finally { if (current()) setOlderBusy(false) }
  }

  async function send() {
    if (!conversation || writeLock.current || !current() || (!conversation.canSend && !uncertain[conversation.id])) return
    const id = conversation.id
    const payload = draft.mode === 'template'
      ? { type: 'template', template: { name: chosenTemplate?.name, language: chosenTemplate?.language, parameters: draft.parameters } }
      : { type: 'text', body: draft.body.trim() }
    const signature = JSON.stringify({ payload, file: file ? [file.name, file.size, file.type, file.lastModified] : null })
    let saved: { key: string; signature: string; draft: Draft; hasFile: boolean } | null = null
    try { saved = JSON.parse(sessionStorage.getItem(intentSlot(id)) || 'null') } catch { /* Start a new explicit intent. */ }
    const recorded = () => {
      if (!current()) return
      try { sessionStorage.removeItem(intentSlot(id)) } catch { /* The durable request is still recoverable. */ }
      setDrafts(value => ({ ...value, [id]: emptyDraft() }))
      setFiles(value => ({ ...value, [id]: undefined }))
      setUncertain(value => ({ ...value, [id]: false }))
      if (activeId.current === id && fileInput.current) fileInput.current.value = ''
      setVersion(value => value + 1)
    }
    if (saved && uncertain[id]) {
      writeLock.current = true; setBusy(true); setThreadError('')
      try {
        await api.get(`/whatsapp/conversations/${id}/send-requests/${encodeURIComponent(saved.key)}`)
        if (!current()) return
        recorded()
        return
      } catch (error) {
        if (!current()) return
        if (!(error instanceof ApiError && error.status === 404)) { setThreadError(messageOf(error)); return }
        if (!conversation.canSend) { setThreadError('We cannot see this message yet. Check again in a moment. You cannot send a new one right now.'); return }
        if (saved.hasFile && !file) { setThreadError('We cannot see this message yet. Attach the same file again to check it safely.'); return }
      } finally { writeLock.current = false; if (current()) setBusy(false) }
    }
    if (!current() || !conversation.canSend) return
    if (saved && saved.signature !== signature) { setThreadError('An earlier message needs checking first. Put back the same file and text, then check it.'); return }
    const key = saved?.key ?? crypto.randomUUID()
    try { sessionStorage.setItem(intentSlot(id), JSON.stringify({ key, signature, draft, hasFile: !!file })) } catch { setThreadError('Your browser cannot keep this message safe while it sends. Allow site storage and try again.'); return }
    writeLock.current = true
    setBusy(true); setThreadError('')
    try {
      if (file) {
        const form = new FormData()
        form.set('requestKey', key); form.set('caption', draft.body.trim()); form.set('file', file)
        await api.form(`/whatsapp/conversations/${id}/send-media`, form)
      } else await api.post(`/whatsapp/conversations/${id}/send`, { requestKey: key, ...payload })
      recorded()
    } catch (e) {
      // Retain the same key after a lost response; the server returns the existing intent.
      const outcome = await classifySendFailure(e instanceof ApiError ? e.status : undefined,
        () => api.get(`/whatsapp/conversations/${id}/send-requests/${encodeURIComponent(key)}`), current)
      if (outcome === 'stale') return
      if (outcome === 'recorded') { recorded(); return }
      const rejected = outcome === 'rejected'
      if (rejected) { try { sessionStorage.removeItem(intentSlot(id)) } catch { /* Keep the visible draft. */ } }
      setUncertain(value => ({ ...value, [id]: !rejected }))
      if (activeId.current === id) setThreadError(messageOf(e) + (rejected ? '' : ' Check this message before you write another.'))
    } finally { writeLock.current = false; if (current()) { setBusy(false); setVersion(v => v + 1) } }
  }

  async function openConversation() {
    if (writeLock.current) return
    writeLock.current = true; setBusy(true); setActionError('')
    try {
      const result = await api.post<{ conversation: ChatConversation }>('/whatsapp/conversations/open', openConversationBody({ phoneNumberId: newPhone, peerNumber: peer, candidateId, role: store.me.role }))
      if (!current()) return
      setSelected(result.conversation.id); setOpening(false); setVersion(v => v + 1)
    } catch (e) { if (current()) setActionError(messageOf(e)) }
    finally { writeLock.current = false; if (current()) setBusy(false) }
  }

  const sendReady = draft.mode === 'template'
    ? !!chosenTemplate?.supported && chosenTemplate.status === 'APPROVED' && draft.parameters.length === chosenTemplate.parameterCount && draft.parameters.every(p => p.trim())
    : windowOpen && (!!file || !!draft.body.trim())

  return <section className={`wa-workspace ${selected ? 'wa-has-selection' : ''}`} aria-label="WhatsApp conversations">
    <aside className="wa-conversation-list">
      <div className="wa-list-heading"><div><h2>Conversations</h2><p>{visibleList ? `${visibleList.total} ${visibleList.total === 1 ? 'contact' : 'contacts'}` : 'Your business messages'}</p></div><Button size="sm" icon={<Plus size={16} />} onClick={() => { setNewPhone(channels.find(c => c.enabled)?.id ?? ''); setPeer(''); setCandidateId(''); setActionError(''); setOpening(true) }}>New chat</Button></div>
      <div className="wa-list-search"><SearchInput placeholder="Search contacts" value={query} onChange={(value) => { setQuery(value); setPage(1) }} /></div>
      {error && <p role="alert" className="px-4 pb-3 text-sm text-stamp-700">{error}<button className="ml-2 underline" onClick={() => setVersion(v => v + 1)}>Retry</button></p>}
      <div className="wa-contacts">{!visibleList ? <p role="status" className="p-5 text-sm text-ink-500">{error ? 'Conversations are unavailable right now.' : 'Loading conversations…'}</p> : !visibleList.conversations.length ? <div className="wa-empty"><MessageCircle size={30} /><h3>No conversations yet</h3><p>Incoming WhatsApp messages will appear here. Start a conversation with a known contact when you are ready.</p></div> : visibleList.conversations.map(item => { const left = windowLeftMs(item.serviceWindowExpiresAt); const replyOpen = item.serviceWindowOpen && left > 0; return <button key={item.id} className={`wa-contact ${selected === item.id ? 'is-selected' : ''}`} onClick={() => { if (!busy) { setSelected(item.id); setThreadError('') } }} aria-pressed={selected === item.id}>
        <WaAvatar name={item.displayName || item.peerNumber} /><span className="wa-contact-text"><span className="wa-contact-line"><span className="wa-contact-title" title={item.displayName || item.peerNumber}>{item.displayName || item.peerNumber}</span>{item.lastMessageAt && <time className="wa-contact-time">{smartTime(item.lastMessageAt)}</time>}</span><span className="wa-contact-preview" title={item.lastPreview || item.candidate?.name || undefined}>{item.lastPreview || item.candidate?.name || "No messages yet"}</span><span className="wa-contact-meta">{item.candidate ? <span className="wa-ref"><UserCheck size={11} />{item.candidate.ref}</span> : item.peerNumber}<span className="wa-win-text" data-tone={replyOpen ? windowTone(left) : 'over'}>{replyOpen ? leftLabel(left) : 'Reply time over'}</span></span></span>
      </button> })}</div>
      {visibleList && visibleList.total > 20 && <div className="wa-list-paging"><Button size="sm" disabled={page === 1} onClick={() => setPage(p => p - 1)}>Previous</Button><span>{page}</span><Button size="sm" disabled={page * 20 >= visibleList.total} onClick={() => setPage(p => p + 1)}>Next</Button></div>}
    </aside>
    <div className="wa-thread">
      {!selected ? <div className="wa-empty wa-welcome"><span className="wa-welcome-icon"><MessageCircle size={38} /></span><h2>Pick a chat</h2><p>Choose a contact to read their messages, send documents and follow up.</p><span>Documents candidates send are in the Candidate documents tab.</span></div> : <>
        <header className="wa-thread-heading"><button className="wa-mobile-back" aria-label="Back to conversations" onClick={() => { if (!busy) setSelected('') }}><ArrowLeft size={20} /></button><WaAvatar name={conversation?.displayName || conversation?.peerNumber || "WA"} large /><div className="min-w-0 flex-1"><h2>{conversation?.displayName || conversation?.peerNumber || 'Loading conversation…'}</h2><p>{conversation?.displayName && conversation.peerNumber}{conversation?.candidate && <> · <Link to={`/candidates/${conversation.candidate.id}`}>{conversation.candidate.ref} · {conversation.candidate.name}</Link></>}</p></div>{supervisor && conversation && <Button size="sm" disabled={busy} onClick={() => { setCandidateId(conversation.candidate?.id ?? ''); setLinkReason(''); setActionError(''); setLinking(true) }}>{conversation.candidate ? 'Change link' : 'Link candidate'}</Button>}<Button size="sm" aria-label="Refresh conversation" disabled={busy} onClick={() => setVersion(v => v + 1)}><RefreshCw size={15} /></Button></header>
        {conversation && <div className="wa-window"><Clock3 size={14} /><span>{!conversation.channelEnabled ? 'WhatsApp is disconnected. Reconnect it to send messages.' : windowOpen ? <>{conversation.canSend ? 'You can reply until' : 'The reply window is open until'} {dateTimeLabel(conversation.serviceWindowExpiresAt)} <b className="wa-win-left" data-tone={windowTone(windowLeftMs(conversation.serviceWindowExpiresAt))}>{leftLabel(windowLeftMs(conversation.serviceWindowExpiresAt))}</b></> : 'The reply time is over. Send an approved template message instead.'}</span>{conversation.canSend && <Badge tone={windowOpen ? 'green' : 'amber'} variant="dot">{windowOpen ? 'You can reply' : 'Template needed'}</Badge>}</div>}
        {threadError && <div role="alert" className="wa-error">{threadError}<button className="ml-2 underline" onClick={() => setVersion(v => v + 1)}>Refresh status</button></div>}
        <div className="wa-messages" ref={scroll} role="log" aria-label="Conversation messages">
          {!data ? <p role="status">Loading messages…</p> : <>{data.nextBefore && <div className="text-center"><Button size="sm" disabled={olderBusy} onClick={() => void loadOlder()}>{olderBusy ? 'Loading…' : 'Show older messages'}</Button></div>}{!data.messages.length && <div className="wa-empty"><p>No messages in this conversation yet.</p></div>}{data.messages.map(message => <article key={`${message.direction}:${message.id}`} className={`wa-message wa-message-${message.direction}`}>
            <div className="wa-bubble">{message.template && <p className="wa-template-label">Template · {message.template.name}</p>}{message.hasFile && message.fileUrl && <a className="wa-file" href={fileHref(message.fileUrl)} target="_blank" rel="noreferrer"><FileIcon name={message.fileName || 'file'} /><span>{message.fileName || 'Open attachment'}</span></a>}{message.body && <p className="wa-message-body">{message.body}</p>}{!message.body && !message.hasFile && <p>{message.type.replaceAll('_', ' ')} message</p>}<div className="wa-message-time"><time dateTime={message.at}>{dateTimeLabel(message.at)}</time>{message.direction === 'outbound' && <span className={`wa-delivery wa-delivery-${message.state}`}>{['read', 'delivered'].includes(message.state) ? <CheckCheck size={14} /> : ['sent', 'accepted'].includes(message.state) ? <Check size={14} /> : <Clock3 size={13} />}{message.state === 'unknown' ? 'Not confirmed' : message.state}</span>}</div>{message.lastError && <p className="wa-send-error">{message.lastError}</p>}{message.state === 'unknown' && <p className="wa-send-error">Check if it was delivered before you send again. It will not be sent again by itself.</p>}</div>
          </article>)}</>}
        </div>
        {conversation && <div className="wa-composer">
          {!conversation.canSend ? <div className="space-y-3"><p className="text-sm text-ink-500">{conversation.readOnlyReason || 'You can read this chat. Only the assigned recruiter or a manager can reply.'}</p>{uncertain[selected] && <Button size="sm" disabled={busy} onClick={() => void send()}>Check the earlier message</Button>}</div> : <>
            <div className="wa-composer-tools"><Button size="sm" variant={draft.mode === 'text' ? 'primary' : 'ghost'} disabled={!windowOpen || busy || uncertain[selected]} onClick={() => updateDraft({ mode: 'text' })}>Reply</Button><Button size="sm" variant={draft.mode === 'template' ? 'primary' : 'ghost'} disabled={busy || uncertain[selected]} onClick={() => { updateDraft({ mode: 'template' }); setFiles(v => ({ ...v, [selected]: undefined })) }}>Approved template</Button>{store.me.role === 'admin' && draft.mode === 'template' && <Button className="ml-auto" size="sm" disabled={busy} onClick={async () => { if (writeLock.current) return; writeLock.current = true; setBusy(true); setTemplateError(''); try { const result = await api.post<{ templates: ChatTemplate[] }>('/whatsapp/templates/sync', { phoneNumberId: conversation.phoneNumberId }); if (current()) setTemplates({ phone: conversation.phoneNumberId, rows: result.templates }) } catch (e) { if (current()) setTemplateError(messageOf(e)) } finally { writeLock.current = false; if (current()) setBusy(false) } }}>Update templates</Button>}</div>
            {templateError && <p role="alert" className="mb-3 text-sm text-stamp-700">{templateError}</p>}
            {draft.mode === 'template' ? <div className="space-y-3"><Field label="Approved template"><Select value={draft.templateId} disabled={busy || uncertain[selected]} onChange={e => { const row = availableTemplates.find(t => t.id === e.target.value); updateDraft({ templateId: e.target.value, parameters: Array(row?.parameterCount ?? 0).fill('') }) }}><option value="">Choose a template</option>{availableTemplates.map(t => <option key={t.id} value={t.id} disabled={!t.supported || t.status !== 'APPROVED'}>{t.name} · {t.language}{!t.supported ? ' · not supported' : t.status !== 'APPROVED' ? ` · ${t.status}` : ''}</option>)}</Select></Field>{chosenTemplate?.parameters.map((parameter, index) => <Field key={parameter.index} label={parameter.label || `Value ${index + 1}`}><Input value={draft.parameters[index] ?? ''} disabled={busy || uncertain[selected]} maxLength={1024} onChange={e => updateDraft({ parameters: draft.parameters.map((value, i) => i === index ? e.target.value : value) })} /></Field>)}{chosenTemplate && <div className="wa-template-preview"><span>Message preview</span><p>{templatePreview(chosenTemplate, draft.parameters)}</p></div>}{!availableTemplates.length && <p className="text-sm text-ink-500">No templates yet. An admin can update the approved templates from Meta.</p>}</div> : <><Textarea aria-label={file ? 'Attachment caption' : 'WhatsApp reply'} placeholder={windowOpen ? 'Write your message…' : 'Choose an approved template to carry on this chat.'} value={draft.body} maxLength={file ? 1024 : 4096} rows={3} disabled={!canCompose || !windowOpen || uncertain[selected]} onChange={e => updateDraft({ body: e.target.value })} />{file && <div className="wa-selected-file"><Paperclip size={15} /><span>{file.name}</span><button aria-label="Remove attachment" disabled={busy || uncertain[selected]} onClick={() => setFiles(v => ({ ...v, [selected]: undefined }))}><X size={16} /></button></div>}</>}
            <div className="wa-send-row"><input ref={fileInput} type="file" hidden accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.jpg,.jpeg,.png,.mp3,.ogg,.mp4,.3gp,.aac,.amr" onChange={e => { const next = e.target.files?.[0]; if (next) { setFiles(v => ({ ...v, [selected]: next })); setThreadError('') } }} /><Button size="sm" icon={<Paperclip size={16} />} disabled={!canCompose || (!windowOpen && !uncertain[selected]) || draft.mode !== 'text'} onClick={() => fileInput.current?.click()}>Attach file</Button><span>{uncertain[selected] ? 'Your earlier message is saved' : draft.mode === 'text' ? `${draft.body.length} / ${file ? 1024 : 4096}` : 'Check the preview before sending'}</span><Button variant="primary" icon={<Send size={16} />} disabled={!canCompose || (!sendReady && !uncertain[selected])} onClick={() => void send()}>{busy ? 'Sending…' : uncertain[selected] ? 'Check the earlier message' : 'Send message'}</Button></div>
          </>}
        </div>}
      </>}
    </div>
    <Dialog open={opening} onClose={() => { if (!busy) setOpening(false) }} title="Start a conversation" description="Type the contact’s WhatsApp number with the country code. For a new chat you must send an approved template message first, until they reply." footer={<><Button disabled={busy} onClick={() => setOpening(false)}>Cancel</Button><Button variant="primary" disabled={busy || !newPhone || !peer.trim()} onClick={() => void openConversation()}>{busy ? 'Opening…' : 'Open conversation'}</Button></>}>
      <div className="space-y-4">{actionError && <p role="alert" className="text-sm text-stamp-700">{actionError}</p>}<Field label="Business number"><Select value={newPhone} onChange={e => setNewPhone(e.target.value)}><option value="">Choose a connected number</option>{channels.filter(c => c.enabled).map(c => <option key={c.id} value={c.id}>{c.displayNumber || c.id}</option>)}</Select></Field><Field label="Contact’s WhatsApp number"><Input inputMode="tel" placeholder="+91 98765 43210" value={peer} onChange={e => setPeer(e.target.value)} maxLength={40} /></Field>{supervisor && <Field label="Linked candidate (optional)"><Select value={candidateId} onChange={e => setCandidateId(e.target.value)}><option value="">No candidate linked</option>{store.candidates.map(c => <option key={c.id} value={c.id}>{c.ref} · {c.name}</option>)}</Select></Field>}<p className="text-sm text-ink-500">Contact people who have agreed to receive recruitment messages from your business.</p></div>
    </Dialog>
    <Dialog open={linking} onClose={() => { if (!busy) setLinking(false) }} title="Link the right candidate" description="This connects the chat to a candidate and their current recruiter. It does not change the candidate’s details." footer={<><Button disabled={busy} onClick={() => setLinking(false)}>Cancel</Button><Button variant="primary" disabled={busy || !candidateId || linkReason.trim().length < 3} onClick={async () => { if (writeLock.current || !selected) return; writeLock.current = true; setBusy(true); setActionError(''); try { await api.patch(`/whatsapp/conversations/${selected}/candidate`, { candidateId, reason: linkReason }); if (current()) { setLinking(false); setVersion(v => v + 1) } } catch (e) { if (current()) setActionError(messageOf(e)) } finally { writeLock.current = false; if (current()) setBusy(false) } }}>Save link</Button></>}>
      <div className="space-y-4">{actionError && <p role="alert" className="text-sm text-stamp-700">{actionError}</p>}<Field label="Candidate"><Select value={candidateId} onChange={e => setCandidateId(e.target.value)}><option value="">Select a candidate</option>{store.candidates.map(c => <option key={c.id} value={c.id}>{c.ref} · {c.name}</option>)}</Select></Field><Field label="Why are you linking this?"><Textarea value={linkReason} onChange={e => setLinkReason(e.target.value)} maxLength={2000} /></Field></div>
    </Dialog>
  </section>
}

