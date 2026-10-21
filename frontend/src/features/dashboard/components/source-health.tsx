import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowUpRight } from 'lucide-react'
import { api, type InboxHealth } from '@/shared/api/client'
import { ago } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'

type WhatsAppHealth = {
  enabled: boolean; configured: boolean
  counts: Record<'received' | 'pending' | 'processing' | 'retry' | 'needs_review' | 'completed' | 'ignored' | 'failed', number>
  channels: { id: string; displayNumber?: string; enabled: boolean; autoIngest: boolean; lastReceivedAt?: string; workerSeenAt?: string; lastError?: string }[]
}

/** Admin-only summary endpoint: no mailbox bodies or extraction payloads. */
export function SourceHealth() {
  const store = useStore()
  const navigate = useNavigate()
  const [data, setData] = useState<InboxHealth | null>(null)
  const [error, setError] = useState('')
  const [whatsapp, setWhatsapp] = useState<WhatsAppHealth | null>(null)
  const [whatsappError, setWhatsappError] = useState('')
  const current = useRef(store)
  current.current = store

  useEffect(() => {
    let live = true
    let pending: AbortController | null = null
    const read = async () => {
      const session = current.current
      if (!live || pending || document.visibilityState !== 'visible' || !session.isCurrentSession()) return
      const controller = new AbortController()
      pending = controller
      try {
        const [mailResult, whatsappResult] = await Promise.allSettled([
          api.inbox.health({ signal: controller.signal }),
          api.get<WhatsAppHealth>('/whatsapp/health', { signal: controller.signal }),
        ])
        if (!live || !session.isCurrentSession()) return
        if (mailResult.status === 'fulfilled') { setData(mailResult.value); setError('') }
        else if (!controller.signal.aborted) setError(mailResult.reason instanceof Error ? mailResult.reason.message : 'Email status is unavailable.')
        if (whatsappResult.status === 'fulfilled') { setWhatsapp(whatsappResult.value); setWhatsappError('') }
        else if (!controller.signal.aborted) setWhatsappError(whatsappResult.reason instanceof Error ? whatsappResult.reason.message : 'WhatsApp status is unavailable.')
      } catch (failure) {
        if (live && session.isCurrentSession() && !controller.signal.aborted) {
          setError(failure instanceof Error ? failure.message : 'Source status is unavailable.')
        }
      } finally { if (pending === controller) pending = null }
    }
    void read()
    const timer = window.setInterval(() => void read(), 10_000)
    const visible = () => void read()
    document.addEventListener('visibilitychange', visible)
    return () => {
      live = false
      pending?.abort()
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', visible)
    }
  }, [store.me.id])

  const heartbeat = data?.worker.lastHeartbeat ? Date.parse(data.worker.lastHeartbeat) : NaN
  const recent = Number.isFinite(heartbeat) && Date.now() - heartbeat <= 15 * 60_000
  const waiting = data ? data.counts.pending + data.counts.retry : 0
  const attention = data ? data.counts.needs_review + data.counts.failed : 0

  return (
    <section className="analytics-card" aria-labelledby="sources-title">
      <header className="analytics-card-heading">
        <h2 id="sources-title">Connected sources</h2>
        <button className="dashboard-icon-link" aria-label="Open inbox" onClick={() => navigate('/inbox')}><ArrowUpRight size={14} /></button>
      </header>
      {error && <p role="alert" className="px-4 py-2 text-xs text-amber-900">{error} {data && 'Showing last loaded status.'}</p>}
      {!data && !error && <p role="status" className="px-4 py-4 text-xs text-ink-500">Loading source status…</p>}
      {data && <>
        <h3 className="px-4 pt-4 text-xs font-semibold text-ink-700">Email accounts</h3>
        <p className="px-4 pt-3 text-xs text-ink-500">
          {!data.worker.enabled ? 'Automatic processing is disabled.' : recent ? 'Worker checked in recently.' : 'Worker activity is not confirmed. Open Inbox to check processing.'}
        </p>
        <div className="flex flex-wrap gap-x-4 gap-y-1 px-4 py-3 text-xs text-ink-600">
          <span>Queued: {waiting}</span><span>Processing: {data.counts.processing}</span>
          <span>Needs attention: {attention}</span><span>Completed: {data.counts.completed}</span>
        </div>
        {data.accounts.length > 0 ? <ul className="operational-list source-list">
          {data.accounts.map((account) => <li key={account.id}>
            <i className={account.autoIngest && !account.lastError && recent && data.worker.enabled ? 'is-connected' : ''} />
            <span><strong>{account.address}</strong><span>
              {account.lastError || (account.lastSyncAt ? `Synced ${ago(account.lastSyncAt)}` : 'No completed sync yet')}
              {!account.autoIngest && ' · Auto-ingest off'}
            </span></span>
          </li>)}
        </ul> : <p className="px-4 py-4 text-xs text-ink-500">No mailbox connected. Connect a server-configured account in Inbox.</p>}
        <button className="m-4 text-xs font-medium text-navy-700 underline" onClick={() => navigate('/inbox')}>Review messages and processing</button>
      </>}
      <div className="border-t border-rule">
        <h3 className="px-4 pt-4 text-xs font-semibold text-ink-700">WhatsApp Business</h3>
        {whatsappError && <p role="alert" className="px-4 py-2 text-xs text-amber-900">{whatsappError} {whatsapp && 'Showing last loaded status.'}</p>}
        {!whatsapp && !whatsappError && <p role="status" className="px-4 py-3 text-xs text-ink-500">Loading WhatsApp status…</p>}
        {whatsapp && <>
          {!whatsapp.configured ? <p className="px-4 pt-3 text-xs text-ink-500">WhatsApp is not connected.</p> : <>
            <div className="flex flex-wrap gap-x-4 gap-y-1 px-4 py-3 text-xs text-ink-600">
              <span>Queued: {whatsapp.counts.pending + whatsapp.counts.retry}</span><span>Processing: {whatsapp.counts.processing}</span>
              <span>Needs attention: {whatsapp.counts.needs_review + whatsapp.counts.failed}</span><span>Completed: {whatsapp.counts.completed}</span>
            </div>
            {whatsapp.channels.length ? <ul className="operational-list source-list">{whatsapp.channels.map((channel) => {
              const checked = channel.workerSeenAt ? Date.parse(channel.workerSeenAt) : NaN
              const live = Number.isFinite(checked) && Date.now() - checked <= 15 * 60_000
              return <li key={channel.id}><i className={channel.enabled && channel.autoIngest && !channel.lastError && live ? 'is-connected' : ''} /><span><strong>{channel.displayNumber || channel.id}</strong><span>
                {channel.lastError || (channel.lastReceivedAt ? `Last receipt ${ago(channel.lastReceivedAt)}` : 'No receipts yet')}
                {!channel.enabled ? ' · Disconnected' : live ? ' · Worker checked in' : ' · Worker activity unconfirmed'}{!channel.autoIngest && ' · Auto-ingest off'}
              </span></span></li>
            })}</ul> : <p className="px-4 py-3 text-xs text-ink-500">Server setup is ready. Connect the business number in WhatsApp to begin receiving messages.</p>}
          </>}
          <button className="m-4 text-xs font-medium text-navy-700 underline" onClick={() => navigate('/whatsapp')}>Review WhatsApp messages and processing</button>
        </>}
      </div>
    </section>
  )
}
