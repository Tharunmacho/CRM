/** Rules and wording for deleting a job (the server still calls it an order). Plain data in and out, so it is easy to test. */

/** Only an admin sees a delete button for a job; the server refuses everyone else too. */
export const canDeleteOrder = (role: string | undefined): boolean => role === 'admin'

export type OrderDeleteBlocker = {
  /** The plain sentence to show. */
  message: string
  /** True when the admin may confirm "remove the candidates and delete". A deployed placement can never be overridden. */
  canWithdraw: boolean
  candidates: number
  active: number
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/**
 * What stands between this job and its deletion, from what the page already knows, or null when nothing does.
 * The server decides in the end (it also refuses invoices and payments, and counts documents) and its message is shown
 * if it disagrees.
 */
export function orderDeleteBlocker(
  applications: ReadonlyArray<{ status: string; candidateId: string }>,
  ref: string,
  candidateName: (id: string) => string | undefined = () => undefined,
): OrderDeleteBlocker | null {
  const candidates = new Set(applications.map((application) => application.candidateId)).size
  const active = applications.filter((application) => application.status === 'active').length
  const deployed = applications.find((application) => application.status === 'deployed')
  if (deployed) {
    const who = candidateName(deployed.candidateId) ?? 'A candidate'
    return {
      message: `${who} was deployed through ${ref}, and a deployed placement is kept on record. This job cannot be deleted. Close it instead.`,
      canWithdraw: false, candidates, active,
    }
  }
  if (!applications.length) return null
  const verb = candidates === 1 ? 'is' : 'are'
  const who = `${plural(candidates, 'candidate', 'candidates')} ${verb} on ${ref}${active ? ` (${active} active)` : ' (all closed)'}`
  return { message: `${who}.`, canWithdraw: true, candidates, active }
}

/** True when the server's refusal says the admin can still confirm (the page's own view of the job was out of date). */
export function serverAsksToConfirm(error: unknown): boolean {
  const detail = (error as { detail?: unknown } | null)?.detail
  return !!detail && typeof detail === 'object' && (detail as { canWithdraw?: unknown }).canWithdraw === true
}

/** The query string for the delete call: the admin's explicit confirmation to remove the candidates with the job. */
export const orderDeletePath = (id: string, withdrawApplications?: boolean): string =>
  `/orders/${id}${withdrawApplications ? '?withdrawApplications=true' : ''}`
