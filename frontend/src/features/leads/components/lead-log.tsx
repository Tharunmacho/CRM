import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Sheet } from '@/shared/ui/overlay'
import { leadApi, type LeadActivity } from '../lead-api'
import { createdParts, type Lead } from '../lead-model'

const KIND_LABEL: Record<string, string> = { call: 'Remarks', note: 'Note', status: 'Status', assign: 'Assigned', import: 'Imported', convert: 'Converted', unconvert: 'Moved back to Lead', resume: 'Resume', edit: 'Edited' }

/** Everything that happened with a lead, newest first as the server sends it. */
export function LeadTimeline({ items }: { items: LeadActivity[] }) {
  if (items.length === 0) return <p className="ld-hint">Nothing has happened with this lead yet.</p>
  return (
    <ol className="ld-timeline">
      {items.map((item) => {
        const parts = createdParts(item.createdAt)
        return (
          <li key={item.id} className={cn('ld-tl', `is-${item.kind}`)}>
            <span className="ld-tl-dot" aria-hidden="true" />
            <div className="ld-tl-body">
              <p><b>{KIND_LABEL[item.kind] ?? item.kind}</b>{item.userName ? ` · ${item.userName}` : ''}<time dateTime={item.createdAt}>{parts ? `${parts.day} ${parts.year}, ` : ''}{new Date(item.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></p>
              {item.summary && <p className="ld-tl-text">{item.summary}</p>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/** The log of one lead in its own panel, opened by a button (from the row menu or from the open lead), so nobody has to scroll for it. */
export function LeadLogSheet({ lead, version, onClose }: { lead: Lead | null; version?: unknown; onClose: () => void }) {
  const [items, setItems] = useState<LeadActivity[] | null>(null)
  const [error, setError] = useState('')
  const id = lead?.id
  // Another lead: forget the previous one's log. A reload of the list (version) refetches in place, without a "Loading..." flash.
  useEffect(() => { setItems(null); setError('') }, [id])
  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    leadApi.detail(id, controller.signal)
      .then((detail) => { if (!controller.signal.aborted) { setItems(detail.activities); setError('') } })
      .catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'We could not load the log.') })
    return () => controller.abort()
  }, [id, version])
  if (!lead) return null
  return (
    <Sheet open onClose={onClose} title={`Log · ${lead.name || 'Lead'}`} width="md">
      <div className="ld-drawer">
        {error && <p className="ld-alert" role="alert"><AlertTriangle size={16} aria-hidden="true" /><span>{error}</span></p>}
        {!error && (items ? <LeadTimeline items={items} /> : <p className="ld-hint" role="status">Loading…</p>)}
      </div>
    </Sheet>
  )
}
