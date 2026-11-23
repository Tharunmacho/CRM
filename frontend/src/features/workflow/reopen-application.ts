/**
 * Reopening a CLOSED application (POST /applications/{id}/reopen-application): the words and the small rules the screens share, as pure
 * functions so they can be tested. Reopening a finished STAGE is a different action (the admin-only "Reopen stage").
 */

/** The server wants at least this many characters of reason. */
export const MIN_REOPEN_REASON = 3

export const reopenReasonOk = (reason: string) => reason.trim().length >= MIN_REOPEN_REASON

/** Whether to offer the button: a closed (not deployed, not open) placement, to somebody who may work the candidate. */
export const canReopenApplication = (status: string | null | undefined, canWork: boolean) => status === 'closed' && canWork

/** What the dialog says will happen. `stageTitle` is the stage where the application ended ("Prescreening", "Interview result"...). */
export const reopenExplanation = (stageTitle: string) =>
  `The candidate goes back to ${stageTitle} on the Tasks Current list, with an open task for their recruiter. Earlier feedback and files stay on record. Anything that ended the application at this stage is set aside.`

/** The line under an empty stage: it says what is true of the application, and names the button when there is one. */
export function emptyStageHint(status: string | null | undefined, currentStage: number, canReopen: boolean): string {
  if (status === 'active') return `Complete stage ${currentStage} to continue in order.`
  if (status === 'deployed') return 'This placement is deployed, so there are no more stages to work on.'
  return canReopen
    ? 'This application is closed. Choose Reopen application to continue working on its stages.'
    : 'This application is closed. Only the candidate\'s recruiter, an admin or the MD can reopen it.'
}

/** The line under the stage tiles of the application form. */
export const closedFootnote = (status: string | null | undefined) =>
  status === 'closed' ? 'This application is closed. Open a stage and choose Reopen application to continue.' : null

/** The server's refusals are plain sentences already; this only guards against an empty message. */
export const reopenFailure = (message: string | null | undefined) =>
  message && message.trim() ? message : 'We could not reopen the application. Please try again.'
