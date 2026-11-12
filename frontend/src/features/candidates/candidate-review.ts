import type { Candidate } from '@/shared/model/types'

export interface CandidateReviewIssue {
  key: string
  label: string
  action: string
}

/** Profile review is separate from recruitment progress and document-reading status. */
export function candidateReviewIssues(candidate: Pick<Candidate, 'name' | 'phone' | 'ownerId' | 'needsReview' | 'status'>): CandidateReviewIssue[] {
  if (!candidate.needsReview && candidate.status !== 'needs_review') return []
  const issues: CandidateReviewIssue[] = []
  const name = (candidate.name ?? '').trim()
  if (!name || name.toLowerCase() === 'name needs review') issues.push({
    key: 'name', label: 'Name missing or unreadable', action: 'Open Edit profile and enter the full name from the original document.',
  })
  if (!candidate.phone?.trim()) issues.push({
    key: 'phone', label: 'Phone number missing', action: 'Confirm the candidate’s phone number and add it in Edit profile.',
  })
  if (!candidate.ownerId) issues.push({
    key: 'recruiter', label: 'Recruiter not assigned', action: 'Use Assign recruiter to choose who will handle this candidate.',
  })
  if (!issues.length) issues.push({
    key: 'review', label: 'Profile check pending',
    action: 'The profile is still marked for review. Check the saved details and document-reading messages, then mark the review as done in Edit profile.',
  })
  return issues
}
