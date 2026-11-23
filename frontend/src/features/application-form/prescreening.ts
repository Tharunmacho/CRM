/**
 * Candidate-level Prescreening: stage 1 for a candidate with no client or job yet. The owner's rule: Prescreening is open to everyone
 * WITHOUT a client and a job (save feedback, decline, reject, move on); a client AND a job are mandatory from stage 2, so only
 * "Move to next stage" needs them. Plain data in, plain data out (no imports from the app), so the rules are tested without a browser.
 * The server enforces the same rule (`POST /api/candidates/{id}/prescreen`, `backend/app/services/prescreening.py`).
 */
import type { StageRow } from './process-stages'

export type PrescreenState = 'not_started' | 'in_progress' | 'declined' | 'rejected' | 'on_job'
export type PrescreenAction = 'save' | 'decline' | 'reject' | 'forward'

/** What GET /candidates/{id}/prescreen answers. */
export interface PrescreenInfo {
  state: PrescreenState
  feedback: string
  savedAt: string | null
  decidedAt: string | null
  canWork: boolean
}

export const STAGE_2_LOCK_TEXT = 'Client and job are required from stage 2'
export const NEED_CLIENT_AND_JOB = 'Choose the client and the job: they are required from stage 2.'
export const NEED_FEEDBACK = 'Write feedback before moving the candidate to the next stage.'

/** The chip beside the stage title. */
export function prescreenChip(state: PrescreenState): { label: string; tone: 'idle' | 'busy' | 'bad' } {
  if (state === 'in_progress') return { label: 'In progress', tone: 'busy' }
  if (state === 'declined') return { label: 'Declined', tone: 'bad' }
  if (state === 'rejected') return { label: 'Rejected', tone: 'bad' }
  return { label: 'Not started', tone: 'idle' }
}

/** One line under the title that says where the candidate is. */
export function prescreenLine(state: PrescreenState): string {
  if (state === 'declined') return 'The candidate said no. They are on the Declined tab of the Tasks page. Record a Yes call to bring them back.'
  if (state === 'rejected') return 'The candidate was rejected. They are on the Rejected tab of the Tasks page. Record a Yes call to bring them back.'
  if (state === 'in_progress') return 'Feedback is saved. Decline, reject, or move the candidate to the next stage when you are ready.'
  return 'Prescreening needs no client or job. Write your feedback, then decline, reject, or move the candidate to the next stage.'
}

/** Why "Move to next stage" cannot be pressed yet, or an empty string when it can. Client and job come first: they are the stage 2 rule. */
export function forwardBlocker(input: { clientId: string; orderId: string; feedback: string }): string {
  if (!input.clientId || !input.orderId) return STAGE_2_LOCK_TEXT
  if (!input.feedback.trim()) return NEED_FEEDBACK
  return ''
}

export interface JobChoice { orderId: string; ref: string; title: string; openings: number | null; status: string }
export interface ClientChoice { id: string; jobs: JobChoice[] }

/** The jobs of one client: those a candidate can be put on first, filled / closed / on-hold ones after them (disabled, with the reason). */
export function jobChoices<T extends JobChoice>(clients: readonly { id: string; jobs: T[] }[], clientId: string, reasonOf: (job: T) => string): { job: T; reason: string }[] {
  const client = clients.find((item) => item.id === clientId)
  const rows = (client?.jobs ?? []).map((job, index) => ({ job, index, reason: reasonOf(job) }))
  return rows.sort((a, b) => Number(Boolean(a.reason)) - Number(Boolean(b.reason)) || a.index - b.index).map(({ job, reason }) => ({ job, reason }))
}

/** The request body of one action. Only "forward" carries the job; the others never need a client or job. */
export function prescreenBody(action: PrescreenAction, feedback: string, orderId = ''): { action: PrescreenAction; feedback?: string; orderId?: string } {
  const text = feedback.trim()
  return {
    action,
    ...(text ? { feedback: text } : {}),
    ...(action === 'forward' && orderId ? { orderId } : {}),
  }
}

/** The server's sentence, or a plain one when there is none. */
export function prescreenFailure(message: string | undefined | null, action: PrescreenAction): string {
  const text = (message ?? '').trim()
  if (text && !/^(failed to fetch|network|load failed|request failed)/i.test(text)) return text
  return action === 'forward' ? 'The candidate could not be moved to the next stage. Check the connection and try again.' : 'This could not be saved. Check the connection and try again.'
}

/**
 * The stage grid of a candidate with no job: stage 1 (Prescreening) is the open one, stages 2-13 stay locked ("client and job are required
 * from stage 2"). With a placement the rows come from the server unchanged.
 */
export function prescreenRows(rows: StageRow[], open: boolean, state: PrescreenState = 'not_started'): StageRow[] {
  if (!open) return rows
  const ended = state === 'declined' || state === 'rejected'
  return rows.map((row) => row.stage !== 1 ? row
    : ended ? { ...row, key: 'failed', icon: 'alert', chip: 'Failed', openable: false }
      : { ...row, key: 'current', icon: 'unlock', chip: 'In progress', openable: false })
}
