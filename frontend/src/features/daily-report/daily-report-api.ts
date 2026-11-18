import { api, fileHref } from '@/shared/api/client'
import { apiQuery, drillQuery, historyQuery, normaliseMyDay } from './daily-report-view'
import type { DailyQuery, DailyReport, DrillPage, HistoryPage, MyDay } from './daily-report-types'

/** The two calls the client needs; the real one is the app's api client, tests pass a stand-in. */
export interface Http {
  get<T>(path: string, options?: { signal?: AbortSignal; fresh?: boolean }): Promise<T>
  post<T>(path: string, body?: unknown, options?: { signal?: AbortSignal }): Promise<T>
}

/** The endpoints of the Daily CRM Work Report. Every call carries the session cookie through the app's api client. */
export function createDailyReportApi(http: Http) {
  return {
    report: (query: DailyQuery, signal?: AbortSignal) => http.get<DailyReport>(`/reports/daily-work?${apiQuery(query)}`, { signal }),
    drilldown: (metric: string, query: DailyQuery, page: number, pageSize = 25, signal?: AbortSignal) =>
      http.get<DrillPage>(`/reports/daily-work/drilldown?${drillQuery(metric, query, page, pageSize)}`, { signal }),
    history: (query: DailyQuery, page: number, pageSize = 25, signal?: AbortSignal) =>
      http.get<HistoryPage>(`/reports/daily-work/history?${historyQuery(query, page, pageSize)}`, { signal }),
    /** The server sends the caller's staff row here; it is turned into the same shape a submit returns. */
    mine: async (date: string, signal?: AbortSignal): Promise<MyDay> => normaliseMyDay(await http.get<Record<string, unknown>>(`/reports/daily-work/mine?date=${encodeURIComponent(date)}`, { signal, fresh: true })),
    submit: async (date: string, note: string | null): Promise<MyDay> => ({ ...normaliseMyDay(await http.post<Record<string, unknown>>('/reports/daily-work/submit', { date, note })), submitted: true }),
  }
}

export const dailyReportApi = createDailyReportApi(api)

/** Save a blob as a file the person can open (the browser's own download). */
export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url; link.download = name
  document.body.appendChild(link); link.click(); link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** A CSV with a byte-order mark, so Excel reads names in any language correctly. */
export function saveCsv(text: string, name: string) {
  saveBlob(new Blob(['﻿', text], { type: 'text/csv;charset=utf-8' }), name)
}

/** The Excel file of the same figures and filters as the screen. */
export async function downloadDailyExport(query: DailyQuery) {
  const controller = new AbortController()
  const deadline = window.setTimeout(() => controller.abort(), 90_000)
  try {
    const response = await fetch(fileHref(`/api/reports/daily-work/export?${apiQuery(query)}`), { credentials: 'include', signal: controller.signal })
    if (!response.ok) {
      const failure = await response.json().catch(() => null)
      throw new Error(typeof failure?.detail === 'string' ? failure.detail : 'The Excel file could not be created. Please try again.')
    }
    const named = /filename="?([^";]+)"?/i.exec(response.headers.get('content-disposition') ?? '')?.[1]
    saveBlob(await response.blob(), named || `daily-work-report-${query.from === query.to ? query.from : `${query.from}-to-${query.to}`}.xlsx`)
  } catch (error) {
    if (controller.signal.aborted) throw new Error('The export took too long. Try a shorter period or retry.')
    throw error
  } finally { window.clearTimeout(deadline) }
}
