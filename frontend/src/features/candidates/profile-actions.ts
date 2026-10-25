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

/** Only an admin sees "Delete candidate"; the server refuses everyone else too. */
export const canDeleteCandidate = (role: string | undefined): boolean => role === 'admin'

/**
 * Why a candidate cannot be deleted from what the page already knows, or null when they can.
 * The server decides in the end and its message is shown if it disagrees.
 */
export function deleteCandidateBlocker(
  applications: ReadonlyArray<{ status: string; orderRef?: string }>,
): string | null {
  if (applications.some((application) => application.status === 'active')) {
    return 'This candidate is still on an order. Remove them from the order first.'
  }
  const deployed = applications.find((application) => application.status === 'deployed')
  if (deployed) {
    return `This candidate was deployed${deployed.orderRef ? ` through ${deployed.orderRef}` : ''}, and a deployed placement is kept on record. They cannot be deleted.`
  }
  return null
}
