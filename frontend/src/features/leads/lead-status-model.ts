/**
 * Lead status templates, as plain data: what "+ Add a status" checks before it asks the server, and how the shared list changes while a
 * new status is being saved (shown at once, taken back if the server refuses). The server (backend/app/api/routers/lead_status_templates.py)
 * has the last word on every rule here. No imports, so it runs under node:test.
 */

export type StatusTone = 'neutral' | 'positive' | 'warning' | 'negative'

export interface LeadStatusTemplate {
  id: string
  name: string
  tone: StatusTone
  active: boolean
  builtin: boolean
  sortOrder: number
  /** The built-in status the template counts as in the reports (contacted, interested, not_interested). */
  countsAs?: string
  /** Shown while its POST is still on the way. */
  pending?: boolean
}

/** The tones a new status can have, with what each one means for the reports (the server's rule, in the person's words). */
export const STATUS_TONES: { value: StatusTone; label: string; hint: string }[] = [
  { value: 'neutral', label: 'Neutral', hint: 'Counts as contacted' },
  { value: 'positive', label: 'Positive', hint: 'Counts as interested' },
  { value: 'warning', label: 'Tried, not reached', hint: 'Counts as contacted, not reached' },
  { value: 'negative', label: 'Negative', hint: 'Counts as not interested; no follow-up' },
]
export const DEFAULT_TONE: StatusTone = 'neutral'

export const NAME_MAX = 40

/** The value of the last entry of every status select: it opens the inline box that adds a status. */
export const ADD_STATUS_VALUE = '__add_status'
export const ADD_STATUS_LABEL = '+ Add a status'
export const isAddStatus = (value: string): boolean => value === ADD_STATUS_VALUE

export const squash = (text: string): string => text.replace(/\s+/g, ' ').trim()
export const sameName = (a: string, b: string): boolean => squash(a).toLowerCase() === squash(b).toLowerCase()

const TONES = new Set<string>(['neutral', 'positive', 'warning', 'negative'])
export const isTone = (value: unknown): value is StatusTone => typeof value === 'string' && TONES.has(value)

/** The list the server sent ({items:[...]} or a bare array): cleaned, built-in first (their order), the rest A to Z; rows that are not templates are dropped. */
export function normalizeStatusTemplates(payload: unknown): LeadStatusTemplate[] {
  const raw = Array.isArray(payload) ? payload : payload && typeof payload === 'object' && Array.isArray((payload as { items?: unknown }).items) ? (payload as { items: unknown[] }).items : []
  const out: LeadStatusTemplate[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const row = item as Record<string, unknown>
    const id = row.id == null ? '' : String(row.id)
    const name = typeof row.name === 'string' ? squash(row.name) : ''
    if (!id || !name) continue
    const order = Number(row.sortOrder)
    out.push({
      id, name, tone: isTone(row.tone) ? row.tone : DEFAULT_TONE, active: row.active !== false, builtin: row.builtin === true,
      sortOrder: Number.isFinite(order) ? order : 0, countsAs: typeof row.countsAs === 'string' ? row.countsAs : undefined,
    })
  }
  return sortStatusTemplates(out)
}

export function sortStatusTemplates<T extends Pick<LeadStatusTemplate, 'builtin' | 'sortOrder' | 'name'>>(list: readonly T[]): T[] {
  return [...list].sort((a, b) => Number(b.builtin) - Number(a.builtin)
    || (a.builtin ? a.sortOrder - b.sortOrder : 0) || a.name.toLowerCase().localeCompare(b.name.toLowerCase()))
}

export const activeStatusTemplates = (list: readonly LeadStatusTemplate[]): LeadStatusTemplate[] => list.filter((item) => item.active)

/**
 * What "+ Add a status" does with what was typed: a calm sentence when it cannot be added (empty, too long, a comma, already a
 * standard status or in the list), otherwise the cleaned name and tone to send. `standard` are the built-in statuses' labels and codes.
 */
export function newStatusCheck(name: string, tone: string, existing: readonly Pick<LeadStatusTemplate, 'name'>[], standard: readonly string[]):
  { ok: true; name: string; tone: StatusTone } | { ok: false; message: string } {
  const clean = squash(name)
  if (!clean) return { ok: false, message: 'Write the status name first.' }
  if (clean.length > NAME_MAX) return { ok: false, message: `Keep it under ${NAME_MAX} characters (it is ${clean.length}).` }
  if (clean.includes(',')) return { ok: false, message: 'A status name cannot contain a comma.' }
  if (standard.some((item) => sameName(item, clean))) return { ok: false, message: 'That is already one of the standard statuses.' }
  if (existing.some((item) => sameName(item.name, clean))) return { ok: false, message: 'That status is already in the list.' }
  return { ok: true, name: clean, tone: isTone(tone) ? tone : DEFAULT_TONE }
}

/** The counting code the server gives a tone (only for the optimistic row; the server's answer replaces it). */
export const COUNTS_AS: Record<StatusTone, string> = { neutral: 'contacted', positive: 'interested', warning: 'contacted', negative: 'not_interested' }

/** The shared list with the new status shown at once (marked pending) ... */
export function withPendingStatus(list: readonly LeadStatusTemplate[], pending: LeadStatusTemplate): LeadStatusTemplate[] {
  return sortStatusTemplates([...list.filter((item) => !sameName(item.name, pending.name)), { ...pending, pending: true }])
}

/** ... taken out again when the server refuses it ... */
export const withoutStatus = (list: readonly LeadStatusTemplate[], id: string): LeadStatusTemplate[] => list.filter((item) => item.id !== id)

/** ... or swapped for the server's own row when it accepts it. */
export function settleStatus(list: readonly LeadStatusTemplate[], pendingId: string, real: LeadStatusTemplate): LeadStatusTemplate[] {
  return sortStatusTemplates([...list.filter((item) => item.id !== pendingId && item.id !== real.id), real])
}

/** The server's refusal of a duplicate carries the status that already exists; null for any other failure. */
export function existingFromError(detail: unknown): LeadStatusTemplate | null {
  if (!detail || typeof detail !== 'object') return null
  const code = (detail as { code?: unknown }).code
  const existing = (detail as { existing?: unknown }).existing
  if (code !== 'status_exists' || !existing) return null
  return normalizeStatusTemplates([existing])[0] ?? null
}
