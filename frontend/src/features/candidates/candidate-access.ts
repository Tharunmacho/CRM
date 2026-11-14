/**
 * Who may WORK a candidate (edit details, upload documents, record the initial call, save or complete stages,
 * edit the application form, match to an order). The server decides in the end (core/deps.py,
 * `candidate_work_refusal`); this mirrors it so the screen can hide or disable the action and say why.
 *
 * Seeing a candidate is open to everyone signed in (the Data Bank is shared); working one belongs to the
 * recruiter the candidate is assigned to. Admin and manager may work any candidate (PRD role table), and
 * only the admin can hand a candidate to someone else.
 */
export interface AccessActor { id: string; role: string }
export interface AccessCandidate { ownerId: string | null }

const SUPERVISOR_ROLES = ['admin', 'manager']

export function canWorkCandidate(me: AccessActor | null | undefined, candidate: AccessCandidate | null | undefined): boolean {
  if (!me || !candidate) return false
  if (SUPERVISOR_ROLES.includes(me.role)) return true
  return Boolean(candidate.ownerId) && candidate.ownerId === me.id
}

/** The sentence that explains a disabled action, or null when the person may work this candidate. Same wording as the server. */
export function workRefusal(me: AccessActor | null | undefined, candidate: AccessCandidate | null | undefined, ownerName?: string | null): string | null {
  if (canWorkCandidate(me, candidate)) return null
  if (!candidate?.ownerId) return 'This candidate has no recruiter yet. Only a manager or an admin can work on it.'
  return `This candidate is assigned to ${ownerName || 'another recruiter'}. Only they, a manager or an admin can work on it.`
}

/** A staff member or manager who adds a candidate keeps it; only the admin chooses the recruiter (or leaves it to the next free one). */
export const choosesRecruiter = (role: string | undefined): boolean => role === 'admin'
