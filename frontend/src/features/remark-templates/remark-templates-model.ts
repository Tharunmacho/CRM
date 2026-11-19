/**
 * Remark templates: the short, ready-made remarks the Tasks list offers in its REMARKS dropdown (Visa in progress, PCC Pending, ...).
 * An admin keeps the list (Users > Agency settings); everybody else only picks from the active ones. No imports, so it runs under node:test.
 */

export interface RemarkTemplate { id: string; text: string; active: boolean; sortOrder: number }

/** The longest template the admin can write: a remark in a table cell must stay a short phrase. */
export const TEMPLATE_MAX = 120

/** Whitespace and capitals do not make two templates different ("Visa  in progress" is "visa in progress"). */
export const sameText = (a: string, b: string): boolean => squash(a).toLowerCase() === squash(b).toLowerCase()
export const squash = (text: string): string => text.replace(/\s+/g, ' ').trim()

const asText = (value: unknown): string => (typeof value === 'string' ? value : '')

/** The list the server sent ({items:[...]}, or a bare array), cleaned up and in display order; anything that is not a template is dropped. */
export function normalizeTemplates(payload: unknown): RemarkTemplate[] {
  const raw = Array.isArray(payload) ? payload : payload && typeof payload === 'object' && Array.isArray((payload as { items?: unknown }).items) ? (payload as { items: unknown[] }).items : []
  const out: RemarkTemplate[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const row = item as Record<string, unknown>
    const id = row.id == null ? '' : String(row.id)
    const text = squash(asText(row.text))
    if (!id || !text) continue
    const order = Number(row.sortOrder)
    out.push({ id, text, active: row.active !== false, sortOrder: Number.isFinite(order) ? order : 0 })
  }
  return sortTemplates(out)
}

/** The server's order: by sortOrder, then by name so two equal orders never jump around. */
export function sortTemplates<T extends Pick<RemarkTemplate, 'sortOrder' | 'text'>>(list: readonly T[]): T[] {
  return [...list].sort((a, b) => a.sortOrder - b.sortOrder || a.text.localeCompare(b.text))
}

export const activeTemplates = (list: readonly RemarkTemplate[]): RemarkTemplate[] => list.filter((item) => item.active)

/**
 * What is wrong with a template's text, in a calm sentence; '' when it is fine. `ignoreId` is the template being renamed
 * (it may keep its own text).
 */
export function templateProblem(text: string, existing: readonly Pick<RemarkTemplate, 'id' | 'text'>[], ignoreId?: string): string {
  const clean = squash(text)
  if (!clean) return 'Write the remark first.'
  if (clean.length > TEMPLATE_MAX) return `Keep it under ${TEMPLATE_MAX} characters (it is ${clean.length}).`
  if (existing.some((item) => item.id !== ignoreId && sameText(item.text, clean))) return 'That remark is already in the list.'
  return ''
}

export interface OrderChange { id: string; sortOrder: number }

/**
 * Moving one template up or down: the whole list is renumbered 1, 2, 3... in the new order (two templates may share a sortOrder on the
 * server) and only the rows whose number changed are returned, ready to be sent one PATCH each. Nothing changes at either end of the list.
 */
export function moveTemplate(list: readonly Pick<RemarkTemplate, 'id' | 'sortOrder' | 'text'>[], id: string, direction: -1 | 1): OrderChange[] {
  const ordered = sortTemplates(list)
  const from = ordered.findIndex((item) => item.id === id)
  const to = from + direction
  if (from < 0 || to < 0 || to >= ordered.length) return []
  const next = [...ordered]
  ;[next[from], next[to]] = [next[to], next[from]]
  const before = new Map(ordered.map((item) => [item.id, item.sortOrder]))
  return next.map((item, index) => ({ id: item.id, sortOrder: index + 1 })).filter((change) => before.get(change.id) !== change.sortOrder)
}

/** The same list as it will look after `moveTemplate` is applied (so the screen can show the move before the server answers). */
export function applyOrder<T extends Pick<RemarkTemplate, 'id' | 'sortOrder' | 'text'>>(list: readonly T[], changes: readonly OrderChange[]): T[] {
  const byId = new Map(changes.map((change) => [change.id, change.sortOrder]))
  return sortTemplates(list.map((item) => (byId.has(item.id) ? { ...item, sortOrder: byId.get(item.id) as number } : item)))
}

/**
 * What the dropdown's "+ Add a template" box does with what was typed: a calm sentence when it cannot be added (empty, too long, already
 * in the active list), otherwise the cleaned text to send. A template the server already holds (switched off, or added a moment ago by
 * someone else) is not an error there: the server answers with that template and `createdText` picks it.
 */
export function newTemplateCheck(text: string, active: readonly Pick<RemarkTemplate, 'id' | 'text'>[]): { ok: true; text: string } | { ok: false; message: string } {
  const message = templateProblem(text, active)
  return message ? { ok: false, message } : { ok: true, text: squash(text) }
}

/** The text to save as the remark after POST /remark-templates: the item the server returned (new, or the existing one), else what was typed. */
export function createdText(payload: unknown, typed: string): string {
  const row = payload && typeof payload === 'object' ? (payload as { item?: unknown }).item ?? payload : null
  const text = row && typeof row === 'object' ? (row as { text?: unknown }).text : null
  return typeof text === 'string' && squash(text) ? squash(text) : squash(typed)
}
