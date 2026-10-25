import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCheck, RefreshCw } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { liveInterval } from '@/shared/state/live'
import { smartTime } from '@/shared/lib/format'
import { Button, Field, Select, Textarea } from '@/shared/ui/primitives'

type SourceKind = 'email' | 'whatsapp'
interface Attempt {
  id: string; sourceId: string; recipient: string; body: string; subject?: string
  status: 'pending' | 'sending' | 'sent' | 'unknown' | 'failed' | 'suppressed' | 'paused'
  createdAt: string; sentAt?: string; lastError?: string; providerMessageId?: string; canRetry: boolean
}
interface TemplateChoice { name: string; language: string; parameters: string[] }
interface ApprovedTemplate { id: string; name: string; language: string; status: string; supported: boolean; parameterCount: number; body: string }
interface Snapshot {
  sourceKind: SourceKind; channelId: string; enabled: boolean; body: string; enabledAt?: string
  connected: boolean; workerEnabled: boolean; attempts: Attempt[]
  template?: TemplateChoice | null; templateAvailable?: boolean; templateValues?: Record<string, string>
}
const LABELS: Record<Attempt['status'], string> = {
  pending: 'Queued', sending: 'Sending', sent: 'Submitted to provider', unknown: 'Delivery unconfirmed',
  failed: 'Not sent', suppressed: 'Skipped', paused: 'Paused',
}

