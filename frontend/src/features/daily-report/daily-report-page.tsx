import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AlertCircle, FileSpreadsheet, Loader2, Printer, RefreshCw } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { useStore } from '@/shared/state/crm-store'
import { Button } from '@/shared/ui/primitives'
import { downloadDailyExport } from './daily-report-api'
import { DrilldownDrawer } from './drilldown-drawer'
import { MyDayCard } from './my-day-card'
import { ReportToolbar } from './daily-report-toolbar'
import { ReportsHeader } from './reports-header'
import { HistoryDrawer, SubmissionPanel } from './submission-panels'
import { LiveBadge } from './report-parts'
import { LeadReportView } from './report-lead'
import { TaskReportView } from './report-task'
import { useAgencyToday } from './use-agency-today'
import { useDailyReport } from './use-daily-report'
import {
  AGENCY_TIMEZONE, apiQuery, asksAllTime, choiceFromParams, DEFAULT_CHOICE, drillTitle, isOutdatedServer, parseChoice, reportPeople, resolveRange, serialiseChoice, showsReport, type RangeChoice,
} from './daily-report-view'
import type { DailyReport, StaffRow } from './daily-report-types'
import '@/styles/tasks-page.css'
import '@/styles/daily-work.css'
import '@/styles/daily-work-board.css'
import '@/styles/daily-report.css'
import '@/styles/report-management.css'

/** 'daily' is the Task report (the default tab), 'lead' the Lead sheet report. */
export type ReportView = 'daily' | 'lead'
const STORAGE_KEY = 'crm.daily-report.range'

function readChoice(): RangeChoice {
  try { return parseChoice(window.localStorage.getItem(STORAGE_KEY)) } catch { return DEFAULT_CHOICE }
}
function saveChoice(choice: RangeChoice) {
  if (choice.preset === 'range') return // a range from an older link is shown, never remembered
  try { window.localStorage.setItem(STORAGE_KEY, serialiseChoice(choice)) } catch { /* private window or blocked storage: the choice just is not remembered */ }
}

function Skeleton() {
  return <div className="dwr-skeleton" role="status" aria-label="Loading the report"><i /><i /><i /><i /><i /></div>
}

/**
 * The two period views of the Reports page (Task report, Lead sheet report) share
 * one live read: the same period, staff and client choices, one header and one period row. The page keeps its state when you
 * move between the two tabs. `tabs` is the tab strip the Reports screen puts under the header.
 */
