import { isAdminLevel } from '@/shared/model/role-labels'

/**
 * Deleting a team member (Users > User Management > row menu > Delete user). Plain data in and out so it is easy to test.
 * The server is the authority (`DELETE /api/users/:id`); this only mirrors its rules so the menu and the dialog are honest.
 */
export const LEAVE_UNASSIGNED = '__unassigned'

export interface DeleteActor { id: string; role: string }
export interface DeleteTarget { id: string; role: string; deleted?: boolean }
export interface DeleteSummary { candidates: number; openTasks: number; leads: number }

/** The menu item is offered only to an Admin or MD, only for a Recruiter account, never for yourself. */
export function canDeleteUser(me: DeleteActor | null | undefined, target: DeleteTarget | null | undefined): boolean {
  if (!me || !target || target.deleted) return false
  return isAdminLevel(me.role) && target.role === 'staff' && target.id !== me.id
}

export const hasWorkToMove = (summary: DeleteSummary | null | undefined): boolean =>
  !!summary && (summary.candidates > 0 || summary.openTasks > 0 || summary.leads > 0)

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`

/** "29 candidates and 26 open tasks" (a lead count joins when there are leads); "Nothing to move" when there is nothing. */
export function workSummaryText(summary: DeleteSummary | null | undefined): string {
  if (!hasWorkToMove(summary)) return 'Nothing to move'
  const parts = [plural(summary!.candidates, 'candidate', 'candidates'), plural(summary!.openTasks, 'open task', 'open tasks')]
  if (summary!.leads > 0) parts.push(plural(summary!.leads, 'lead', 'leads'))
  return parts.length === 2 ? parts.join(' and ') : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
}

/** The red button stays disabled until the person has chosen where the work goes (nothing to choose when there is no work). */
export function deleteReady(summary: DeleteSummary | null | undefined, choice: string, busy: boolean): boolean {
  if (busy || !summary) return false
  return !hasWorkToMove(summary) || choice !== ''
}

/** The query string of the delete request for a choice: a recruiter id, or the explicit "leave unassigned". */
export function deleteQuery(choice: string): string {
  if (choice === LEAVE_UNASSIGNED) return '?leaveUnassigned=true'
  return choice ? `?reassignTo=${encodeURIComponent(choice)}` : ''
}

/** What stays after a delete: the account is gone for good; the history is re-pointed to the one system account with this name. */
export const FORMER_USER_LABEL = 'Former user'
export const DELETE_DESCRIPTION = `The account is deleted permanently. Everything they did stays in the history under “${FORMER_USER_LABEL}”.`
export const DELETE_NOTE = `They can no longer sign in, they disappear from the team list and every picker, and their email address can be used again. `
  + `Their notes, calls, remarks, uploads and audit entries stay, shown as “${FORMER_USER_LABEL}”. This cannot be undone.`
export const DELETING_TEXT = 'Deleting the account and moving its history. This can take a few seconds; please keep this window open.'

export function successText(name: string, summary: DeleteSummary, recipientName: string | null): string {
  if (!hasWorkToMove(summary)) return `${name} was deleted permanently.`
  return recipientName
    ? `${name} was deleted permanently. ${workSummaryText(summary)} moved to ${recipientName}.`
    : `${name} was deleted permanently. ${workSummaryText(summary)} are now unassigned.`
}