interface Props {
  sourceKind: SourceKind; channelId: string; channelLabel?: string; canManage: boolean
}
export function IntakeAcknowledgements(props: Props) {
  return <AcknowledgementPanel key={`${props.sourceKind}/${props.channelId}`} {...props} />
}
function AcknowledgementPanel({ sourceKind, channelId, channelLabel, canManage }: Props) {
  const store = useStore()
  const [data, setData] = useState<Snapshot | null>(null)
  const [body, setBody] = useState('')
  const [previewed, setPreviewed] = useState(false)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [resolving, setResolving] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [providerChecked, setProviderChecked] = useState(false)
  const [templates, setTemplates] = useState<ApprovedTemplate[]>([])
  const [templateKey, setTemplateKey] = useState('')
  const [templateValues, setTemplateValues] = useState<string[]>([])
  const scope = `${sourceKind}/${channelId}`
  const active = useRef(scope)
  active.current = scope
  const mounted = useRef(true)
  const sequence = useRef(0)
  const writing = useRef(false)
  const valid = useCallback((key: string) => mounted.current && active.current === key && store.isCurrentSession(), [store.isCurrentSession])
  const read = useCallback(async (initialize = false) => {
    const request = ++sequence.current
    try {
      const result = await api.get<Snapshot>(`/intake-acknowledgements/${scope}`)
      if (!valid(scope) || request !== sequence.current) return
      setData(result)
      if (initialize) {
        setBody(result.body)
        setTemplateKey(result.template ? `${result.template.name}|${result.template.language}` : '')
        setTemplateValues(result.template?.parameters ?? [])
      }
      setError('')
    } catch (cause) {
      if (valid(scope) && request === sequence.current) setError(cause instanceof Error ? cause.message : 'Acknowledgement settings could not load.')
    } finally {
      if (valid(scope) && request === sequence.current) setLoading(false)
    }
  }, [scope, valid])
  useEffect(() => {
    mounted.current = true
    setData(null); setLoading(true); setBody(''); setPreviewed(false); setError(''); setNotice(''); setResolving(null)
    void read(true)
    return () => { mounted.current = false; sequence.current += 1 }
  }, [read])
  useEffect(() => {
    if (!open || sourceKind !== 'whatsapp' || !data?.templateAvailable) return
    let stale = false
    void api.get<{ templates: ApprovedTemplate[] }>(`/whatsapp/templates?phone_number_id=${encodeURIComponent(channelId)}`)
      .then(result => { if (!stale) setTemplates(result.templates) }).catch(() => { /* the plain text still works */ })
    return () => { stale = true }
  }, [open, sourceKind, channelId, data?.templateAvailable])
  useEffect(() => {
    if (!open) return
    return liveInterval(() => {
      if (document.visibilityState === 'visible' && !writing.current) void read()
    }, 10000, ['mail', 'whatsapp'])
  }, [open, read])
  const mutate = async (work: () => Promise<unknown>, success: string) => {
    if (writing.current) return
    writing.current = true; setBusy(true); setError(''); setNotice('')
    sequence.current += 1
    try {
      await work()
      if (!valid(scope)) return
      setNotice(success); setResolving(null)
      await read()
    } catch (cause) {
      if (valid(scope)) setError((cause instanceof Error ? cause.message : 'The change could not be confirmed.') + ' Refresh status before repeating the action.')
    } finally {
      writing.current = false
      if (valid(scope)) setBusy(false)
    }
  }
  const chosen = templates.find(item => `${item.name}|${item.language}` === templateKey)
  const valueChoices = Object.entries(data?.templateValues ?? {})
  const templateReady = !templateKey || (!!chosen && templateValues.length === chosen.parameterCount && templateValues.every(Boolean))
  const save = (enabled: boolean) => mutate(() => api.patch(`/intake-acknowledgements/${scope}`, {
    enabled, body: enabled ? body.trim() : data!.body, previewedBody: previewed ? body.trim() : undefined,
    ...(enabled && sourceKind === 'whatsapp' && data?.templateAvailable ? { template: templateKey && chosen ? { name: chosen.name, language: chosen.language, parameters: templateValues } : null } : {}),
  }), enabled ? 'Acknowledgements enabled for newly received, successfully saved intake.' : 'Acknowledgements turned off. Already sending messages retain their delivery status.')
  return <details className="rounded-control border border-rule bg-white p-4" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer text-sm font-semibold text-ink">
      <span className="inline-flex flex-wrap items-center gap-2"><CheckCheck className="h-4 w-4 text-navy-600" />
        Intake acknowledgements <span className="rounded-full bg-field px-2 py-0.5 text-xs font-medium">{data ? data.enabled ? 'On' : 'Off' : 'Settings'}</span>
      </span>
    </summary>
    <div className="mt-4 space-y-4">
      <p className="text-sm text-ink-600">A short confirmation from {channelLabel || 'this channel'} after candidate documents are saved. One confirmation per incoming message, including a reviewed document containing several people.</p>
      <p className="text-xs text-ink-500">Pending review, failed intake and old messages are not acknowledged. Email mailing lists and automated senders are skipped. WhatsApp acknowledgements must fit the customer reply window.</p>
      {loading && <p role="status" className="text-sm text-ink-500">Loading acknowledgement settings…</p>}
      {error && <p role="alert" className="rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
      {notice && <p role="status" className="text-sm text-navy-700">{notice}</p>}
      <Button size="sm" icon={<RefreshCw className="h-4 w-4" />} disabled={busy || loading} onClick={() => void read(!data)}>Refresh status</Button>
      {data && <>
        {!data.connected && <p className="text-sm text-amber-900">This channel is disconnected. New acknowledgements cannot be sent until it is reconnected.</p>}
        {!data.workerEnabled && <p className="text-sm text-amber-900">The channel worker is disabled on the server. Enabling this setting alone does not send queued confirmations.</p>}
        {data.enabledAt && <p className="text-xs text-ink-500">Current activation: {smartTime(data.enabledAt)}. Earlier receipts will not be added to the queue.</p>}
        {canManage ? <div className="space-y-3 border-t border-rule-soft pt-4">
          <Field label="Acknowledgement message" hint="Plain text, up to 2,000 characters. This confirms receipt; it does not promise a job or selection.">
            <Textarea rows={4} maxLength={2000} value={body} disabled={busy} onChange={(event) => { setBody(event.target.value); setPreviewed(false) }} />
          </Field>
          {sourceKind === 'whatsapp' && data.templateAvailable && <div className="space-y-3">
            <Field label="Approved WhatsApp template (optional)" hint="Use this when the 24-hour reply time may have ended. Leave it empty to send the message above as plain text.">
              <Select value={templateKey} disabled={busy} onChange={event => {
                const next = templates.find(item => `${item.name}|${item.language}` === event.target.value)
                setTemplateKey(event.target.value); setTemplateValues(next ? Array(next.parameterCount).fill('first_name') : [])
              }}>
                <option value="">No template: send the message above</option>
                {templates.map(item => <option key={item.id} value={`${item.name}|${item.language}`} disabled={!item.supported || item.status !== 'APPROVED'}>{item.name} · {item.language}{!item.supported ? ' · not supported' : item.status !== 'APPROVED' ? ` · ${item.status}` : ''}</option>)}
              </Select>
            </Field>
            {chosen && <>
              {templateValues.map((value, index) => <Field key={index} label={`What goes into value ${index + 1}`}>
                <Select value={value} disabled={busy} onChange={event => setTemplateValues(current => current.map((item, i) => i === index ? event.target.value : item))}>
                  {valueChoices.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                </Select>
              </Field>)}
              <p className="text-xs text-ink-500">Template text: {chosen.body}</p>
            </>}
            {!templates.length && <p className="text-xs text-ink-500">No templates yet. An admin can update the approved templates from the WhatsApp chat.</p>}
          </div>}
          <div className="rounded-control border border-rule bg-field/40 p-3">
            <p className="mb-2 text-xs font-semibold text-ink-600">Sender preview</p>
            <p className="whitespace-pre-wrap break-words text-sm text-ink-700">{body.trim() || 'Enter the confirmation message above.'}</p>
          </div>
          <label className="flex items-start gap-2 text-sm text-ink-700">
            <input className="mt-1" type="checkbox" checked={previewed} disabled={busy || body.trim().length < 10} onChange={(event) => setPreviewed(event.target.checked)} />
            I reviewed this exact message and approve automatic confirmations for new saved intake.
          </label>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" disabled={busy || !data.connected || !previewed || body.trim().length < 10 || !templateReady} onClick={() => void save(true)}>{busy ? 'Saving…' : data.enabled ? 'Save approved message' : 'Enable acknowledgements'}</Button>
            {data.enabled && <Button disabled={busy} onClick={() => void save(false)}>Turn off acknowledgements</Button>}
          </div>
        </div> : <p className="text-sm text-ink-500">An administrator can preview and change this acknowledgement.</p>}
        <div className="border-t border-rule-soft pt-4">
          <h3 className="text-sm font-semibold">Recent confirmations</h3>
          <p className="mt-1 text-xs text-ink-500">Latest 20 eligible receipt decisions. Provider acceptance is shown separately from confirmed delivery in WhatsApp conversations.</p>
          {!data.attempts.length && <p className="mt-3 text-sm text-ink-500">No confirmations yet. New successful intake appears here after acknowledgements are enabled.</p>}
          <div className="mt-3 space-y-3">{data.attempts.map((row) => <div key={row.id} className="rounded-control border border-rule-soft p-3">
            <div className="flex flex-wrap items-center justify-between gap-2"><span className="break-all text-sm font-medium">{row.recipient || 'Sender unavailable'}</span><span className="rounded-full bg-field px-2 py-1 text-xs">{LABELS[row.status]}</span></div>
            <p className="mt-1 text-xs text-ink-500">{smartTime(row.sentAt || row.createdAt)}</p>
            {row.lastError && <p className="mt-2 break-words text-sm text-amber-900">{row.lastError}</p>}
            <details className="mt-2 text-xs text-ink-600"><summary className="cursor-pointer">View saved confirmation</summary><p className="mt-2 whitespace-pre-wrap break-words">{row.body}</p>{row.providerMessageId && <p className="mt-2 break-all">Provider reference: {row.providerMessageId}</p>}</details>
            {canManage && row.canRetry && <Button className="mt-3" size="sm" disabled={busy || !data.connected || !data.enabled} onClick={() => void mutate(() => api.post(`/intake-acknowledgements/attempts/${row.id}/retry`), 'The same confirmation is queued again. Its receipt identifier is preserved.')}>Retry unsent confirmation</Button>}
            {canManage && row.status === 'unknown' && sourceKind === 'email' && <Button className="mt-3" size="sm" disabled={busy} onClick={() => { setResolving(row.id); setReason(''); setProviderChecked(false) }}>Review uncertain delivery</Button>}
            {sourceKind === 'whatsapp' && ['unknown', 'failed'].includes(row.status) && <p className="mt-2 text-sm text-ink-600">Open this sender’s WhatsApp conversation to inspect the outgoing message. An uncertain send is never automatically retried.</p>}
            {resolving === row.id && <div className="mt-3 space-y-3 rounded-control bg-field p-3">
              <p className="text-sm">Check the mailbox provider’s Sent folder using the provider reference above. Resolving this status does not send another message.</p>
              <Field label="Provider check notes"><Textarea value={reason} maxLength={1000} disabled={busy} onChange={(event) => setReason(event.target.value)} /></Field>
              <label className="flex items-start gap-2 text-sm"><input className="mt-1" type="checkbox" checked={providerChecked} disabled={busy} onChange={(event) => setProviderChecked(event.target.checked)} />I checked the provider before resolving this delivery.</label>
              <div className="flex flex-wrap gap-2">{(['sent', 'stop'] as const).map((outcome) => <Button key={outcome} size="sm" disabled={busy || !providerChecked || reason.trim().length < 8} onClick={() => void mutate(() => api.post(`/intake-acknowledgements/attempts/${row.id}/resolve`, { outcome, providerChecked, reason: reason.trim() }), 'Delivery status resolved. No new message was sent.')}>{outcome === 'sent' ? 'Confirm provider sent it' : 'Stop without resending'}</Button>)}<Button size="sm" disabled={busy} onClick={() => setResolving(null)}>Cancel</Button></div>
            </div>}
          </div>)}</div>
        </div>
      </>}
    </div>
  </details>
}
