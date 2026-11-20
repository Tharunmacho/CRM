import { isAdminLevel } from '@/shared/model/role-labels'
/**
 * Deleting a note of the Tasks page's NOTES column, as plain data. A note is a daily update on a candidate's task; deleting it is a soft
 * delete on the server (`DELETE /api/remarks/{id}`: the row stays, an audit entry keeps its text), so the screen only has to hide it.
 * Who may: the note's author, the candidate's recruiter and any Admin or MD. The server enforces the same rule (403 otherwise); this is the
 * mirror that decides whether the button is drawn. No other imports, so it runs under node:test.
 */

export const DELETE_NOTE_LABEL = 'Delete note'
export const DELETE_NOTE_TITLE = 'Delete this note?'
export const DELETE_NOTE_HINT = 'It disappears from the list. The change is recorded in the activity log.'
export const DELETE_NOTE_ERROR = 'The note could not be deleted. Please try again.'

export interface NoteOwner { byId?: string | null }
export interface NoteRow { ownerId: string }
export interface NoteViewer { id: string; role?: string | null }

/** The same rule as the server: the author, the candidate's recruiter, an Admin or an MD. */
export function canDeleteNote(note: NoteOwner, row: NoteRow, me: NoteViewer | null | undefined): boolean {
  if (!me?.id) return false
  return isAdminLevel(me.role) || (!!note.byId && note.byId === me.id) || (!!row.ownerId && row.ownerId === me.id)
}

/** The row without these notes (the screen hides a deleted note at once, before the server answers and the list is read again). */
export function withoutNotes<T extends { todayNotes?: { id: string }[] }>(row: T, hidden: ReadonlySet<string>): T {
  if (!hidden.size || !row.todayNotes?.some((note) => hidden.has(note.id))) return row
  return { ...row, todayNotes: row.todayNotes.filter((note) => !hidden.has(note.id)) }
}

/** The text of a failed delete: the server's own words when it said something, else the calm default. */
export function deleteFailure(failure: unknown): string {
  return failure instanceof Error && failure.message ? failure.message : DELETE_NOTE_ERROR
}
