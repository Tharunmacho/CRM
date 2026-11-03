import { fileHref } from '@/shared/api/client'

export const staffReportPath = (range: { from: string; to: string }, staffId?: string) =>
  `/reports/staff-weekly${staffId ? `/${encodeURIComponent(staffId)}` : ''}?${new URLSearchParams({ from_date: range.from, to_date: range.to })}`

/** Download the weekly staff report as a file; the server builds it from the stored rows at this moment. */
export async function downloadStaffReport(format: 'xlsx' | 'csv', range: { from: string; to: string }) {
  const controller = new AbortController()
  const deadline = window.setTimeout(() => controller.abort(), 90_000)
  try {
    const query = new URLSearchParams({ from_date: range.from, to_date: range.to, format })
    const response = await fetch(fileHref(`/api/reports/staff-weekly/export?${query}`), { credentials: 'include', signal: controller.signal })
    if (!response.ok) {
      const failure = await response.json().catch(() => null)
      throw new Error(typeof failure?.detail === 'string' ? failure.detail : 'The file could not be created. Please try again.')
    }
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `staff-report-${range.from}-to-${range.to}.${format}`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) {
    if (controller.signal.aborted) throw new Error('The file took too long to create. Try a shorter period or retry.')
    throw error
  } finally {
    window.clearTimeout(deadline)
  }
}
