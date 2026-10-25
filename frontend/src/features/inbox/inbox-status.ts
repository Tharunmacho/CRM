import type { InboxAccount, InboxJob, InboxSnapshot, MailProvider } from '@/shared/api/client'

export const LIVE_PROVIDERS: MailProvider[] = ['gmail', 'microsoft', 'zoho', 'imap']
export const PROVIDER_LABEL: Record<MailProvider, string> = { gmail: 'Gmail', microsoft: 'Microsoft 365', zoho: 'Zoho Mail', imap: 'IMAP / SMTP' }
export const isLiveAccount = (account: Pick<InboxAccount, 'provider' | 'connectionStatus'> | undefined) =>
  !!account && LIVE_PROVIDERS.includes(account.provider) && account.connectionStatus !== 'disconnected'

export const JOB_LABEL: Record<InboxJob['status'], string> = {
  pending: 'Waiting',
  processing: 'Reading attachments',
  retry: 'Will try again',
  needs_review: 'Needs review',
  completed: 'Done',
  ignored: 'No candidate made',
  failed: 'Did not work',
}

export function canRetryJob(job: InboxJob): boolean {
  return ['needs_review', 'failed', 'ignored'].includes(job.status)
}

export function isJobPending(job: InboxJob): boolean {
  return ['pending', 'processing', 'retry'].includes(job.status)
}

export function intakeUnavailable(account: InboxAccount | undefined): string | null {
  if (!account) return 'We cannot see the mailbox details. Refresh before processing these attachments.'
  if (!LIVE_PROVIDERS.includes(account.provider)) {
    return 'This mailbox type is archived. Saved mail history is read only. Connect the mailbox again to make candidates from new mail. You can still download the original attachments.'
  }
  if (account.connectionStatus === 'disconnected' || account.disconnectedAt) {
    return 'An admin must reconnect this mailbox before candidates can be created or read again. Saved messages, results and downloads stay available.'
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

/** A short, plain-English line for the message list: where an email stands on its way to a candidate. Null when it needs no mark. */
export function intakeHeadline(attachments: number, candidates: number, jobs: InboxJob[]): { tone: 'neutral' | 'navy' | 'amber' | 'green' | 'stamp'; text: string } | null {
  if (candidates > 0 || !jobs.length) return null
  if (jobs.some((job) => isJobPending(job))) return { tone: 'navy', text: 'Reading attachments' }
  if (jobs.some((job) => job.status === 'needs_review')) return { tone: 'amber', text: 'Needs review' }
  if (jobs.every((job) => job.status === 'failed')) return { tone: 'stamp', text: 'Did not work' }
  if (jobs.every((job) => job.status === 'ignored')) return { tone: 'neutral', text: attachments ? 'No candidate made' : 'No attachment' }
  return null
}

/** Folder names come from the mail server ("INBOX", "Sent Items"). The inbox and the mailbox drafts get plain names; every other folder keeps its own. */
export const folderLabel = (name: string, role?: string) => role === 'drafts' ? 'Mailbox drafts' : role === 'inbox' && name.toUpperCase() === 'INBOX' ? 'Inbox' : name
