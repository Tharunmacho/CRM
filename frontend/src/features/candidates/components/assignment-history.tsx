import { useEffect, useRef, useState } from 'react'
import { History } from 'lucide-react'
import { api } from '@/shared/api/client'
import { dateTimeLabel } from '@/shared/lib/format'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { EmptyState } from '@/shared/ui/display'

type Entry = { id: string; at: string; action: string; actorName: string; fromName: string; toName: string; reason: string | null }
type HistoryPage = { items: Entry[]; nextBefore: string | null }

export function CandidateAssignmentHistory({ candidateId }: { candidateId: string }) {
  const [open, setOpen] = useState(false)
  const [page, setPage] = useState<HistoryPage | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [revision, setRevision] = useState(0)
  const generation = useRef(0)
  useEffect(() => {
    generation.current += 1
    if (!open) return
    const controller = new AbortController()
    setPage(null); setError(''); setLoading(true)
    api.get<HistoryPage>(`/candidates/${candidateId}/assignment-history`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setPage(result) })
      .catch((err) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'We could not load the history.') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => { controller.abort(); generation.current += 1 }
  }, [open, candidateId, revision])
  const more = async () => {
    if (!page?.nextBefore || loading) return
    const requestGeneration = generation.current
    setLoading(true); setError('')
    try {
      const next = await api.get<HistoryPage>(`/candidates/${candidateId}/assignment-history?before=${encodeURIComponent(page.nextBefore)}`)
      if (requestGeneration !== generation.current) return
      setPage((current) => current ? { items: [...current.items, ...next.items], nextBefore: next.nextBefore } : next)
    } catch (err) { if (requestGeneration === generation.current) setError(err instanceof Error ? err.message : 'We could not load more.') }
    finally { if (requestGeneration === generation.current) setLoading(false) }
  }
  return <>
    <Button size="sm" className="ml-auto" icon={<History size={16} />} onClick={() => setOpen(true)}>History</Button>
    <Dialog open={open} onClose={() => setOpen(false)} title="Recruiter history"
      description="Each time the recruiter changed: who changed it, and why. Older changes may not be listed.">
      {error && <p role="alert" className="mb-4 text-sm text-stamp-700">{error} <button className="underline" onClick={() => page?.nextBefore ? void more() : setRevision((value) => value + 1)}>Try again</button></p>}
      {!page && loading && <p role="status" className="text-sm text-ink-500">Loading history…</p>}
      {page?.items.length === 0 && <EmptyState title="The recruiter has not changed" description="Future changes, including cover when a recruiter is absent, will show up here." />}
      <ol className="space-y-4">{page?.items.map((entry) => <li key={entry.id} className="rounded-xl border border-rule p-4">
        <p className="text-sm font-medium text-ink">{entry.fromName} → {entry.toName}</p>
        <p className="mt-2 whitespace-pre-wrap text-sm text-ink-600">{entry.reason || 'No reason given'}</p>
        <p className="mt-3 text-xs text-ink-400">{entry.actorName} · {dateTimeLabel(entry.at)}</p>
        <p className="mt-1 text-xs text-ink-400">{entry.action.replace(/_/g, ' ')}</p>
      </li>)}</ol>
      {page?.nextBefore && <Button className="mt-4" disabled={loading} onClick={() => void more()}>{loading ? 'Loading…' : 'Load older changes'}</Button>}
    </Dialog>
  </>
}
