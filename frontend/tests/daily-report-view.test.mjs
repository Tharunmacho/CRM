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

test('the day-close footer', () => {
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
  assert.deepEqual(cols.map(c => c.label), ['No job', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'])
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

// ------------------------------------------------------------------------------ Task progress by stage, Lead sheet by status, the attendance day

const STAGE_LABELS = ['Prescreening', 'Forwarded', 'Interview scheduling', 'Interview result', 'Offer letter', 'Advance payment', 'Passport', 'Medical', 'PCC', 'Visa', 'Google review', 'Visa stamping', 'Air ticket']

test('stageProgress: 13 rows numbered like "1 · Prescreening", now / completed / stopped, shares of the candidates now at a stage, and a totals row', () => {
  const totals = [{ no: 0, key: 'none', label: 'Not on an order yet', count: 4 }, ...STAGE_LABELS.map((label, i) => ({ no: i + 1, key: 's' + (i + 1), label, count: i === 0 ? 6 : i === 12 ? 10 : 0 }))]
  const stageFlow = STAGE_LABELS.map((label, i) => ({ no: i + 1, key: 's' + (i + 1), label, completed: i + 1, stopped: i === 2 ? 1 : 0 }))
  const p = v.stageProgress({ stages: { totals, perStaff: [] }, stageFlow })
  assert.equal(p.hasFlow, true)
  assert.deepEqual(p.rows.map(r => r.name), ['0 · Not on an order yet', ...STAGE_LABELS.map((l, i) => `${i + 1} · ${l}`)])
  assert.deepEqual(p.rows.map(r => r.no), Array.from({ length: 14 }, (_, i) => i))
  const one = p.rows[1]
  assert.deepEqual({ now: one.now, completed: one.completed, stopped: one.stopped, share: one.share, drill: one.drill }, { now: 6, completed: 1, stopped: 0, share: 30, drill: 'stage:1' })
  assert.equal(p.rows[13].share, 50)
  assert.equal(p.rows[2].drill, undefined) // nobody there: nothing to open
  assert.equal(p.rows[3].stopped, 1)
  assert.deepEqual(p.total, { now: 20, completed: 91, stopped: 1 })
})

test('stageProgress: the "0 · Not on an order yet" row appears only when candidates are in that group; labels fall back to the 13 stage names', () => {
  const totals = [{ no: 0, key: 'none', label: 'Not on an order yet', count: 0 }, { no: 1, key: 's1', label: 'Prescreening', count: 2 }]
  const p = v.stageProgress({ stages: { totals, perStaff: [] }, stageFlow: [] })
  assert.equal(p.rows.length, 13)
  assert.equal(p.rows[0].name, '1 · Prescreening')
  assert.equal(p.rows[12].name, '13 · Air ticket') // no label from the server: the built-in name
  assert.deepEqual(v.STAGE_NAMES, STAGE_LABELS)
  // an empty stageFlow list is a real answer (nothing completed): zeros, not dashes
  assert.equal(p.hasFlow, true)
  assert.equal(p.rows[0].completed, 0)
})

test('stageProgress: an older server without stageFlow gives null (a dash) in Completed and Stopped; without stages as well, in Now too; never an error', () => {
  const totals = [{ no: 1, key: 's1', label: 'Prescreening', count: 3 }]
  const old = v.stageProgress({ stages: { totals, perStaff: [] } })
  assert.equal(old.hasFlow, false)
  assert.equal(old.rows.length, 13)
  assert.deepEqual([old.rows[0].now, old.rows[0].completed, old.rows[0].stopped], [3, null, null])
  assert.deepEqual(old.total, { now: 3, completed: null, stopped: null })
  const bare = v.stageProgress({})
  assert.equal(bare.rows.length, 13)
  assert.ok(bare.rows.every(r => r.now == null && r.completed == null && r.stopped == null && r.share === 0 && r.drill === undefined))
  assert.deepEqual(bare.total, { now: null, completed: null, stopped: null })
})

test('leadStatusTable: the statuses with count and share, and the total; null without a lead sheet', () => {
  assert.equal(v.leadStatusTable(undefined), null)
  const table = v.leadStatusTable(report.leadSheet)
  assert.equal(table.rows.length, 9)
  assert.deepEqual(table.rows[0], { key: 'new', label: 'Not contacted', count: 70, pct: 47 })
  assert.equal(table.total, 150)
  assert.equal(table.totalPct, 100)
  assert.deepEqual(v.leadStatusTable({ ...report.leadSheet, byStatus: [] }), { rows: [], total: 0, totalPct: 0 })
})

test('attendanceDay: the single day of the period, never after today; Total and ranges use today', () => {
  const day = (from, to, all = false, today = '2026-11-17') => v.attendanceDay({ from, to }, all, today)
  assert.equal(day('2026-11-17', '2026-11-17'), '2026-11-17')
  assert.equal(day('2026-11-16', '2026-11-16'), '2026-11-16')
  assert.equal(day('2026-11-10', '2026-11-17'), '2026-11-17')
  assert.equal(day('2026-11-10', '2026-11-12'), '2026-11-17') // a range that ended earlier: still today
  assert.equal(day('2000-01-01', '2026-11-17', true), '2026-11-17')
  assert.equal(day('2026-11-20', '2026-11-20'), '2026-11-17')
})

// ------------------------------------------------------------------------------ Task report: the matrix

const person = (id, name) => ({ id, name })
const counts = (map) => Object.fromEntries(Array.from({ length: 14 }, (_, n) => [String(n), map[n] ?? 0]))
const flowOf = (map) => Object.fromEntries(Array.from({ length: 13 }, (_, i) => [String(i + 1), map[i + 1] ?? 0]))
const taskReport = {
  staff: [person('b', 'Ravi'), person('a', 'asha'), person('c', 'Meena')],
  stages: {
    totals: Array.from({ length: 14 }, (_, no) => ({ no, key: `k${no}`, label: no === 0 ? 'Not on an order yet' : `Stage ${no}`, count: 0 })),
    perStaff: [{ id: 'a', counts: counts({ 0: 2, 1: 3, 13: 1 }), total: 6 }, { id: 'b', counts: counts({ 2: 4 }), total: 4 }, { id: 'c', counts: counts({}), total: 0 }, { id: 'gone', counts: counts({ 5: 2 }), total: 2 }],
  },
  stageFlow: Array.from({ length: 13 }, (_, i) => ({ no: i + 1, key: `k${i + 1}`, label: `Stage ${i + 1}`, completed: 0, stopped: 0 })),
  stageFlowStaff: [{ id: 'a', completed: flowOf({ 1: 5, 2: 2 }), stopped: flowOf({ 2: 1 }) }, { id: 'b', completed: flowOf({ 2: 1, 13: 4 }), stopped: flowOf({}) }],
}

test('the matrix has one row per recruiter (alphabetical), the 13 stage columns, a Not on an order column and a total', () => {
  const waiting = v.buildStageMatrix(taskReport)
  assert.equal(waiting.stages.length, 13)
  assert.deepEqual(waiting.stages.map(s => s.no), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13])
  assert.deepEqual(waiting.stages.map(s => s.short), ['Screen', 'Forward', 'Schedule', 'Result', 'Offer', 'Advance', 'Passport', 'Medical', 'PCC', 'Visa', 'Review', 'Stamping', 'Ticket'])
  assert.deepEqual(waiting.rows.map(r => r.name), ['asha', 'Meena', 'Ravi', 'Other']) // people no longer on the staff list are one "Other" row
  const asha = waiting.rows[0].cells
  assert.deepEqual([asha['0'], asha['1'], asha['13'], asha.total], [2, 3, 1, 6])
  assert.equal(waiting.rows[3].cells['5'], 2)
  assert.equal(waiting.total.total, 12) // asha 6 + ravi 4 + other 2
  assert.equal(waiting.total['0'], 2)
  assert.equal(waiting.rows[1].cells.total, 0) // a recruiter with nothing still has their row
  for (let no = 1; no <= 13; no++) assert.equal(waiting.total[String(no)], waiting.rows.reduce((all, r) => all + r.cells[String(no)], 0))
  assert.equal('mode' in waiting, false)
})

test('one recruiter picked: only that row, whose total row equals it', () => {
  const m = v.buildStageMatrix(taskReport, 'a')
  assert.deepEqual(m.rows.map(r => r.id), ['a'])
  assert.deepEqual(m.total, m.rows[0].cells)
  assert.deepEqual(v.buildStageMatrix(taskReport, 'nobody').rows, [])
})

test('"Stage by stage": stages 1 to 13 with waiting, a share and a total row, and no completed or stopped figures', () => {
  const table = v.buildStageTable(taskReport)
  assert.equal(table.rows.length, 13) // never a "0 · Not on an order yet" row here
  assert.equal(table.rows[0].name, '1 · Stage 1')
  assert.deepEqual(Object.keys(table.rows[0]).sort(), ['drill', 'name', 'no', 'share', 'waiting'])
  assert.deepEqual(Object.keys(table.total), ['waiting'])
  assert.deepEqual([table.rows[0].waiting, table.rows[0].share, table.rows[0].drill], [3, 30, 'stage:1'])
  assert.deepEqual([table.rows[1].waiting, table.rows[1].share], [4, 40])
  assert.equal(table.rows[2].drill, undefined) // nobody there: nothing to open
  assert.equal(table.total.waiting, 10) // stages 1 to 13 only: the matrix total less the No order column
  assert.equal(table.total.waiting, v.buildStageMatrix(taskReport).total.total - v.buildStageMatrix(taskReport).total['0'])
  assert.deepEqual(v.buildStageTable({ staff: [] }).total, { waiting: null }) // an older server: dashes
  assert.equal(v.buildStageTable(taskReport, 'a').total.waiting, 4)
})

// ------------------------------------------------------------------------------ who is listed: recruiters only

const withRole = (id, name, role) => ({ id, name, role })
const peopleReport = {
  staff: [withRole('adm', 'Admin Root', 'admin'), withRole('md', 'Mahesh', 'manager'), withRole('r1', 'Zoya', 'staff'), withRole('r2', 'Anil', 'staff')].map(row => ({ ...row, tasks: { total: 0, done: 0, pending: 0, overdue: row.id === 'adm' ? 7 : row.id === 'md' ? 2 : 1 } })),
  tasks: { total: 0, done: 0, pending: 0, overdue: 10 },
  stages: {
    totals: Array.from({ length: 14 }, (_, no) => ({ no, key: `k${no}`, label: `Stage ${no}`, count: 0 })),
    perStaff: [{ id: 'adm', counts: counts({ 0: 5, 1: 5 }), total: 10 }, { id: 'md', counts: counts({ 1: 2, 3: 1 }), total: 3 }, { id: 'r1', counts: counts({ 2: 4 }), total: 4 }, { id: 'r2', counts: counts({ 1: 1, 0: 1 }), total: 2 }],
  },
}

test('reportPeople keeps recruiters and excludes owners', () => {
  assert.deepEqual(v.reportPeople(peopleReport.staff).map(r => r.name), ['Zoya', 'Anil'])
  assert.deepEqual(v.reportPeople([{ id: 'x', name: 'No role' }]).map(r => r.id), ['x'])
  assert.deepEqual([...v.HIDDEN_REPORT_ROLES], ['admin', 'manager', 'md'])
})

test('Task report: owners are excluded, and every total adds up from the rows shown', () => {
  const matrix = v.buildStageMatrix(peopleReport)
  assert.deepEqual(matrix.rows.map(r => r.name), ['Anil', 'Zoya'])
  assert.equal(matrix.rows.some(r => r.id === 'adm' || r.id === 'other'), false) // the Administrator's candidates are not folded into "Other"
  assert.equal(matrix.total.total, 4 + 2)
  assert.equal(matrix.total['0'], 1)
  assert.equal(matrix.total['1'], 1)
  const table = v.buildStageTable(peopleReport)
  assert.equal(table.total.waiting, 5) // the matrix total (6) less the No order column (1)
  assert.equal(table.rows[0].waiting, 1)
  assert.equal(table.rows[0].share, 20)
  assert.equal(v.buildStageMatrix(peopleReport, 'md').rows.length, 0)
  assert.equal(v.buildStageMatrix(peopleReport, 'adm').rows.length, 0) // not on the filter either
  assert.equal(v.shownOverdue(peopleReport), 1 + 1) // the tile adds up the rows shown, not the server's 10
  assert.equal(v.shownOverdue({ staff: [], tasks: peopleReport.tasks }), 10)
  assert.equal(v.shownOverdue({ staff: [] }), null)
})
