/**
 * The Lead sheet's status templates over the API. One shared copy of the list serves every status select on the screen: it is requested
 * once (the API layer keeps the answer and joins simultaneous requests) and is read again when somebody changes the list (a live `leads`
 * event, or this person's own add / rename / hide / delete).
 *
 * GET /lead-status-templates -> {items:[{id,name,tone,active,builtin,sortOrder,countsAs}]} (active only; an admin or MD with ?all=1 also gets hidden ones),
 * POST /lead-status-templates {name, tone?} (anybody signed in), PATCH /lead-status-templates/{id} {name?, active?}, DELETE /lead-status-templates/{id} (admin or MD).
 */
import { useEffect, useSyncExternalStore } from 'react'
import { ApiError, api } from '@/shared/api/client'
import { live } from '@/shared/state/live'
import { registerStatusTemplates } from './lead-model'
import {
  COUNTS_AS, existingFromError, normalizeStatusTemplates, settleStatus, withPendingStatus, withoutStatus, activeStatusTemplates,
  type LeadStatusTemplate, type StatusTone,
} from './lead-status-model'

interface Snapshot { items: LeadStatusTemplate[]; loaded: boolean; failed: boolean }

const EMPTY: Snapshot = { items: [], loaded: false, failed: false }
let snapshot: Snapshot = EMPTY
let inflight: Promise<void> | null = null
let sequence = 0
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((listener) => listener())

const URL_PATH = '/lead-status-templates'
/** Kept for the session and dropped by this person's own writes (same first path word), or by a live change on the lead sheet. A failed read (an older server answers 404) is not repeated for 30 seconds. */
const POLICY = { ttlMs: 3_600_000, tags: ['lead-status-templates', 'lead_status_templates', 'leads'], errorTtlMs: 30_000 } as const

function show(next: Snapshot) {
  snapshot = next
  registerStatusTemplates(next.items)   // chips anywhere find a template's tone from this copy
  emit()
}

/** Load (or reload) the active list. A failure keeps what is shown. */
export function refreshLeadStatusTemplates(): Promise<void> {
  if (inflight) return inflight
  inflight = api.get<unknown>(URL_PATH, { cache: POLICY })
    .then((payload) => { show({ items: activeStatusTemplates(normalizeStatusTemplates(payload)), loaded: true, failed: false }) })
    .catch(() => { show({ ...snapshot, loaded: true, failed: true }) })
    .finally(() => { inflight = null })
  return inflight
}

/** Forget the shared copy and read it again (sign-out, or the admin saved a change). */
export function resetLeadStatusTemplates(): Promise<void> {
  show(EMPTY)
  return refreshLeadStatusTemplates()
}

/** The list as the screens hold it right now (tests and non-React callers). */
export const currentLeadStatusTemplates = (): Snapshot => snapshot

let stopLive: (() => void) | null = null
const subscribe = (listener: () => void) => {
  listeners.add(listener)
  if (listeners.size === 1) stopLive = live.subscribe(['leads'], () => { void refreshLeadStatusTemplates() }, { debounceMs: 3000 })
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) { stopLive?.(); stopLive = null }
  }
}

/** The shared active templates: `{ items, loaded, failed }`. Safe to call from any number of selects; the first one starts the single request. */
export function useLeadStatusTemplates(): Snapshot {
  const current = useSyncExternalStore(subscribe, () => snapshot, () => snapshot)
  useEffect(() => { if (!snapshot.loaded && !inflight) void refreshLeadStatusTemplates() }, [])
  return current
}

/* ------------------------------------------------------------------ Add (anybody) */

/**
 * Add a status for everybody. The new status is in the list at once (so it can be selected while the request is on its way); if the server
 * refuses, it is taken out again and the error is thrown for the caller to show. A duplicate the server already holds (added a moment ago by
 * somebody else) is not an error: that status is returned. Returns the saved template.
 */
export async function addLeadStatus(name: string, tone: StatusTone): Promise<LeadStatusTemplate> {
  const pending: LeadStatusTemplate = { id: `pending:${++sequence}`, name, tone, active: true, builtin: false, sortOrder: Number.MAX_SAFE_INTEGER, countsAs: COUNTS_AS[tone], pending: true }
  show({ ...snapshot, items: withPendingStatus(snapshot.items, pending) })
  try {
    const created = normalizeStatusTemplates([await api.post<unknown>(URL_PATH, { name, tone })])[0]
    if (!created) throw new Error('The status could not be added. Please try again.')
    show({ ...snapshot, items: settleStatus(snapshot.items, pending.id, created) })
    return created
  } catch (failure) {
    const existing = failure instanceof ApiError ? existingFromError(failure.detail) : null
    if (existing && existing.active) {
      show({ ...snapshot, items: settleStatus(snapshot.items, pending.id, existing) })
      return existing
    }
    show({ ...snapshot, items: withoutStatus(snapshot.items, pending.id) })
    throw failure instanceof Error ? failure : new Error('The status could not be added. Please try again.')
  }
}

/* ------------------------------------------------------------------ Admin or MD */

/** Every template, hidden ones too (admin or MD only). Never cached: the manager sees exactly what the server holds. */
export async function listAllLeadStatusTemplates(): Promise<LeadStatusTemplate[]> {
  return normalizeStatusTemplates(await api.get<unknown>(`${URL_PATH}?all=1`, { fresh: true }))
}

const changed = async <T>(call: Promise<T>): Promise<T> => {
  const result = await call
  await refreshLeadStatusTemplates()   // the write already dropped the cached list; read it again so every select has the change
  return result
}

export const renameLeadStatus = (id: string, name: string) => changed(api.patch<unknown>(`${URL_PATH}/${encodeURIComponent(id)}`, { name }))
export const setLeadStatusActive = (id: string, active: boolean) => changed(api.patch<unknown>(`${URL_PATH}/${encodeURIComponent(id)}`, { active }))
export const deleteLeadStatus = (id: string) => changed(api.del<unknown>(`${URL_PATH}/${encodeURIComponent(id)}`))
