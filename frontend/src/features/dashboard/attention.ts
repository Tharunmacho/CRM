import type { AttentionItem, AttentionSource } from '@/shared/api/client'

export const SOURCE_LABEL: Record<AttentionSource, string> = {
  email: 'Email', whatsapp: 'WhatsApp', manual: 'Manual upload',
}

/** API path (without the `/api` prefix) of the existing per-source retry route. */
export function retryPath(item: Pick<AttentionItem, 'source' | 'jobId'>): string {
  const id = encodeURIComponent(item.jobId)
  if (item.source === 'email') return `/inbox/jobs/${id}/retry`
  if (item.source === 'whatsapp') return `/whatsapp/messages/${id}/retry`
  return `/ocr/jobs/${id}/retry`
}

/** Where a person goes to inspect the item, or null when nothing is shareable (a private manual draft). */
export function openPath(item: Pick<AttentionItem, 'source' | 'messageId' | 'candidateId'>): string | null {
  if (item.candidateId) return `/candidates/${encodeURIComponent(item.candidateId)}`
  if (item.source === 'email' && item.messageId) return `/inbox?message=${encodeURIComponent(item.messageId)}`
  if (item.source === 'whatsapp') return '/whatsapp'
  return null
}

/** One short line: the status, then the recorded reason unless it only repeats the status. */
export function attentionReason(item: Pick<AttentionItem, 'status' | 'reason'>): string {
  const status = item.status === 'needs_review' ? 'Needs review' : 'Failed'
  const reason = item.reason.trim()
  if (!reason || reason.toLowerCase() === status.toLowerCase() || /^(processing failed|needs review)$/i.test(reason)) return status
  return `${status} · ${reason}`
}

/** Subtitle: file names when known, otherwise who sent it and through which channel. */
export function attentionDetail(item: Pick<AttentionItem, 'files' | 'sender' | 'channel'>): string {
  const files = item.files.length > 2 ? `${item.files.slice(0, 2).join(', ')} +${item.files.length - 2} more` : item.files.join(', ')
  return [files, item.sender, item.channel].filter(Boolean).join(' · ')
}

/** "Showing 20 of 36" when the capped list hides rows, so a long backlog is never mistaken for a short one. */
export function hiddenCount(shown: number, total: number): number {
  return Math.max(0, total - shown)
}

/** Data Bank sidebar count: candidates that need a human, i.e. flagged for review or without a recruiter. */
export function dataBankAttention(candidates: ReadonlyArray<{ status: string; needsReview?: boolean; ownerId: string | null }>): number {
  let count = 0
  for (const candidate of candidates) if (candidate.needsReview || candidate.status === 'needs_review' || !candidate.ownerId) count += 1
  return count
}
