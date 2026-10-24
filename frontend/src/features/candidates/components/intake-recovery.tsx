import { useState } from 'react'
import { FileText, FolderClock, RotateCcw } from 'lucide-react'
import { dateTimeLabel } from '@/shared/lib/format'
import { Button } from '@/shared/ui/primitives'
import { FileLink } from '@/shared/ui/file-drop'
import { EntrySection } from './entry-section'
import type { useDocumentIntake } from '../use-document-intake'

const labels: Record<string, string> = { uploaded: 'File saved', pending: 'Waiting to be read', processing: 'Reading the file',
  retry: 'Will try reading again', completed: 'Ready to check', needs_review: 'Please check', failed: 'Could not read it. Try again',
  attached: 'Added to the candidate', dismissed: 'Removed from the list' }
/** Soft-filled status pill tone for a reading state. */
const pillTone = (status: string) => status === 'failed' ? 'is-bad' : status === 'needs_review' || status === 'retry' ? 'is-warn'
  : status === 'completed' || status === 'attached' ? 'is-good' : status === 'processing' || status === 'pending' ? 'is-busy' : 'is-muted'

export function IntakeRecovery({ intake, disabled, onRecover, onSaveDraft }: {
  intake: ReturnType<typeof useDocumentIntake>; disabled: boolean; onRecover: (id: string) => Promise<void>; onSaveDraft: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [showAll, setShowAll] = useState(false)
  const groups = [...new Map(intake.available.filter((job) => job.groupId !== intake.jobs[0]?.groupId).map((job) => [job.groupId, job])).values()]
  const act = async (work: () => Promise<unknown>) => {
    setBusy(true); setError('')
    try { await work() } catch (err) { setError(err instanceof Error ? err.message : 'That did not work. Please try again.') }
    finally { setBusy(false) }
  }
  if (!intake.jobs.length && !groups.length && !intake.error && !intake.loading) return null
  return <EntrySection icon={FolderClock} title="Saved uploads" meta="Your files and what we have read stay here if you leave this page." className="ac-recovery">
    <div className="ac-body ac-recovery-body">
      {intake.loading && <p role="status" className="ac-muted">Loading saved uploads…</p>}
      {(intake.error || error) && <div role="alert" className="ac-inline-alert is-bad"><p>{intake.error || error}</p><Button size="sm" onClick={() => void intake.reload()}>Reload saved uploads</Button></div>}
      {intake.draftError && <div role="alert" className="ac-inline-alert is-warn"><p>{intake.draftError}</p><Button disabled={busy || disabled} size="sm" onClick={() => void act(onSaveDraft)}>Try saving the form again</Button></div>}
      {intake.jobs.length > 0 && <ul className="ac-job-list" aria-label="Files in this upload">
        {intake.jobs.map((job) => <li key={job.id} className="ac-job">
          <span className="ac-job-icon" aria-hidden><FileText size={16} /></span>
          <div className="ac-job-main">
            <p className="ac-job-name">{job.filename}</p>
            <p className="ac-job-status"><span className={`ac-pill ${pillTone(job.status)}`}><i aria-hidden />{labels[job.status] ?? job.status}</span></p>
            {job.lastError && <p className="ac-job-error">{job.lastError}</p>}
          </div>
          <div className="ac-job-links"><FileLink url={job.originalUrl}>View file</FileLink><FileLink url={job.originalUrl} download>Download</FileLink></div>
        </li>)}
      </ul>}
      {groups.length > 0 && <div className="ac-unfinished">
        <p className="ac-subhead">Unfinished uploads</p>
        <ul className="ac-job-list">
          {(showAll ? groups : groups.slice(0, 3)).map((job) => <li key={job.groupId} className="ac-job">
            <span className="ac-job-icon" aria-hidden><FolderClock size={16} /></span>
            <div className="ac-job-main">
              <p className="ac-job-name">{job.filename}</p>
              <p className="ac-job-status"><span className={`ac-pill ${pillTone(job.status)}`}><i aria-hidden />{labels[job.status] ?? job.status}</span><span>{dateTimeLabel(job.updatedAt)}</span></p>
            </div>
            <div className="ac-job-actions">
              <Button size="sm" icon={<RotateCcw size={14} />} disabled={disabled || busy || intake.loading} onClick={() => void act(() => onRecover(job.groupId))}>Continue this upload</Button>
              <Button size="sm" variant="ghost" disabled={disabled || busy || intake.loading} onClick={() => void act(() => intake.dismiss(job.groupId))}>Remove from list</Button>
            </div>
          </li>)}
        </ul>
        {groups.length > 3 && <Button size="sm" variant="ghost" onClick={() => setShowAll((value) => !value)}>{showAll ? 'Show fewer' : `Show all ${groups.length} unfinished uploads`}</Button>}
      </div>}
    </div>
  </EntrySection>
}