export default function DailyReportPage({ view, tabs }: { view: ReportView; tabs: ReactNode }) {
  const store = useStore()
  const [params] = useSearchParams()
  const timeZone = store.agencyTimezone || AGENCY_TIMEZONE
  const today = useAgencyToday(timeZone)   // moves to the new day at midnight even if nothing else redraws the page
  const supervisor = store.can('all_reports')
  // An older link ("View staff report", /staff-report) names a person and dates in the address.
  const [choice, setChoice] = useState<RangeChoice>(() => choiceFromParams(params.get('from'), params.get('to'), today) ?? readChoice())
  const [staff, setStaff] = useState(() => (supervisor ? params.get('staff') || 'all' : 'all'))
  const [drill, setDrill] = useState<{ metric: string; staff: string | null; client: string | null } | null>(null)
  const [submissionOf, setSubmissionOf] = useState<string | null>(null)
  const [history, setHistory] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')

  // Total is asked as period=all; if the server refuses that (400 or 422) Total becomes the last 366 days.
  const [allOk, setAllOk] = useState(true)
  const range = useMemo(() => resolveRange(choice, today, allOk), [choice, today, allOk])
  const allTime = asksAllTime(choice, allOk)
  // Both reports follow the period: the Task report counts what waits now, and what was completed and lost in the period; the Lead sheet report counts the period's leads.
  // A lead has no client, and the stage figures are read for the whole agency: the report takes no client filter.
  const query = { from: range.from, to: range.to, staff: supervisor ? staff : 'all', client: 'all', all: allTime }
  const state = useDailyReport(query, store.me.id)
  const { data, error } = state
  const scope: DailyReport['scope'] = data?.scope ?? (supervisor ? 'team' : 'self')

  useEffect(() => { saveChoice(choice) }, [choice])
  useEffect(() => { if (error && allTime && (error.status === 400 || error.status === 422)) setAllOk(false) }, [error, allTime])
  // Someone else's report that is not allowed (403) or no longer exists (404): go back to everyone instead of showing an error.
  useEffect(() => {
    if (error && query.staff !== 'all' && (error.status === 403 || error.status === 404)) { setStaff('all'); store.notify("That person's report is not available. Showing all recruiters.", 'warn') }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [error])

  // The recruiter filter always lists everyone (the Team board and the table show only the picked person): the last answer for all recruiters is kept
  // while one person is picked. Opened straight on one person (a link), there is none yet, so everyone is read once, quietly, for the filter's list.
  const everyone = useRef<StaffRow[]>([])
  if (data && state.current && query.staff === 'all' && scope === 'team') everyone.current = data.staff
  const rosterRead = useDailyReport({ ...query, staff: 'all', client: 'all' }, store.me.id, supervisor && query.staff !== 'all' && everyone.current.length === 0)
  if (rosterRead.data && rosterRead.current && rosterRead.data.scope === 'team' && everyone.current.length === 0) everyone.current = rosterRead.data.staff
  const roster = everyone.current.length ? everyone.current : data?.staff ?? []
  const people = reportPeople(roster).map(row => ({ id: row.id, name: row.name })).sort((a, b) => a.name.localeCompare(b.name))

  async function exportFile() {
    setExporting(true); setExportError('')
    try { await downloadDailyExport(query, view === 'daily' ? 'task' : 'lead'); store.notify('Excel file downloaded.') }
    catch (failure) { setExportError(failure instanceof Error ? failure.message : 'The Excel file could not be created.') }
    finally { setExporting(false) }
  }

  // `data` is the newest answer, but it answers THIS question only when `state.current`: after the period, the recruiter or the tab changes it is the
  // previous question's answer, and showing it under the new heading would present another period's numbers as this one's (a "Today" that reads
  // yesterday's work). So a different question shows the loading state, never the old figures; a live refresh of the same question keeps its numbers.
  const answers = showsReport(state)
  const outdated = !answers && !!error && isOutdatedServer(error.status)
  const blocking = !answers && !!error && !outdated
  const scopeKey = `${view}|${apiQuery(query)}`
  const common = answers && data ? { report: data, allTime, roster, today, timeZone, scope: scopeKey, staff: staff === 'all' ? '' : staff, onPickStaff: (id: string) => setStaff(id || 'all'), clientFilter: 'all', onDrill: (metric: string, id: string | null) => setDrill({ metric, staff: id, client: null }),
    onDrillClient: (metric: string, id: string) => setDrill({ metric, staff: null, client: id }), onSubmission: (id: string) => setSubmissionOf(id), onHistory: () => setHistory(true) } : null

  return <div className="tk dw dwb dwr">
    <ReportsHeader actions={<>
      <Button onClick={() => void exportFile()} disabled={exporting}>{exporting ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <FileSpreadsheet size={15} aria-hidden="true" />}{exporting ? 'Preparing…' : 'Export Excel'}</Button>
      <Button onClick={() => window.print()}><Printer size={15} aria-hidden="true" />Print</Button>
      <Button onClick={state.refresh} disabled={state.loading && !!data}><RefreshCw size={15} className={state.loading && data ? 'animate-spin' : undefined} aria-hidden="true" />Refresh</Button>
    </>} />
    {tabs}
    <ReportToolbar choice={choice} onChoice={setChoice} range={range} today={today}      people={people} staff={staff === 'all' ? '' : staff} onPickStaff={id => setStaff(id || 'all')} team={scope === 'team'}
      live={<LiveBadge updatedAt={state.updatedAt} busy={state.loading && !!data} />} />

    <div className="dw-alerts">
      {(error || exportError) && !blocking && !outdated && <p className="is-warn" role="alert"><AlertCircle size={16} aria-hidden="true" />{exportError || error?.message}{error && answers ? ' Showing the last loaded result.' : ''} <button type="button" className="dwr-textlink" onClick={exportError ? () => void exportFile() : state.refresh}>Try again</button></p>}
      {outdated && <p className="is-warn" role="status"><AlertCircle size={16} aria-hidden="true" />The new report needs the latest server version. It will appear here once the server is updated. <button type="button" className="dwr-textlink" onClick={state.refresh}>Check again</button></p>}
      {blocking && <p className="is-warn" role="alert"><AlertCircle size={16} aria-hidden="true" />{error?.message || 'The report could not be loaded.'} <button type="button" className="dwr-textlink" onClick={state.refresh}>Try again</button></p>}
    </div>

    {!answers && !error && <Skeleton />}
    {answers && data && common && <div className="dwr-body">
      {view === 'daily' && <TaskReportView {...common} />}
      {view === 'lead' && <LeadReportView {...common} />}
      {view === 'daily' && data.scope === 'self' && <MyDayCard today={today} timeZone={timeZone} refreshKey={state.version} onSubmitted={state.refresh} focus={params.get('focus') === 'myday'} />}
    </div>}

    <DrilldownDrawer metric={drill?.metric ?? null} title={drill ? drillTitle(drill.metric, data) : ''} query={{ ...query, staff: drill?.staff ?? query.staff, client: drill?.client ?? query.client }} today={today} timeZone={timeZone} version={state.version} onClose={() => setDrill(null)} />
    <SubmissionPanel row={data?.staff.find(row => row.id === submissionOf) ?? null} day={range.from} today={today} timeZone={timeZone} onClose={() => setSubmissionOf(null)} />
    <HistoryDrawer open={history} query={query} today={today} timeZone={timeZone} version={state.version} onClose={() => setHistory(false)} />
  </div>
}
