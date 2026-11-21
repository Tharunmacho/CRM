import { useSearchParams } from 'react-router-dom'
import { useStore } from '@/shared/state/crm-store'
import { Tabs } from '@/shared/ui/overlay'
import DailyReportPage, { type ReportView } from '@/features/daily-report/daily-report-page'
import { ReportsHeader } from '@/features/daily-report/reports-header'
import { AttendanceReportTab } from '@/features/daily-report/report-attendance-tab'
import { pageTabFromParams, pageTabs, paramsForPageTab, type PageTab } from '@/features/reports/report-tabs'
import { ClientTrackerReport } from '@/features/reports/client-tracker'
import OtherReports from './other-reports'
import '@/styles/tasks-page.css'
import '@/styles/daily-work.css'
import '@/styles/daily-report.css'

/**
 * Reports: Task report (the 13 stages, recruiter by recruiter), Lead sheet report, Attendance and the Client tracker (the running sheet of
 * placements by client, with its Excel file; ?tab=tracker), and for the admin a quiet Accounts tab with the fee and advance collections.
 * The earlier reports are not shown (SHOW_LEGACY_REPORTS in report-tabs.ts brings them back). The tab is in the address
 * (?tab=lead|attendance|tracker|accounts); no tab is the Task report (an old ?tab=stages link too), and an older link that names a person
 * (/staff-report, "View staff report") opens the Task report filtered to that person.
 */
export default function ReportsPage() {
  const store = useStore()
  const [params, setParams] = useSearchParams()
  const accounts = store.can('view_invoices')
  const tab = pageTabFromParams(params, accounts)
  const choose = (key: string) => setParams(paramsForPageTab(params, key as PageTab))
  const tabs = <Tabs className="dw-tabs" tabs={pageTabs(accounts)} active={tab} onChange={choose} />
  // One DailyReportPage element for the two period views: its period, staff and client choices survive moving between them.
  const view: ReportView | null = tab === 'daily' || tab === 'lead' ? tab : null
  if (view) return <DailyReportPage view={view} tabs={tabs} />
  return <div className="tk dw">
    <ReportsHeader />
    {tabs}
    {tab === 'attendance' ? <AttendanceReportTab /> : tab === 'tracker' ? <ClientTrackerReport /> : <OtherReports embedded />}
  </div>
}
