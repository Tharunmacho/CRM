import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/** daily-report-view.ts is pure (its only import is a type): transpile and load it as it is. */
const load = async (path, patch = text => text) => {
  const source = patch(await readFile(new URL(`../src/features/daily-report/${path}`, import.meta.url), 'utf8'))
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const v = await load('daily-report-view.ts')
const fixture = JSON.parse(await readFile(new URL('./fixtures/daily-work-report.json', import.meta.url), 'utf8'))
const report = fixture.team

// ------------------------------------------------------------------------------ dates in agency time

test('the agency day follows Asia/Kolkata, not the machine: 22:30 UTC is already tomorrow in India', () => {
  assert.equal(v.dayInZone('2026-11-16T22:30:00Z', 'Asia/Kolkata'), '2026-11-17')
  assert.equal(v.dayInZone('2026-11-16T22:30:00Z', 'UTC'), '2026-11-16')
  assert.equal(v.dayInZone('2026-11-16T18:29:00Z'), '2026-11-16')
  assert.equal(v.AGENCY_TIMEZONE, 'Asia/Kolkata')
})

test('presets: today, yesterday, this week from Monday, this month from the 1st', () => {
  assert.deepEqual(v.presetRange('today', '2026-11-17'), { from: '2026-11-17', to: '2026-11-17' })
  assert.deepEqual(v.presetRange('yesterday', '2026-11-17'), { from: '2026-11-16', to: '2026-11-16' })
  assert.deepEqual(v.presetRange('week', '2026-11-17'), { from: '2026-11-16', to: '2026-11-17' }) // a Tuesday
  assert.deepEqual(v.presetRange('month', '2026-11-17'), { from: '2026-11-01', to: '2026-11-17' })
  assert.deepEqual(v.presetRange('week', '2026-11-16'), { from: '2026-11-16', to: '2026-11-16' })
  assert.deepEqual(v.presetRange('week', '2026-11-22'), { from: '2026-11-16', to: '2026-11-22' })
  assert.deepEqual(v.presetRange('yesterday', '2027-01-01'), { from: '2026-12-31', to: '2026-12-31' })
  assert.deepEqual(v.presetRange('week', '2026-12-31'), { from: '2026-12-28', to: '2026-12-31' })
})

test('a range is put right: swapped ends, bad dates and the future are repaired', () => {
  assert.deepEqual(v.fixCustomRange('2026-11-10', '2026-11-12', '2026-11-17'), { from: '2026-11-10', to: '2026-11-12' })
  assert.deepEqual(v.fixCustomRange('2026-11-12', '2026-11-10', '2026-11-17'), { from: '2026-11-10', to: '2026-11-12' })
  assert.deepEqual(v.fixCustomRange('2026-11-30', '2026-12-05', '2026-11-17'), { from: '2026-11-17', to: '2026-11-17' })
  assert.deepEqual(v.fixCustomRange('nope', '2026-11-12', '2026-11-17'), { from: '2026-11-12', to: '2026-11-17' })
  assert.equal(v.isDay('2026-02-30'), false)
  assert.equal(v.isDay('2026-02-28'), true)
})

test('the period buttons: Today, Yesterday, This week, This month, Total; no Custom', () => {
  assert.deepEqual(v.PRESETS.map(p => p.label), ['Today', 'Yesterday', 'This week', 'This month', 'Total'])
  assert.ok(!v.PRESETS.some(p => /custom|range/i.test(p.key + p.label)))
})

test('a preset follows today; Total is everything, or the last 366 days if the server refuses period=all; the remembered choice survives bad storage and a saved Custom becomes Today', () => {
  assert.deepEqual(v.resolveRange({ preset: 'today', from: '', to: '' }, '2026-11-18'), { from: '2026-11-18', to: '2026-11-18' })
  assert.deepEqual(v.resolveRange({ preset: 'total', from: '', to: '' }, '2026-11-18'), { from: '2000-01-01', to: '2026-11-18' })
  assert.deepEqual(v.resolveRange({ preset: 'total', from: '', to: '' }, '2026-11-18', false), { from: '2025-11-18', to: '2026-11-18' }) // 366 days, both ends counted
  assert.equal(v.asksAllTime({ preset: 'total', from: '', to: '' }), true)
  assert.equal(v.asksAllTime({ preset: 'total', from: '', to: '' }, false), false) // refused: ask for dates instead
  assert.equal(v.asksAllTime({ preset: 'week', from: '', to: '' }), false)
  assert.deepEqual(v.resolveRange({ preset: 'range', from: '2026-11-01', to: '2026-11-05' }, '2026-11-18'), { from: '2026-11-01', to: '2026-11-05' })
  assert.deepEqual(v.parseChoice(v.serialiseChoice({ preset: 'total', from: '', to: '' })), { preset: 'total', from: '', to: '' })
  assert.deepEqual(v.parseChoice(v.serialiseChoice({ preset: 'week', from: '', to: '' })), { preset: 'week', from: '', to: '' })
  // a Custom range saved by the earlier version, and a range from a link, are not restored: Today
  assert.deepEqual(v.parseChoice(JSON.stringify({ preset: 'custom', from: '2026-11-01', to: '2026-11-05' })), v.DEFAULT_CHOICE)
  assert.deepEqual(v.parseChoice(JSON.stringify({ preset: 'range', from: '2026-11-01', to: '2026-11-05' })), v.DEFAULT_CHOICE)
  for (const bad of [null, undefined, '', '{', '[]', '{"preset":"forever"}']) assert.deepEqual(v.parseChoice(bad), v.DEFAULT_CHOICE, String(bad))
})

test('Total is asked as period=all, with the filters, for the report, its lists, history and file', () => {
  const q = { from: '2000-01-01', to: '2026-11-18', staff: 'u1', client: 'all', all: true }
  assert.equal(v.apiQuery(q), 'period=all&staff=u1&client=all')
  assert.equal(v.drillQuery('visas', q, 0, 25), 'metric=visas&period=all&staff=u1&client=all&page=0&page_size=25')
  assert.equal(v.historyQuery(q, 1, 25), 'period=all&staff=u1&client=all&page=1&page_size=25')
  assert.equal(v.apiQuery({ ...q, all: false }), 'from=2000-01-01&to=2026-11-18&staff=u1&client=all')
})

test('with Total there is nothing to compare with: no change text from a missing previous value', () => {
  const noPrevious = report.metrics.map(m => ({ ...m, previous: null, changePct: null }))
  assert.deepEqual(v.totalVs(noPrevious), {})
  assert.deepEqual(Object.keys(v.totalVs(report.metrics)).sort(), ['forwarded', 'offers', 'prescreened', 'shortlisted', 'travelled', 'visas'])
})

test('an older "View staff report" link carries dates in the address: they are shown as a range, with no button pressed', () => {
  assert.deepEqual(v.choiceFromParams('2026-11-02', '2026-11-08', '2026-11-17'), { preset: 'range', from: '2026-11-02', to: '2026-11-08' })
  assert.equal(v.choiceFromParams(null, '2026-11-08', '2026-11-17'), null)
  assert.equal(v.choiceFromParams('x', 'y', '2026-11-17'), null)
  assert.ok(!v.PRESETS.some(p => p.key === 'range')) // so no period button is pressed for it
})

test('labels, clock time and who may submit', () => {
  assert.equal(v.dayLabel('2026-11-17', '2026-11-17'), 'Tue 17 Nov')
  assert.equal(v.dayLabel('2025-12-31', '2026-11-17'), 'Wed 31 Dec 2025')
  assert.equal(v.rangeLabel('2026-11-16', '2026-11-22', '2026-11-17'), 'Mon 16 Nov to Sun 22 Nov')
  assert.equal(v.canSubmitDay('2026-11-17', '2026-11-17'), true)
  assert.equal(v.canSubmitDay('2026-11-16', '2026-11-17'), true)
  assert.equal(v.canSubmitDay('2026-11-15', '2026-11-17'), false)
  assert.equal(v.canSubmitDay('2026-11-18', '2026-11-17'), false)
  assert.equal(v.clockTime('2026-11-17T13:12:00Z', 'Asia/Kolkata'), '18:42')
  assert.equal(v.clockTime(null), '')
  assert.equal(v.clockTime('garbage'), '')
})

// ------------------------------------------------------------------------------ the Daily report table

test('Daily report columns: exactly the client list, in the client order, with the client wording', () => {
  assert.deepEqual([...v.DAILY_ORDER], ['forwarded', 'prescreened', 'shortlisted', 'interviewsOnline', 'offers', 'visas', 'travelled'])
  const many = v.dailyCols(report.metrics, false)
  assert.deepEqual(many.map(c => c.label), [
    'Resumes forwarded to clients', 'Resumes prescreened', 'Workers shortlisted by clients', 'Online interviews conducted',
    'Offer letters received', 'Visas received', 'Workers travelled',
  ])
  assert.deepEqual(many.map(c => c.head), ['Forwarded', 'Prescreened', 'Shortlisted', 'Online interviews', 'Offers', 'Visas', 'Travelled'])
  const today = v.dailyCols(report.metrics, true)
  assert.equal(today[1].label, 'Resumes prescreened today') // only when the period is the single day today
  assert.equal(today.length, 7)
  assert.ok(today.every(c => c.info && c.info.length > 10)) // each heading carries its definition as its title
  assert.ok(!today.some(c => /lead|task|stage|assigned/i.test(c.id))) // nothing that is not on the client's list
  assert.equal(v.DAY_CLOSED_COL.label, 'Day closed')
})

test('which numbers open a drill-down: the seven figures (online interviews open the interviews list)', () => {
  const drills = Object.fromEntries(v.dailyCols().map(c => [c.id, c.drill]))
  assert.deepEqual(drills, { forwarded: 'forwarded', prescreened: 'prescreened', shortlisted: 'shortlisted', interviewsOnline: 'interviews', offers: 'offers', visas: 'visas', travelled: 'travelled' })
})

test('the muted line under a band total, and the day-close footer', () => {
  assert.equal(v.changeLine({ kind: 'down', text: '\u221225' }, 'yesterday'), '\u221225 vs yesterday')
  assert.equal(v.changeLine({ kind: 'up', text: '+1' }, 'yesterday'), '+1 vs yesterday')
  assert.equal(v.changeLine({ kind: 'flat', text: '0' }, 'yesterday'), 'no change')
  assert.equal(v.changeLine(undefined, 'yesterday'), '')
  assert.equal(v.dayCloseFoot(report.compliance), 'not submitted yet \u2014 Anil Kumar, Zoya Khan')
  assert.equal(v.dayCloseFoot({ expected: 2, missing: [] }), 'everyone has submitted.')
  assert.equal(v.dayCloseFoot(null), '')
  assert.match(v.dayCloseFoot({ expected: 12, missing: Array.from({ length: 10 }, (_, i) => ({ name: 'P' + i })) }), /P7 and 2 more$/)
})

test('one row per person: the seven figures, online interviews from the person', () => {
  assert.deepEqual(v.dailyCells(report.staff[0]), { forwarded: 6, prescreened: 11, shortlisted: 3, interviewsOnline: 2, offers: 2, visas: 1, travelled: 0 })
  assert.deepEqual(v.dailyCells(report.staff[2]), { forwarded: 0, prescreened: 0, shortlisted: 0, interviewsOnline: 0, offers: 0, visas: 0, travelled: 0 })
  const noOnline = { ...report.staff[0] }; delete noOnline.interviewsOnline
  assert.equal(v.dailyCells(noOnline).interviewsOnline, 0)
})

test('the Total row: the server totals, with online interviews from extra', () => {
  assert.deepEqual(v.totalCells(report, report.staff), { forwarded: 9, prescreened: 18, shortlisted: 4, interviewsOnline: 3, offers: 2, visas: 1, travelled: 0 })
  // without metrics the rows are added up
  const bare = { ...report, metrics: [], extra: undefined }
  assert.deepEqual(v.totalCells(bare, report.staff), { forwarded: 9, prescreened: 18, shortlisted: 4, interviewsOnline: 3, offers: 2, visas: 1, travelled: 0 })
})

test('interviews not counted as online get one quiet footnote', () => {
  assert.equal(v.interviewsNotOnline(report), 2) // 5 conducted, 3 online
  assert.equal(v.interviewsNote(report), '2 interviews were recorded without a mode and are not counted as online.')
  const one = { ...report, extra: { ...report.extra, interviewsOnline: 4 } }
  assert.equal(v.interviewsNote(one), '1 interview was recorded without a mode and is not counted as online.')
  assert.equal(v.interviewsNote({ ...report, extra: { ...report.extra, interviewsOnline: 5 } }), '')
  assert.equal(v.interviewsNote({ ...report, extra: { ...report.extra, interviewsOnline: 9 } }), '') // never negative
})

test('"vs yesterday" appears only for the figures with a previous value, as a signed difference', () => {
  assert.deepEqual(v.vsOf(27, 24), { kind: 'up', text: '+3' })
  assert.deepEqual(v.vsOf(1, 3), { kind: 'down', text: '\u22122' })
  assert.deepEqual(v.vsOf(4, 4), { kind: 'flat', text: '0' })
  const vs = v.totalVs(report.metrics)
  assert.deepEqual(Object.keys(vs).sort(), ['forwarded', 'offers', 'prescreened', 'shortlisted', 'travelled', 'visas'])
  assert.equal(vs.prescreened.text, '\u22122') // 18 against 20
  assert.equal(vs.shortlisted.text, '+4') // 4 against nothing
  assert.equal(vs.travelled.kind, 'flat')
  assert.equal(vs.interviewsOnline, undefined) // the online subset has no previous value of its own
  assert.equal(v.vsLabel({ from: '2026-11-17', to: '2026-11-17' }, '2026-11-17'), 'yesterday')
  assert.equal(v.vsLabel({ from: '2026-11-16', to: '2026-11-16' }, '2026-11-17'), 'day before')
  assert.equal(v.vsLabel({ from: '2026-11-10', to: '2026-11-16' }, '2026-11-17'), 'previous period')
})

test('the Day closed tile hover text: how many recruiters submitted and who has not (long lists are cut)', () => {
  assert.equal(v.dayCloseTitle(report.compliance), '1 of 3 recruiters submitted. Not submitted yet \u2014 Anil Kumar, Zoya Khan')
  assert.equal(v.dayCloseTitle({ expected: 2, submitted: 2, missing: [] }), '2 of 2 recruiters submitted')
  assert.equal(v.dayCloseTitle(null), '')
  assert.equal(v.dayCloseTitle({ expected: 0, submitted: 0, missing: [] }), '')
  const many = { expected: 9, submitted: 0, missing: 'ABCDEFGHI'.split('').map(name => ({ name })) }
  assert.equal(v.dayCloseTitle(many), '0 of 9 recruiters submitted. Not submitted yet \u2014 A, B, C, D, E, F, G, H and 1 more')
})

test('"Day closed" pill text: time in agency zone, not submitted, or a dash outside a single day', () => {
  assert.deepEqual(v.dayClosed({ submitted: true, submittedAt: '2026-11-17T13:12:00Z' }, true), { kind: 'submitted', text: 'Submitted 18:42' })
  assert.deepEqual(v.dayClosed({ submitted: false, submittedAt: null }, true), { kind: 'missing', text: 'Not submitted' })
  assert.deepEqual(v.dayClosed({ submitted: null, submittedAt: null }, true), { kind: 'none', text: '–' })
  assert.equal(v.dayClosed({ submitted: true, submittedAt: '2026-11-17T13:12:00Z' }, false).kind, 'none')
  assert.equal(v.dayClosed({ submitted: true, submittedAt: null }, true).text, 'Submitted')
})

test('numbers: zero and missing are told apart for the table (dash) and big numbers use Indian grouping', () => {
  assert.equal(v.numberText(null), '–')
  assert.equal(v.numberText(undefined), '–')
  assert.equal(v.numberText(0), '0')
  assert.equal(v.numberText(1234567), '12,34,567')
  assert.equal(v.pctText(33.4), '33%')
  assert.equal(v.pctText(null), '–')
})

// ------------------------------------------------------------------------------ Lead sheet and Stages tabs

test('Lead sheet columns: the period\'s own Assigned, Not contacted by the Lead sheet rule, Contacted % against the workload held now', () => {
  assert.deepEqual(v.leadCols(report).map(c => c.label), ['Assigned', 'Contacted', 'Interested', 'Call back', 'Not interested', 'Wrong number', 'No answer / not reachable', 'Not contacted'])
  assert.equal(v.LEAD_PCT_COL.label, 'Contacted %')
  assert.equal(v.hasLeadPeriod(report), true)
  assert.equal(v.leadCols(report)[0].drill, 'leadsAssignedInPeriod')
  const cells = v.leadCells(report.staff[0])
  assert.deepEqual(cells, { assigned: 8, contacted: 20, interested: 4, callback: 5, notInterested: 2, wrongNumber: 1, noResponse: 6, notContacted: 55, workload: 60 })
  const total = v.sumCells(report.staff.map(v.leadCells))
  assert.deepEqual([total.assigned, total.contacted, total.callback, total.notContacted, total.workload], [20, 31, 7, 150, 120])
  assert.equal(v.contactedPct(cells.workload, cells.contacted), 33) // 20 of the 60 held now, not of the 8 given today
  assert.equal(v.contactedPct(10, 30), 100)
  assert.equal(v.contactedPct(0, 5), null)
  assert.equal(v.contactedPct(null, 5), null)
  // not reachable joins no answer when the server sends it
  const withReach = { ...report.staff[0], leads: { ...report.staff[0].leads, notReachable: 2 } }
  assert.equal(v.leadCells(withReach).noResponse, 8)
  // the unassigned leads, in either shape the server sends them
  assert.deepEqual(v.unassignedInfo(report.leadSheet), { total: 30, neverContacted: 30, newInPeriod: 4 })
  assert.deepEqual(v.unassignedInfo({ unassigned: 30 }), { total: 30, neverContacted: null, newInPeriod: null })
  assert.equal(v.unassignedInfo(undefined), null)
  assert.equal(v.neverContactedTotal(report.leadSheet), 180)
})

test('an older server without the period-aware lead keys keeps the all-time figures and the old drill-down', () => {
  const old = JSON.parse(JSON.stringify(report))
  for (const key of ['newInPeriod', 'assignedInPeriod', 'notContactedInPeriod', 'backlog', 'previous', 'neverContacted']) delete old.leadSheet[key]
  old.leadSheet.unassigned = 30
  for (const row of old.staff) { delete row.leads.assignedInPeriod; delete row.leads.notContactedInPeriod; delete row.leads.neverContacted }
  assert.equal(v.hasLeadPeriod(old), false)
  assert.equal(v.leadCols(old)[0].drill, 'leadsAssigned')
  assert.deepEqual(v.leadCells(old.staff[0]), { assigned: 60, contacted: 20, interested: 4, callback: 5, notInterested: 2, wrongNumber: 1, noResponse: 6, notContacted: 40, workload: 60 })
  // a recruiter row with no leads object at all: dashes, never zeros
  const bare = { ...old.staff[0] }; delete bare.leads
  assert.equal(v.leadCells(bare).assigned, null)
  assert.equal(v.leadCells(bare).contacted, 20)
})

test('the new lead drill-down titles', () => {
  assert.equal(v.drillTitle('leadsNewInPeriod'), 'New leads')
  assert.equal(v.drillTitle('leadsAssignedInPeriod'), 'Leads assigned')
  assert.equal(v.drillTitle('leadsBacklog'), 'Leads still to contact')
})

test('Lead status now: counts with their share of all leads on the sheet', () => {
  const rows = v.statusRows(report.leadSheet)
  assert.deepEqual(rows.map(r => r.pct), [47, 20, 12, 4, 3, 7, 4, 2, 2])
  assert.equal(rows.length, 9) // all nine statuses, zeros included
  assert.equal(rows.reduce((s, r) => s + r.count, 0), report.leadSheet.totalLeads)
  assert.deepEqual(v.statusRows(undefined), [])
})

test('Tasks and stages: tasks, then candidates at No order and stages 1 to 12, per person, with a Total row', () => {
  const cols = v.stageColumns(report)
  assert.deepEqual(v.TASK_COLS.map(c => c.label), ['Done', 'Pending', 'Overdue'])
  assert.equal(v.taskStageCols(report).length, 16)
  assert.deepEqual(cols.map(c => c.label), ['No order', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'])
  assert.equal(cols[3].drill, 'stage:3')
  assert.match(cols[3].info, /Interview scheduling/)
  const rows = v.taskStageRows(report)
  assert.deepEqual(rows.map(r => [r.name, r.cells.total]), [['Meera Nair', 28], ['Anil Kumar', 13], ['Zoya Khan', 0]])
  assert.deepEqual([rows[0].cells.done, rows[0].cells.pending, rows[0].cells.overdue, rows[0].cells['1']], [8, 3, 1, 10])
  assert.equal(rows[2].cells.overdue, 4) // a person with only overdue tasks is listed
  const total = v.taskStageTotals(report)
  assert.deepEqual([total.done, total.pending, total.overdue, total.total], [13, 5, 5, 41])
  for (const col of cols) assert.equal(total[col.id], rows.reduce((s, r) => s + (r.cells[col.id] ?? 0), 0), col.id)
  assert.deepEqual(v.stageSummary(report), { inProgress: 36, notOnOrder: 5 })
  assert.equal(v.taskStageCols({ ...report, stages: undefined }).length, 3) // an older server: tasks only
})

test('titles of lists the server has not named', () => {
  assert.equal(v.drillTitle('stage:3', report), 'Stage 3: Interview scheduling')
  assert.equal(v.drillTitle('stage:0', report), 'Not on an order yet')
  assert.equal(v.drillTitle('stage:7'), 'Stage 7')
  assert.equal(v.drillTitle('tasksOverdue'), 'Tasks overdue')
  assert.equal(v.drillTitle('leadsAssigned'), 'Leads assigned')
  assert.equal(v.drillTitle('visas'), 'Visas received')
})

// ------------------------------------------------------------------------------ people

test('avatar colour is stable per person and initials come from first and last word', () => {
  assert.equal(v.avatarSlot('Meera Nair'), v.avatarSlot('  meera nair '))
  for (const name of ['', 'A', 'Meera Nair', 'Tharun']) assert.ok(v.avatarSlot(name) >= 0 && v.avatarSlot(name) < 8)
  assert.equal(v.initialsFor('Meera Nair'), 'MN')
  assert.equal(v.initialsFor('Mohamed Ali Khan'), 'MK')
  assert.equal(v.initialsFor(''), '?')
})

// ------------------------------------------------------------------------------ queries and paging

test('queries carry the dates, staff and client; "all" is sent when nothing is chosen', () => {
  assert.equal(v.apiQuery({ from: '2026-11-17', to: '2026-11-17', staff: '', client: '' }), 'from=2026-11-17&to=2026-11-17&staff=all&client=all')
  assert.equal(v.apiQuery({ from: '2026-11-01', to: '2026-11-17', staff: 'abc', client: 'c 1' }), 'from=2026-11-01&to=2026-11-17&staff=abc&client=c+1')
  assert.equal(v.drillQuery('stage:3', { from: 'a', to: 'b', staff: 'u1', client: 'all' }, 2, 25), 'metric=stage%3A3&from=a&to=b&staff=u1&client=all&page=2&page_size=25')
})

test('paging text and counts', () => {
  assert.equal(v.pageCount(0, 25), 1)
  assert.equal(v.pageCount(26, 25), 2)
  assert.equal(v.pageRange(63, 0, 25), '1–25 of 63')
  assert.equal(v.pageRange(63, 2, 25), '51–63 of 63')
  assert.equal(v.pageRange(0, 0, 25), '0 rows')
})

test('which server answers mean "the new report is not there yet"', () => {
  for (const status of [404, 405, 501, 502]) assert.equal(v.isOutdatedServer(status), true, String(status))
  for (const status of [200, 400, 401, 403, 409, 500, 503, undefined]) assert.equal(v.isOutdatedServer(status), false, String(status))
})

test('a drill-down row links to the candidate, or to the lead, or to nothing', () => {
  assert.equal(v.drillHref({ kind: 'candidate', candidateId: 'c 1', leadId: null }), '/candidates/c%201')
  assert.equal(v.drillHref({ kind: 'lead', candidateId: null, leadId: 'L9' }), '/leads?lead=L9')
  assert.equal(v.drillHref({ kind: 'lead', candidateId: 'c1', leadId: 'L9' }), '/candidates/c1')
  assert.equal(v.drillHref({ kind: 'lead', candidateId: null, leadId: null }), null)
})

// ------------------------------------------------------------------------------ CSV

test('CSV cells are quoted when needed and a leading = + - @ is kept as text (no formula runs)', () => {
  assert.equal(v.csvCell('a,b'), '"a,b"')
  assert.equal(v.csvCell('say "hi"'), '"say ""hi"""')
  assert.equal(v.csvCell('=SUM(A1:A9)'), "'=SUM(A1:A9)")
  assert.equal(v.csvCell('@cmd'), "'@cmd")
  assert.equal(v.csvCell(null), '')
  assert.equal(v.csvCell(7), '7')
})

test('the CSV of a list: header, one line per row, agency date and time, CRLF', () => {
  const csv = v.buildCsv([
    { at: '2026-11-17T13:12:00Z', staffName: 'Meera Nair', kind: 'candidate', candidateId: 'c1', leadId: null, careId: 'CI-00042', name: 'Rashid, K', client: 'Al Noor', job: 'Welder', detail: 'Sent to client' },
    { at: '2026-11-16T22:30:00Z', staffName: 'Anil', kind: 'lead', candidateId: null, leadId: 'l1', careId: null, name: 'Lead One', client: null, job: null, detail: '' },
  ], 'Asia/Kolkata')
  const lines = csv.split('\r\n')
  assert.equal(lines[0], 'Date,Time,Recruiter,Name,Candidate ID,Type,Client,Job,Detail')
  assert.equal(lines[1], '2026-11-17,18:42,Meera Nair,"Rashid, K",CI-00042,Candidate,Al Noor,Welder,Sent to client')
  assert.equal(lines[2], '2026-11-17,04:00,Anil,Lead One,,Lead,,,')
  assert.equal(v.buildCsv([]), 'Date,Time,Recruiter,Name,Candidate ID,Type,Client,Job,Detail')
  assert.equal(v.csvFileName('Visas received', '2026-11-01', '2026-11-17'), 'visas-received-2026-11-01-to-2026-11-17.csv')
})

// ------------------------------------------------------------------------------ my day and the typed client

test("my day: the caller's staff row (figures under metrics) and a submit answer become one shape", () => {
  const mine = v.normaliseMyDay(fixture.mine)
  const sent = v.normaliseMyDay(fixture.submit)
  for (const day of [mine, sent]) {
    assert.equal(day.date, '2026-11-17')
    assert.equal(day.submittedAt, '2026-11-17T13:12:00+00:00')
    assert.equal(day.note, 'Two callbacks for tomorrow')
    assert.equal(day.submitted, true)
    assert.equal(day.figures.interviewsOnline, 2)
    assert.equal(day.figures.prescreened, 11)
  }
  const fresh = v.normaliseMyDay({ date: '2026-11-17', metrics: { prescreened: 1 }, interviewsOnline: 0, submitted: false, submittedAt: null, note: null })
  assert.equal(fresh.submitted, false)
  assert.equal(v.normaliseMyDay({ date: 'x' }).submitted, false)
})

test('the client asks the contract URLs and returns /mine and /submit as one shape', async () => {
  globalThis.__dailyView = v
  const mod = await load('daily-report-api.ts', text => text
    .replace(/^import \{ api, fileHref \} from .*$/m, 'const api = {}; const fileHref = path => path')
    .replace(/^import \{ apiQuery, drillQuery, historyQuery, normaliseMyDay \} from .*$/m, 'const { apiQuery, drillQuery, historyQuery, normaliseMyDay } = globalThis.__dailyView')
    .replace(/^import type .*$/m, ''))
  const calls = []
  const http = {
    get: async (path, options) => { calls.push(['GET', path, options]); return path.includes('/mine') ? fixture.mine : { ok: path } },
    post: async (path, body) => { calls.push(['POST', path, body]); return fixture.submit },
  }
  const client = mod.createDailyReportApi(http)
  const query = { from: '2026-11-17', to: '2026-11-17', staff: 'all', client: 'c9' }
  await client.report(query)
  await client.drilldown('stage:3', query, 1, 25)
  const mine = await client.mine('2026-11-17')
  const sent = await client.submit('2026-11-16', 'Left early')
  assert.deepEqual(calls.map(call => call.slice(0, 2)), [
    ['GET', '/reports/daily-work?from=2026-11-17&to=2026-11-17&staff=all&client=c9'],
    ['GET', '/reports/daily-work/drilldown?metric=stage%3A3&from=2026-11-17&to=2026-11-17&staff=all&client=c9&page=1&page_size=25'],
    ['GET', '/reports/daily-work/mine?date=2026-11-17'],
    ['POST', '/reports/daily-work/submit'],
  ])
  assert.equal(calls[2][2].fresh, true)
  assert.deepEqual(calls[3][2], { date: '2026-11-16', note: 'Left early' })
  assert.equal(mine.figures.interviewsOnline, 2)
  assert.equal(sent.submitted, true)
})

// ------------------------------------------------------------------------------ by client, day-close accountability, reminder

test('By client rows: busiest first, online interviews from the client, hidden when empty or a client filter is on', () => {
  const rows = v.clientRows(report)
  assert.deepEqual(rows.map(r => [r.name, r.cells.total]), [['Al Noor Contracting', 22], ['Gulf Build', 15]])
  assert.deepEqual(rows[0].cells, { forwarded: 5, prescreened: 10, shortlisted: 3, interviewsOnline: 2, offers: 1, visas: 1, travelled: 0, total: 22 })
  // the clients add up to the team's figures
  for (const key of ['forwarded', 'prescreened', 'shortlisted', 'interviewsOnline', 'offers', 'visas']) {
    assert.equal(rows.reduce((s, r) => s + r.cells[key], 0), v.totalCells(report, report.staff)[key], key)
  }
  // the Total is the sum of the seven visible columns even when an older server added every metric (interviews conducted too)
  const stale = v.clientRows({ byClient: [{ id: 'f', name: 'Finesco', metrics: { forwarded: 6, prescreened: 7, shortlisted: 6, interviews: 6, offers: 6, visas: 0, travelled: 0 }, interviewsOnline: 0, total: 31 }] })
  assert.equal(stale[0].cells.total, 25)
  assert.equal(stale[0].cells.total, v.DAILY_ORDER.reduce((all, key) => all + stale[0].cells[key], 0))
  assert.deepEqual(v.clientRows({}), []) // an older server sends no byClient
  assert.deepEqual(v.clientRows({ byClient: [{ id: 'c', name: 'Idle', metrics: {}, total: 0 }] }), []) // no activity, no row
  assert.equal(v.showByClient(rows, 'all'), true)
  assert.equal(v.showByClient(rows, ''), true)
  assert.equal(v.showByClient(rows, 'co-1'), false)
  assert.equal(v.showByClient([], 'all'), false)
})

test('numbers changed after the report was submitted: which figures and the quiet line', () => {
  const meera = report.staff[0]
  assert.deepEqual(v.figureChanges(meera), [{ key: 'forwarded', now: 6, was: 5 }])
  assert.equal(v.changedSinceText(meera), 'Numbers changed after this report was submitted (now 6 forwarded, was 5)')
  assert.equal(v.changedSinceText(report.staff[1]), '') // nothing submitted
  assert.equal(v.changedSinceText({ ...meera, submittedFigures: { ...meera.submittedFigures, forwarded: 6 }, changedSinceSubmit: false }), '') // nothing moved
  assert.equal(v.changedSinceText({ metrics: {}, submittedFigures: null, changedSinceSubmit: true }), 'Numbers changed after this report was submitted') // the server says so, without figures
  const many = { metrics: { forwarded: 9, prescreened: 9, shortlisted: 9, offers: 9 }, interviewsOnline: 0, submittedFigures: { forwarded: 1, prescreened: 1, shortlisted: 1, offers: 1 } }
  assert.match(v.changedSinceText(many), /\(now 9 forwarded, was 1; now 9 prescreened, was 1; now 9 shortlisted, was 1; and 1 more\)$/)
})

test('the submitted-reports list: excerpts, key figures and the query', () => {
  assert.equal(v.excerpt(null), '')
  assert.equal(v.excerpt('  two   callbacks\nfor tomorrow '), 'two callbacks for tomorrow')
  assert.equal(v.excerpt('x'.repeat(200), 20), `${'x'.repeat(19)}…`)
  assert.equal(v.figuresLine({ forwarded: 5, prescreened: 11, shortlisted: 3, interviewsOnline: 2, offers: 2, visas: 1 }), '5 forwarded · 11 prescreened · 3 shortlisted · 2 online interviews · +2 more')
  assert.equal(v.figuresLine({ forwarded: 2 }), '2 forwarded')
  assert.equal(v.figuresLine({}), 'No figures')
  assert.equal(v.figuresLine(null), 'No figures')
  assert.equal(v.historyQuery({ from: '2026-11-01', to: '2026-11-17', staff: 'all', client: 'all' }, 1, 25), 'from=2026-11-01&to=2026-11-17&staff=all&client=all&page=1&page_size=25')
})

test('the reminder banner: only a recruiter, from 17:00 agency time, with activity, not submitted, not dismissed today', () => {
  const at = (iso) => new Date(iso) // 17:00 in Kolkata is 11:30 UTC
  const base = { recruiter: true, submitted: false, activity: 3, dismissedDay: null, today: '2026-11-17', timeZone: 'Asia/Kolkata' }
  assert.equal(v.agencyHour(at('2026-11-17T11:29:00Z'), 'Asia/Kolkata'), 16)
  assert.equal(v.agencyHour(at('2026-11-17T11:30:00Z'), 'Asia/Kolkata'), 17)
  assert.equal(v.agencyHour(at('2026-11-17T18:30:00Z'), 'Asia/Kolkata'), 0) // midnight is the next day
  assert.equal(v.reminderVisible({ ...base, now: at('2026-11-17T11:29:00Z') }), false) // before 17:00
  assert.equal(v.reminderVisible({ ...base, now: at('2026-11-17T11:30:00Z') }), true) // from 17:00
  assert.equal(v.reminderVisible({ ...base, now: at('2026-11-17T15:00:00Z') }), true)
  assert.equal(v.reminderVisible({ ...base, now: at('2026-11-17T15:00:00Z'), submitted: true }), false)
  assert.equal(v.reminderVisible({ ...base, now: at('2026-11-17T15:00:00Z'), activity: 0 }), false) // nothing done today
  assert.equal(v.reminderVisible({ ...base, now: at('2026-11-17T15:00:00Z'), recruiter: false }), false) // admins and managers do not get it
  assert.equal(v.reminderVisible({ ...base, now: at('2026-11-17T15:00:00Z'), dismissedDay: '2026-11-17' }), false) // hidden for today
  assert.equal(v.reminderVisible({ ...base, now: at('2026-11-17T15:00:00Z'), dismissedDay: '2026-11-16' }), true) // yesterday's dismissal does not count
  assert.equal(v.REMINDER_DISMISS_KEY, 'crm.daily-report.reminder-dismissed')
  // any figure of the day is activity, leads contacted included
  assert.equal(v.dayActivity({ leadsContacted: 2 }), 2)
  assert.equal(v.dayActivity({ forwarded: 1, offers: 2 }), 3)
  assert.equal(v.dayActivity(null), 0)
})

test('the reminder is a small component on Tasks and the Dashboard, reading /mine, dismissed per day with a guarded storage', async () => {
  const read = name => readFile(new URL(`../src/${name}`, import.meta.url), 'utf8')
  const hook = await read('features/daily-report/use-day-reminder.ts')
  assert.match(hook, /dailyReportApi\.mine\(today/)
  assert.match(hook, /CACHE_MS = 60_000/)
  assert.match(hook, /\['daily_report'\]/)
  assert.match(hook, /try \{ return window\.localStorage/)
  assert.match(hook, /try \{ window\.localStorage\.setItem/)
  const banner = await read('features/daily-report/reminder-banner.tsx')
  assert.match(banner, /Your daily report is not submitted yet\./)
  assert.match(banner, /navigate\('\/reports\?focus=myday'\)/)
  assert.match(banner, /Submit now/)
  assert.match(await read('screens/tasks/daily-work.tsx'), /<ReminderBanner \/>/)
  assert.match(await read('screens/dashboard/dashboard.tsx'), /<ReminderBanner \/>/)
  assert.match(await read('features/daily-report/my-day-card.tsx'), /focus/) // the card takes the focus when opened from the banner
})
