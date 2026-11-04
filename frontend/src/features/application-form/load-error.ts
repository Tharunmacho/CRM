/**
 * Turns a failed load of the application form into something a recruiter can act on.
 * A bare 404 whose detail is exactly "Not Found" is the web framework's answer for a route that does not exist: the server
 * process is older than this screen, which is not the same as the candidate being missing.
 */
export type LoadErrorKind = 'server_outdated' | 'candidate_not_found' | 'order_not_found' | 'schema_pending' | 'forbidden' | 'network' | 'other'

export interface LoadErrorView {
  kind: LoadErrorKind
  title: string
  text: string
  /** Whether trying again can help. */
  retryable: boolean
}

export function classifyLoadError(error: { status?: number; message?: string } | null | undefined): LoadErrorView {
  const status = error?.status ?? -1
  const message = (error?.message ?? '').trim()
  if (status === 404 && message.toLowerCase() === 'not found') {
    return {
      kind: 'server_outdated',
      title: 'The server needs to be updated',
      text: 'The server needs to be updated to show application forms. Ask your admin to restart or update the CRM, then try again.',
      retryable: true,
    }
  }
  if (status === 404 && /application|order/i.test(message)) {
    return { kind: 'order_not_found', title: 'That order is not on this candidate', text: message, retryable: false }
  }
  if (status === 404 && /candidate/i.test(message)) {
    return { kind: 'candidate_not_found', title: 'We could not find this candidate', text: 'They may have been combined with another candidate or deleted.', retryable: false }
  }
  if (status === 404) {
    return { kind: 'order_not_found', title: 'That order is not on this candidate', text: message || 'Open the form from the candidate to pick one of their orders.', retryable: false }
  }
  if (/update required|migrat|schema/i.test(message) || status === 503) {
    return { kind: 'schema_pending', title: 'The database update is pending', text: 'The database still has to be updated for application forms. Ask your admin to finish the update, then try again.', retryable: true }
  }
  if (status === 403) {
    return { kind: 'forbidden', title: 'You cannot open this form', text: message || 'Your account does not have access to this candidate.', retryable: false }
  }
  if (status === 0) {
    return { kind: 'network', title: 'Cannot reach the server', text: message || 'Check your connection and try again.', retryable: true }
  }
  return { kind: 'other', title: 'The form could not be loaded', text: message || 'Something went wrong. Please try again.', retryable: true }
}
