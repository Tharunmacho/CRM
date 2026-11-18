/**
 * Merging a duplicate candidate into the existing one: what the confirmation says, which button a person sees, and the search for the
 * candidate to merge into. No React and no imports of values, so it is tested on its own (tests/application-form.test.mjs).
 *
 * The server does the work (POST /api/candidates/{duplicate}/merge-into/{existing}); `dryRun` answers what would move, be skipped or
 * refuse, so the dialog can say it before anything changes.
 */

/**
 * The merge UI is switched off (the owner deletes a duplicate by hand). The code, this model and the backend endpoint stay, so
 * setting this to true brings back the "Merge duplicate into…" header button and the "Merge into CI-xxx" button beside the
 * "Already in the CRM" note. Every place that mounts merge UI checks this one constant.
 */
export const SHOW_MERGE_UI = false

export interface MergeRefusal { code: string; message: string; canConfirm: boolean }
export interface MergedCounts { documents: number; jobs: number; notes: number; tasks: number; applications: number }
export interface MergeSkip { kind: string; name: string; reason: string }
export interface MergeParty { id: string; careId: string | null; name: string }

export interface MergePlan {
  allowed: boolean
  alreadyMerged: boolean
  source: MergeParty | null
  mergedInto: MergeParty | null
  moved: MergedCounts
  skipped: MergeSkip[]
  refusals: MergeRefusal[]
  /** The existing candidate is deployed: only documents and empty details are added, its stages are not touched. */
  targetDeployed: boolean
  /** The server's plain sentence about that (shown above the plan). */
  notice: string | null
  /** After a real merge: what happened to the documents an identity check had stopped. */
  identityCheck: { accepted: number; stillNeedReview: number; requeued: number } | null
}

const rec = (value: unknown): Record<string, unknown> => (value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {})
const num = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0)
const str = (value: unknown): string => (typeof value === 'string' ? value : '')

function party(value: unknown): MergeParty | null {
  const entry = rec(value)
  return typeof entry.id === 'string' ? { id: entry.id, careId: str(entry.careId) || null, name: str(entry.name) } : null
}

/** The server's answer, whatever it holds, as a plan that can always be read. */
export function parseMergePlan(value: unknown): MergePlan {
  const body = rec(value)
  const moved = rec(body.moved)
  const check = rec(body.identityCheck)
  return {
    // A real merge has no `allowed` (it either happened or answered 409): a body without refusals is allowed.
    allowed: typeof body.allowed === 'boolean' ? body.allowed : true,
    alreadyMerged: body.alreadyMerged === true,
    source: party(body.source),
    mergedInto: party(body.mergedInto),
    moved: { documents: num(moved.documents), jobs: num(moved.jobs), notes: num(moved.notes), tasks: num(moved.tasks), applications: num(moved.applications) },
    skipped: (Array.isArray(body.skipped) ? body.skipped : []).map((item) => ({ kind: str(rec(item).kind), name: str(rec(item).name), reason: str(rec(item).reason) })),
    targetDeployed: body.targetDeployed === true,
    notice: str(body.notice) || null,
    refusals: (Array.isArray(body.refusals) ? body.refusals : []).map((item) => ({ code: str(rec(item).code), message: str(rec(item).message), canConfirm: rec(item).canConfirm === true })),
    identityCheck: Object.keys(check).length ? { accepted: num(check.accepted), stillNeedReview: num(check.stillNeedReview), requeued: num(check.requeued) } : null,
  }
}

const count = (value: number, one: string, many = `${one}s`) => `${value} ${value === 1 ? one : many}`

/** "12 documents, 3 notes, 1 task": only what is moving; empty when nothing is. */
export function movedSummary(moved: Pick<MergedCounts, 'documents' | 'notes' | 'tasks'> & Partial<MergedCounts>): string {
  return [
    moved.documents > 0 && count(moved.documents, 'document'),
    moved.notes > 0 && count(moved.notes, 'note'),
    moved.tasks > 0 && count(moved.tasks, 'task'),
    (moved.applications ?? 0) > 0 && count(moved.applications ?? 0, 'placement'),
  ].filter(Boolean).join(', ')
}

/** The confirmation sentence: what moves, that the duplicate is then removed, and that the existing candidate's own files stay. */
export function mergeConfirmText(plan: Pick<MergePlan, 'moved'>, sourceLabel: string, targetLabel: string): string {
  const moving = movedSummary(plan.moved)
  const head = moving ? `${moving} will be moved to ${targetLabel}; this candidate ${sourceLabel} is then removed.` : `Nothing needs to move to ${targetLabel}; this candidate ${sourceLabel} is removed.`
  return `${head} Files already on ${targetLabel} are kept as they are.`
}

export type MergeOffer =
  | { kind: 'merge'; targetId: string; targetLabel: string }
  | { kind: 'ask_admin'; text: string }

/**
 * What a person is offered beside "Already in the CRM as CI-139 · Name": an admin gets the primary Merge button, everybody else is told
 * to ask one. Nothing when the document names this very candidate (no merge with oneself).
 */
