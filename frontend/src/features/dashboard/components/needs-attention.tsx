import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CheckCheck, FileUp, Mail, MessageCircle, RefreshCw, RotateCw } from 'lucide-react'
import { api, type AttentionItem, type AttentionSource, type InboxAttention } from '@/shared/api/client'
import { ago } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { liveInterval } from '@/shared/state/live'
import { attentionDetail, attentionReason, hiddenCount, openPath, retryPath, SOURCE_LABEL } from '@/features/dashboard/attention'

const ICON: Record<AttentionSource, typeof Mail> = { email: Mail, whatsapp: MessageCircle, manual: FileUp }

/**
 * Failed and needs-review intake from every channel, newest first. Admin and manager
 * can read it; only admin sees Retry (the server also decides which rows are retryable).
 */
export function NeedsAttention() {
  const store = useStore()
  const navigate = useNavigate()
  const [data, setData] = useState<InboxAttention | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({})
  const current = useRef(store)
  current.current = store
  const pending = useRef<AbortController | null>(null)

  const load = useCallback(async () => {
    const session = current.current
    if (pending.current || document.visibilityState !== 'visible' || !session.isCurrentSession()) return
    const controller = new AbortController()
    pending.current = controller
    try {
      const result = await api.inbox.attention({ signal: controller.signal })
      if (controller.signal.aborted || !session.isCurrentSession()) return
      setData(result); setError('')
    } catch (failure) {
      if (!controller.signal.aborted && session.isCurrentSession()) {
        setError(failure instanceof Error ? failure.message : 'We could not load this list. Check your internet and try again.')
      }
    } finally { if (pending.current === controller) pending.current = null }
  }, [])

  useEffect(() => {
    void load()
    const stopLive = liveInterval(() => void load(), 60_000, ['mail', 'whatsapp', 'documents', 'candidates'], {
      onChange: () => { pending.current?.abort(); pending.current = null; void load() },
    })
    const visible = () => void load()
    document.addEventListener('visibilitychange', visible)
    return () => {
      pending.current?.abort(); pending.current = null
      stopLive()
      document.removeEventListener('visibilitychange', visible)
    }
  }, [load, store.me.id])

  async function retry(item: AttentionItem) {
    setBusy(item.id)
    setRowErrors(({ [item.id]: _cleared, ...rest }) => rest)
    try {
      await api.post(retryPath(item))
      store.notify(`We will try ${item.title} again.`)
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : 'We could not try again. Please wait a moment and try once more.'
      setRowErrors(previous => ({ ...previous, [item.id]: message }))
      store.notify(message, 'warn')
    } finally {
      setBusy('')
      pending.current?.abort(); pending.current = null
      void load()
    }
  }

  const items = data?.items ?? []
  const total = data?.totals.all ?? 0
  const hidden = hiddenCount(items.length, total)

  return (
    <section className="dr-card dr-intake" aria-labelledby="attention-title">
      <header className="dr-card-head">
        <div><h2 id="attention-title">Imports that need a look{total > 0 && <span className="dr-count">{total}</span>}</h2><p>Files we could not read, or that need a person to check them.</p></div>
        <button className="dr-icon-btn" aria-label="Refresh this list" title="Refresh" onClick={() => void load()}><RefreshCw size={15} /></button>
      </header>
      {error && <p role="alert" className="dr-intake-note is-warn">{error} {data && 'Showing the last loaded list.'}</p>}
      {!data && !error && <p role="status" className="dr-intake-note">Loading…</p>}
      {data && items.length === 0 && (
        <div className="dr-empty is-small"><span><CheckCheck size={18} /></span><strong>Nothing needs attention</strong><p>Emails, WhatsApp messages and uploaded files are being imported normally.</p></div>
      )}
      {items.length > 0 && <ul className="dr-intake-list">
        {items.map(item => {
          const Icon = ICON[item.source]
          const to = openPath(item)
          const failure = rowErrors[item.id]
          return <li key={item.id}>
            <span className={`dr-intake-icon${item.status === 'failed' ? ' is-failed' : ''}`} title={SOURCE_LABEL[item.source]}><Icon size={16} aria-hidden="true" /><span className="sr-only">{SOURCE_LABEL[item.source]}</span></span>
            <div className="dr-intake-copy">
              <strong>{item.title}</strong>
              <span>{attentionDetail(item)}</span>
              <span className={item.status === 'failed' ? 'dr-intake-reason is-failed' : 'dr-intake-reason'}>{attentionReason(item)}</span>
              {failure && <span role="alert" className="dr-intake-reason is-failed">{failure}</span>}
            </div>
            <div className="dr-intake-side">
              <span className="dr-intake-time">{ago(item.updatedAt)}</span>
              <div className="dr-intake-actions">
                {to && <button className="dr-mini" aria-label={`Open ${item.title}`} onClick={() => navigate(to)}>Open</button>}
                {data?.canRetry && item.retryable && <button className="dr-mini is-primary" aria-label={`Try ${item.title} again`} disabled={!!busy} onClick={() => void retry(item)}><RotateCw size={12} />{busy === item.id ? 'Trying…' : 'Try again'}</button>}
              </div>
            </div>
          </li>
        })}
      </ul>}
      {hidden > 0 && <p className="dr-intake-note">Showing the {items.length} newest of {total}. Fix these to see older ones.</p>}
    </section>
  )
}
