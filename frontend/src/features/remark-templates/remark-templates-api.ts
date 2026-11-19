/**
 * The remark templates over the API. One shared copy of the ACTIVE list serves every dropdown on the Tasks page: it is requested once
 * (the API layer keeps the answer and joins simultaneous requests) and is read again when an admin changes the list
 * (`refreshRemarkTemplates()`, called by the admin card after each change, or a live `settings` / `remark_templates` event).
 *
 * GET /remark-templates -> {items:[{id,text,active,sortOrder}]} (active only, ordered; an admin with ?all=1 also gets the inactive ones),
 * POST /remark-templates {text}, PATCH /remark-templates/{id} {text?, active?, sortOrder?}, DELETE /remark-templates/{id} (admin).
 */
import { useEffect, useSyncExternalStore } from 'react'
import { api } from '@/shared/api/client'
import { live } from '@/shared/state/live'
import { activeTemplates, normalizeTemplates, type RemarkTemplate } from './remark-templates-model'

interface Snapshot { items: RemarkTemplate[]; loaded: boolean; failed: boolean }

const EMPTY: Snapshot = { items: [], loaded: false, failed: false }
let snapshot: Snapshot = EMPTY
let inflight: Promise<void> | null = null
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((listener) => listener())

/** The list changes only when an admin edits it, so it is kept for the session; a failed read (an older server answers 404) is not repeated for 30 seconds. */
const POLICY = { ttlMs: 3_600_000, tags: ['remark-templates', 'remark_templates', 'settings'], errorTtlMs: 30_000 } as const

/** Load (or reload) the active list. A failure keeps what is shown. */
export function refreshRemarkTemplates(): Promise<void> {
  if (inflight) return inflight
  inflight = api.get<unknown>('/remark-templates', { cache: POLICY })
    .then((payload) => { snapshot = { items: activeTemplates(normalizeTemplates(payload)), loaded: true, failed: false } })
    .catch(() => { snapshot = { ...snapshot, loaded: true, failed: true } })
    .finally(() => { inflight = null; emit() })
  return inflight
}

/** Forget the shared copy and read it again (sign-out, or the admin saved a change). */
export function resetRemarkTemplates(): Promise<void> {
  snapshot = EMPTY
  emit()
  return refreshRemarkTemplates()
}

let stopLive: (() => void) | null = null
const subscribe = (listener: () => void) => {
  listeners.add(listener)
  if (listeners.size === 1) stopLive = live.subscribe(['remark_templates', 'settings'], () => { void refreshRemarkTemplates() }, { debounceMs: 500 })
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) { stopLive?.(); stopLive = null }
  }
}

/** The shared active templates: `{ items, loaded, failed }`. Safe to call from any number of cells; the first one starts the single request. */
export function useRemarkTemplates(): Snapshot {
  const current = useSyncExternalStore(subscribe, () => snapshot, () => snapshot)
  useEffect(() => { if (!snapshot.loaded && !inflight) void refreshRemarkTemplates() }, [])
  return current
}

/* ------------------------------------------------------------------ Admin */

/** Every template, inactive ones too (admin only). Never cached: the admin sees exactly what the server holds. */
export async function listAllRemarkTemplates(): Promise<RemarkTemplate[]> {
  return normalizeTemplates(await api.get<unknown>('/remark-templates?all=1', { fresh: true }))
}

const changed = async <T>(call: Promise<T>): Promise<T> => {
  const result = await call
  // The write already dropped the cached list (same first path word); read it again so every dropdown has the change before the caller goes on.
  await refreshRemarkTemplates()
  return result
}

/** Returns the created item, or the existing one when the text is already there (see `createdText`). */
export const createRemarkTemplate = (text: string) => changed(api.post<unknown>('/remark-templates', { text }))
export const updateRemarkTemplate = (id: string, patch: { text?: string; active?: boolean; sortOrder?: number }) =>
  changed(api.patch<unknown>(`/remark-templates/${encodeURIComponent(id)}`, patch))
export const deleteRemarkTemplate = (id: string) => changed(api.del<unknown>(`/remark-templates/${encodeURIComponent(id)}`))
