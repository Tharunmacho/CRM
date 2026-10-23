import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CheckCheck, FileUp, Mail, MessageCircle, RefreshCw, RotateCw } from 'lucide-react'
import { api, type AttentionItem, type AttentionSource, type InboxAttention } from '@/shared/api/client'
import { ago } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { Button } from '@/shared/ui/primitives'
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
        setError(failure instanceof Error ? failure.message : 'The attention list is unavailable.')
      }
    } finally { if (pending.current === controller) pending.current = null }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 30_000)
    const visible = () => void load()
    document.addEventListener('visibilitychange', visible)
    return () => {
      pending.current?.abort(); pending.current = null
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', visible)
    }
  }, [load, store.me.id])

  async function retry(item: AttentionItem) {
    setBusy(item.id)
    setRowErrors(({ [item.id]: _cleared, ...rest }) => rest)
    try {
      await api.post(retryPath(item))
      store.notify(`${item.title} queued for another attempt.`)
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : 'Retry failed.'
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
    <section className="analytics-card" aria-labelledby="attention-title">
      <header className="analytics-card-heading">
        <h2 id="attention-title">Needs attention{total > 0 && <span className="ml-2 text-sm font-medium text-amber-700">{total}</span>}</h2>
        <button className="dashboard-icon-link" aria-label="Refresh attention list" onClick={() => void load()}><RefreshCw size={14} /></button>
      </header>
      {error && <p role="alert" className="px-6 py-2 text-xs text-amber-900">{error} {data && 'Showing the last loaded list.'}</p>}
      {!data && !error && <p role="status" className="px-6 py-4 text-xs text-ink-500">Loading failed intake…</p>}
      {data && items.length === 0 && (
        <p className="flex items-center gap-2 px-6 py-4 text-sm text-ink-500"><CheckCheck size={16} />Nothing needs attention. Email, WhatsApp and uploaded documents are processing normally.</p>
      )}
      {items.length > 0 && <ul className="divide-y divide-rule-soft">
        {items.map(item => {
          const Icon = ICON[item.source]
          const to = openPath(item)
          const failure = rowErrors[item.id]
          return <li key={item.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3">
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-field text-ink-600" title={SOURCE_LABEL[item.source]}><Icon size={16} aria-hidden="true" /><span className="sr-only">{SOURCE_LABEL[item.source]}</span></span>
            <div className="min-w-0 flex-1 basis-64">
              <strong className="block truncate text-sm font-medium text-ink">{item.title}</strong>
              <span className="block truncate text-xs text-ink-500">{attentionDetail(item)}</span>
              <span className={item.status === 'failed' ? 'block text-xs text-stamp-700' : 'block text-xs text-amber-700'}>{attentionReason(item)}</span>
              {failure && <span role="alert" className="block text-xs text-stamp-700">{failure}</span>}
            </div>
            <span className="shrink-0 text-xs text-ink-400">{ago(item.updatedAt)}</span>
            <div className="flex shrink-0 gap-2">
              {to && <Button size="sm" aria-label={`Open ${item.title}`} onClick={() => navigate(to)}>Open</Button>}
              {data?.canRetry && item.retryable && <Button size="sm" variant="primary" aria-label={`Retry ${item.title}`} icon={<RotateCw size={13} />} disabled={!!busy} onClick={() => void retry(item)}>{busy === item.id ? 'Retrying…' : 'Retry'}</Button>}
            </div>
          </li>
        })}
      </ul>}
      {hidden > 0 && <p className="px-6 pb-4 pt-2 text-xs text-ink-500">Showing the {items.length} most recent of {total}. Resolve these to see older items.</p>}
    </section>
  )
}
