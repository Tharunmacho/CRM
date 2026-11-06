/**
 * Pure rules behind the Lead sheet's bulk actions, status change with undo, and delete: the selection (across pages, and
 * "all N matching" which is filter based), the request body, the exact-count confirmation wording and the result toast.
 * No React and no imports of app code, so tests/lead-bulk.test.mjs loads this file directly.
 *
 * The server owns the truth (backend/app/services/lead_bulk.py, docs/workflows/lead-sheet-contract.md "Bulk actions and delete").
 */

/** Mirrors the server's hard cap per call. */
export const BULK_LIMIT = 10_000
/** Deleting more than this many leads needs the word DELETE typed. */
export const TYPED_CONFIRM_OVER = 20
export const TYPED_WORD = 'DELETE'
/** How long the "Undo" button stays on a status-change toast. */
export const UNDO_MS = 10_000

export type BulkAction = 'assign' | 'status' | 'not_interested' | 'delete' | 'export'

/* --------------------------------------------------------------- selection */

/** What is ticked: explicit lead ids (they survive page changes), or every lead the current filters match. */
export interface Selection { ids: string[]; allMatching: boolean }
export const EMPTY_SELECTION: Selection = { ids: [], allMatching: false }

/** How many leads an action would touch. */
export const selectionCount = (selection: Selection, matchingTotal: number): number => (selection.allMatching ? matchingTotal : selection.ids.length)

export const hasSelection = (selection: Selection, matchingTotal: number): boolean => selectionCount(selection, matchingTotal) > 0

/** The ids the table should show ticked: the whole page while "all matching" is on, else the explicit ids. */
export const visibleSelected = (selection: Selection, pageIds: string[]): string[] => (selection.allMatching ? pageIds : selection.ids)

/** The table reported a tick change (a row, or the header box). Any manual change leaves "all matching" mode and keeps exactly what the table shows. */
export const changeSelection = (_selection: Selection, next: string[]): Selection => ({ ids: [...new Set(next)], allMatching: false })

export const selectAllMatching = (selection: Selection): Selection => ({ ids: selection.ids, allMatching: true })

/** Why "select all matching" cannot be offered (more than one call can hold), or null when it can. */
export function allMatchingBlock(matchingTotal: number): string | null {
  return matchingTotal > BULK_LIMIT
    ? `${matchingTotal.toLocaleString()} leads match. Bulk actions handle up to ${BULK_LIMIT.toLocaleString()} at a time: narrow the filters first.`
    : null
}

/** Show the "Select all N matching leads" offer: something is ticked, there are more matches than ticks, and it is not on yet. */
export const canOfferAllMatching = (selection: Selection, matchingTotal: number): boolean =>
  !selection.allMatching && selection.ids.length > 0 && matchingTotal > selection.ids.length

/** The first line of the bar. */
export function selectionLine(selection: Selection, matchingTotal: number): string {
  const n = selectionCount(selection, matchingTotal)
  if (selection.allMatching) return `All ${n.toLocaleString()} matching ${n === 1 ? 'lead is' : 'leads are'} selected`
  return `${n.toLocaleString()} selected`
}

/** Ids that are not in the loaded rows can stay selected (other pages); this drops only ids the server says are gone. */
export const dropIds = (selection: Selection, gone: string[]): Selection => ({ ...selection, ids: selection.ids.filter((id) => !gone.includes(id)) })

/* ----------------------------------------------------------------- request */

const SKIP_PARAMS = new Set(['sort', 'direction', 'page', 'page_size', 'extra'])

/**
 * The list's query string (without paging) as the bulk endpoint's `filter`: the very same parameters, so "select all N matching"
 * acts on exactly the rows the list shows. A repeated parameter becomes an array.
 */
export function filterFromQuery(query: string): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {}
  for (const [key, value] of new URLSearchParams(query)) {
    if (SKIP_PARAMS.has(key) || value === '') continue
    const known = out[key]
    out[key] = known === undefined ? value : Array.isArray(known) ? [...known, value] : [known, value]
  }
  return out
}

export interface BulkOptions { ownerId?: string; status?: string; confirm?: string; sort?: string; direction?: 'asc' | 'desc' }

/** The POST /api/leads/bulk body. A filter selection also sends the count the person saw, so a list that changed meanwhile is refused. */
export function bulkBody(selection: Selection, filter: Record<string, string | string[]>, matchingTotal: number, action: BulkAction, options: BulkOptions = {}): Record<string, unknown> {
  const body: Record<string, unknown> = { action }
  if (selection.allMatching) { body.filter = filter; body.expectedCount = matchingTotal } else body.ids = selection.ids
  if (options.ownerId) body.ownerId = options.ownerId
  if (options.status && action === 'status') body.status = options.status
  if (options.confirm) body.confirm = options.confirm
  if (options.sort) body.sort = options.sort
  if (options.direction) body.direction = options.direction
  return body
}

/* ------------------------------------------------------------ confirmation */

const leads = (n: number) => (n === 1 ? 'lead' : 'leads')
const count = (n: number) => n.toLocaleString()

export interface ConfirmCopy { title: string; lines: string[]; confirmLabel: string; danger: boolean; typed: boolean }

