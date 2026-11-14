/**
 * "Request" in the Data Bank: a staff member asks the admins for a candidate who is not theirs.
 * Pure (no React, no imports) so the rules are unit-tested directly (tests/request-allocation.test.mjs).
 * The server decides in the end (POST /candidates/{id}/request-allocation); this decides who is shown the button.
 */
export interface RequestActor { id: string; role: string }
export interface RequestCandidate { id: string; ownerId: string | null }

export interface AllocationRequestResult { requested: boolean; alreadyRequested: boolean; notified: number }

/** Only staff ask; the admin assigns directly (a manager works their own candidates). A candidate already assigned to the person is never requested. */
export function canRequestAllocation(me: RequestActor | null | undefined, candidate: RequestCandidate | null | undefined): boolean {
  if (!me || !candidate || me.role !== 'staff') return false
  return candidate.ownerId !== me.id
}

export const requestAllocationPath = (candidateId: string): string => `/candidates/${encodeURIComponent(candidateId)}/request-allocation`

export const REQUEST_LABEL = 'Request'
export const REQUESTED_LABEL = 'Requested'

export const requestTitle = (name: string, requested: boolean): string =>
  requested ? `You asked the admin for ${name}. They will allocate the candidate.` : `Ask the admin to allocate ${name} to you`

export function requestMessage(result: AllocationRequestResult, name: string): { text: string; tone: 'ok' | 'warn' } {
  if (result.alreadyRequested) return { text: `You already asked for ${name}. The admin has been told.`, tone: 'ok' }
  if (result.notified === 0) return { text: `Your request for ${name} was saved, but no admin is active to receive it.`, tone: 'warn' }
  return { text: `Request sent. The admin will allocate ${name}.`, tone: 'ok' }
}

/** Candidates each signed-in person has already asked for (keyed by user id, so another person on this browser never sees them), shared by every row, card and expanded row that shows the button. */
const requested = new Set<string>()
const listeners = new Set<() => void>()
let version = 0

const requestKey = (userId: string, candidateId: string): string => `${userId}:${candidateId}`

export const hasRequested = (userId: string, candidateId: string): boolean => requested.has(requestKey(userId, candidateId))
export function markRequested(userId: string, candidateId: string): void {
  const key = requestKey(userId, candidateId)
  if (requested.has(key)) return
  requested.add(key)
  version += 1
  listeners.forEach(listener => listener())
}
export function subscribeRequested(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}
export const requestedVersion = (): number => version
export function resetRequested(): void { requested.clear(); version += 1; listeners.forEach(listener => listener()) }
