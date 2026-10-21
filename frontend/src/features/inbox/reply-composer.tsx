import { useEffect, useState } from 'react'
import { CornerUpLeft } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { smartTime } from '@/shared/lib/format'
import { Button, Textarea } from '@/shared/ui/primitives'
import { Badge } from '@/shared/ui/display'

interface Reply {
  id: string; body: string; status: 'pending' | 'sending' | 'sent' | 'unknown' | 'failed'
  lastError?: string; createdAt: string; sentAt?: string; requestKey: string
}

export function ReplyComposer({ messageId, address, disconnected }: { messageId: string; address: string; disconnected: boolean }) {
  const store = useStore()
  const cacheKey = `crm-mail-draft:${store.me.id}:${messageId}`
  const [savedDraft] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem(cacheKey) || 'null') as { body: string; key: string; attempted: boolean } | null } catch { return null }
  })
  const [body, setBody] = useState(savedDraft?.body ?? '')
  const [key, setKey] = useState(() => savedDraft?.key ?? crypto.randomUUID())
  const [rows, setRows] = useState<Reply[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [attempted, setAttempted] = useState(savedDraft?.attempted ?? false)
  const [result, setResult] = useState<Reply | null>(null)
  useEffect(() => {
    try {
      if (body || attempted) sessionStorage.setItem(cacheKey, JSON.stringify({ body, key, attempted }))
      else sessionStorage.removeItem(cacheKey)
    } catch { /* A browser that blocks storage still retains the on-screen draft. */ }
  }, [cacheKey, body, key, attempted])
  const load = async () => {
    const loaded = await api.get<Reply[]>(`/inbox/messages/${messageId}/replies`)
    if (!store.isCurrentSession()) return
    setRows(loaded)
    const own = loaded.find((row) => row.requestKey === key)
    if (own?.status === 'sent') { setBody(''); setKey(crypto.randomUUID()); setAttempted(false); setResult(null); setError('') }
    else if (own) setResult(own)
  }
  useEffect(() => {
    let live = true
    const refresh = async () => {
      try {
        const loaded = await api.get<Reply[]>(`/inbox/messages/${messageId}/replies`)
        if (live && store.isCurrentSession()) {
          setRows(loaded)
          const own = loaded.find((row) => row.requestKey === key)
          if (own?.status === 'sent') { setBody(''); setKey(crypto.randomUUID()); setAttempted(false); setResult(null) }
          else if (own) setResult(own)
        }
      } catch (cause) { if (live && store.isCurrentSession()) setError(cause instanceof Error ? cause.message : 'Reply history could not load.') }
    }
    void refresh()
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void refresh() }, 10000)
    return () => { live = false; window.clearInterval(timer) }
  }, [messageId, key, store.isCurrentSession])
  const send = async () => {
    if (!body.trim() || busy || disconnected) return
    setBusy(true); setError(''); setAttempted(true)
    try {
      const saved = await api.post<Reply>(`/inbox/messages/${messageId}/reply`, { body: body.trim(), request_key: key })
      if (!store.isCurrentSession()) return
      setResult(saved)
      setRows((current) => [...current.filter((row) => row.id !== saved.id), saved])
      if (saved.status === 'sent') {
        setBody(''); setKey(crypto.randomUUID()); setAttempted(false); setResult(null)
        store.notify('Reply sent from ' + address)
      }
    } catch (cause) {
      if (store.isCurrentSession()) setError((cause instanceof Error ? cause.message : 'The reply request could not be confirmed.') + ' Check reply status before trying again; this request keeps the same identifier.')
    } finally { setBusy(false) }
  }
  return <section className="inbox-reply border-t border-rule-soft bg-field/40 px-4 py-4">
    {rows.length > 0 && <div className="mb-4 space-y-3" aria-label="Reply history">{rows.map((row) => <div key={row.id} className="rounded-control border border-rule bg-white p-3">
      <div className="flex flex-wrap items-center gap-2"><Badge tone={row.status === 'sent' ? 'navy' : 'amber'}>{row.status === 'unknown' ? 'Delivery unconfirmed' : row.status}</Badge><span className="text-xs text-ink-500">{smartTime(row.sentAt || row.createdAt)}</span></div>
      <p className="mt-2 whitespace-pre-wrap break-words text-sm text-ink-700">{row.body}</p>
      {row.lastError && <p className="mt-2 text-xs text-amber-900">{row.lastError}</p>}
    </div>)}</div>}
    <p className="mb-2 flex items-center gap-2 text-xs text-ink-600"><CornerUpLeft className="h-4 w-4" />Reply from {address}</p>
    {disconnected && <p className="mb-3 text-sm text-amber-900">Reconnect this mailbox to reply. Existing conversations remain available.</p>}
    {error && <p role="alert" className="mb-3 text-sm text-stamp-700">{error}</p>}
    <Textarea rows={4} value={body} onChange={(event) => setBody(event.target.value)} disabled={busy || disconnected || attempted} placeholder="Write your reply" maxLength={50000} aria-label="Reply message" />
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {!attempted ? <Button variant="primary" disabled={busy || disconnected || !body.trim()} onClick={() => void send()}>Send reply</Button>
        : <Button disabled={busy} onClick={() => void load().catch((cause) => setError(cause.message))}>Check reply status</Button>}
      {attempted && !result && <Button disabled={busy || disconnected} onClick={() => void send()}>{busy ? 'Checking…' : 'Retry this request safely'}</Button>}
      {result?.status === 'failed' && <Button disabled={busy} onClick={() => { setAttempted(false); setResult(null); setKey(crypto.randomUUID()); setError('') }}>Edit and try again</Button>}
      {result?.status === 'unknown' && <Button disabled={busy} onClick={() => { if (window.confirm('Confirm you checked the mailbox Sent folder and need to send a new reply.')) { setAttempted(false); setResult(null); setKey(crypto.randomUUID()); setError('') } }}>I checked Sent mail; compose again</Button>}
    </div>
  </section>
}
