import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '@/shared/api/client'
import { agencyDay } from '@/shared/lib/task-time'
import { useStore } from '@/shared/state/crm-store'
import { liveInterval } from '@/shared/state/live'
import { validDate, type StaffDirectoryResponse } from './model'

export function useStaffDirectory() {
  const store = useStore()
  const [params, setParams] = useSearchParams()
  const today = agencyDay(new Date(), store.agencyTimezone)
  const from = validDate(params.get('from')) ? params.get('from')! : `${today.slice(0, 7)}-01`
  const to = validDate(params.get('to')) ? params.get('to')! : today
  const validPeriod = from <= to
  const sessionKey = `${store.me.id}:${store.me.role}`
  const key = `${sessionKey}:${from}:${to}`
  const [snapshot, setSnapshot] = useState<{ key: string; sessionKey: string; data: StaffDirectoryResponse } | null>(null)
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const reload = useRef<() => void>(() => {})

  useEffect(() => {
    const controller = new AbortController()
    let running = false
    let again = false // a change arrived while a read was running: that read may predate it
    if (!validPeriod) return () => controller.abort()
    const load = async () => {
      if (running || controller.signal.aborted) return
      running = true
      setBusyKey(key)
      try {
        const data = await api.get<StaffDirectoryResponse>(`/staff-directory?${new URLSearchParams({ from, to })}`, { signal: controller.signal })
        if (!controller.signal.aborted) { setSnapshot({ key, sessionKey, data }); setFailure(null) }
      } catch (cause) {
        if (!controller.signal.aborted) setFailure({ key, message: cause instanceof Error ? cause.message : 'We could not load the staff directory. Try again.' })
      } finally {
        running = false
        if (!controller.signal.aborted) setBusyKey(previous => previous === key ? null : previous)
        if (again && !controller.signal.aborted) { again = false; void load() }
      }
    }
    reload.current = () => { void load() }
    const visibleRefresh = () => { if (document.visibilityState === 'visible') void load() }
    void load()
    const stopLive = liveInterval(visibleRefresh, 30_000, ['attendance', 'users', 'tasks', 'candidates', 'applications'], {
      debounceMs: 1_000,
      onChange: () => { if (document.visibilityState !== 'visible') return; if (running) again = true; else void load() },
    })
    window.addEventListener('focus', visibleRefresh)
    document.addEventListener('visibilitychange', visibleRefresh)
    return () => {
      controller.abort(); stopLive(); reload.current = () => {}
      window.removeEventListener('focus', visibleRefresh)
      document.removeEventListener('visibilitychange', visibleRefresh)
    }
  }, [from, to, validPeriod, key, sessionKey])

  const updateParam = (name: string, value: string) => setParams(previous => {
    const next = new URLSearchParams(previous)
    if (value) next.set(name, value); else next.delete(name)
    return next
  }, { replace: true })

  const result = snapshot?.key === key ? snapshot.data : null
  // Identity and live workload may remain visible while another performance period
  // loads. Period-based metrics must use result, never lastResult.
  const lastResult = snapshot?.sessionKey === sessionKey ? snapshot.data : null
  const error = !validPeriod ? 'The start date must be on or before the end date.' : failure?.key === key ? failure.message : ''
  const setPeriod = (range: { from: string; to: string } | null) => setParams(previous => {
    const next = new URLSearchParams(previous)
    if (range && !(range.from === `${today.slice(0, 7)}-01` && range.to === today)) { next.set('from', range.from); next.set('to', range.to) } else { next.delete('from'); next.delete('to') }
    return next
  }, { replace: true })
  const refresh = () => {
    if (!validPeriod) {
      setParams(previous => { const next = new URLSearchParams(previous); next.delete('from'); next.delete('to'); return next }, { replace: true })
    } else reload.current()
  }
  return { result, lastResult, loading: !result && !error, refreshing: busyKey === key && !!result, error, params, updateParam, setPeriod, today, period: { from, to }, refresh }
}
