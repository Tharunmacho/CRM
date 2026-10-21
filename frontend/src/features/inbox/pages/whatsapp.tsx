import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Download, ExternalLink, MessageCircle, RefreshCw } from 'lucide-react'
import { absoluteApiHref, api, fileHref } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Badge, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { Dialog } from '@/shared/ui/overlay'
import { DataTable, type Column } from '@/shared/ui/table'
import { dateTimeLabel } from '@/shared/lib/format'

type Message = { id: string; phoneNumberId: string; senderNumber: string; senderName?: string; type: string; body?: string; fileName?: string; hasFile: boolean; receivedAt: string; status: string; attempts: number; lastError?: string; candidateId?: string }
type Inbox = { configured: boolean; configuredPhoneNumberId?: string; enabled: boolean; missingSettings: string[]; webhookPath: string; total: number; page: number; pageSize: number; messages: Message[]; channels: { id: string; displayNumber?: string; enabled: boolean; autoIngest: boolean; lastReceivedAt?: string; workerSeenAt?: string; lastError?: string }[] }

export default function WhatsAppPage() {
  const store = useStore()
  const navigate = useNavigate()
  const admin = store.me.role === 'admin'
  const [data, setData] = useState<Inbox | null>(null)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [selected, setSelected] = useState<Message | null>(null)
  const [candidateId, setCandidateId] = useState('')
  const [reason, setReason] = useState('')
  const [dialogError, setDialogError] = useState('')
  const configuredChannel = data?.channels.find((channel) => channel.id === data.configuredPhoneNumberId)
  useEffect(() => {
    const controller = new AbortController()
    let pending = false
    const read = async () => {
      if (pending || document.visibilityState !== 'visible') return
      pending = true
      try {
        const params = new URLSearchParams({ q: query, page: String(page), page_size: '20', ...(status ? { status } : {}) })
        const result = await api.get<Inbox>(`/whatsapp?${params}`, { signal: controller.signal })
        if (!controller.signal.aborted) { setData(result); setError('') }
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Messages could not be loaded') }
      finally { pending = false }
    }
    const delay = window.setTimeout(() => void read(), 250)
    const timer = window.setInterval(() => void read(), 15_000)
    return () => { controller.abort(); window.clearTimeout(delay); window.clearInterval(timer) }
  }, [query, status, page, revision, store.me.id])
  async function act(id: string, action: () => Promise<unknown>) {
    setBusy(id); setError('')
    try { await action(); setRevision((v) => v + 1) }
    catch (e) { setError(e instanceof Error ? e.message : 'The action could not be saved') }
    finally { setBusy('') }
  }
  const columns: Column<Message>[] = [
    { key: 'sender', header: 'Sender', render: (row) => <div><p className="font-medium text-ink">{row.senderName || row.senderNumber}</p><p className="text-xs text-ink-500">{row.senderNumber}</p></div> },
    { key: 'message', header: 'Message / document', render: (row) => <div className="max-w-sm"><p className="font-medium text-ink-700">{row.fileName || row.type}</p><p className="mt-1 line-clamp-2 text-xs text-ink-500">{row.body || 'No caption'}</p>{row.lastError && <p className="mt-1 text-xs text-stamp-700">{row.lastError}</p>}</div> },
    { key: 'received', header: 'Received', render: (row) => dateTimeLabel(row.receivedAt) },
    { key: 'status', header: 'Processing', render: (row) => <Badge tone={row.status === 'completed' ? 'navy' : row.status === 'needs_review' || row.status === 'failed' ? 'amber' : 'neutral'}>{row.status.replaceAll('_', ' ')}</Badge> },
    { key: 'actions', header: 'Actions', render: (row) => <div className="flex flex-wrap gap-2">
      {row.hasFile && <><a className="inline-flex items-center gap-1 text-xs font-medium text-navy-700" href={fileHref(`/api/whatsapp/messages/${row.id}/file`)} target="_blank" rel="noreferrer"><ExternalLink size={13} /> View</a><a className="text-navy-700" aria-label={`Download ${row.fileName}`} href={fileHref(`/api/whatsapp/messages/${row.id}/file?download=true`)}><Download size={14} /></a></>}
      {row.candidateId ? <Button size="sm" onClick={() => navigate(`/candidates/${row.candidateId}`)}>Candidate</Button> : admin && !['pending', 'processing', 'retry'].includes(row.status) && <><Button size="sm" disabled={!!busy || !row.hasFile} onClick={() => { setSelected(row); setCandidateId(''); setReason(''); setDialogError('') }}>Review</Button>{['received', 'needs_review', 'failed'].includes(row.status) && <Button size="sm" disabled={!!busy || !data?.channels.some((channel) => channel.id === row.phoneNumberId && channel.enabled)} onClick={() => void act(row.id, () => api.post(`/whatsapp/messages/${row.id}/retry`))}>{busy === row.id ? 'Queuing…' : 'Process'}</Button>}</>}
    </div> },
  ]
  return <div className="space-y-5">
    <PageHeader title="WhatsApp" eyebrow="Business conversations" description="Receive candidate documents, review extraction results and keep the original message connected to the profile." actions={<>{admin && data?.configured && !configuredChannel?.enabled && <Button variant="primary" disabled={!!busy} onClick={() => void act('connect', () => api.post('/whatsapp/channels/connect'))}>{busy === 'connect' ? 'Connecting…' : configuredChannel ? 'Reconnect WhatsApp' : 'Connect WhatsApp'}</Button>}<Button icon={<RefreshCw size={15} />} onClick={() => setRevision((v) => v + 1)}>Refresh</Button></>} />
    {error && <p role="alert" className="rounded-panel border border-stamp-100 bg-stamp-50 p-4 text-sm text-stamp-700">{error}</p>}
    {data && !data.configured && <Panel><div className="flex gap-3"><MessageCircle className="shrink-0 text-navy-600" /><div><h2 className="font-semibold text-ink">Connect WhatsApp Business</h2><p className="mt-2 text-sm text-ink-500">Your Meta Business phone and webhook need to be configured before new messages can arrive.</p>{admin && <details className="mt-3 text-sm"><summary className="cursor-pointer font-medium text-navy-700">Connection setup</summary><p className="mt-2 break-all text-xs text-ink-600">Callback: {absoluteApiHref(data.webhookPath)}</p><p className="mt-2 text-xs text-ink-600">{!data.enabled && 'Enable WHATSAPP_ENABLED on the server. '}{data.missingSettings.length > 0 && `Required server settings: ${data.missingSettings.join(', ')}.`} Keep credentials in server secrets.</p></details>}</div></div></Panel>}
    {data?.channels.map((channel) => <Panel key={channel.id}><div className="flex flex-wrap items-center gap-4"><div className="min-w-0 flex-1"><p className="font-semibold text-ink">{channel.displayNumber || channel.id}</p><p className="mt-1 text-xs text-ink-500">Last receipt: {dateTimeLabel(channel.lastReceivedAt)} · Worker: {dateTimeLabel(channel.workerSeenAt)}</p></div>{admin && channel.enabled && <><Button disabled={!!busy} onClick={() => void act(channel.id, () => api.patch(`/whatsapp/channels/${channel.id}`, { autoIngest: !channel.autoIngest }))}>{channel.autoIngest ? 'Pause automatic intake' : 'Enable automatic intake'}</Button><Button disabled={!!busy} onClick={() => void act(channel.id, () => api.post(`/whatsapp/channels/${channel.id}/disconnect`))}>Disconnect</Button></>}<Badge tone={channel.enabled && channel.autoIngest ? 'navy' : 'neutral'}>{!channel.enabled ? 'Disconnected' : channel.autoIngest ? 'Automatic intake' : 'Manual review'}</Badge></div><p className="mt-3 text-xs text-ink-500">{channel.enabled ? 'Disconnecting stops new receipts and pauses queued processing. Existing messages and documents stay available.' : 'New receipts are not stored and queued processing is paused. Reconnect to resume saved work; existing messages and documents remain available.'}</p>{channel.lastError && <p className="mt-3 text-sm text-stamp-700">{channel.lastError}</p>}</Panel>)}
    <Panel flush><PanelHeader title="Incoming messages" meta={`${data?.total ?? 0} matching messages`} /><div className="flex flex-wrap gap-3 border-b border-rule p-4"><Input aria-label="Search WhatsApp messages" className="min-w-48 flex-1" placeholder="Search sender, number or message" value={query} onChange={(e) => { setQuery(e.target.value); setPage(1) }} /><Select aria-label="Processing status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}><option value="">All statuses</option>{['received', 'pending', 'processing', 'retry', 'needs_review', 'completed', 'ignored', 'failed'].map((value) => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}</Select></div>
      {!data ? <p className="p-6 text-sm text-ink-500" role="status">Loading messages…</p> : <><DataTable rows={data.messages} columns={columns} rowKey={(row) => row.id} pageSize={20} /><div className="flex items-center justify-between gap-3 border-t border-rule p-4 text-sm"><span>Page {page} of {Math.max(1, Math.ceil(data.total / 20))}</span><div className="flex gap-2"><Button size="sm" disabled={page <= 1} onClick={() => setPage((v) => v - 1)}>Previous</Button><Button size="sm" disabled={page * 20 >= data.total} onClick={() => setPage((v) => v + 1)}>Next</Button></div></div></>}
    </Panel>
    <Dialog open={!!selected} onClose={() => { if (!busy) setSelected(null) }} title="Review the source message" description="Review the original document, then link it to the correct candidate. Existing profile fields will be preserved." footer={<><Button variant="ghost" disabled={!!busy} onClick={() => setSelected(null)}>Cancel</Button><Button variant="primary" disabled={!!busy || !candidateId || reason.trim().length < 3} onClick={async () => {
      if (!selected) return
      setBusy(selected.id); setDialogError('')
      try {
        await api.post(`/whatsapp/messages/${selected.id}/resolve`, { candidateId, reason })
        setSelected(null); setRevision((v) => v + 1)
        try { await store.refresh({ keepCurrent: true }) } catch { store.notify('Message linked. Refresh the workspace to see the candidate update.', 'warn') }
      }
      catch (e) { setDialogError(e instanceof Error ? e.message : 'Review could not be saved') }
      finally { setBusy('') }
    }}>{busy ? 'Saving…' : 'Link candidate'}</Button></>}>
      {dialogError && <p role="alert" className="mb-3 text-sm text-stamp-700">{dialogError}</p>}
      {selected?.hasFile && <a className="mb-4 inline-block text-sm text-navy-700 underline" href={fileHref(`/api/whatsapp/messages/${selected.id}/file`)} target="_blank" rel="noreferrer">Open original document</a>}
      <div className="space-y-4"><Field label="Candidate" required><Select value={candidateId} onChange={(e) => setCandidateId(e.target.value)}><option value="">Select the matching profile</option>{store.candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.ref} · {candidate.name}</option>)}</Select></Field><Field label="Review note" required><Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={2000} /></Field><p className="text-xs text-ink-500">For a new person, create their profile in Add Candidate with WhatsApp as the source, then return here to link the original message.</p></div>
    </Dialog>
  </div>
}
