/**
 * What the Lead sheet's list area shows, decided in one pure place so it can be tested: skeleton rows while the first answer (or
 * the answer to a changed filter) is on its way, the error with Retry, the empty state only after a successful answer with no
 * rows, else the rows. Also the count line. No imports (tests load this file on its own).
 */
export type ListView = 'skeleton' | 'error' | 'empty' | 'rows'

export interface ListState {
  /** A request for the list is in flight. */
  loading: boolean
  /** The rows on screen belong to an earlier search / filter / page than the one being asked for. */
  stale: boolean
  /** A first answer has arrived at least once. */
  hasPage: boolean
  /** The last request failed (message), else ''. */
  error: string
  /** Rows on screen. */
  rows: number
  /** The in-flight request for changed filters has taken longer than the grace period (so old rows are replaced by the skeleton). */
  slow?: boolean
}

export function listView(state: ListState): ListView {
  if (state.loading && !state.hasPage) return 'skeleton' // also while Retry is in flight, so the click is seen to do something
  if (state.error && !state.hasPage) return 'error'
  if (state.loading && state.stale) return state.rows > 0 && !state.slow ? 'rows' : 'skeleton'
  return state.rows === 0 ? 'empty' : 'rows'
}

/** How long old rows stay on screen (dimmed) after the filters changed, before the skeleton takes over. */
export const SKELETON_GRACE_MS = 350

/** "Loading…" until the first answer; then "Showing 3 leads", with "· updating…" while a newer answer is on its way. */
export function countText(input: { loading: boolean; hasPage: boolean; total: number; busy: boolean; error?: string }): string {
  if (!input.hasPage) return input.error ? 'Could not load the leads' : 'Loading…'
  const noun = input.total === 1 ? 'lead' : 'leads'
  return `Showing ${input.total.toLocaleString()} ${noun}${input.busy ? ' · updating…' : ''}`
}

/** The text that goes with the skeleton rows (read aloud, and shown under them). */
export const LOADING_LEADS = 'Loading leads…'
/** How many skeleton rows to draw: enough to fill the page without making the layout jump. */
export const skeletonRows = (pageSize: number): number => Math.max(5, Math.min(12, Math.floor(pageSize) || 8))
