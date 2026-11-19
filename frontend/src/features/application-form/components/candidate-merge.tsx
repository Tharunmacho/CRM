import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { GitMerge } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { primaryId } from '@/features/candidates/candidate-ids'
import { applicationFormHref } from '../form-model'
import { SHOW_MERGE_UI, findMergeTargets, mergeButtonLabel, mergeDialogView, mergeDoneText, mergeOffer, type MergePhase, type MergePlan } from '../duplicate-merge'
import { mergeCandidate, previewMerge } from '../merge-api'
import { isAdminLevel } from '@/shared/model/role-labels'

/** The duplicate-merge dialog: asks the server what would move (a dry run), says it, and only then merges. Admin only (the server checks too). */
function MergeDialog({ sourceId, sourceLabel, target, onClose }: { sourceId: string; sourceLabel: string; target: { id: string; label: string }; onClose: () => void }) {
  const store = useStore()
  const navigate = useNavigate()
  const [phase, setPhase] = useState<MergePhase>('checking')
  const [plan, setPlan] = useState<MergePlan | null>(null)
  const [error, setError] = useState('')
  const [confirmed, setConfirmed] = useState(false)

  useEffect(() => {
    let live = true
    setPhase('checking'); setPlan(null); setError('')
    previewMerge(sourceId, target.id)
      .then((result) => { if (live) { setPlan(result); setPhase('ready') } })
      .catch((cause) => { if (live) { setError(cause instanceof Error ? cause.message : 'The merge could not be checked.'); setPhase('failed') } })
    return () => { live = false }
  }, [sourceId, target.id])

  const labels = { source: sourceLabel, target: target.label }
  const view = mergeDialogView({ phase, plan, error, identityConfirmed: confirmed }, labels)
  const close = () => { if (phase !== 'merging') onClose() }

  const merge = async () => {
    if (!view.confirmEnabled || phase === 'merging') return
    setPhase('merging'); setError('')
    try {
      const done = await mergeCandidate(sourceId, target.id, confirmed)
      store.notify(mergeDoneText(done, labels))
      // The duplicate is gone from the Data Bank: refresh the lists (best effort), then open the candidate it was merged into. Its form
      // loads the documents the identity check has already judged against it, so the passport that was refused now fills.
      try { await store.refresh({ keepCurrent: true }) } catch { /* the page still opens; the lists catch up on their next refresh */ }
      navigate(applicationFormHref(target.id, null, null), { replace: true })
      onClose()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The merge was not done. Nothing was changed.')
      setPhase('failed')
    }
  }

  return (
    <Dialog
      open
      onClose={close}
      title={view.title}
      description="The documents, notes and tasks of the duplicate are added to the existing candidate."
      width="md"
      footer={<>
        <Button variant="ghost" disabled={view.busy} onClick={close}>{view.cancelLabel}</Button>
        {(plan?.allowed || view.needsIdentityConfirm) && !plan?.alreadyMerged && <Button variant="danger" disabled={!view.confirmEnabled} onClick={() => void merge()}>{view.confirmLabel}</Button>}
      </>}
    >
      <div className="af-merge" aria-busy={view.busy}>
        {view.lines.map((line) => <p key={line} role={view.blocked ? 'alert' : undefined}>{line}</p>)}
        {view.skipped.length > 0 && <>
          <p><b>Not carried over:</b></p>
          <ul>{view.skipped.map((line) => <li key={line}>{line}</li>)}</ul>
        </>}
        {view.needsIdentityConfirm && (
          <label className="af-merge-confirm">
            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
            <span>I have checked that these are the same person.</span>
          </label>
        )}
      </div>
    </Dialog>
  )
}

/**
 * Beside "Already in the CRM as CI-139 · Name": an admin gets the primary "Merge into CI-139" button; anybody else is told to ask one.
 * The candidate being merged away is the one whose page this is (the route's :id).
 */
export function DuplicateMergeAction({ duplicate }: { duplicate: { candidateId: string; careId: string | null; name: string } }) {
  const store = useStore()
  const { id = '' } = useParams()
  const [open, setOpen] = useState(false)
  const offer = SHOW_MERGE_UI ? mergeOffer(duplicate, { isAdmin: isAdminLevel(store.me.role), candidateId: id }) : null
  if (!offer) return null
  if (offer.kind === 'ask_admin') return <small className="af-merge-ask">{offer.text}</small>
  const here = store.candidates.find((candidate) => candidate.id === id)
  return (
    <>
      <button type="button" className="af-docbtn is-primary" onClick={() => setOpen(true)}><GitMerge aria-hidden="true" />{mergeButtonLabel(offer.targetLabel)}</button>
      {open && <MergeDialog sourceId={id} sourceLabel={primaryId(here) || here?.name || 'this candidate'} target={{ id: offer.targetId, label: offer.targetLabel }} onClose={() => setOpen(false)} />}
    </>
  )
}

/**
 * "Merge duplicate into…" in the page header (admins): search the candidate that should keep the records (by name, Care India ID or
 * phone), then the same dry-run and confirmation as above. The candidate whose page this is goes away.
 */
export function MergeIntoPicker({ sourceId, sourceLabel }: { sourceId: string; sourceLabel: string }) {
  const store = useStore()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [target, setTarget] = useState<{ id: string; label: string } | null>(null)
  const results = useMemo(() => findMergeTargets(store.candidates, query, sourceId), [store.candidates, query, sourceId])
  if (!SHOW_MERGE_UI || !isAdminLevel(store.me.role)) return null
  const closeAll = () => { setOpen(false); setTarget(null); setQuery('') }
  return (
    <>
      <Button onClick={() => setOpen(true)} icon={<GitMerge />} aria-haspopup="dialog" title="This candidate is a duplicate: move its documents and notes onto the existing candidate and remove it">Merge duplicate into…</Button>
      {open && !target && (
        <Dialog open onClose={closeAll} title={`Merge ${sourceLabel} into…`} description="Search for the candidate that already exists and should keep everything." width="md" footer={<Button variant="ghost" onClick={closeAll}>Cancel</Button>}>
          <div className="af-merge">
            <input className="ui-input" type="search" autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name, Care India ID or phone number" aria-label="Search the candidate to merge into" />
            {query.trim().length >= 2 && !results.length && <p>No other candidate matches.</p>}
            <ul className="af-merge-results">
              {results.map((candidate) => (
                <li key={candidate.id}>
                  <Button variant="ghost" onClick={() => setTarget({ id: candidate.id, label: primaryId(candidate) || candidate.name })}>
                    <b>{primaryId(candidate)}</b>&nbsp;{candidate.name}{candidate.phone ? ` · ${candidate.phone}` : ''}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        </Dialog>
      )}
      {open && target && <MergeDialog sourceId={sourceId} sourceLabel={sourceLabel} target={target} onClose={closeAll} />}
    </>
  )
}
