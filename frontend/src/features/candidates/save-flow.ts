/**
 * What happens between pressing Save on Add candidate and the screen being done, kept free of React so it can be tested.
 *
 * The hosted database costs a round trip per statement, so the order matters:
 *
 *  - The duplicate rule is enforced by the save itself (the server refuses a duplicate with 409 and the match, before anything is
 *    written), so there is no separate duplicate check in front of it. `duplicateFromSaveError` turns that refusal into the same
 *    decision dialog the pre-check used to open.
 *  - After the candidate exists, the form draft and the document attachments do not depend on each other: they run together.
 *  - The workspace refresh is the slowest read of all and nothing on the "saved" screen needs it, so it starts once the writes
 *    are done and is NOT waited for. Whoever really needs it (opening the profile) waits for that same refresh instead of
 *    starting another.
 */

/** The existing candidate the server names when it refuses a duplicate. */
export type DuplicateCandidate = { matchedOn: string[] } & Record<string, unknown>

type ErrorLike = { status?: unknown; detail?: unknown }

/** The duplicate named by a refused save (409 with a `candidate`), or null for every other failure. */
export function duplicateFromSaveError<T extends DuplicateCandidate = DuplicateCandidate>(error: unknown): T | null {
  const { status, detail } = (error ?? {}) as ErrorLike
  if (status !== 409 || !detail || typeof detail !== 'object') return null
  const candidate = (detail as { candidate?: unknown }).candidate
  return candidate && typeof candidate === 'object' ? candidate as T : null
}

export type SavedFollowUp = {
  /** Saves the form draft with the saved candidate's id. A failure never stops the documents from being attached. */
  saveDraft: () => Promise<unknown>
  /** Attaches every saved file; resolves to the failures by kind (empty when all went through). */
  attach: () => Promise<Record<string, string>>
  /** Downloads the workspace again. */
  refresh: () => Promise<unknown>
}

export type SavedOutcome = {
  failed: Record<string, string>
  /** Settles (never rejects) with whether the workspace refresh worked. Await it only when the fresh workspace is needed. */
  refreshed: Promise<boolean>
}

export async function finishSavedCandidate(steps: SavedFollowUp): Promise<SavedOutcome> {
  const [, failed] = await Promise.all([steps.saveDraft().catch(() => undefined), steps.attach()])
  // Always refreshed, also when a file failed: the new candidate must reach the lists either way.
  const refreshed = Promise.resolve().then(steps.refresh).then(() => true, () => false)
  return { failed, refreshed }
}

/** A refresh already running or finished for the saved candidate is reused; otherwise `refresh` starts one. Rejects when it fails. */
export async function freshWorkspace(earlier: Promise<boolean> | null, refresh: () => Promise<unknown>): Promise<void> {
  if (earlier && await earlier) return
  await refresh()
}
