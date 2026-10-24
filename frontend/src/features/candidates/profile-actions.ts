/** Wording for profile actions that are unavailable until the candidate has a working recruiter. */

/** Why "Match to an order" cannot be used right now, or null when it can. */
export function mapToOrderBlocker(candidate: { ownerId: string | null }, ownerActive: boolean): string | null {
  if (!candidate.ownerId) return 'Assign an active recruiter first. A candidate needs a recruiter before they can be matched to an order.'
  if (!ownerActive) return 'The assigned recruiter is inactive. Reassign this candidate to an active recruiter first.'
  return null
}

/** The candidate's linked WhatsApp conversation, as returned by `/whatsapp/candidates/:id/conversation`. */
export function linkedConversationId(response: { conversation?: { id?: unknown } | null } | null | undefined): string {
  const id = response?.conversation?.id
  return typeof id === 'string' ? id : ''
}
