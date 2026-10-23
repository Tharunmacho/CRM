import { useEffect, useState } from 'react'
import { Download, FolderOpen, LockKeyhole } from 'lucide-react'
import { api, fileHref } from '@/shared/api/client'
import { dateTimeLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { Button } from '@/shared/ui/primitives'
import { DocumentBundleReview } from './document-bundle-review'

type Evidence = { reviews: { bundleId: string; sourceId: string; completedAt: string; originals: { id: string; name: string; url: string }[] }[]; hasMore: boolean }

export function CandidateIntakeEvidence({ candidateId }: { candidateId: string }) {
  const store = useStore()
  return <IntakeEvidence key={`${candidateId}:${store.me?.id}`} candidateId={candidateId} />
}

function IntakeEvidence({ candidateId }: { candidateId: string }) {
  const [evidence, setEvidence] = useState<Evidence | null>(null)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [review, setReview] = useState<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    setError('')
    api.get<Evidence>(`/document-bundles/candidates/${candidateId}/sources`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setEvidence(value) })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Saved intake history could not be loaded.') })
    return () => controller.abort()
  }, [candidateId, retry])
  if (error) return <div className="border-t border-rule-soft py-4"><p role="alert" className="text-sm text-stamp-700">Your original intake history could not load. {error}</p><Button className="mt-3" size="sm" onClick={() => setRetry(value => value + 1)}>Retry intake history</Button></div>
  if (!evidence?.reviews.length) return null
  return <section className="border-t border-rule-soft py-4">
    <div className="flex items-start gap-2"><LockKeyhole size={16} className="mt-1 shrink-0 text-navy-600" /><div><h3 className="text-sm font-semibold">Your original document intakes</h3><p className="mt-1 text-sm text-ink-500">Only you can open these full uploads. They may contain other people; this candidate's documents above contain their reviewed pages.</p></div></div>
    <div className="mt-4 space-y-4">{evidence.reviews.map(item => <article key={item.bundleId} className="min-w-0 rounded-control border border-rule bg-field/40 p-3">
      <p className="text-sm text-ink-500">Reviewed {dateTimeLabel(item.completedAt)}</p>
      <ul className="mt-3 space-y-3">{item.originals.map(original => <li key={original.id}><a href={fileHref(`${original.url}?download=true`)} className="flex items-start gap-2 text-sm font-medium text-navy-700"><Download size={16} className="mt-0.5 shrink-0" /><span className="min-w-0 break-words">{original.name}<span className="block text-xs font-normal text-ink-500">Download unchanged original</span></span></a></li>)}</ul>
      <Button className="mt-3" size="sm" icon={<FolderOpen size={15} />} onClick={() => setReview(item.sourceId)}>Open completed review</Button>
    </article>)}</div>
    {evidence.hasMore && <p className="mt-3 text-sm text-ink-500">Showing your 100 most recent intakes for this candidate.</p>}
    {review && <DocumentBundleReview sourceType="manual" sourceId={review} onClose={() => setReview(null)} />}
  </section>
}
