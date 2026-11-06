/**
 * NOTE FOR THE APPLICATION-FORM VIEW (features/application-form/): use this hook for every label and choice list
 * (marital status, service charges, interest level, job duration, best time, source, ...) instead of hardcoded
 * arrays. `labelFor(options, category, value)` turns a stored value into the label an admin has set,
 * `choicesFor(options, category, current)` gives a dropdown's rows (the current value stays visible even if it was
 * switched off), `toneStyle(option.tone)` gives a card colour and `FORM_OPTION_CATEGORIES` names the categories.
 * All of it is in ./form-options-model.ts. The hook returns the built-in fallback lists only when the request failed,
 * so a list is never empty because of a network error.
 *
 * One shared copy is loaded for the whole app and refreshed when an admin changes a list (live event) or on demand
 * with `refreshFormOptions()`.
 */
import { useEffect, useSyncExternalStore } from 'react'
import { api } from '@/shared/api/client'
import { live } from '@/shared/state/live'
import { FALLBACK_OPTIONS, normalizeFormOptions, type CategoryMetas, type FormOptions } from './form-options-model'

interface Snapshot { options: FormOptions; meta: CategoryMetas; loaded: boolean; failed: boolean }

let snapshot: Snapshot = { options: FALLBACK_OPTIONS, meta: {}, loaded: false, failed: false }
let inflight: Promise<void> | null = null
let loadedAt = 0

/**
 * The lists change only when an admin edits them (a live `form_options` event or `refreshFormOptions()` after a save),
 * so the API layer keeps the answer for five minutes and a failure (an older server without the route answers 404)
 * for thirty seconds instead of asking again for every component that mounts.
 */
const POLICY = { ttlMs: 300_000, staleMs: 600_000, tags: ['form_options', 'form-options', 'settings'], errorTtlMs: 30_000 } as const
const RECHECK_MS = 300_000
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((listener) => listener())

function applyPayload(payload: unknown) {
  const { options, meta } = normalizeFormOptions(payload)
  // A category the server did not send at all keeps its built-in list, so a half-configured install still works.
  snapshot = { options: { ...FALLBACK_OPTIONS, ...options }, meta, loaded: true, failed: false }
  loadedAt = Date.now()
}

/** Load (or reload) the lists from GET /form-options. A failure keeps what is shown and marks the fallback as in use. */
export function refreshFormOptions(): Promise<void> {
  if (inflight) return inflight
  inflight = api.get<unknown>('/form-options', { cache: { ...POLICY, onRevalidated: (payload) => { applyPayload(payload); emit() } } })
    .then(applyPayload)
    .catch(() => { snapshot = { ...snapshot, failed: !snapshot.loaded } })
    .finally(() => { inflight = null; emit() })
  return inflight
}

let stopLive: (() => void) | null = null
const subscribe = (listener: () => void) => {
  listeners.add(listener)
  if (listeners.size === 1) stopLive = live.subscribe(['form_options', 'settings'], () => { void refreshFormOptions() }, { debounceMs: 300 })
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) { stopLive?.(); stopLive = null }
  }
}

/** The shared form options: `{ options, meta, loaded, failed }`. Safe to call from any number of components. */
export function useFormOptions() {
  const current = useSyncExternalStore(subscribe, () => snapshot, () => snapshot)
  // Mounting never starts a second request while one runs, and a loaded copy is re-checked at most every five minutes.
  useEffect(() => { if (!inflight && (!snapshot.loaded || Date.now() - loadedAt > RECHECK_MS)) void refreshFormOptions() }, [])
  return current
}

/** Names built from the data already in the system, for free-text fields (GET /form-options/suggestions). */
export interface FormSuggestions { countries: string[]; jobPreferences: string[]; skills: string[]; institutes: string[]; companies: string[] }
const EMPTY_SUGGESTIONS: FormSuggestions = { countries: [], jobPreferences: [], skills: [], institutes: [], companies: [] }
let suggestionState: { value: FormSuggestions; at: number } = { value: EMPTY_SUGGESTIONS, at: 0 }
let suggestionFlight: Promise<void> | null = null
const suggestionListeners = new Set<() => void>()

const strings = (value: unknown): string[] => (Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.trim() !== '') : [])

/** Loaded once per minute at most; a failure leaves the lists empty and the page offers its own local suggestions instead. */
export function refreshFormSuggestions(): Promise<void> {
  if (suggestionFlight) return suggestionFlight
  suggestionFlight = api.get<Record<string, unknown>>('/form-options/suggestions', { cache: { ttlMs: 60_000, tags: ['candidates', 'form_options', 'settings'], errorTtlMs: 60_000 } })
    .then((body) => { suggestionState = { value: { countries: strings(body.countries), jobPreferences: strings(body.jobPreferences), skills: strings(body.skills), institutes: strings(body.institutes), companies: strings(body.companies) }, at: Date.now() } })
    .catch(() => { suggestionState = { ...suggestionState, at: Date.now() } })
    .finally(() => { suggestionFlight = null; suggestionListeners.forEach((listener) => listener()) })
  return suggestionFlight
}

export function useFormSuggestions(): FormSuggestions {
  const value = useSyncExternalStore((listener) => { suggestionListeners.add(listener); return () => { suggestionListeners.delete(listener) } }, () => suggestionState, () => suggestionState)
  useEffect(() => { if (Date.now() - suggestionState.at > 60_000) void refreshFormSuggestions() }, [])
  return value.value
}
