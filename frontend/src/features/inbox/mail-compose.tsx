import { useEffect, useRef, useState } from 'react'
import { Download, Paperclip, Send, Trash2 } from 'lucide-react'
import { api, fileHref } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Textarea } from '@/shared/ui/primitives'
import { Badge } from '@/shared/ui/display'
import { Dialog } from '@/shared/ui/overlay'
import type { MailAttempt, MailDraft, SendResult } from './mailbox-types'

type Form = { to: string; cc: string; bcc: string; subject: string; body: string }
const formOf = (draft: MailDraft): Form => ({ to: draft.to.join(', '), cc: draft.cc.join(', '), bcc: draft.bcc.join(', '), subject: draft.subject, body: draft.body })
const addresses = (text: string) => text.split(/[;,\n]/).map((value) => value.trim()).filter(Boolean)
const label = { compose: 'New message', reply: 'Reply', reply_all: 'Reply all', forward: 'Forward message', edit_draft: 'Edit Zoho draft' }

export function MailCompose({ initial, address, close, changed }: { initial: MailDraft; address: string; close: () => void; changed: () => void }) {
  const store = useStore()
  const [draft, setDraft] = useState(initial)
  const [form, setForm] = useState<Form>(() => formOf(initial))
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState('status')
  const [error, setError] = useState('')
  const [attempts, setAttempts] = useState<MailAttempt[]>([])
  const keyName = `crm-zoho-send:${store.me.id}:${initial.id}`
  const [requestKey, setRequestKey] = useState(() => { try { return sessionStorage.getItem(keyName) || crypto.randomUUID() } catch { return crypto.randomUUID() } })
  const [attempted, setAttempted] = useState(['sending', 'sent', 'unknown'].includes(initial.status))
  const [verifiedNoAttempt, setVerifiedNoAttempt] = useState(false)
  const sendVersion = useRef<number | null>((() => { try { const value = sessionStorage.getItem(`${keyName}:version`); return value ? Number(value) : null } catch { return null } })())
  const draftRef = useRef(draft); draftRef.current = draft
  const formRef = useRef(form); formRef.current = form
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty
  const busyRef = useRef(false)
  const autoPaused = useRef(false)
  const live = useRef(true)
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])
  useEffect(() => { try { sessionStorage.setItem(keyName, requestKey) } catch { /* The server also retains every send attempt. */ } }, [keyName, requestKey])
  const locked = attempted || ['sending', 'sent', 'unknown', 'discarded'].includes(draft.status)
  const accept = (next: MailDraft) => { if (live.current && store.isCurrentSession()) { draftRef.current = next; setDraft(next); if (!dirtyRef.current) { const values = formOf(next); formRef.current = values; setForm(values) } changed() } }
  const persist = async () => {
    const current = draftRef.current
    const values = { ...formRef.current }
    if (!dirtyRef.current && current.remoteState === 'synced') return current
    if (!dirtyRef.current && ['error', 'unknown', 'pending', 'syncing'].includes(current.remoteState)) {
      const recovered = await api.post<MailDraft>(`/inbox/drafts/${current.id}/sync`)
      accept(recovered); autoPaused.current = recovered.remoteState === 'error' || recovered.remoteState === 'unknown'
      return recovered
    }
    const next = await api.put<MailDraft>(`/inbox/drafts/${current.id}`, { lock_version: current.lockVersion, to: addresses(values.to), cc: addresses(values.cc), bcc: addresses(values.bcc), subject: values.subject, body: values.body })
    accept(next)
    if (JSON.stringify(formRef.current) === JSON.stringify(values)) { dirtyRef.current = false; setDirty(false); const savedValues = formOf(next); formRef.current = savedValues; setForm(savedValues) }
    autoPaused.current = next.remoteState === 'error'
    return next
  }
  const run = async (action: string, work: () => Promise<void>) => {
    if (busyRef.current || !store.isCurrentSession()) return
    busyRef.current = true; setBusy(action); setError('')
    try { await work() }
    catch (cause) { if (live.current && store.isCurrentSession()) { autoPaused.current = true; setError(cause instanceof Error ? cause.message : 'The mail action could not be completed. Your text remains available.') } }
    finally { busyRef.current = false; if (live.current) setBusy('') }
  }
  const status = async () => {
    const [next, history] = await Promise.all([api.get<MailDraft>(`/inbox/drafts/${initial.id}`), api.get<{ attempts: MailAttempt[] }>(`/inbox/drafts/${initial.id}/attempts`)])
    accept(next)
    if (live.current) {
      setAttempts(history.attempts)
      const active = history.attempts.find((item) => item.requestKey === requestKey)
      if (active) setAttempted(true)
      setVerifiedNoAttempt(!active && ['draft', 'failed'].includes(next.status) && !history.attempts.some((item) => ['pending', 'sending', 'unknown', 'partial'].includes(item.status)))
    }
  }
  useEffect(() => { void run('status', status) }, [])
  useEffect(() => {
    if (!dirty || locked || busy || autoPaused.current) return
    const timer = window.setTimeout(() => void run('save', async () => { await persist() }), 900)
    return () => window.clearTimeout(timer)
  }, [form, dirty, locked, busy])
  const change = (key: keyof Form, value: string) => { autoPaused.current = false; setForm((current) => ({ ...current, [key]: value })); setDirty(true) }
  const leave = () => { if (!busyRef.current) void run('close', async () => { if (dirtyRef.current && !locked) await persist(); close() }) }
  const currentAttempt = attempts.find((item) => item.requestKey === requestKey)
  const latest = currentAttempt ?? (['sent', 'sending', 'unknown'].includes(draft.status) ? attempts[0] : undefined)
  const deliveryLabel = latest?.status === 'partial' ? 'Some recipients were rejected' : draft.status === 'unknown' ? 'Delivery unconfirmed' : attempted && !currentAttempt && ['draft', 'failed'].includes(draft.status) ? 'Send status unconfirmed' : latest?.status ?? draft.status
  const send = () => void run('send', async () => {
    const before = draftRef.current
    const wasDirty = dirtyRef.current
    const intended = { to: addresses(formRef.current.to), cc: addresses(formRef.current.cc), bcc: addresses(formRef.current.bcc), subject: formRef.current.subject, body: formRef.current.body }
    const saved = attempted ? before : await persist()
    const actual = { to: saved.to, cc: saved.cc, bcc: saved.bcc, subject: saved.subject, body: saved.body }
    if (!attempted && (JSON.stringify(intended) !== JSON.stringify(actual) || (!wasDirty && saved.lockVersion !== before.lockVersion))) throw new Error('The saved draft changed while it was being checked. Review the displayed recipients, message and attachments, then send again.')
    if (!attempted) { sendVersion.current = saved.lockVersion; try { sessionStorage.setItem(`${keyName}:version`, String(saved.lockVersion)) } catch { /* Server attempts remain durable. */ } }
    if (sendVersion.current === null) throw new Error('Check send status to recover this request before retrying. Its original draft version is unavailable.')
    setVerifiedNoAttempt(false)
    setAttempted(true)
    const result = await api.post<SendResult>(`/inbox/drafts/${saved.id}/send`, { lock_version: sendVersion.current, request_key: requestKey })
    accept(result.draft)
    if (result.attempt) setAttempts((rows) => [result.attempt!, ...rows.filter((item) => item.id !== result.attempt!.id)])
  })
  return <Dialog open onClose={leave} title={label[draft.mode]} description={`From ${address}`} width="xl" footer={<div className="flex w-full flex-wrap items-center gap-2">
    {!locked && <Button variant="ghost" disabled={!!busy} icon={<Trash2 size={14} />} onClick={() => void run('discard', async () => { await api.del(`/inbox/drafts/${draft.id}?lock_version=${draft.lockVersion}`); changed(); close() })}>Discard</Button>}
    <span className="min-w-0 flex-1 text-xs text-ink-500" role="status">{busy === 'save' ? 'Saving draft…' : dirty ? 'Unsaved changes' : draft.status === 'sent' ? 'Sent' : 'Draft saved in CRM'}{!dirty && draft.remoteState === 'synced' && draft.status !== 'sent' && ' · Synced to Zoho'}</span>
    <Button disabled={!!busy} onClick={leave}>{locked ? 'Close' : 'Save & close'}</Button>
    {!locked && <><Button disabled={!!busy} onClick={() => void run('save', async () => { await persist() })}>Save draft</Button><Button variant="primary" icon={<Send size={14} />} disabled={!!busy || ![form.to, form.cc, form.bcc].some((value) => addresses(value).length)} onClick={send}>{busy === 'send' ? 'Sending…' : 'Send'}</Button></>}
  </div>}>
    <div className="space-y-4">
      {error && <div role="alert" className="rounded-control border border-stamp-100 bg-stamp-50 p-3 text-sm text-stamp-700">{error}{attempted && <p className="mt-2">Check send status before taking another action. The saved request identifier prevents a duplicate send.</p>}</div>}
      {draft.lastError && <p role="status" className="rounded-control bg-amber-50 p-3 text-sm text-amber-900">{draft.lastError} {draft.remoteState === 'error' && 'Your draft is saved in CRM. Save draft again to retry synchronization.'}</p>}
      {locked && <div className="space-y-3 rounded-control border border-rule bg-field p-3"><div className="flex flex-wrap items-center gap-2"><Badge tone={draft.status === 'sent' ? 'navy' : 'amber'}>{deliveryLabel}</Badge><Button size="sm" disabled={!!busy} onClick={() => void run('status', status)}>Check send status</Button>{draft.status === 'unknown' && <Button size="sm" disabled={!!busy} onClick={() => void run('reconcile', async () => { const result = await api.post<SendResult>(`/inbox/drafts/${draft.id}/reconcile`); accept(result.draft); if (result.attempt) setAttempts((rows) => [result.attempt!, ...rows.filter((item) => item.id !== result.attempt!.id)]) })}>Check Zoho Sent folder</Button>}</div>
        {latest?.lastError && <p className="text-sm text-amber-900">{latest.lastError}</p>}
        {!!latest?.refusedRecipients?.length && <p className="break-words text-xs text-ink-600">Rejected recipients: {latest.refusedRecipients.join(', ')}. Other recipients may already have received this message. Review before creating a follow-up.</p>}
        {currentAttempt?.status === 'failed' && <Button size="sm" disabled={!!busy} onClick={() => { setAttempted(false); setRequestKey(crypto.randomUUID()); sendVersion.current = null; setVerifiedNoAttempt(false); try { sessionStorage.removeItem(`${keyName}:version`) } catch { /* Optional local recovery metadata. */ } setError(''); autoPaused.current = false }}>Edit failed draft</Button>}
        {attempted && !currentAttempt && !['sent', 'sending', 'unknown'].includes(draft.status) && sendVersion.current !== null && <Button size="sm" disabled={!!busy} onClick={send}>Retry the same send request safely</Button>}
        {attempted && verifiedNoAttempt && !currentAttempt && ['draft', 'failed'].includes(draft.status) && <Button size="sm" disabled={!!busy} onClick={() => { setAttempted(false); setVerifiedNoAttempt(false); setError(''); autoPaused.current = false }}>Return to editing verified unsent draft</Button>}
        {draft.status === 'unknown' && <p className="text-xs text-ink-600">Delivery could not be confirmed. This message will not be automatically resent.</p>}
      </div>}
      <fieldset disabled={locked || (!!busy && busy !== 'save')} className="space-y-3">
        <Field label="To"><Input type="text" aria-label="Message recipients" value={form.to} onChange={(event) => change('to', event.target.value)} placeholder="name@example.com, another@example.com" /></Field>
        <div className="grid gap-3 sm:grid-cols-2"><Field label="Cc"><Input value={form.cc} onChange={(event) => change('cc', event.target.value)} /></Field><Field label="Bcc"><Input value={form.bcc} onChange={(event) => change('bcc', event.target.value)} /></Field></div>
        <Field label="Subject"><Input value={form.subject} onChange={(event) => change('subject', event.target.value)} maxLength={990} /></Field>
        <Field label="Message"><Textarea rows={12} aria-label="Message body" value={form.body} onChange={(event) => change('body', event.target.value)} maxLength={100000} placeholder="Write your message…" /></Field>
      </fieldset>
      {draft.attachments.length > 0 && <ul className="space-y-2" aria-label="Draft attachments">{draft.attachments.map((file) => <li key={file.id} className="flex min-w-0 items-center gap-3 rounded-control border border-rule p-3"><Paperclip size={15} className="shrink-0 text-ink-400" /><span className="min-w-0 flex-1 break-all text-sm">{file.name}<span className="ml-2 text-xs text-ink-400">{Math.ceil(file.sizeBytes / 1024)} KB</span></span><a href={fileHref(file.downloadUrl || file.url)} aria-label={`Download ${file.name}`}><Download size={15} /></a>{!locked && <Button size="sm" disabled={!!busy} onClick={() => void run('attachment', async () => { const saved = await persist(); accept(await api.del<MailDraft>(`/inbox/drafts/${saved.id}/attachments/${file.id}?lock_version=${saved.lockVersion}`)) })}>Remove</Button>}</li>)}</ul>}
      {!locked && <div><label className="inline-flex cursor-pointer items-center gap-2 rounded-control border border-rule px-3 py-2 text-sm font-medium text-ink-700"><Paperclip size={15} />Attach files<input type="file" multiple className="sr-only" aria-label="Attach files to message" disabled={!!busy} onChange={(event) => { const files = [...(event.target.files ?? [])]; event.target.value = ''; if (!files.length) return; void run('attachment', async () => { const current = draftRef.current; if (current.attachments.length + files.length > 20) throw new Error('A message can contain up to 20 attachments.'); if (current.attachments.reduce((total, file) => total + file.sizeBytes, 0) + files.reduce((total, file) => total + file.size, 0) > 18 * 1024 * 1024) throw new Error('Keep the combined attachment size within 18 MB.'); let saved = await persist(); for (const file of files) { const form = new FormData(); form.append('file', file); form.append('lock_version', String(saved.lockVersion)); saved = await api.form<MailDraft>(`/inbox/drafts/${saved.id}/attachments`, form); accept(saved) } }) }} /></label><p className="mt-2 text-xs text-ink-400">Up to 20 files, 18 MB combined. Your CRM draft list is private; drafts synchronized to Zoho are visible to other users of the mailbox.</p></div>}
      {!locked && error && <Button size="sm" disabled={!!busy} onClick={() => void run('reload', async () => { const saved = await api.get<MailDraft>(`/inbox/drafts/${draft.id}`); accept(saved); setForm(formOf(saved)); setDirty(false); autoPaused.current = false })}>Reload saved draft and replace unsaved text</Button>}
    </div>
  </Dialog>
}
