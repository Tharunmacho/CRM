/**
 * The daily-work gate of the Lead sheet: staff must finish today's daily tasks (Tasks > Daily work) before they work
 * leads. Admin and manager are exempt. The server decides (GET /api/leads/summary carries `dailyWorkGate`, and the work
 * endpoints answer 403 `daily_work_pending`); these pure helpers turn that into the banner text, the progress, the
 * disabled-action reason and the small store the sidebar lock reads. No React, so tests load this file directly.
 */

export interface LeadGate {
  locked: boolean
  open: number
  total: number
  postponed: number
  nextDueAt?: string | null
  message?: string | null
}

export const GATE_REASON = "Finish today's tasks first"

/** The same facts from the daily-work summary the shell already loads (used for the sidebar lock before the lead sheet is opened). */
export function gateFromDailySummary(summary: { total: number; open: number; postponed: number; complete: boolean; nextDueAt?: string | null } | null | undefined): LeadGate | null {
  if (!summary) return null
  return { locked: summary.total > 0 && !summary.complete, open: summary.open, total: summary.total, postponed: summary.postponed, nextDueAt: summary.nextDueAt ?? null }
}

export type GateState = 'exempt' | 'locked' | 'open' | 'unknown'

/** Admin and manager never see the gate. A staff member is locked until the server says otherwise. */
export function gateState(role: string | undefined, gate: LeadGate | null | undefined): GateState {
  if (role === 'admin' || role === 'manager') return 'exempt'
  if (!gate) return 'unknown'
  return gate.locked ? 'locked' : 'open'
}

/** The reason to show on a disabled work action, or null when the person may work leads. */
export function gateReason(state: GateState): string | null {
  return state === 'locked' ? GATE_REASON : null
}

/** Work actions are only disabled when the gate is known to be locked; while it is loading the server still has the last word. */
export const gateBlocks = (state: GateState): boolean => state === 'locked'

/** "3 of 5 done" for the banner (the done count is total minus what is still open). */
export function gateProgress(gate: Pick<LeadGate, 'open' | 'total'>): { done: number; total: number; percent: number; label: string } {
  const total = Math.max(0, gate.total)
  const done = Math.min(total, Math.max(0, total - Math.max(0, gate.open)))
  return { done, total, percent: total ? Math.round((done / total) * 100) : 0, label: `${done} of ${total} done` }
}

export function gatePostponedLabel(postponed: number): string | null {
  if (postponed <= 0) return null
  return `${postponed} carried over from earlier days`
}

/** The server's message without its first sentence (the banner's heading already says it), or the summary built from the counts. */
export function gateBody(gate: Pick<LeadGate, 'open' | 'total' | 'postponed' | 'message'>): string {
  const text = (gate.message ?? '').replace(/^\s*Finish today'?s tasks first\.?\s*/i, '').trim()
  return text || `${gateSummaryText(gate)} The lead sheet is read-only until your daily tasks are complete.`
}

/** The banner's second line: what is left, in words. */
export function gateSummaryText(gate: Pick<LeadGate, 'open' | 'total' | 'postponed'>): string {
  const left = Math.max(0, gate.open)
  const base = `${left} ${left === 1 ? 'task' : 'tasks'} left today.`
  const carried = gatePostponedLabel(gate.postponed)
  return carried ? `${base} ${carried}.` : base
}

/** "42m", "1h 05m": time until the next deadline from a millisecond span; null when there is none or it has passed. */
export function deadlineCountdown(nextDueAt: string | null | undefined, nowMs: number, skewMs = 0): { label: string; late: boolean } | null {
  if (!nextDueAt) return null
  const due = new Date(nextDueAt).getTime()
  if (!Number.isFinite(due)) return null
  const diff = due - (nowMs + skewMs)
  const minutes = Math.floor(Math.abs(diff) / 60_000)
  const text = minutes < 1 ? '<1m' : minutes < 60 ? `${minutes}m` : minutes < 1440 ? `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m` : `${Math.floor(minutes / 1440)}d ${Math.floor((minutes % 1440) / 60)}h`
  return diff > 0 ? { label: `Next deadline in ${text}`, late: false } : { label: `Deadline passed ${text} ago`, late: true }
}

/* ------------------------------------------------- tiny store for the sidebar */

type Listener = () => void
let shared: { userId: string; gate: LeadGate | null } = { userId: '', gate: null }
const listeners = new Set<Listener>()

/** The shell's daily-work prompt publishes what it already loaded; the sidebar reads it. No request of its own. */
export function publishGate(userId: string, gate: LeadGate | null) {
  const same = shared.userId === userId && shared.gate?.locked === gate?.locked && shared.gate?.open === gate?.open && shared.gate?.total === gate?.total
  shared = { userId, gate }
  if (!same) listeners.forEach((listener) => listener())
}
export const subscribeGate = (listener: Listener) => { listeners.add(listener); return () => { listeners.delete(listener) } }
export const readGate = (userId: string): LeadGate | null => (shared.userId === userId ? shared.gate : null)
