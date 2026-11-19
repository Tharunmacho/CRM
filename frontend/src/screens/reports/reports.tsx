import { useSearchParams } from 'react-router-dom'
import { useStore } from '@/shared/state/crm-store'
import { PageHeader } from '@/shared/ui/display'
import { Tabs } from '@/shared/ui/overlay'
import DailyReportPage, { type ReportView } from '@/features/daily-report/daily-report-page'
import { pageTabFromParams, pageTabs, paramsForPageTab, type PageTab } from '@/features/reports/report-tabs'
import { ClientTrackerReport } from '@/features/reports/client-tracker'
import OtherReports from './other-reports'
import '@/styles/tasks-page.css'
import '@/styles/daily-work.css'
import '@/styles/daily-report.css'

/**
 * Reports: three monitoring views (Daily report, Lead sheet monitoring, Candidate monitoring), built like the Tasks screen,
 * the Client tracker (the running sheet of placements by client, with its Excel file; ?tab=tracker), and for the admin a quiet Accounts tab with the fee and advance collections. The earlier reports are not shown
 * (SHOW_LEGACY_REPORTS in report-tabs.ts brings them back). The tab is in the address (?tab=lead|stages|accounts); no tab is
 * the Daily report, and an older link that names a person (/staff-report, "View staff report") opens the Daily report
 * filtered to that person.
 */
export default function ReportsPage() {
  const store = useStore()
  const [params, setParams] = useSearchParams()
  const accounts = store.can('view_invoices')
  const tab = pageTabFromParams(params, accounts)
  const choose = (key: string) => setParams(paramsForPageTab(params, key as PageTab))
  const tabs = <Tabs className="dw-tabs" tabs={pageTabs(accounts)} active={tab} onChange={choose} />
  // One DailyReportPage element for the three views: its period, staff and client choices survive moving between them.
  const view: ReportView | null = tab === 'daily' || tab === 'lead' || tab === 'stages' ? tab : null
  if (view) return <DailyReportPage view={view} tabs={tabs} />
  return <div className="tk dw">
    <PageHeader title="Reports" />
    {tabs}
    {tab === 'tracker' ? <ClientTrackerReport /> : <OtherReports embedded />}
  </div>
}
