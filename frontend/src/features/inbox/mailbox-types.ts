import type { InboxSnapshot } from '@/shared/api/client'

export type FolderRole = 'inbox' | 'sent' | 'drafts' | 'trash' | 'archive' | 'spam' | 'custom'
export type MailFolder = { name: string; role: FolderRole; total: number; unread: number }
export type MailboxMessage = InboxSnapshot['messages'][number] & {
  toAddresses?: string[]; ccAddresses?: string[]; folder?: string; starred?: boolean
  remoteState?: string; remoteError?: string
}
export type MailboxResult = { messages: MailboxMessage[]; total: number; offset: number; limit: number; hasMore: boolean }
export type DraftMode = 'compose' | 'reply' | 'reply_all' | 'forward' | 'edit_draft'
export type MailDraft = {
  id: string; accountId: string; mode: DraftMode; sourceMessageId?: string
  to: string[]; cc: string[]; bcc: string[]; subject: string; body: string; lockVersion: number
  status: 'draft' | 'sending' | 'sent' | 'unknown' | 'failed' | 'discarded'
  remoteState: 'pending' | 'syncing' | 'synced' | 'unknown' | 'error'; lastError?: string
  attachments: { id: string; name: string; sizeBytes: number; mime: string; url: string; downloadUrl: string }[]
  createdAt: string; updatedAt: string
}
export type MailAttempt = {
  id: string; draftId: string; requestKey: string; status: 'pending' | 'sending' | 'sent' | 'partial' | 'unknown' | 'failed'
  providerMessageId?: string; lastError?: string; refusedRecipients: string[]; createdAt: string; sentAt?: string
}
export type SendResult = { draft: MailDraft; attempt: MailAttempt | null }
