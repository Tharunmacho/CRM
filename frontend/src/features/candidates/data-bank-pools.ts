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

/** What an empty recruiter reads as in a table or card. Muted, never a name. */
export const NOT_ASSIGNED_LABEL = 'Not assigned'

export const ownerLabel = (name?: string | null): string => name?.trim() || NOT_ASSIGNED_LABEL

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
    return { text: 'No recruiter was available to take them. Candidates stay unassigned until you choose a recruiter.', tone: 'warn' }
  }
  const rest = result.remaining > 0 ? ` ${plural(result.remaining, 'candidate')} still not assigned.` : ''
  return { text: `Assigned ${plural(result.assigned, 'waiting candidate')}.${rest}`, tone: result.remaining > 0 ? 'warn' : 'ok' }
}

/** What POST /api/candidates/unassign answers. */
export interface UnassignResult {
  unassigned: number
  alreadyUnassigned: number
  refused: { id: string; name: string; reason: string }[]
}

/** Only the admin takes a candidate back from a recruiter, and only one who has a recruiter. */
export const canUnassignCandidate = (role: string | undefined, ownerId: string | null | undefined): boolean => role === 'admin' && Boolean(ownerId?.trim())

/** The confirmation before making candidates "Not assigned": who they leave and what follows. */
export function unassignCopy(count: number, name?: string, ownerName?: string): { title: string; lines: string[]; confirmLabel: string } {
  const one = count === 1
  const who = one ? (name || 'This candidate') : plural(count, 'candidate')
  const from = one && ownerName ? ownerName : 'their recruiter'
  return {
    title: one ? 'Make this candidate unassigned' : `Make ${count} candidates unassigned`,
    lines: [
      `${who} will be taken back from ${from} and will not be assigned to anyone.`,
      `They go back to Not assigned, where you can choose a recruiter or use Assign waiting candidates. Follow-up reminders and untouched initial calls of ${one && ownerName ? ownerName : 'the recruiter'} are removed.`,
      'Candidates who are on an order or have open stage work stay with their recruiter: reassign them instead.',
    ],
    confirmLabel: one ? 'Make unassigned' : `Make ${count} unassigned`,
  }
}

/** The toast after the server answered: what was freed and what stayed, with the server's reasons. */
export function unassignMessage(result: UnassignResult): { text: string; tone: 'ok' | 'warn' } {
  const done = result.unassigned > 0 ? `${plural(result.unassigned, 'candidate')} made unassigned.` : 'Nobody was changed.'
  const already = result.alreadyUnassigned > 0 ? ` ${plural(result.alreadyUnassigned, 'candidate')} had no recruiter already.` : ''
  const refused = result.refused.length > 0 ? ` ${result.refused.map((item) => item.reason).join(' ')}` : ''
  return { text: `${done}${already}${refused}`, tone: result.refused.length > 0 || result.unassigned === 0 ? 'warn' : 'ok' }
}

export function emptyPoolCopy(pool: DataBankPool): { title: string; description: string } | null {
  switch (pool) {
    case 'not_interested':
      return { title: 'Nobody is marked not interested', description: 'Candidates who are not interested show up here, so you can offer them another order later.' }
    case 'unassigned':
      return { title: 'No candidates are waiting for a recruiter', description: 'New candidates stay here until the admin assigns a recruiter.' }
    case 'needs_review':
      return { title: 'Nothing needs review', description: 'Candidates with missing details or document problems show up here until a recruiter checks them.' }
    default:
      return null
  }
}
