import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/**
 * The page model against a fixture shaped like the real server answer (tests/fixtures/daily-work-report.json: the
 * contract in backend/app/api/routers/daily_work_report.py plus its additive leadSheet, tasks, stages and per-person
 * leads/tasks keys): every field is read by one of the three tables, and the page is wired as the owner asked.
 */
const source = await readFile(new URL('../src/features/daily-report/daily-report-view.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const v = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
const fixture = JSON.parse(await readFile(new URL('./fixtures/daily-work-report.json', import.meta.url), 'utf8'))
const report = fixture.team
const read = name => readFile(new URL(`../src/${name}`, import.meta.url), 'utf8')

test('the fixture keeps the server invariants the tables rely on', () => {
  for (const person of report.staff) {
    const f = person.leadFeedback
    assert.equal(f.interested + f.notInterested + f.noAnswer + f.wrongNumber + f.callback + f.other, f.contacted, person.name)
    assert.equal(person.metrics.leadsContacted, f.contacted)
    assert.equal(person.leads.contacted, f.contacted)
    assert.equal(person.leads.assigned, person.leads.contacted + person.leads.notContacted, person.name)
    assert.ok(person.interviewsOnline <= person.metrics.interviews)
  }
  for (const metric of report.metrics) assert.equal(report.staff.reduce((sum, p) => sum + (p.metrics[metric.key] ?? 0), 0), metric.value, metric.key)
  assert.equal(report.staff.reduce((sum, p) => sum + p.interviewsOnline, 0), report.extra.interviewsOnline)
  for (const key of ['done', 'pending', 'overdue']) assert.equal(report.staff.reduce((sum, p) => sum + p.tasks[key], 0), report.tasks[key], key)
  assert.equal(report.tasks.total, report.tasks.done + report.tasks.pending + report.tasks.overdue)
  assert.equal(report.leadSheet.byStatus.reduce((sum, s) => sum + s.count, 0), report.leadSheet.totalLeads)
  assert.equal(report.leadSheet.byStatus.length, 9) // all nine real statuses, zeros included
  const un = v.unassignedInfo(report.leadSheet)
  assert.equal(report.leadSheet.assigned + un.total, report.leadSheet.totalLeads)
  // every number of the Lead sheet tab ties to the Lead sheet screen: what recruiters own plus what nobody owns is the Not contacted figure
  assert.equal(report.staff.reduce((s, p) => s + p.leads.neverContacted, 0) + un.neverContacted, report.leadSheet.neverContacted)
  assert.equal(report.staff.reduce((s, p) => s + p.leads.assignedInPeriod, 0), report.leadSheet.assignedInPeriod)
  for (const item of report.stages.perStaff) assert.equal(Object.values(item.counts).reduce((a, b) => a + b, 0), item.total)
  assert.deepEqual(report.stages.totals.map(s => s.no), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
  assert.deepEqual(report.funnel.map(f => f.key), ['prescreened', 'forwarded', 'shortlisted', 'interviews', 'offers', 'visas', 'travelled'])
})

test("Daily report = the client's seven figures only: every cell, the Total row and the pills come from the answer", () => {
  const cols = v.dailyCols(report.metrics, true)
  assert.equal(cols.length, 7)
  for (const person of report.staff) {
    const cells = v.dailyCells(person)
    assert.deepEqual(Object.keys(cells), cols.map(c => c.id), person.name)
    assert.ok(Object.values(cells).every(value => typeof value === 'number'), person.name)
  }
  const total = v.totalCells(report, report.staff)
  for (const col of cols) assert.equal(total[col.id], report.staff.reduce((sum, p) => sum + v.dailyCells(p)[col.id], 0), col.id)
  assert.deepEqual(report.staff.map(p => v.dayClosed(p, true).text), ['Submitted 18:42', 'Not submitted', 'Not submitted'])
  assert.deepEqual(report.staff.filter(p => p.idle).map(p => p.id), ['u-zoya'])
  assert.equal(v.dayCloseTitle(report.compliance), '1 of 3 recruiters submitted. Not submitted yet \u2014 Anil Kumar, Zoya Khan')
  assert.equal(v.vsLabel(report.period, '2026-11-17'), 'yesterday')
  assert.equal(v.totalVs(report.metrics).offers.text, '−1')
  assert.match(v.interviewsNote(report), /^2 interviews were recorded without a mode and are not counted as online/)
})

test('the same answer from an older server (no leadSheet, tasks, stages, leads) still renders, with dashes', () => {
  const old = JSON.parse(JSON.stringify(report))
  delete old.leadSheet; delete old.tasks; delete old.stages
  for (const row of old.staff) { delete row.leads; delete row.tasks }
  assert.ok(old.staff.every(person => typeof v.dailyCells(person).prescreened === 'number')) // the Daily report needs none of the additive keys
  assert.ok(old.staff.every(person => v.leadCells(person).assigned === null && v.leadCells(person).contacted >= 0))
  assert.deepEqual(v.statusRows(old.leadSheet), [])
  assert.deepEqual(v.stageColumns(old), [])
  assert.equal(v.taskStageTotals(old).done, null)
  assert.deepEqual(v.taskStageRows(old), [])
  assert.equal(v.stageSummary(old).inProgress, 0)
})

test("my day: the caller's row and a submit answer become one shape", () => {
  for (const day of [v.normaliseMyDay(fixture.mine), v.normaliseMyDay(fixture.submit)]) {
    assert.equal(day.submitted, true)
    assert.equal(day.figures.interviewsOnline, 2)
    assert.equal(day.note, 'Two callbacks for tomorrow')
  }
})

test('weekly buckets: the series carries bucket week and the screen draws no chart or summed series at all', async () => {
  assert.equal(fixture.weeklyLeads.period.bucket, 'week')
  assert.ok(fixture.weeklyLeads.metric.series.every(point => point.bucket === 'week'))
  for (const name of ['daily-report-page.tsx', 'report-views.tsx', 'report-table.tsx', 'report-people.tsx']) {
    const text = await read(`features/daily-report/${name}`)
    assert.doesNotMatch(text, /recharts|Sparkline|TrendArea|series\.reduce/, name)
  }
})

test('Reports is Daily report | Lead sheet monitoring | Candidate monitoring only; Accounts and the legacy reports are behind constants', async () => {
  const page = await read('screens/reports/reports.tsx')
  assert.ok(page.includes('pageTabs(accounts)'))
  assert.ok(page.includes('<DailyReportPage view={view} tabs={tabs} />'))
  assert.ok(page.includes('<OtherReports embedded />')) // kept for old Fee / Advance collections links
  assert.doesNotMatch(page, /Other reports/)
  const tabs = await read('features/reports/report-tabs.ts')
  assert.match(tabs, /export const SHOW_LEGACY_REPORTS = false/)
  assert.match(tabs, /export const SHOW_ACCOUNTS_TAB = false/)
  assert.ok(tabs.includes("label: 'Daily report' }, { key: 'lead', label: 'Lead sheet monitoring' }, { key: 'stages', label: 'Candidate monitoring' }"))
  const other = await read('screens/reports/other-reports.tsx')
  assert.ok(other.includes('SHOW_LEGACY_REPORTS ? REPORTS.filter')) // the code is kept; only the tabs are gated
  assert.match(other, /: ACCOUNT_TABS/)
})

test('the Daily report is built from the Tasks screen parts: header, tabs, period row, summary tiles, Team card, table card', async () => {
  const shell = await read('features/daily-report/daily-report-page.tsx')
  assert.ok(shell.indexOf('<PageHeader') < shell.indexOf('{tabs}') && shell.indexOf('{tabs}') < shell.indexOf('<PeriodRow') && shell.indexOf('<PeriodRow') < shell.indexOf('<DailyView'))
  const views = await read('features/daily-report/report-views.tsx')
  const daily = views.slice(views.indexOf('export function DailyView'), views.indexOf('/* ---------------------------------------------------------------- Lead sheet monitoring'))
  assert.ok(daily.indexOf('<Tiles') > -1 && daily.indexOf('<Tiles') < daily.indexOf('<PeopleBoard') && daily.indexOf('<PeopleBoard') < daily.indexOf('<FigureTable'))
  assert.doesNotMatch(daily, /leadSheet|taskStage|stageColumns|LEAD_COLS|stageLegend/) // nothing that is not on the client list
  const leadView = views.slice(views.indexOf('export function LeadSheetView'), views.indexOf('/* ---------------------------------------------------------------- Candidate monitoring'))
  assert.doesNotMatch(leadView, /stage|taskStage|dailyCols|tasks/i) // leads only
  const candidates = views.slice(views.indexOf('export function CandidateMonitoringView'))
  assert.doesNotMatch(candidates, /LEAD_COLS|leadCells|leadSheet|contacted/i) // candidates and tasks only
  const table = await read('features/daily-report/report-table.tsx')
  assert.ok(table.includes('onDrill(col.drill!')) // a number opens its list
  assert.match(table, /onPickName/) // a name filters to the person
  assert.ok(!/total=\{/.test(daily)) // the Daily report has no Total row: the tiles are the total
})

test('a recruiter sees only their own figures and no Team card, and a small My day card under them', async () => {
  const views = await read('features/daily-report/report-views.tsx')
  assert.ok(views.includes('{team && <PeopleBoard'))
  const shell = await read('features/daily-report/daily-report-page.tsx')
  assert.ok(shell.includes("data.scope === 'self' && <MyDayCard"))
})

test('the screen handles a refused or unknown person and links from older addresses', async () => {
  const shell = await read('features/daily-report/daily-report-page.tsx')
  assert.match(shell, /error\.status === 403 \|\| error\.status === 404/)
  assert.match(shell, /setStaff\('all'\)/)
  assert.match(shell, /choiceFromParams\(params\.get\('from'\)/) // /staff-report and "View staff report" links
  assert.match(shell, /params\.get\('staff'\)/)
})
