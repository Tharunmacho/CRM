import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '@/shared/api/client'
import { liveInterval } from '@/shared/state/live'
import { jobRequirementsApi } from './job-requirements-api'
import {
  PAGE_LIMIT, applyPatch, listQuery, mergePatches, mergeRead, mergeSaved, readBlocked, sortedFields, upsert, upsertOption,
  type FieldType, type ListFilters, type OptionKind, type OwnerChoice, type RequirementOption, type RequirementSummary, type RequirementField, type RequirementItem, type RequirementPatch,
} from './job-requirements-model'

export interface RowSave { state: 'saving' | 'saved' | 'error'; message?: string }

const messageOf = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback)

/**
 * The sheet's data. Typing is optimistic: the change shows at once and is sent in the background. A save that fails keeps what was typed on
 * screen, marks the row ("Not saved", with Retry) and holds the unsaved change; the list is not re-read from the server while anything is unsaved,
 * so a refresh can never wipe a typed value. The list is read again when the window regains focus.
 */
export function useJobRequirements(filters: ListFilters, people: readonly OwnerChoice[] = []) {
  const filterKey = JSON.stringify(filters)
  const [items, setItems] = useState<RequirementItem[]>([])
  const [fields, setFields] = useState<RequirementField[]>([])
  const [options, setOptions] = useState<RequirementOption[]>([])
  const [total, setTotal] = useState(0)
  const [summary, setSummary] = useState<RequirementSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [rowSave, setRowSave] = useState<Record<string, RowSave>>({})
  const unsaved = useRef<Record<string, RequirementPatch>>({})
  const inFlight = useRef(0)
  /** A live read was skipped (or thrown away) because a save or create was going on: it is run once, when that has finished. */
  const rerun = useRef(false)
  /** Rows this person created a moment ago: a read that started before the create finished does not have them yet, and must not drop them. */
  const justCreated = useRef<Set<string>>(new Set())
  const timers = useRef<Record<string, number>>({})
  const alive = useRef(true)
  const readNo = useRef(0)
  const itemsRef = useRef(items)
  itemsRef.current = items
  /** Names of the people a requirement can be given to: an optimistic owner change shows their names at once. */
  const peopleRef = useRef(people)
  peopleRef.current = people

  useEffect(() => { alive.current = true; return () => { alive.current = false; Object.values(timers.current).forEach((timer) => window.clearTimeout(timer)) } }, [])

  const mark = useCallback((id: string, value: RowSave | null) => {
    window.clearTimeout(timers.current[id])
    setRowSave((current) => {
      const next = { ...current }
      if (value) next[id] = value; else delete next[id]
      return next
    })
    if (value?.state === 'saved') timers.current[id] = window.setTimeout(() => { if (alive.current) setRowSave((current) => (current[id]?.state === 'saved' ? Object.fromEntries(Object.entries(current).filter(([key]) => key !== id)) : current)) }, 2200)
  }, [])

  const read = useCallback(async (options: { silent?: boolean; fresh?: boolean } = {}) => {
    const mine = ++readNo.current
    if (!options.silent) { setLoading(true); setError('') }
    try {
      // A live refresh keeps the rows already loaded ("load more"), so a change made by someone else never shortens the sheet under the reader.
      const keep = options.silent ? Math.min(2000, Math.max(PAGE_LIMIT, itemsRef.current.length)) : PAGE_LIMIT
      const page = await jobRequirementsApi.list(listQuery(filters, keep), { fresh: options.fresh })
      if (!alive.current || mine !== readNo.current) return
      // A save or create started while this read was on its way: what the server sent may be older than what is on screen, so do not apply it;
      // it is read again as soon as the sending has finished.
      if (options.silent && readBlocked(inFlight.current, Object.keys(unsaved.current).length)) { rerun.current = true; return }
      // Unsaved changes win over what the server still has; a row created a moment ago stays even if this read began before it existed.
      const held = unsaved.current
      const ids = new Set(page.items.map((item) => item.id))
      justCreated.current.forEach((id) => { if (ids.has(id)) justCreated.current.delete(id) })
      setItems((current) => mergeRead(page.items, held, current, justCreated.current, peopleRef.current))
      setFields(sortedFields(page.fields))
      if (page.options) setOptions(page.options)
      setTotal(page.total)
      if (page.summary) setSummary(page.summary)
      setError('')
    } catch (failure) {
      if (!alive.current || mine !== readNo.current) return
      if (!options.silent) setError(messageOf(failure, 'The job requirements could not be loaded.'))
    } finally {
      if (alive.current && mine === readNo.current) setLoading(false)
    }
  }, [filterKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { void read() }, [read])

  /** Run the one read that was skipped while something was being sent, now that nothing is. */
  const flush = useCallback(() => {
    if (!rerun.current || !alive.current || readBlocked(inFlight.current, Object.keys(unsaved.current).length)) return
    rerun.current = false
    void read({ silent: true, fresh: true })
  }, [read])

  // Read again when the person comes back to the window (somebody else may have added rows), unless something typed is still unsaved.
  useEffect(() => {
    const again = () => {
      if (document.visibilityState === 'hidden') return
      if (readBlocked(inFlight.current, Object.keys(unsaved.current).length)) { rerun.current = true; return }
      void read({ silent: true, fresh: true })
    }
    window.addEventListener('focus', again)
    document.addEventListener('visibilitychange', again)
    return () => { window.removeEventListener('focus', again); document.removeEventListener('visibilitychange', again) }
  }, [read])

  // Live: a requirement added, edited or closed by somebody else appears within seconds (topic `job_requirements`), with a poll fallback
  // (30 s while the live stream is down). Nothing is re-read while a typed change is unsaved or being sent, so a refresh never wipes it.
  useEffect(() => liveInterval(() => {
    if (document.visibilityState === 'hidden') return
    if (readBlocked(inFlight.current, Object.keys(unsaved.current).length)) { rerun.current = true; return }   // remembered: it runs when the sending is done
    return read({ silent: true, fresh: true })
  }, 30_000, ['job_requirements'], { debounceMs: 400, safetyMs: 30_000 }), [read])

  const loadMore = useCallback(async () => {
    setLoadingMore(true)
    try {
      const page = await jobRequirementsApi.list(listQuery(filters, PAGE_LIMIT, itemsRef.current.length), { fresh: true })
      if (!alive.current) return
      setItems((current) => [...current, ...page.items.filter((item) => !current.some((entry) => entry.id === item.id))])
      setTotal(page.total)
    } catch (failure) {
      if (alive.current) setError(messageOf(failure, 'More rows could not be loaded.'))
    } finally {
      if (alive.current) setLoadingMore(false)
    }
  }, [filterKey]) // eslint-disable-line react-hooks/exhaustive-deps

  /** Send a change. Resolves true when saved; false when it failed (the typed value stays and the row says so). */
  const save = useCallback(async (id: string, patch: RequirementPatch): Promise<boolean> => {
    const outgoing = unsaved.current[id] ? mergePatches(unsaved.current[id], patch) : patch
    setItems((current) => current.map((item) => (item.id === id ? applyPatch(item, patch, peopleRef.current) : item)))
    inFlight.current += 1
    mark(id, { state: 'saving' })
    try {
      const saved = await jobRequirementsApi.patch(id, outgoing)
      delete unsaved.current[id]
      if (alive.current) { setItems((current) => current.map((item) => (item.id === id ? mergeSaved(item, saved) : item))); mark(id, { state: 'saved' }) }
      if (outgoing.rememberNewFields) rerun.current = true   // the server may have added field names to the catalog: read it again once nothing is being sent
      return true
    } catch (failure) {
      unsaved.current[id] = outgoing
      if (alive.current) mark(id, { state: 'error', message: failure instanceof ApiError && failure.status === 404 ? 'This row no longer exists. Refresh the page.' : messageOf(failure, 'Could not save') })
      return false
    } finally {
      inFlight.current -= 1
      flush()
    }
  }, [mark, flush])

  const retry = useCallback(async (id: string) => {
    const held = unsaved.current[id]
    if (held) await save(id, held)
  }, [save])

  /** A blank row on top, saved at once. Resolves with its id (for focusing the Client name), or null when it failed. */
  const create = useCallback(async (body: RequirementPatch = {}): Promise<string | null> => {
    inFlight.current += 1   // a read that lands while the row is being created must not be applied over it
    try {
      const created = await jobRequirementsApi.create(body)
      justCreated.current.add(created.id)
      if (alive.current) { setItems((current) => upsert(current, created)); setTotal((count) => count + 1) }
      return created.id
    } finally {
      inFlight.current -= 1
      flush()
    }
  }, [flush])

  const remove = useCallback(async (id: string) => {
    await jobRequirementsApi.remove(id)
    delete unsaved.current[id]
    justCreated.current.delete(id)
    if (alive.current) { setItems((current) => current.filter((item) => item.id !== id)); setTotal((count) => Math.max(0, count - 1)); mark(id, null) }
    flush()
  }, [mark, flush])

  const uploadAd = useCallback(async (id: string, file: File) => {
    const saved = await jobRequirementsApi.uploadAd(id, file)
    if (alive.current) setItems((current) => current.map((item) => (item.id === id ? { ...mergeSaved(item, saved, true) } : item)))
  }, [])

  const removeAd = useCallback(async (id: string) => {
    const saved = await jobRequirementsApi.removeAd(id)
    if (alive.current) setItems((current) => current.map((item) => (item.id === id ? { ...mergeSaved(item, saved, true), adUrl: null, adName: null } : item)))
  }, [])

  const addField = useCallback(async (label: string, type: FieldType) => {
    const field = await jobRequirementsApi.createField(label, type)
    if (alive.current) setFields((current) => sortedFields([...current.filter((entry) => entry.id !== field.id), field]))
  }, [])

  const renameField = useCallback(async (id: string, label: string) => {
    const field = await jobRequirementsApi.renameField(id, label)
    if (alive.current) setFields((current) => sortedFields(current.map((entry) => (entry.id === id ? { ...entry, label: field.label } : entry))))
  }, [])

  const deleteField = useCallback(async (id: string) => {
    await jobRequirementsApi.deleteField(id)
    if (alive.current) {
      setFields((current) => current.filter((entry) => entry.id !== id))
      setItems((current) => current.map((item) => { const custom = { ...item.custom }; delete custom[id]; return { ...item, custom } }))
    }
  }, [])

  const createOption = useCallback(async (kind: OptionKind, label: string, standard = false) => {
    const option = await jobRequirementsApi.createOption(kind, label, standard)
    if (alive.current) setOptions((current) => upsertOption(current, option))
    return option
  }, [])

  const updateOption = useCallback(async (id: string, body: { label?: string; standard?: boolean }) => {
    const option = await jobRequirementsApi.updateOption(id, body)
    if (alive.current) setOptions((current) => upsertOption(current, option))
    return option
  }, [])

  const deleteOption = useCallback(async (id: string) => {
    await jobRequirementsApi.deleteOption(id)
    if (alive.current) setOptions((current) => current.filter((option) => option.id !== id))
  }, [])

  return { items, fields, options, total, summary, loading, loadingMore, error, rowSave, reload: read, loadMore, save, retry, create, remove, uploadAd, removeAd, addField, renameField, deleteField, createOption, updateOption, deleteOption }
}
