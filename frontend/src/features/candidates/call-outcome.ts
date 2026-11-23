/** The call-result dialog's form, as plain functions so the rules can be tested: a clean start for every candidate, and a body that can never contradict itself. */
export type CallAnswer = 'yes' | 'no' | 'rejected' | 'unreachable' | ''

export interface CallForm { called: 'yes' | 'no'; interested: string; remarks: string; error: string }

/** What the dialog shows when it opens for a candidate: nothing typed, "Called: Yes", and the answer the row menu pre-chose (if any). */
export const initialCallForm = (initialInterested: CallAnswer = ''): CallForm => ({ called: 'yes', interested: initialInterested, remarks: '', error: '' })

/**
 * The body posted to `/candidates/{id}/call`. "Called: No" can only mean the candidate was not reached, so the answer is forced to
 * "unreachable": the server would otherwise mark them declined or rejected without completing the call task (an inconsistent record).
 */
export function callBody(called: string, interested: string, remarks: string) {
  const calledYes = called === 'yes'
  return { called: calledYes, interested: calledYes ? interested : 'unreachable', remarks }
}

/** Save is allowed once an answer is chosen, or when the call was not made (the answer is then fixed to "not contacted yet"). */
export const callCanSave = (called: string, interested: string) => called === 'no' || interested !== ''

/** The words for a recorded call answer, so the profile badge, the stage window's fact and the Data Bank column never disagree (and "Rejected" is never shown as "Not reachable"). */
export function callAnswerWords(interested: string | null | undefined): { badge: string; fact: string; column: string; tone: 'green' | 'amber' | 'stamp' | 'neutral' } {
  if (interested === 'yes') return { badge: 'Interested', fact: 'Yes', column: 'Interested', tone: 'green' }
  if (interested === 'no') return { badge: 'Not interested', fact: 'No', column: 'Not interested', tone: 'amber' }
  if (interested === 'rejected') return { badge: 'Rejected', fact: 'Rejected', column: 'Rejected', tone: 'stamp' }
  return { badge: 'Not reachable', fact: 'Not reachable', column: 'Unreachable', tone: 'neutral' }
}
