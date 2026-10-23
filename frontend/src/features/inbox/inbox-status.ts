import type { InboxAccount, InboxJob, InboxSnapshot, MailProvider } from '@/shared/api/client'

export const LIVE_PROVIDERS: MailProvider[] = ['gmail', 'microsoft', 'zoho', 'imap']
export const PROVIDER_LABEL: Record<MailProvider, string> = { gmail: 'Gmail', microsoft: 'Microsoft 365', zoho: 'Zoho Mail', imap: 'IMAP / SMTP' }
export const isLiveAccount = (account: Pick<InboxAccount, 'provider' | 'connectionStatus'> | undefined) =>
  !!account && LIVE_PROVIDERS.includes(account.provider) && account.connectionStatus !== 'disconnected'

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

export function intakeUnavailable(account: InboxAccount | undefined): string | null {
  if (!account) return 'Mailbox details are unavailable. Refresh before processing these attachments.'
  if (!LIVE_PROVIDERS.includes(account.provider)) {
    return 'This provider is archived. Stored history is read only; connect the mailbox again for new candidate intake. Original attachments remain available to download.'
  }
  if (account.connectionStatus === 'disconnected' || account.disconnectedAt) {
    return 'An administrator must reconnect this mailbox before creating or retrying candidate processing. Stored messages, processing results and downloads remain available.'
  }
  return null
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
