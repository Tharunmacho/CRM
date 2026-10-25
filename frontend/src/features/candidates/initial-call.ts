import type { Task } from '@/shared/model/types'

/** The first-call task that is still waiting for this candidate (not done, not cancelled). */
export function openInitialCallTask(tasks: readonly Task[], candidateId: string): Task | undefined {
  return tasks.find((task) => task.candidateId === candidateId && task.kind === 'initial_call' && !task.completedAt && !task.cancelledAt)
}

export const MATCH_DONE_MESSAGE = 'Mapped. Stage 1 task raised.'
export const FIRST_CALL_CLOSED_MESSAGE = 'First-call task closed. You can still record the call result from the profile.'

/** What the toast says after a successful match. The server tells us when it closed the first-call task. */
export function matchDoneMessage(closedInitialCall: boolean | undefined): string {
  return closedInitialCall ? `${MATCH_DONE_MESSAGE} ${FIRST_CALL_CLOSED_MESSAGE}` : MATCH_DONE_MESSAGE
}
