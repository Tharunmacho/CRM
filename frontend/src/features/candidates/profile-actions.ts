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

export type DeleteBlocker = {
  /** The plain sentence to show. */
  message: string
  /**
   * True only when the candidate is on an active order and nothing else stops the delete: an admin may confirm
   * "also remove them from the order(s) and delete". A deployed placement can never be overridden.
   */
  canWithdraw: boolean
  /** The active order(s) the delete would take them off. */
  orderRefs: string[]
}

/**
 * Why a candidate cannot be deleted as it stands, from what the page already knows, or null when they can.
 * The server decides in the end (it also refuses invoices and payments) and its message is shown if it disagrees.
 */
export function deleteCandidateBlocker(
  applications: ReadonlyArray<{ status: string; orderRef?: string }>,
): DeleteBlocker | null {
  // A deployed placement is kept on record, so being on another order as well changes nothing: no override.
  const deployed = applications.find((application) => application.status === 'deployed')
  if (deployed) {
    return {
      message: `This candidate was deployed${deployed.orderRef ? ` through ${deployed.orderRef}` : ''}, and a deployed placement is kept on record. They cannot be deleted.`,
      canWithdraw: false,
      orderRefs: [],
    }
  }
  const active = applications.filter((application) => application.status === 'active')
  if (active.length) {
    const orderRefs = [...new Set(active.map((application) => application.orderRef).filter((ref): ref is string => !!ref))]
    return {
      message: `This candidate is still on ${orderRefs.length ? `order ${orderRefs.join(', ')}` : 'an order'}.`,
      canWithdraw: true,
      orderRefs,
    }
  }
  return null
}
