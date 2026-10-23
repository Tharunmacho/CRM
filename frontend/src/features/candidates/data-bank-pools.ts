/** Data Bank pools and the wording around candidates that are still waiting for a recruiter. */

export type DataBankPool = 'all' | 'unmapped' | 'not_interested' | 'unassigned' | 'needs_review'

export interface PoolCounts {
  all: number
  unmapped: number
  not_interested: number
  unassigned: number
  needs_review: number
}

export interface AssignWaitingResult {
  assigned: number
  reviewCleared: number
  remaining: number
}

/** What the Assigned-to column shows when a candidate has no recruiter (the server filters on this label too). */
export const NEEDS_ASSIGNMENT_LABEL = 'Needs assignment'

export const ownerLabel = (name?: string | null): string => name?.trim() || NEEDS_ASSIGNMENT_LABEL

/** Pool counts honour the same optional staff scope as the list. */
export const poolCountsPath = (staff?: string): string =>
  staff ? `/candidates/pool-counts?${new URLSearchParams({ staff })}` : '/candidates/pool-counts'

/** Choosing the pool that is already active returns to everyone, so a summary card toggles its filter. */
export const togglePool = (current: DataBankPool, target: DataBankPool): DataBankPool =>
  current === target ? 'all' : target

export const canAssignWaiting = (counts: Pick<PoolCounts, 'unassigned'> | null | undefined, allowed: boolean): boolean =>
  allowed && (counts?.unassigned ?? 0) > 0

export const assignWaitingLabel = (waiting: number, busy = false): string =>
  busy ? 'Assigning…' : `Assign waiting candidates (${waiting})`

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`

export function assignWaitingMessage(result: AssignWaitingResult): { text: string; tone: 'ok' | 'warn' } {
  if (result.assigned === 0) {
    return { text: 'No recruiter is eligible right now. Candidates stay in the waiting list until one is available.', tone: 'warn' }
  }
  const rest = result.remaining > 0 ? ` ${plural(result.remaining, 'candidate')} still waiting for an eligible recruiter.` : ''
  return { text: `Assigned ${plural(result.assigned, 'waiting candidate')}.${rest}`, tone: result.remaining > 0 ? 'warn' : 'ok' }
}

export function emptyPoolCopy(pool: DataBankPool): { title: string; description: string } | null {
  switch (pool) {
    case 'not_interested':
      return { title: 'Nobody in the not-interested pool', description: 'Candidates who decline land here so you can offer them another order later.' }
    case 'unassigned':
      return { title: 'No candidates are waiting for a recruiter', description: 'New candidates are assigned automatically while an eligible recruiter is available.' }
    case 'needs_review':
      return { title: 'Nothing needs review', description: 'Candidates with missing details or document warnings appear here until a recruiter confirms them.' }
    default:
      return null
  }
}
