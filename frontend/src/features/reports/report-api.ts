import { useEffect, useState } from 'react'
import { api, fileHref, takeEarlyRead } from '@/shared/api/client'
import { peekReport, rememberReport } from '@/shared/state/snapshot-cache'

export interface Option { id: string; label: string }
export interface OrderMetrics { id: string; ref: string; company: string; client: string; designation: string; status: string; openings: number; matched: number; selected: number; deployed: number }
export interface ReportResult {
  generatedAt: string; timezone: string
  filters: { fromDate: string; toDate: string; clientId: string | null; orderId: string | null; designationId: string | null; staffId: string | null }
  notes: string[]
  summary: { applications: number; active: number; deployed: number; intake: number }
  options: { clients: Option[]; orders: Option[]; designations: Option[]; staff: Option[] }
  pipeline: { stage: number; label: string; reached: number; active: number; progressed: number; closed: number; dropOffPercent: number; lostToNext?: number | null; lostToNextPercent?: number | null }[]
  staff: { id: string; name: string; assigned: number; completedTasks: number; averageStageHours: number | null; durationSamples: number; escalations: number; missedUpdates: number }[]
  stageHours: { stage: number; label: string; completed: number; averageHours: number | null }[]
  sources: { source: string; label: string; count: number }[]
  accounts: { account: string; count: number }[]
  orders: OrderMetrics[]
  rejections: { stage: number; reason: string; label: string; count: number }[]
  advances: { id: string; candidateId: string; candidate: string; orderId: string; order: string; date: string; amount: string; currency: string }[]
  advanceTotals: { currency: string; amount: string }[]
  advanceMonths: { month: string; currency: string; amount: string }[]
}
export interface DashboardTask {
  id: string; title: string; candidateId: string; candidate: string; applicationId: string | null; stage: number | null
  ownerId: string; owner: string; dueAt: string; overdue: boolean; dueToday: boolean; escalated: boolean; notUpdated: boolean
}
export interface DashboardResult {
  generatedAt: string; timezone: string; date: string; staffId: string | null; staffOptions: Option[]
  today: { total: number; email: number; whatsapp: number; walk_in: number; referral: number; other: number }
  taskCounts: { open: number; dueToday: number; overdue: number; escalated: number; notUpdated: number }
  pipeline: { stage: number; label: string; count: number; applications: { id: string; candidateId: string; candidate: string; order: string }[] }[]
  orders: OrderMetrics[]; tasks: DashboardTask[]; myTasks: DashboardTask[]
}

/** How often a visible report re-reads, and the least time between two reads triggered by focus. */
const REFRESH_MS = 60_000
const FOCUS_REFRESH_MIN_MS = 15_000

/**
 * Clear prior scope immediately, abort on navigation, and refresh visible desks.
 *
 * The newest answer for each user and query is remembered, so returning to a screen (or reloading the page,
 * for the dashboard) shows the previous numbers at once while the server confirms them.
 */
export function useReportData<T>(path: string, sessionId: string, revision = '') {
  const [snapshot, setSnapshot] = useState<{ key: string; data: T } | null>(null)
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null)
  const [reload, setReload] = useState(0)
  const [reading, setReading] = useState<string | null>(null)
  const key = `${sessionId}:${path}:${revision}`
  useEffect(() => {
    let active = true
    let request: AbortController | null = null
    let finishedAt = 0
    let first = true
    const read = async (force = false) => {
      if (request || document.visibilityState === 'hidden') return
      if (!force && Date.now() - finishedAt < FOCUS_REFRESH_MIN_MS) return
      const controller = new AbortController(); request = controller
      setReading(key)
      try {
        // index.html starts the default dashboard read before the app loads; use it for the first read.
        const early = first && path === '/dashboard' ? await takeEarlyRead<T>('dashboard', path) : null
        first = false
        const data = early ?? await api.get<T>(path, { signal: controller.signal })
        if (active) { rememberReport(sessionId, path, revision, data); setSnapshot({ key, data }); setFailure(null) }
      } catch (error) {
        if (active && !controller.signal.aborted) setFailure({ key, message: error instanceof Error ? error.message : 'Unable to load this view.' })
      } finally { finishedAt = Date.now(); if (request === controller) request = null; if (active) setReading(null) }
    }
    void read(true)
    const interval = window.setInterval(() => void read(true), REFRESH_MS)
    const visible = () => void read()
    document.addEventListener('visibilitychange', visible)
    window.addEventListener('focus', visible)
    return () => { active = false; request?.abort(); window.clearInterval(interval); document.removeEventListener('visibilitychange', visible); window.removeEventListener('focus', visible) }
  }, [key, path, reload])
  const remembered = snapshot?.key === key ? snapshot.data : peekReport<T>(sessionId, path, revision)
  return { data: remembered, error: failure?.key === key ? failure.message : '', refreshing: reading === key, refresh: () => setReload(n => n + 1) }
}

export async function downloadReport(report: string, format: 'xlsx' | 'pdf', query: string) {
  const controller = new AbortController()
  const deadline = window.setTimeout(() => controller.abort(), 90_000)
  try {
  const response = await fetch(fileHref(`/api/reports/${report}/export?${query}&format=${format}`), { credentials: 'include', signal: controller.signal })
  if (!response.ok) {
    const failure = await response.json().catch(() => null)
    throw new Error(typeof failure?.detail === 'string' ? failure.detail : 'The export could not be created. Please try again.')
  }
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url; link.download = `${report}-${new URLSearchParams(query).get('from_date')}.${format}`
  document.body.appendChild(link); link.click(); link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) {
    if (controller.signal.aborted) throw new Error('The export took too long. Try a shorter reporting period or retry.')
    throw error
  } finally { window.clearTimeout(deadline) }
}
