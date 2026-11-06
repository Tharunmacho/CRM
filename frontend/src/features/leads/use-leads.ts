import { useCallback, useEffect, useRef, useState } from 'react'
import { liveInterval } from '@/shared/state/live'
import { leadApi } from './lead-api'
import type { LeadFacets, LeadPage } from './lead-model'
import type { LeadSummaryResponse } from './lead-api'

export type LeadSummaryWithGate = LeadSummaryResponse

/** Short, so results follow the typing. */
const SEARCH_DELAY_MS = 160

interface Params {
  /** The list query without paging (lead-model buildLeadQuery). */
  query: string
  page: number
  pageSize: number
  /** KPI scope: the viewer's own leads or everyone's. */
  scope: 'mine' | 'all'
  enabled?: boolean
}

/**
 * The lead list, the key figures and the filter choices. A new search keeps showing the previous page until the answer
 * arrives. The "leads" and "tasks" live topics reload everything without a page refresh (coalesced); a task change
 * only needs the figures, because the daily-work gate rides on them.
 */
export function useLeadData({ query, page, pageSize, scope: summaryScope, enabled = true }: Params) {
  const [snapshot, setSnapshot] = useState<{ data: LeadPage; scope: string } | null>(null)
  const [summary, setSummary] = useState<LeadSummaryWithGate | null>(null)
  const [facets, setFacets] = useState<LeadFacets | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [token, setToken] = useState(0)
  const scope = `${query}|${page}|${pageSize}`
  const scopeRef = useRef({ query, page, pageSize, summaryScope })
  scopeRef.current = { query, page, pageSize, summaryScope }
  const firstLoad = useRef(true)

  const loadSummary = useCallback(async (signal?: AbortSignal) => {
    try { const data = await leadApi.summary(scopeRef.current.summaryScope, signal); if (!signal?.aborted) setSummary(data) } catch { /* the figures keep their last value */ }
  }, [])
  const loadFacets = useCallback(async (signal?: AbortSignal) => {
    try { const data = await leadApi.facets(signal); if (!signal?.aborted) setFacets(data) } catch { /* the filters still work as free text elsewhere */ }
  }, [])
  const loadList = useCallback(async (signal?: AbortSignal) => {
    const current = scopeRef.current
    const key = `${current.query}|${current.page}|${current.pageSize}`
    try {
      const data = await leadApi.list(current.query, current.page, current.pageSize, signal)
      if (signal?.aborted) return
      setSnapshot({ data, scope: key })
      setError('')
    } catch (e) {
      if (signal?.aborted) return
      setError(e instanceof Error ? e.message : 'We could not load the leads. Please try again.')
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  // The list follows the search, the filters, the page and the Refresh button.
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    setLoading(true)
    const wait = firstLoad.current ? 0 : SEARCH_DELAY_MS
    firstLoad.current = false
    const timer = window.setTimeout(() => { void loadList(controller.signal) }, wait)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [enabled, scope, token, loadList])

  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    void loadSummary(controller.signal)
    void loadFacets(controller.signal)
    return () => controller.abort()
  }, [enabled, token, summaryScope, loadSummary, loadFacets])

  // Live: a lead change reloads list and figures; a task change (the daily-work gate) only the figures.
  useEffect(() => {
    if (!enabled) return
    return liveInterval(
      () => Promise.all([loadList(), loadSummary()]),
      60_000,
      ['leads', 'tasks'],
      {
        debounceMs: 500,
        onChange: (change) => {
          const leads = change.resync || change.topics.includes('leads') || change.topics.includes('*')
          void loadSummary()
          if (leads) { void loadList(); void loadFacets() }
        },
      },
    )
  }, [enabled, loadList, loadSummary, loadFacets])

  const refresh = useCallback(() => setToken((value) => value + 1), [])
  const stale = !!snapshot && snapshot.scope !== scope
  return { page: snapshot?.data ?? null, summary, facets, loading, error, stale, refresh, reloadSummary: loadSummary, reloadList: loadList }
}
