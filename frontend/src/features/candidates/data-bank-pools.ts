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

/**
 * The eleven PRD columns, in order, each with the server filter key it filters on
 * (`owner` is filtered through the dedicated `staff` id parameter instead).
 */
export const PRD_COLUMNS = [
  { key: 'name', label: 'Name' },
  { key: 'phone', label: 'Phone' },
  { key: 'designation', label: 'Job title' },
  { key: 'experience', label: 'Experience' },
  { key: 'passport', label: 'Passport' },
  { key: 'source', label: 'Source' },
  { key: 'owner', label: 'Recruiter' },
  { key: 'order', label: 'Order' },
  { key: 'stage', label: 'Stage' },
  { key: 'status', label: 'Status' },
  { key: 'added', label: 'Date added' },
] as const

/** How many of the filter controls currently narrow the list (column filters, staff, skills, coverage). */
export function activeFilterCount(columnFilters: Record<string, string>, staff: string, skills: string[], skillStatus: string): number {
  const columns = Object.entries(columnFilters).filter(([key, value]) => key !== 'owner' && value.trim()).length
  return columns + (staff ? 1 : 0) + skills.length + (skillStatus !== 'all' ? 1 : 0)
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
    return { text: 'No recruiter is free right now. Candidates stay on the waiting list until one is.', tone: 'warn' }
  }
  const rest = result.remaining > 0 ? ` ${plural(result.remaining, 'candidate')} still waiting for a free recruiter.` : ''
  return { text: `Assigned ${plural(result.assigned, 'waiting candidate')}.${rest}`, tone: result.remaining > 0 ? 'warn' : 'ok' }
}

export function emptyPoolCopy(pool: DataBankPool): { title: string; description: string } | null {
  switch (pool) {
    case 'not_interested':
      return { title: 'Nobody is marked not interested', description: 'Candidates who are not interested show up here, so you can offer them another order later.' }
    case 'unassigned':
      return { title: 'No candidates are waiting for a recruiter', description: 'New candidates are given to a free recruiter automatically.' }
    case 'needs_review':
      return { title: 'Nothing needs review', description: 'Candidates with missing details or document problems show up here until a recruiter checks them.' }
    default:
      return null
  }
}
