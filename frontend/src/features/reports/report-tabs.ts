import type { ReportKey } from './report-config'

/** The tabs of the Reports page, in the order they are shown. The URL carries the current one as `?tab=`. */
export const REPORT_TABS: readonly ReportKey[] = ['funnel', 'staff', 'source', 'orders', 'rejections', 'advance']
export const DEFAULT_REPORT_TAB: ReportKey = 'funnel'

export function isReportTab(value: string | null | undefined): value is ReportKey {
  return !!value && (REPORT_TABS as readonly string[]).includes(value)
}

/**
 * Which tab the address asks for. A known `?tab=` wins. Older links (Staff Directory's "View staff report", and the
 * former /staff-report page) carry only `?staff=`, which for a supervisor means the staff performance tab.
 */
export function reportTabFromParams(params: URLSearchParams, supervisor: boolean): ReportKey {
  const tab = params.get('tab')
  if (isReportTab(tab)) return tab
  return supervisor && params.get('staff') ? 'staff' : DEFAULT_REPORT_TAB
}

/**
 * The address for choosing a tab. Dates and staff in the address belong to the tab you are leaving (a week on the
 * staff performance tab, a starting filter elsewhere), so they are dropped; the default tab needs no `tab`.
 */
export function paramsForTab(params: URLSearchParams, tab: ReportKey): URLSearchParams {
  const next = new URLSearchParams(params)
  for (const key of ['from', 'to', 'staff']) next.delete(key)
  if (tab === DEFAULT_REPORT_TAB) next.delete('tab'); else next.set('tab', tab)
  return next
}

/** Where the former /staff-report page now lives: the staff performance tab, keeping any week or person already chosen. */
export function staffReportRedirect(search: string): string {
  const params = new URLSearchParams(search)
  params.set('tab', 'staff')
  return `/reports?${params}`
}
