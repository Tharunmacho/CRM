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
        else if (!controller.signal.aborted) setError(mailResult.reason instanceof Error ? mailResult.reason.message : 'We could not load the email status. Try again later.')
        if (whatsappResult.status === 'fulfilled') { setWhatsapp(whatsappResult.value); setWhatsappError('') }
        else if (!controller.signal.aborted) setWhatsappError(whatsappResult.reason instanceof Error ? whatsappResult.reason.message : 'We could not load the WhatsApp status. Try again later.')
      } catch (failure) {
        if (live && session.isCurrentSession() && !controller.signal.aborted) {
          setError(failure instanceof Error ? failure.message : 'We could not load the status. Try again later.')
        }
      } finally { if (pending === controller) pending = null }
    }
    void read()
    const timer = window.setInterval(() => void read(), 60_000)
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

  const stats = (queued: number, processing: number, needs: number, done: number) => <dl className="dr-intake-stats">
    <div><dt>Waiting</dt><dd>{queued}</dd></div><div><dt>In progress</dt><dd>{processing}</dd></div>
    <div className={needs ? 'is-warn' : ''}><dt>Needs a look</dt><dd>{needs}</dd></div><div><dt>Done</dt><dd>{done}</dd></div>
  </dl>

  return (
    <section className="dr-card dr-intake" aria-labelledby="sources-title">
      <header className="dr-card-head">
        <div><h2 id="sources-title">Connected email and WhatsApp</h2><p>When each email account and WhatsApp number last checked for new messages.</p></div>
        <button className="dr-icon-btn" aria-label="Open inbox" title="Open inbox" onClick={() => navigate('/inbox')}><ArrowUpRight size={15} /></button>
      </header>
      <div className="dr-intake-block">
        <h3>Email accounts</h3>
        {error && <p role="alert" className="dr-intake-note is-warn">{error} {data && 'Showing the status as it was last loaded.'}</p>}
        {!data && !error && <p role="status" className="dr-intake-note">Loading…</p>}
        {data && <>
          <p className="dr-intake-hint">
            {!data.worker.enabled ? 'Automatic import is turned off.' : recent ? 'The import service checked in recently.' : 'We cannot confirm the import service is running. Open Inbox to check.'}
          </p>
          {stats(waiting, data.counts.processing, attention, data.counts.completed)}
          {data.accounts.length > 0 ? <ul className="dr-source-list">
            {data.accounts.map((account) => <li key={account.id}>
              <i className={account.autoIngest && !account.lastError && recent && data.worker.enabled ? 'is-connected' : account.lastError ? 'is-error' : ''} />
              <span><strong>{account.address}</strong><small className={account.lastError ? 'is-error' : ''}>
                {account.lastError || (account.lastSyncAt ? `Checked ${ago(account.lastSyncAt)}` : 'Not checked yet')}
                {!account.autoIngest && ' · Automatic import is off'}
              </small></span>
            </li>)}
          </ul> : <p className="dr-intake-hint">No email account connected. Connect one in Inbox to start getting applications.</p>}
          <button className="dr-link" onClick={() => navigate('/inbox')}>Review emails<ArrowUpRight size={12} /></button>
        </>}
      </div>
      <div className="dr-intake-block">
        <h3>WhatsApp Business</h3>
        {whatsappError && <p role="alert" className="dr-intake-note is-warn">{whatsappError} {whatsapp && 'Showing the status as it was last loaded.'}</p>}
        {!whatsapp && !whatsappError && <p role="status" className="dr-intake-note">Loading…</p>}
        {whatsapp && <>
          {!whatsapp.configured ? <p className="dr-intake-hint">WhatsApp is not connected.</p> : <>
            {stats(whatsapp.counts.pending + whatsapp.counts.retry, whatsapp.counts.processing, whatsapp.counts.needs_review + whatsapp.counts.failed, whatsapp.counts.completed)}
            {whatsapp.channels.length ? <ul className="dr-source-list">{whatsapp.channels.map((channel) => {
              const checked = channel.workerSeenAt ? Date.parse(channel.workerSeenAt) : NaN
              const live = Number.isFinite(checked) && Date.now() - checked <= 15 * 60_000
              return <li key={channel.id}><i className={channel.enabled && channel.autoIngest && !channel.lastError && live ? 'is-connected' : channel.lastError ? 'is-error' : ''} /><span><strong>{channel.displayNumber || channel.id}</strong><small className={channel.lastError ? 'is-error' : ''}>
                {channel.lastError || (channel.lastReceivedAt ? `Last message ${ago(channel.lastReceivedAt)}` : 'No messages yet')}
                {!channel.enabled ? ' · Disconnected' : live ? ' · Import service running' : ' · Import service not confirmed'}{!channel.autoIngest && ' · Automatic import is off'}
              </small></span></li>
            })}</ul> : <p className="dr-intake-hint">Setup is ready. Connect the business number in WhatsApp to start receiving messages.</p>}
          </>}
          <button className="dr-link" onClick={() => navigate('/whatsapp')}>Review WhatsApp messages<ArrowUpRight size={12} /></button>
        </>}
      </div>
    </section>
  )
}
