import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import { dateTimeLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { Button } from '@/shared/ui/primitives'
import { Sheet } from '@/shared/ui/overlay'
import { ArrowRight } from 'lucide-react'
import { Avatar } from '@/features/clients/components/parts'
import { DialogMark } from '@/features/master-data/components/dialog-mark'

export async function refreshMasterData(store: ReturnType<typeof useStore>, message: string) {
  if (!store.isCurrentSession()) return
  try { await store.refresh({ keepCurrent: true }); store.notify(message) }
  catch { store.notify(message + '. Your changes were saved. Reload the page to see the latest records.', 'warn') }
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
      .then(setItems).catch((err) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'We could not load the history. Please try again.') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [kind, id, retry])
  return <Sheet open onClose={onClose} title="Change history" description="The last 100 changes and file uploads.">
    <DialogMark />
    {loading ? <p role="status" className="cl-audit-state">Loading history…</p>
      : error ? <div role="alert" className="cl-audit-state is-error space-y-3"><p>{error}</p><Button onClick={() => setRetry((value) => value + 1)}>Try again</Button></div>
      : items.length === 0 ? <p className="cl-audit-state">No changes yet.</p>
      : <ol className="cl-audit">{items.map((item) => {
        const actor = store.user(item.actorId)?.name ?? 'Team member'
        return <li key={item.id}>
          <Avatar name={actor} />
          <div className="cl-audit-body">
            <p className="cl-audit-action">{item.action}</p>
            <p className="cl-audit-meta">{actor} · {dateTimeLabel(item.at)}</p>
            {item.meta?.changes && <dl className="cl-audit-changes">{Object.entries(item.meta.changes).map(([field, change]) => <div key={field}>
              <dt>{field.replaceAll('_', ' ')}</dt>
              <dd><del>{readable(change.from)}</del><ArrowRight size={12} aria-hidden="true" /><ins>{readable(change.to)}</ins></dd>
            </div>)}</dl>}
          </div>
        </li>
      })}</ol>}
  </Sheet>
}
