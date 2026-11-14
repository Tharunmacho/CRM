/**
 * The staff default view of the Data Bank: the candidates assigned to the signed-in person that are still New, newest
 * assignment first. It is a view, not a permission: one click shows the whole shared Data Bank.
 * Pure (no React), so the rules are unit-tested (tests/data-bank-scope.test.mjs).
 */

/** Only staff open on their own new candidates. A link that names a recruiter or a pool is an explicit request for that list. */
export function startsOnMyNew(role: string | undefined, staffParam: string | null | undefined, poolParam: string | null | undefined): boolean {
  return role === 'staff' && !staffParam && !poolParam
}

export const MY_NEW_LABEL = 'My new candidates'
export const ALL_CANDIDATES_LABEL = 'All candidates'
export const SHOW_ALL_LABEL = 'Show all candidates'
export const SHOW_MY_NEW_LABEL = 'Show my new candidates'

export const myNewNote = (): string =>
  'Candidates assigned to you that nobody has worked on yet, newest assignment first. Choose All candidates to see the whole Data Bank.'

export const countLine = (mineNew: boolean, total: number, onFile: number): string =>
  mineNew ? `Showing ${total} new ${total === 1 ? 'candidate' : 'candidates'} assigned to you` : `Showing ${total} of ${onFile} candidates`

export const myNewEmptyCopy = (): { title: string; description: string } => ({
  title: 'No new candidates assigned to you',
  description: 'When the admin assigns you candidates, the newest ones show here first. You can still see everyone in the Data Bank.',
})