export interface ConfirmInput {
  action: Exclude<BulkAction, 'export'>
  count: number
  allMatching: boolean
  /** The chosen recruiter's name, or 'auto'. */
  ownerName?: string
  /** Label of the chosen status. */
  statusLabel?: string
}

/** Does this delete need the word DELETE typed? */
export const needsTypedConfirm = (action: BulkAction, n: number): boolean => action === 'delete' && n > TYPED_CONFIRM_OVER
export const typedConfirmOk = (typed: string): boolean => typed.trim() === TYPED_WORD

/** The dialog before a bulk write: the exact number of leads and the action, in words. */
export function confirmCopy(input: ConfirmInput): ConfirmCopy {
  const n = input.count
  const what = `${count(n)} ${leads(n)}`
  const scope = input.allMatching ? `All ${what} that match the current search and filters (not only the ones on this page)` : `${what[0].toUpperCase()}${what.slice(1)}`
  switch (input.action) {
    case 'assign': {
      const who = input.ownerName === 'auto' || !input.ownerName ? 'the available staff, spread evenly' : input.ownerName
      return { title: `Assign ${what}`, lines: [`${scope} will be assigned to ${who}.`, 'Converted leads are never moved. Leads that already belong to that person are skipped.'], confirmLabel: `Assign ${what}`, danger: false, typed: false }
    }
    case 'status':
      return { title: `Change status of ${what}`, lines: [`${scope} will be marked ${input.statusLabel ?? 'with the new status'}.`, 'Converted leads and leads that already have this status are skipped.'], confirmLabel: `Change ${what}`, danger: false, typed: false }
    case 'not_interested':
      return { title: `Mark ${what} not interested`, lines: [`${scope} will be marked Not interested and leave the follow-up list.`, 'Converted leads are skipped. You can change the status again later.'], confirmLabel: `Mark ${what}`, danger: false, typed: false }
    default:
      return {
        title: `Delete ${what}`,
        lines: [
          `${scope} will be permanently deleted, with their call history and resumes. This cannot be undone.`,
          'Leads converted to a candidate are skipped. If this person is in a later upload they will be added again.',
          ...(n > TYPED_CONFIRM_OVER ? [`Type ${TYPED_WORD} to confirm.`] : []),
        ],
        confirmLabel: `Delete ${what}`, danger: true, typed: n > TYPED_CONFIRM_OVER,
      }
  }
}

/** The confirmation of deleting one lead (row menu or drawer). */
export function deleteOneCopy(name: string): ConfirmCopy {
  return {
    title: 'Delete this lead',
    lines: [
      `${name || 'This lead'} will be permanently deleted, with their call history and resume. This cannot be undone.`,
      'If this person is in a later upload they will be added again.',
      'A lead that was converted to a candidate cannot be deleted.',
    ],
    confirmLabel: 'Delete lead', danger: true, typed: false,
  }
}

/* ------------------------------------------------------------------ result */

export interface BulkResult {
  action?: string
  requested?: number
  updated: number
  skipped: number
  skippedReasons?: { code: string; count: number; message: string }[]
  owners?: { id: string; name: string; count: number }[]
  status?: string
}

/** "N leads updated, K skipped (reason)". */
export function resultToast(result: Pick<BulkResult, 'updated' | 'skipped' | 'skippedReasons'>, verb = 'updated'): string {
  const head = `${count(result.updated)} ${leads(result.updated)} ${verb}`
  if (!result.skipped) return `${head}.`
  const reasons = (result.skippedReasons ?? []).filter((item) => item.count > 0).map((item) => `${count(item.count)} ${item.message}`)
  return `${head}, ${count(result.skipped)} skipped${reasons.length ? ` (${reasons.join('; ')})` : ''}.`
}

/** The verb of the toast for an action. */
export const resultVerb = (action: BulkAction): string => (action === 'delete' ? 'deleted' : action === 'assign' ? 'assigned' : 'updated')

/* -------------------------------------------------------------------- undo */

/** One status change that can still be taken back. */
export interface UndoEntry { leadId: string; name: string; from: string; to: string; at: number }

export function undoEntry(lead: { id: string; name?: string | null; status: string }, to: string, at: number = Date.now()): UndoEntry | null {
  return lead.status === to ? null : { leadId: lead.id, name: lead.name ?? '', from: lead.status, to, at }
}

/** "Marked not interested." or "Status changed to Contacted." */
export function statusToast(to: string, labelOf: (status: string) => string): string {
  return to === 'not_interested' ? 'Marked not interested.' : `Status changed to ${labelOf(to)}.`
}

/** The undo is offered for a short while only, so it can never overwrite a later change by someone else. */
export const undoAlive = (entry: UndoEntry | null, now: number = Date.now()): boolean => !!entry && now - entry.at < UNDO_MS

/** The PATCH body that restores the previous status. */
export const undoBody = (entry: UndoEntry): { status: string } => ({ status: entry.from })

/** The toast after an undo. */
export const undoneToast = (entry: UndoEntry, labelOf: (status: string) => string): string => `Status restored to ${labelOf(entry.from)}.`
