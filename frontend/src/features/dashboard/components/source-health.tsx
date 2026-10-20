import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowUpRight } from 'lucide-react'
import { api, type InboxHealth } from '@/shared/api/client'
import { ago } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'

/** Admin-only summary endpoint: no mailbox bodies or extraction payloads. */
export function SourceHealth() {
  const store = useStore()
  const navigate = useNavigate()
  const [data, setData] = useState<InboxHealth | null>(null)
  const [error, setError] = useState('')
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
        const next = await api.inbox.health({ signal: controller.signal })
        if (!live || !session.isCurrentSession()) return
        setData(next)
        setError('')
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
    </section>
  )
}
