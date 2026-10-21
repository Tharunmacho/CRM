import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/shared/api/client'
import { dateTimeLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'

interface Evidence {
  messageId: string; subject: string; mailbox: string; fromAddress: string; receivedAt: string
  review?: { reason: string; actor_id: string; at: string }
}

export function EmailEvidenceHistory({ candidateId }: { candidateId: string }) {
  const store = useStore()
  const [rows, setRows] = useState<Evidence[]>([])
  const [error, setError] = useState(false)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let live = true
    setRows([]); setError(false)
    api.get<Evidence[]>(`/inbox/candidates/${candidateId}/evidence`).then((result) => { if (live && store.isCurrentSession()) setRows(result) }).catch(() => { if (live) setError(true) })
    return () => { live = false }
  }, [candidateId, retry, store.isCurrentSession])
  if (!rows.length && !error) return null
  return <section className="mt-5 border-t border-rule pt-4" aria-label="Email source history">
    <h3 className="mb-3 text-sm font-semibold text-ink">Email source history</h3>
    {error && <p className="text-xs text-stamp-700">Source history could not load. <button className="underline" onClick={() => setRetry((value) => value + 1)}>Retry</button></p>}
    <div className="space-y-3">{rows.map((row) => <div key={row.messageId} className="rounded-control border border-rule bg-field/30 p-3 text-xs">
      <Link className="font-medium text-navy-700 underline" to={`/inbox?message=${encodeURIComponent(row.messageId)}`}>{row.subject}</Link>
      <p className="mt-1 break-words text-ink-500">{row.fromAddress} to {row.mailbox} · {dateTimeLabel(row.receivedAt)}</p>
      {row.review && <p className="mt-2 whitespace-pre-wrap break-words text-ink-700">Reviewed by {store.user(row.review.actor_id)?.name ?? 'Former team member'} on {dateTimeLabel(row.review.at)}: {row.review.reason}</p>}
    </div>)}</div>
  </section>
}
