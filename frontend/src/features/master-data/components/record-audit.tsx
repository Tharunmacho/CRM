import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import { dateTimeLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { Button } from '@/shared/ui/primitives'
import { Sheet } from '@/shared/ui/overlay'

export async function refreshMasterData(store: ReturnType<typeof useStore>, message: string) {
  if (!store.isCurrentSession()) return
  try { await store.refresh({ keepCurrent: true }); store.notify(message) }
  catch { store.notify(message + '. Reload to see the latest records; your changes were saved.', 'warn') }
}

type Audit = { id: string; action: string; actorId: string; at: string; meta?: { changes?: Record<string, { from: unknown; to: unknown }> } }
const readable = (value: unknown): string => value === null || value === undefined || value === '' ? 'Not set' : Array.isArray(value) ? value.join(', ') || 'None' : String(value)

export function RecordAudit({ kind, id, onClose }: { kind: 'client' | 'company' | 'designation' | 'order'; id: string; onClose: () => void }) {
  const store = useStore()
  const [items, setItems] = useState<Audit[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    api.get<Audit[]>('/master-data/' + kind + '/' + encodeURIComponent(id) + '/audit', { signal: controller.signal })
      .then(setItems).catch((err) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Could not load activity') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [kind, id, retry])
  return <Sheet open onClose={onClose} title="Record activity" description="The latest 100 saved changes and file uploads.">
    {loading ? <p role="status" className="text-sm text-ink-500">Loading activity…</p> : error ? <div role="alert" className="space-y-3 text-sm text-stamp-700"><p>{error}</p><Button onClick={() => setRetry((value) => value + 1)}>Retry</Button></div> : items.length === 0 ? <p className="text-sm text-ink-500">No recorded changes yet.</p> :
      <ol className="space-y-5">{items.map((item) => <li key={item.id} className="rounded-panel border border-rule-soft p-4">
        <p className="font-medium text-ink">{item.action}</p>
        <p className="mt-1 text-xs text-ink-500">{store.user(item.actorId)?.name ?? 'Team member'} · {dateTimeLabel(item.at)}</p>
        {item.meta?.changes && <dl className="mt-3 space-y-2">{Object.entries(item.meta.changes).map(([field, change]) => <div key={field} className="border-t border-rule-soft pt-2 text-xs">
          <dt className="mb-1 capitalize text-ink-500">{field.replaceAll('_', ' ')}</dt>
          <dd className="break-words text-ink-700">{readable(change.from)} → {readable(change.to)}</dd>
        </div>)}</dl>}
      </li>)}</ol>}
  </Sheet>
}
