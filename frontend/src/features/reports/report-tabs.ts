import type { ReportKey } from './report-config'

/** The tabs of the Reports page, in the order they are shown. The URL carries the current one as `?tab=`. */
export const REPORT_TABS: readonly ReportKey[] = ['collections', 'funnel', 'staff', 'source', 'orders', 'rejections', 'advance']
export const DEFAULT_REPORT_TAB: ReportKey = 'funnel'

export function isReportTab(value: string | null | undefined): value is ReportKey {
  return !!value && (REPORT_TABS as readonly string[]).includes(value)
}

/**
 * Which tab the address asks for. A known `?tab=` wins. Older links (Staff Directory's "View staff report", and the
 * former /staff-report page) carry only `?staff=`, which for a supervisor means the staff performance tab.
 * Fee collections (`?tab=collections`) is the admin's alone (`invoices`, the `view_invoices` capability): for anyone else the link opens Pipeline.
 */
export function reportTabFromParams(params: URLSearchParams, supervisor: boolean, invoices = false): ReportKey {
  const tab = params.get('tab')
  if (tab === 'collections' && !invoices) return DEFAULT_REPORT_TAB
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


/**
 * The older reports (Pipeline, Recruiter performance, Sources, Order status, Rejections and the weekly staff report) are no
 * longer shown: management reads the Task report and the Lead sheet report instead. Their code and APIs stay; set this
 * to true to bring their tabs back (as one extra "Legacy" tab).
 */
export const SHOW_LEGACY_REPORTS = false

/**
 * The Accounts tab (Fee collections and Advance collections, the admin's) is not shown: the Reports screen is the three report
 * views only. The code stays and old links (?tab=collections, ?tab=advance, ?tab=accounts) still open it for the admin; set
 * this to true to show it as a tab again.
 */
export const SHOW_ACCOUNTS_TAB = false

/** The tabs of the Reports page. `legacy` exists only while SHOW_LEGACY_REPORTS is on; `accounts` is the admin's alone. */
export type PageTab = 'daily' | 'lead' | 'attendance' | 'tracker' | 'accounts' | 'legacy'
/** The reports inside Accounts. */
export const ACCOUNT_TABS: readonly ReportKey[] = ['collections', 'advance']

export function pageTabs(accounts: boolean, legacy: boolean = SHOW_LEGACY_REPORTS, showAccounts: boolean = SHOW_ACCOUNTS_TAB): { key: PageTab; label: string }[] {
  return [
    { key: 'daily', label: 'Task report' }, { key: 'lead', label: 'Lead sheet report' }, { key: 'attendance', label: 'Attendance' },
    { key: 'tracker', label: 'Client tracker' },
    ...(accounts && showAccounts ? [{ key: 'accounts' as const, label: 'Accounts' }] : []),
    ...(legacy ? [{ key: 'legacy' as const, label: 'Legacy' }] : []),
  ]
}

/**
 * Which tab the address asks for. No `?tab=` is the Task report (so the end-of-day link /reports?from=..&to=.. keeps working), and so is
 * the former Candidate monitoring (`?tab=stages`). `collections` and `advance` (and `accounts`) belong to Accounts, for the admin only.
 * An old report key opens the Task report unless the legacy reports are switched on.
 */
export function pageTabFromParams(params: URLSearchParams, accounts: boolean, legacy: boolean = SHOW_LEGACY_REPORTS): PageTab {
  const tab = params.get('tab')
  if (tab === 'lead' || tab === 'attendance' || tab === 'tracker') return tab
  if (tab === 'accounts' || tab === 'collections' || tab === 'advance') return accounts ? 'accounts' : 'daily'
  if (legacy && (isReportTab(tab) || (tab == null && params.get('staff')))) return 'legacy'
  return 'daily'
}

/** The address for choosing a page tab: the Task report needs no `tab`; dates and person belong to the page state, not the address. */
export function paramsForPageTab(params: URLSearchParams, tab: PageTab): URLSearchParams {
  const next = new URLSearchParams(params)
  for (const key of ['from', 'to', 'staff', 'view']) next.delete(key)
  if (tab === 'daily') next.delete('tab'); else next.set('tab', tab === 'legacy' ? DEFAULT_REPORT_TAB : tab)
  return next
}

/**
 * Where the former /staff-report page now lives: the Task report filtered to that person (and dates), or the Staff
 * performance tab while the legacy reports are on.
 */
export function staffReportRedirect(search: string, legacy: boolean = SHOW_LEGACY_REPORTS): string {
  const params = new URLSearchParams(search)
  if (legacy) params.set('tab', 'staff'); else params.delete('tab')
  const text = params.toString()
  return text ? `/reports?${text}` : '/reports'
}
