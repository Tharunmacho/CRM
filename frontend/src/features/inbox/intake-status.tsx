import type { InboxJob } from '@/shared/api/client'
import { smartTime } from '@/shared/lib/format'
import { Badge } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { canRetryJob, JOB_LABEL } from './inbox-status'
import { ReceiptReview, type ReceiptDecision } from './receipt-review'

export function JobReport({ job, busy, canRetry, retryUnavailable, opening, onOpen, onRetry, onReview }: {
  job: InboxJob; busy: boolean; canRetry: boolean; opening: boolean
  retryUnavailable?: string | null
  onOpen: (id: string) => Promise<void>; onRetry: () => Promise<boolean>
  onReview: (body: ReceiptDecision) => Promise<boolean>
}) {
  const pages = (numbers: number[]) => numbers?.length ? numbers.join(', ') : 'None'
  return <section className="rounded-control border border-rule bg-field/40 p-3 text-xs" aria-label="Reading result">
    <div className="flex flex-wrap items-center gap-2"><Badge tone={job.status === 'completed' ? 'navy' : 'amber'}>{JOB_LABEL[job.status] ?? job.status}</Badge><span className="text-ink-500">Tries: {job.attempts} · Updated {smartTime(job.updatedAt)}</span></div>
    {job.lastError && <p className="mt-2 whitespace-pre-wrap break-words text-amber-900">{job.lastError}</p>}
    {job.inspection?.map((report, index) => <details key={report.attachmentId ?? index} className="mt-3 rounded border border-rule bg-white p-2">
      <summary className="cursor-pointer break-words font-medium text-ink">{report.fileName} · {report.pageCount} pages · {report.status.replaceAll('_', ' ')}</summary>
      <dl className="mt-2 grid gap-2 text-ink-600 sm:grid-cols-2">
        <div><dt>Resume pages</dt><dd>{pages(report.resumePages)}</dd></div><div><dt>Passport pages</dt><dd>{pages(report.passportPages)}</dd></div><div><dt>Aadhaar pages</dt><dd>{pages(report.aadhaarPages)}</dd></div>
        <div><dt>Pages we could not read</dt><dd>{pages(report.unreadPages)}</dd></div><div><dt>Pages we are not sure about</dt><dd>{pages(report.unknownPages)}</dd></div>
      </dl>
    </details>)}
    <div className="mt-3 flex flex-wrap gap-2">{job.candidateId && <Button size="sm" disabled={opening} onClick={() => void onOpen(job.candidateId!)}>Open candidate</Button>}{canRetry && canRetryJob(job) && <><Button size="sm" disabled={busy || !!retryUnavailable} title={retryUnavailable ?? undefined} onClick={() => void onRetry()}>Read again</Button><ReceiptReview busy={busy} submit={onReview} /></>}</div>
  </section>
}
