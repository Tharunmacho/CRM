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

export interface BulkOptions { ownerId?: string; assigneeIds?: string[]; status?: string; confirm?: string; sort?: string; direction?: 'asc' | 'desc' }

/** The POST /api/leads/bulk body. A filter selection also sends the count the person saw, so a list that changed meanwhile is refused. */
export function bulkBody(selection: Selection, filter: Record<string, string | string[]>, matchingTotal: number, action: BulkAction, options: BulkOptions = {}): Record<string, unknown> {
  const body: Record<string, unknown> = { action }
  if (selection.allMatching) { body.filter = filter; body.expectedCount = matchingTotal } else body.ids = selection.ids
  if (options.assigneeIds?.length) body.assigneeIds = options.assigneeIds
  else if (options.ownerId) body.ownerId = options.ownerId
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
  /** Several recruiters chosen: the leads are shared equally between exactly these people (names in the order shown). */
  recruiterNames?: string[]
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
      if (isUnassign(input.ownerName)) {
        return {
          title: `Make ${what} unassigned`,
          lines: [`${scope} will be taken back from their recruiter and will not be assigned to anyone.`, 'The recruiter no longer sees them, their follow-up reminders go, and the status and history stay. Assign them again from the Unassigned filter. Converted leads are never moved and leads nobody owns are skipped.'],
          confirmLabel: `Make ${what} unassigned`, danger: false, typed: false,
        }
      }
      const names = input.recruiterNames ?? []
      const who = names.length > 1 ? `${joinNames(names)}, shared equally (${splitSizes(n, names.length).map(count).join(', ')})`
        : names.length === 1 ? names[0]
        : input.ownerName === 'auto' || !input.ownerName ? 'the available recruiters, spread evenly' : input.ownerName
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

/* ------------------------------------------------- several recruiters, one split */

/**
 * How many of `total` leads each of `recruiters` people gets when the leads are shared as equally as possible, biggest share
 * first: 10 leads over 3 recruiters is [4, 3, 3]. The server (backend/app/services/lead_import.py split_equally) gives everyone
 * total // n and the total % n left over one each to the recruiters with the fewest open leads, so the sizes are the same
 * whatever the workloads; only WHO gets the extra lead depends on them.
 */
export function splitSizes(total: number, recruiters: number): number[] {
  if (!Number.isFinite(total) || !Number.isFinite(recruiters) || recruiters < 1) return []
  const whole = Math.max(0, Math.floor(total))
  const people = Math.floor(recruiters)
  const base = Math.floor(whole / people)
  const extra = whole % people
  return Array.from({ length: people }, (_, index) => base + (index < extra ? 1 : 0))
}

/** "Asha, Ravi and Mina". */
export const joinNames = (names: string[]): string => (names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`)

/** What the picker says under the list: "10 leads over 3 recruiters: 4, 3, 3". `total` null = not known yet. */
export function splitSummary(total: number | null, chosen: number, available: number): string {
  const what = total === null ? 'the leads' : `${count(total)} ${leads(total)}`
  if (chosen === 0) {
    return available > 0
      ? `Nobody ticked, so ${what} will be spread between all ${available} available ${available === 1 ? 'recruiter' : 'recruiters'}, balancing their current workload.`
      : 'No recruiter is available right now.'
  }
  if (chosen === 1) return `${what[0].toUpperCase()}${what.slice(1)} to 1 recruiter.`
  return total === null ? `The leads will be shared equally between ${chosen} recruiters.` : `${what[0].toUpperCase()}${what.slice(1)} over ${chosen} recruiters: ${splitSizes(total, chosen).map(count).join(', ')}`
}

/** The request fields for the recruiters ticked: nothing ticked is the "spread evenly" shortcut, one or more are shared equally. */
export const assignChoice = (assigneeIds: string[]): { ownerId: string } | { assigneeIds: string[] } => {
  const unique = [...new Set(assigneeIds)]
  return unique.length ? { assigneeIds: unique } : { ownerId: 'auto' }
}

/** The import fields for the upload dialog's "Assign new leads to": nothing, the recruiters ticked (shared equally), or all available ones. */
export const uploadAssignment = (canAssign: boolean, nobody: boolean, recruiters: string[]): { assignTo?: string; assigneeIds?: string[] } =>
  !canAssign || nobody ? {} : recruiters.length ? { assigneeIds: [...new Set(recruiters)] } : { assignTo: 'auto' }

/** Tick or untick one person. */
export const toggleRecruiter = (selected: string[], id: string, on: boolean): string[] => (on ? (selected.includes(id) ? selected : [...selected, id]) : selected.filter((item) => item !== id))

/** Everyone who can take leads now: active recruiters who are not on leave. */
export const availableRecruiters = <T extends { id: string; role: string; active: boolean; onLeave: boolean }>(users: T[]): T[] =>
  users.filter((user) => user.active && user.role === 'staff' && !user.onLeave)

/** "Sangeetha 4, Yuvaraj 3, Mina 3" from the server's per-recruiter counts. */
export const ownersLine = (owners: { name: string; count: number }[] | undefined): string => (owners ?? []).filter((o) => o.count > 0).map((o) => `${o.name} ${count(o.count)}`).join(', ')

/** The toast after assigning: the usual "N leads assigned, K skipped" plus who got how many when it was shared. */
export function assignToast(result: Pick<BulkResult, 'updated' | 'skipped' | 'skippedReasons' | 'owners'>, verb = 'assigned'): string {
  const line = result.owners && result.owners.length > 1 ? ownersLine(result.owners) : ''
  return line ? `${resultToast(result, verb)} ${line}.` : resultToast(result, verb)
}

/** The owner choice that means "nobody": the server reads `ownerId: 'none'` as taking the leads back. */
export const UNASSIGNED = 'none'
export const isUnassign = (ownerId?: string | null): boolean => ownerId === UNASSIGNED

/** The confirmation of taking one lead back from its recruiter (the drawer's "Make unassigned"). */
export function unassignOneCopy(name: string, ownerName: string): ConfirmCopy {
  const who = ownerName || 'the current recruiter'
  return {
    title: 'Make this lead unassigned',
    lines: [
      `${name || 'This lead'} will be taken back from ${who} and will not be assigned to anyone.`,
      `${who} no longer sees it and their follow-up reminder for it goes. The status and history stay.`,
      'You can assign it to someone again later. A converted lead cannot be moved.',
    ],
    confirmLabel: 'Make unassigned', danger: false, typed: false,
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
export const resultVerb = (action: BulkAction, ownerId?: string | null): string =>
  (action === 'delete' ? 'deleted' : action === 'assign' ? (isUnassign(ownerId) ? 'made unassigned' : 'assigned') : 'updated')

/* -------------------------------------------------------------------- undo */

/** One status change that can still be taken back. */
export interface UndoEntry { leadId: string; name: string; from: string; to: string; at: number }

export function undoEntry(lead: { id: string; name?: string | null; status: string; statusLabel?: string | null }, to: string, at: number = Date.now()): UndoEntry | null {
  const from = (lead.statusLabel ?? '').trim() || lead.status   // the select value: a template's name, else the built-in code
  return from === to ? null : { leadId: lead.id, name: lead.name ?? '', from, to, at }
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