export function mergeOffer(duplicate: { candidateId: string; careId: string | null; name: string } | null | undefined, viewer: { isAdmin: boolean; candidateId: string }): MergeOffer | null {
  if (!duplicate || !duplicate.candidateId || duplicate.candidateId === viewer.candidateId) return null
  if (!viewer.isAdmin) return { kind: 'ask_admin', text: 'Ask an admin to merge it' }
  return { kind: 'merge', targetId: duplicate.candidateId, targetLabel: duplicate.careId || duplicate.name || 'that candidate' }
}

/** The label on the button that opens the merge: "Merge into CI-139". */
export const mergeButtonLabel = (targetLabel: string): string => `Merge into ${targetLabel}`

export type MergePhase = 'checking' | 'ready' | 'merging' | 'failed'

export interface MergeDialogView {
  title: string
  /** Paragraphs, in order. */
  lines: string[]
  /** Skipped files, shown as a short list. */
  skipped: string[]
  /** An admin must tick "these are the same person" before the merge button works (the only refusal the server lets an admin override). */
  needsIdentityConfirm: boolean
  confirmEnabled: boolean
  confirmLabel: string
  cancelLabel: string
  busy: boolean
  /** The reasons shown as a warning, not as the plan. */
  blocked: boolean
}

/** Everything the dialog shows, from one state. Pure, so the same rules are tested without a browser. */
export function mergeDialogView(
  state: { phase: MergePhase; plan: MergePlan | null; error?: string | null; identityConfirmed?: boolean },
  labels: { source: string; target: string },
): MergeDialogView {
  const base: MergeDialogView = {
    title: `Merge ${labels.source} into ${labels.target}`, lines: [], skipped: [], needsIdentityConfirm: false, confirmEnabled: false,
    confirmLabel: `Merge and remove ${labels.source}`, cancelLabel: 'Cancel', busy: false, blocked: false,
  }
  if (state.phase === 'checking') return { ...base, lines: ['Checking what would move…'], busy: true }
  if (state.phase === 'merging') return { ...base, lines: ['Merging. This can take a moment…'], busy: true, confirmLabel: 'Merging…' }
  if (state.phase === 'failed' || !state.plan) return { ...base, blocked: true, lines: [state.error || 'The merge could not be checked. Try again.'], cancelLabel: 'Close' }
  const plan = state.plan
  if (plan.alreadyMerged) return { ...base, lines: [`${labels.source} was already merged into ${labels.target}.`], cancelLabel: 'Close' }
  if (!plan.allowed) {
    const confirmable = plan.refusals.length > 0 && plan.refusals.every((refusal) => refusal.canConfirm)
    return {
      ...base, blocked: true, cancelLabel: confirmable ? 'Cancel' : 'Close', needsIdentityConfirm: confirmable,
      confirmEnabled: confirmable && state.identityConfirmed === true, confirmLabel: `Merge anyway and remove ${labels.source}`,
      lines: [...plan.refusals.map((refusal) => refusal.message), ...(plan.refusals.length ? [] : ['This merge is not allowed.'])],
    }
  }
  return {
    ...base, confirmEnabled: true,
    lines: [...(plan.notice ? [plan.notice] : plan.targetDeployed ? [`${labels.target} is deployed: documents and empty details are added, its stages are not touched.`] : []),
      mergeConfirmText(plan, labels.source, labels.target)],
    skipped: plan.skipped.map((item) => item.name ? `${item.name}: ${item.reason}` : item.reason).filter(Boolean),
  }
}

/** What the page says after a merge: who went where, and what became of the documents an identity check had stopped. */
export function mergeDoneText(plan: Pick<MergePlan, 'identityCheck'>, labels: { source: string; target: string }): string {
  const parts = [`${labels.source} was merged into ${labels.target}.`]
  const check = plan.identityCheck
  if (check && check.accepted > 0) parts.push(`${count(check.accepted, 'stopped document')} ${check.accepted === 1 ? 'is' : 'are'} now read for ${labels.target}.`)
  if (check && check.stillNeedReview + check.requeued > 0) parts.push('Some documents still need a look.')
  return parts.join(' ')
}

export interface MergeSearchable { id: string; ref?: string | null; careId?: string | null; name: string; phone?: string | null }

const digits = (value: string) => value.replace(/\D/g, '')

/** The candidates to offer when someone searches for the one to merge into: by name, Care India ID, CRM reference or phone number. */
export function findMergeTargets<T extends MergeSearchable>(candidates: readonly T[], query: string, excludeId: string, limit = 8): T[] {
  const text = query.trim().toLowerCase()
  if (text.length < 2) return []
  const number = digits(text)
  const rank = (candidate: T): number => {
    const care = (candidate.careId ?? '').toLowerCase()
    if (care === text || (candidate.ref ?? '').toLowerCase() === text) return 0
    if (care.includes(text) || (candidate.ref ?? '').toLowerCase().includes(text)) return 1
    if (candidate.name.toLowerCase().startsWith(text)) return 2
    if (candidate.name.toLowerCase().includes(text)) return 3
    if (number.length >= 4 && digits(candidate.phone ?? '').includes(number)) return 4
    return 9
  }
  return candidates
    .filter((candidate) => candidate.id !== excludeId)
    .map((candidate) => ({ candidate, rank: rank(candidate) }))
    .filter((entry) => entry.rank < 9)
    .sort((a, b) => a.rank - b.rank || a.candidate.name.localeCompare(b.candidate.name))
    .slice(0, limit)
    .map((entry) => entry.candidate)
}
