import type { InboxJob, InboxSnapshot } from '@/shared/api/client'

export const JOB_LABEL: Record<InboxJob['status'], string> = {
  pending: 'Queued',
  processing: 'Reading attachments',
  retry: 'Retry scheduled',
  needs_review: 'Needs review',
  completed: 'Completed',
  ignored: 'No candidate created',
  failed: 'Failed',
}

export function canRetryJob(job: InboxJob): boolean {
  return ['needs_review', 'failed', 'ignored'].includes(job.status)
}

export function isJobPending(job: InboxJob): boolean {
  return ['pending', 'processing', 'retry'].includes(job.status)
}

export function workerHealth(worker: InboxSnapshot['worker'], now = Date.now()) {
  if (!worker.enabled) return { healthy: false, label: 'Automatic processing is disabled' }
  const heartbeat = worker.lastHeartbeat ? Date.parse(worker.lastHeartbeat) : NaN
  if (!Number.isFinite(heartbeat) || now - heartbeat > 15 * 60_000) {
    return { healthy: false, label: 'Processing is enabled; a recent worker check-in is not confirmed' }
  }
  if ('extractionHeartbeat' in worker) {
    const extracted = worker.extractionHeartbeat ? Date.parse(worker.extractionHeartbeat) : NaN
    if (!Number.isFinite(extracted) || now - extracted > 15 * 60_000) return { healthy: false, label: 'Mail receiver is active; document worker check-in is overdue' }
  }
  return { healthy: true, label: 'Attachment worker checked in recently' }
}
