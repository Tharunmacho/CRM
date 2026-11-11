import { fileHref } from '@/shared/api/client'
import { useReportData } from '@/features/reports/report-api'
import { exportName, queryFor, type TravelledFilters, type TravelledResult } from './travelled-model'

/**
 * The travelled list for these filters. It is read like a report: the last answer for the same filters shows at once while the
 * server confirms it, and it is re-read when the desk changes (a deployment or a corrected travel date).
 */
export function useTravelled(filters: TravelledFilters, sessionId: string) {
  const query = queryFor(filters)
  return useReportData<TravelledResult>(`/travelled${query ? `?${query}` : ''}`, sessionId)
}

/** Download the same list as a file, with the filters of the screen. */
export async function downloadTravelled(filters: TravelledFilters, format: 'xlsx' | 'csv') {
  const controller = new AbortController()
  const deadline = window.setTimeout(() => controller.abort(), 90_000)
  try {
    const query = new URLSearchParams(queryFor(filters))
    query.set('format', format)
    const response = await fetch(fileHref(`/api/travelled/export?${query.toString()}`), { credentials: 'include', signal: controller.signal })
    if (!response.ok) {
      const failure = await response.json().catch(() => null)
      throw new Error(typeof failure?.detail === 'string' ? failure.detail : 'The file could not be created. Please try again.')
    }
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = exportName(filters, format)
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) {
    if (controller.signal.aborted) throw new Error('The file took too long to create. Narrow the dates and try again.')
    throw error
  } finally {
    window.clearTimeout(deadline)
  }
}
