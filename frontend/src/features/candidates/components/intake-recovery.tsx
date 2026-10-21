import { useState } from 'react'
import { FileText, RotateCcw } from 'lucide-react'
import { dateTimeLabel } from '@/shared/lib/format'
import { Button } from '@/shared/ui/primitives'
import { FileLink } from '@/shared/ui/file-drop'
import { Panel, PanelHeader } from '@/shared/ui/display'
import type { useDocumentIntake } from '../use-document-intake'

const labels: Record<string, string> = { uploaded: 'Original saved', pending: 'Queued for reading', processing: 'Reading document',
  retry: 'Reading will resume', completed: 'Ready for review', needs_review: 'Needs your review', failed: 'Reading needs a retry',
  attached: 'Added to candidate', dismissed: 'Dismissed' }

export function IntakeRecovery({ intake, disabled, onRecover, onSaveDraft }: {
  intake: ReturnType<typeof useDocumentIntake>; disabled: boolean; onRecover: (id: string) => Promise<void>; onSaveDraft: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const groups = [...new Map(intake.available.filter((job) => job.groupId !== intake.jobs[0]?.groupId).map((job) => [job.groupId, job])).values()]
  const act = async (work: () => Promise<unknown>) => {
    setBusy(true); setError('')
    try { await work() } catch (err) { setError(err instanceof Error ? err.message : 'This action could not finish. Please retry.') }
    finally { setBusy(false) }
  }
  if (!intake.jobs.length && !groups.length && !intake.error && !intake.loading) return null
  return <Panel flush className="mb-4">
    <PanelHeader title="Saved document intake" meta="Originals and reading progress stay available when you leave this page." />
    <div className="space-y-3 px-5 py-4">
      {intake.loading && <p role="status" className="text-sm text-ink-500">Loading saved intake…</p>}
      {(intake.error || error) && <div role="alert" className="space-y-2 text-sm text-stamp-700"><p>{intake.error || error}</p><Button size="sm" onClick={() => void intake.reload()}>Reload saved intake</Button></div>}
      {intake.draftError && <div role="alert" className="space-y-2 rounded-control bg-amber-50 p-3 text-sm text-amber-900"><p>{intake.draftError}</p><Button disabled={busy || disabled} size="sm" onClick={() => void act(onSaveDraft)}>Retry draft backup</Button></div>}
      {intake.jobs.map((job) => <div key={job.id} className="flex flex-wrap items-center gap-3 rounded-control border border-rule-soft p-3">
        <FileText size={18} className="shrink-0 text-navy-600" />
        <div className="min-w-0 flex-1"><p className="break-words text-sm font-medium">{job.filename}</p><p className="mt-1 text-xs text-ink-500">{labels[job.status] ?? job.status}</p>
          {job.lastError && <p className="mt-1 text-xs text-amber-800">{job.lastError}</p>}</div>
        <div className="flex w-full gap-3 pl-7 text-xs sm:w-auto sm:pl-0"><FileLink url={job.originalUrl}>View original</FileLink><FileLink url={job.originalUrl} download>Download</FileLink></div>
      </div>)}
      {groups.length > 0 && <div className="space-y-2"><p className="text-xs font-medium text-ink-500">Unfinished intakes</p>
        {groups.map((job) => <div key={job.groupId} className="flex flex-wrap items-center gap-3 rounded-control bg-field/60 p-3">
          <div className="min-w-0 flex-1"><p className="break-words text-sm">{job.filename}</p><p className="mt-1 text-xs text-ink-500">{dateTimeLabel(job.updatedAt)} · {labels[job.status] ?? job.status}</p></div>
          <Button size="sm" icon={<RotateCcw size={14} />} disabled={disabled || busy || intake.loading} onClick={() => void act(() => onRecover(job.groupId))}>Resume intake</Button>
          <Button size="sm" variant="ghost" disabled={disabled || busy || intake.loading} onClick={() => void act(() => intake.dismiss(job.groupId))}>Dismiss</Button>
        </div>)}
      </div>}
    </div>
  </Panel>
}
