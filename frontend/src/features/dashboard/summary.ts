/** Counts behind the dashboard cards, as plain functions so they can be tested. */

/** Candidates (once each) who have been put through to prescreening or any later stage of an order. */
export function candidatesAfterPrescreening(candidateIds: Iterable<string>, applications: { candidateId: string }[]): number {
  const allowed = new Set(candidateIds)
  return new Set(applications.filter(application => allowed.has(application.candidateId)).map(application => application.candidateId)).size
}

/** A candidate belongs to the view when the viewer is a supervisor (optionally narrowed to one recruiter) or owns them. */
export function inView<T extends { ownerId?: string | null }>(rows: T[], viewer: { id: string; isTeam: boolean }, staff = ''): T[] {
  return rows.filter(row => viewer.isTeam ? !staff || row.ownerId === staff : row.ownerId === viewer.id)
}
