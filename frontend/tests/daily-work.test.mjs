import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/daily-work/daily-work-model.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const m = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
/** A source file without its comments, so a rule about the rendered words is not tripped by an explanation of what was removed. */
const code = async (path) => (await read(path)).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const item = (over = {}) => ({ id: 'c1', status: 'open', applicationId: null, waiting: false, ...over })
const summary = (over = {}) => ({ total: 15, toWork: 12, doneToday: 3, ...over })

test('the header summary reads "N to work · M done today", with no goals or quota', () => {
  assert.equal(m.summaryLine(summary()), '12 to work · 3 done today')
  assert.equal(m.summaryLine(summary({ toWork: 0, doneToday: 0 })), '0 to work · 0 done today')
  assert.equal(m.staffSummaryLine(summary()), '12 to work · 3 done today')
  assert.equal(m.staffSummaryLine({ total: 0, toWork: 0, doneToday: 0 }), 'No candidates') // never a bare 0/0
})

test('the recruiter board lists who has the most left to work first, then the most done, then by name', () => {
  const staff = [
    { userId: 'a', name: 'Asha', active: true, summary: summary({ toWork: 2, doneToday: 5 }) },
    { userId: 'b', name: 'Bala', active: true, summary: summary({ toWork: 9, doneToday: 0 }) },
    { userId: 'c', name: 'Chitra', active: true, summary: { total: 0, toWork: 0, doneToday: 0 } },
    { userId: 'd', name: 'Dev', active: true, summary: summary({ toWork: 2, doneToday: 1 }) },
    { userId: 'e', name: 'Anil', active: true, summary: summary({ toWork: 2, doneToday: 5 }) },
  ]
  assert.deepEqual(m.staffByWorkLeft(staff).map((s) => s.name), ['Bala', 'Anil', 'Asha', 'Dev', 'Chitra'])
})

test('the list is split into the candidates to work and the Done today group, each keeping the server order', () => {
  const rows = [item({ id: '1' }), item({ id: '2', waiting: true }), item({ id: '3', status: 'done' }), item({ id: '4' }), item({ id: '5', status: 'done' })]
  const groups = m.splitItems(rows)
  assert.deepEqual(groups.toWork.map((r) => r.id), ['1', '2', '4'])
  assert.deepEqual(groups.done.map((r) => r.id), ['3', '5'])
  assert.deepEqual(m.splitItems([]), { toWork: [], done: [] })
})

test('a row offers Complete on a placement, Open (the application form) without one, and View once done today', () => {
  assert.equal(m.rowAction(item({ applicationId: 'app-1' })), 'Complete')
  assert.equal(m.rowAction(item()), 'Open')
  assert.equal(m.rowAction(item({ applicationId: 'app-1', status: 'done' })), 'View')
})

test('times display on the agency clock, not the browser clock', () => {
  assert.equal(m.clockTime('2026-11-03T05:30:00Z', 'Asia/Kolkata'), '11:00')
  assert.equal(m.clockTime('2026-11-03T05:30:00Z', 'UTC'), '05:30')
  assert.equal(m.clockTime('bad'), '--:--')
})

test('the quota, slot, deadline and countdown rules are gone from the model', () => {
  for (const name of ['dueTone', 'countdownLabel', 'spanLabel', 'msUntil', 'nextCrossing', 'sessionProgress', 'postponedLabel', 'quotaLine',
    'pacingMessage', 'checkpointLabel', 'goalPercent', 'promptState', 'clockSkew', 'staffNeedingAttention', 'candidatesCell']) {
    assert.equal(m[name], undefined, name)
  }
})

test('the referral column names the referrer, otherwise how the CV arrived', () => {
  assert.equal(m.referralLabel({ source: 'referral', label: 'Referral', detail: ' Suresh Kumar ' }), 'Suresh Kumar')
  assert.equal(m.referralLabel({ source: 'referral', label: 'Referral', detail: null }), 'Referral')
  assert.equal(m.referralLabel({ source: 'whatsapp', label: 'WhatsApp', detail: null }), 'WhatsApp')
})

test('the table has the agreed sheet columns and no Deadline column; the Recruiter column is for supervisors only', async () => {
  const table = await code('../src/screens/tasks/daily-table.tsx')
  const columns = [...table.slice(table.indexOf('const TASK_COLUMNS'), table.indexOf('] as const')).matchAll(/title: '([^']+)'/g)].map((c) => c[1])
  // The nine agreed columns in the agency's order; Recruiter (supervisors only) sits after the candidate and the unlabeled actions cell trails.
  assert.deepEqual(columns, ['S NO', 'CLIENT / COUNTRY', 'CANDIDATE NAME', 'Recruiter', 'JOB', 'REF', 'STAGE', 'REMARKS', 'AMOUNT', 'GOOGLE REVIEW', 'Actions'])
  assert.deepEqual(columns.filter((c) => c !== 'Recruiter' && c !== 'Actions'),
    ['S NO', 'CLIENT / COUNTRY', 'CANDIDATE NAME', 'JOB', 'REF', 'STAGE', 'REMARKS', 'AMOUNT', 'GOOGLE REVIEW'])
  assert.match(table, /key: 'actions', title: 'Actions', width: \d+, quiet: true/)
  assert.doesNotMatch(table, /Referral|'S\.No'/)
  assert.match(table, /key: 'staff', title: 'Recruiter', width: \d+, supervisor: true/)
  assert.doesNotMatch(table, /Deadline|countdown|overdue|Postponed|sessionLabel|slotNo|dueAt/i)
  // ONE ID per candidate: the Data Bank ID (the shared helper); the CND reference is not rendered next to it.
  assert.match(table, /import \{ primaryId \} from/)
  assert.match(table, /cid-main/)
  assert.doesNotMatch(table, /secondaryId|cid-ref/)
  // Open goes to the application form, the row menu keeps both entries, remarks stay.
  const screen = await code('../src/screens/tasks/daily-work.tsx')
  assert.match(screen, /navigate\(applicationFormHref\(row\.candidateId\)\)/)
  assert.match(table, /Open candidate profile/)
  assert.match(table, /Open stage/)
  assert.doesNotMatch(table, /Add remark/, 'no typing in the Remarks cell: it is a dropdown of remark templates')
  assert.match(table, /Remark for/)
})

test('the screen has no morning/evening, postponed, quota, goals or hourly checkpoints, and says "recruiters" not "staff"', async () => {
  const screen = await code('../src/screens/tasks/daily-work.tsx')
  const board = await code('../src/screens/tasks/daily-staff-board.tsx')
  for (const text of [screen, board]) {
    assert.doesNotMatch(text, /Morning|Evening|Postponed|carried over|quota|GoalsPanel|GoalsDialog|daily-goals|Checkpoint|Next deadline|overdue/i)
    assert.doesNotMatch(text, /All staff|Filter by staff/)
  }
  assert.match(screen, /listTitle\(person\)/)
  assert.match(screen, /All recruiters/)
  assert.doesNotMatch(screen, /StatStrip|Called today|Stages moved today/, 'no summary cards above the recruiters: each recruiter card carries the figures')
  assert.match(board, /called/)
  assert.match(board, /stage moved/)
  assert.match(board, /staffByAttention/)
  assert.match(board, /recruiterStatus/)
  assert.match(board, /WorkBoard/)
})

test('the status chip rule: no candidates, all done, idle (nothing done), behind (under a third done), on track', () => {
  const s = (toWork, doneToday) => m.recruiterStatus({ toWork, doneToday })
  assert.equal(s(0, 0), 'none')
  assert.equal(s(0, 8), 'on-track') // everything on the list is done
  assert.equal(s(31, 0), 'idle') // something to work, nothing done
  assert.equal(s(24, 3), 'behind') // 3 of 27 = 11%
  assert.equal(s(20, 9), 'behind') // 9 of 29 = 31%
  assert.equal(s(20, 10), 'on-track') // exactly a third (10 of 30) is on track
  assert.equal(s(6, 6), 'on-track')
  assert.deepEqual(['idle', 'behind', 'on-track', 'none'].map((k) => m.STATUS_LABEL[k]), ['Idle', 'Behind', 'On track', 'No candidates'])
  assert.equal(m.donePercent({ toWork: 17, doneToday: 10 }), 37)
  assert.equal(m.donePercent({ toWork: 0, doneToday: 0 }), 0)
})

test('recruiters who need attention come first: idle, behind, on track, then no candidates; the biggest backlog first inside a status', () => {
  const row = (name, toWork, doneToday) => ({ userId: name, name, active: true, summary: { total: toWork + doneToday, toWork, doneToday } })
  const staff = [row('Rohit', 0, 0), row('Divya', 0, 8), row('Gayathri', 17, 10), row('Priya', 12, 0), row('Arjun', 24, 3), row('Sana', 31, 0), row('Anil', 12, 0)]
  assert.deepEqual(m.staffByAttention(staff).map((x) => x.name), ['Sana', 'Anil', 'Priya', 'Arjun', 'Gayathri', 'Divya', 'Rohit'])
  assert.deepEqual(m.staffByAttention([]), [])
  assert.deepEqual(m.statusCounts(staff), { idle: 3, behind: 1, 'on-track': 2, none: 1 })
  assert.deepEqual(m.teamTotals(staff), { total: 117, toWork: 96, doneToday: 21 })
  // sorting never mutates the server's list
  assert.equal(staff[0].name, 'Rohit')
})

test('list titles name the first name of the picked recruiter, else everyone', () => {
  assert.equal(m.listTitle(''), "Everyone's candidates")
  assert.equal(m.listTitle('Gayathri Nair'), "Gayathri's candidates")
  assert.equal(m.listTitle('Gayathri Nair', 'follow-up candidates'), "Gayathri's follow-up candidates")
  assert.deepEqual(m.followUpByCount([
    { userId: 'a', name: 'Asha', active: true, summary: { total: 0 } },
    { userId: 'b', name: 'Bala', active: true, summary: { total: 6 } },
    { userId: 'c', name: 'Chitra', active: true, summary: { total: 6 } },
  ]).map((x) => x.name), ['Bala', 'Chitra', 'Asha'])
})

test('the "complete your tasks first" banner is gone from every page', async () => {
  const shell = await read('../src/app/layout/app-shell.tsx')
  assert.doesNotMatch(shell, /DailyWorkPrompt/)
  const hook = await code('../src/features/daily-work/use-daily-work.ts')
  assert.doesNotMatch(hook, /nextCrossing|clockSkew|skew/)
})

const country = (over = {}) => ({ country: null, countrySource: null, locationHint: null, ...over })

test('the country cell prefers the order, then the form preference, then a country recognised in the location', () => {
  const known = (text) => ['UAE', 'India'].includes(text) ? 'x' : null
  const order = m.countryCell(country({ country: 'Qatar', countrySource: 'order' }), known)
  assert.deepEqual([order.names, order.source], [['Qatar'], 'order'])
  const pref = m.countryCell(country({ country: 'Saudi Arabia, United Arab Emirates', countrySource: 'preference' }), known)
  assert.deepEqual([pref.names, pref.source], [['Saudi Arabia', 'United Arab Emirates'], 'preference'])
  assert.match(pref.hint, /preferred country/)
  const place = m.countryCell(country({ locationHint: 'Dubai, UAE' }), known)
  assert.deepEqual([place.names, place.source], [['UAE'], 'location'])
  assert.equal(m.countryCell(country({ locationHint: 'Kochi' }), known).source, 'none')
})

test('with nothing saved the country cell says so honestly (nationality is never used)', () => {
  const none = m.countryCell(country())
  assert.deepEqual([none.names, none.source, none.hint], [[], 'none', 'No preferred country saved yet'])
})

test('position and client cells say where the value came from or why it is missing', () => {
  assert.equal(m.positionCell({ position: 'Welder', positionSource: 'order' }).source, 'order')
  assert.equal(m.positionCell({ position: 'Mason', positionSource: 'preference' }).source, 'preference')
  assert.equal(m.positionCell({ position: 'Rigging', positionSource: 'skill' }).source, 'skill')
  assert.deepEqual(m.positionCell({ position: null, positionSource: null }), { text: '', source: 'none', hint: 'No job preference saved yet' })
  assert.deepEqual(m.clientCell({ client: null, kind: 'call' }), { text: '', hint: 'Not matched to an order yet' })
  assert.equal(m.clientCell({ client: 'Gulf Builders', kind: 'stage' }).text, 'Gulf Builders')
})

test('long country names get their short form', () => {
  assert.equal(m.countryShort('United Arab Emirates'), 'UAE')
  assert.equal(m.countryShort('Saudi Arabia'), 'KSA')
  assert.equal(m.countryShort('Qatar'), 'Qatar')
})

test('the free-text remark box is gone from the model (the Remarks cell is a dropdown)', () => {
  for (const name of ['remarkBox', 'REMARK_MIN', 'REMARK_MAX']) assert.equal(m[name], undefined, name)
})

test("the Tasks page has one 'Candidates' tab; Follow-up and Travelled are not tabs", () => {
  assert.deepEqual(m.bucketTabs({ today: 12, followUp: 7 }), [{ key: 'daily', label: 'Candidates' }])
  assert.equal(m.tabLabel('Follow-up', 0), 'Follow-up (0)')
})

test('follow-up says how long ago the last update was', () => {
  const now = Date.parse('2026-11-10T12:00:00Z')
  assert.equal(m.staleLabel('2026-11-10T01:00:00Z', now), 'today')
  assert.equal(m.staleLabel('2026-11-09T01:00:00Z', now), '1 day ago')
  assert.equal(m.staleLabel('2026-10-31T12:00:00Z', now), '10 days ago')
  assert.equal(m.staleLabel('2026-11-12T12:00:00Z', now), 'today') // never negative
  assert.equal(m.staleLabel('bad', now), '')
})

test('the Tasks page is one list of every recruiter\'s candidates with what happened to each today, and no Follow-up or Travelled tab', async () => {
  const tasks = await code('../src/screens/tasks/tasks.tsx')
  assert.match(tasks, /wantsFollowUp/)
  assert.match(tasks, /bucketTabs\(bucketCounts\)/)
  const daily = await code('../src/screens/tasks/daily-work.tsx')
  assert.doesNotMatch(daily, /TasksTabs|TravelledPanel|FollowUpTable/)
  assert.match(daily, /taskRows\(/)
  assert.match(daily, /recruiterWork\(/)
  assert.match(daily, /WorkBoard/)
  const table = await code('../src/screens/tasks/task-table.tsx')
  assert.match(m.TASK_LIST_COLUMNS.map((c) => c.title).join('|'), /TODAY/)
  assert.match(table, /todayChips\(/)
  assert.match(table, /Nothing yet/)
  assert.doesNotMatch(table, /Remark added/, 'a remark is no longer a chip in TODAY')
  assert.match(await code('../src/features/daily-work/daily-work-model.ts'), /Called/)
  assert.match(await code('../src/features/daily-work/daily-work-model.ts'), /Stage moved/)
  assert.match(table, /from '\.\/daily-table'/) // the cells are the Tasks cells, not copies
})

test('what a recruiter did today: called, stage moved, remarked; the board counts them and the idle ones come first', () => {
  const zone = 'Asia/Kolkata'
  const base = { kind: 'stage', status: 'open', waiting: false, completedAt: null, referral: { source: '', label: '', detail: null }, remark: null, stage: 3 }
  const daily = [
    { ...base, id: 'a', candidateId: 'c1', ownerId: 'u1', kind: 'call', status: 'done', stage: null, completedAt: '2026-11-18T05:00:00Z' },
    { ...base, id: 'b', candidateId: 'c2', ownerId: 'u1', status: 'done', completedAt: '2026-11-18T06:00:00Z' },
    { ...base, id: 'c', candidateId: 'c3', ownerId: 'u1', remark: { body: 'x', at: '2026-11-18T07:00:00Z', by: 'G', count: 1 } },
    { ...base, id: 'd', candidateId: 'c4', ownerId: 'u2' },
  ]
  const follow = [
    { ...base, id: 'e', applicationId: 'e', candidateId: 'c2', ownerId: 'u1' },   // already in the daily list: shown once
    { ...base, id: 'f', applicationId: 'f', candidateId: 'c5', ownerId: 'u2', remark: { body: 'old', at: '2026-11-10T07:00:00Z', by: 'Y', count: 1 } },
  ]
  const rows = m.taskRows(daily, follow, '2026-11-18', zone)
  assert.equal(rows.length, 5)
  assert.deepEqual(rows.slice(0, 2).map((r) => r.candidateId).sort(), ['c4', 'c5'], 'candidates nothing happened to come first')
  const work = m.recruiterWork(rows)
  assert.deepEqual(work.get('u1'), { assigned: 3, called: 3, moved: 1, remarks: 1, untouched: 0, calledAll: 3, movedAll: 4 })
  assert.deepEqual(work.get('u2'), { assigned: 2, called: 0, moved: 0, remarks: 0, untouched: 2, calledAll: 2, movedAll: 4 })
  assert.equal(m.workStatus(work.get('u1')), 'done')
  assert.equal(m.workStatus(work.get('u2')), 'idle')
  assert.deepEqual(m.peopleByWork([{ userId: 'u1', name: 'A' }, { userId: 'u2', name: 'B' }], work).map((p) => p.userId), ['u2', 'u1'])
  assert.equal(m.dayOf('2026-11-18T20:00:00Z', zone), '2026-11-19', 'the agency clock, not UTC')
})

test('rowAction: View once done today, Complete on a placement, Open otherwise', () => {
  assert.equal(m.rowAction(item({ status: 'done', applicationId: 'a1' })), 'View')
  assert.equal(m.rowAction(item({ applicationId: 'a1' })), 'Complete')
  assert.equal(m.rowAction(item()), 'Open')
})

test('the Tasks table header row is the primary blue of the Complete button, scoped to the Tasks screen (not the Reports tables)', async () => {
  const css = await read('../src/styles/daily-work.css')
  const rule = (selector) => {
    const at = css.indexOf(`${selector} {`)
    assert.ok(at >= 0, `${selector} rule exists`)
    return css.slice(at, css.indexOf('}', at))
  }
  const head = rule('.dw:not(.dwr) .dw-table thead th') // .dwr is the Reports screen: it keeps its own header
  assert.match(head, /background: var\(--primary\)/)
  assert.match(head, /color: var\(--on-primary\)/) // the token the primary button text uses: readable in the light and dark themes
  assert.match(head, /font-weight: 700/)
  assert.doesNotMatch(head, /#[0-9a-f]{3,8}\b|rgb\(/i) // no hard-coded blue
  assert.match(rule('.dw:not(.dwr) .dw-main thead th.is-actions'), /background: var\(--primary\)/) // the sticky actions header stays opaque blue
  assert.match(rule('.dw:not(.dwr) .dw-table thead th + th'), /border-left-color: var\(--table-header-line\)/) // the lighter header divider (theme.css), no gap lines
  // the button it matches
  assert.match(await read('../src/screens/tasks/task-table.tsx'), /variant=\{called \|\| moved \? 'secondary' : 'primary'\}/)
  // the 48-hour list header is blue too
  const page = await read('../src/styles/tasks-page.css')
  assert.match(page, /:root:root \.tk \.tk-table thead th \{ background: var\(--primary\);[^}]*color: var\(--on-primary\)/)
})

test('the admin Tasks list fits the panel at a 1440px window (no sideways scroll, nothing under the sticky actions column)', async () => {
  const widths = m.taskColumnsFor(true).map((c) => c.width)
  assert.equal(widths.length, 11) // S NO, DOC, client, job, candidate, recruiter, agency, stage, today, remarks, actions
  assert.ok(widths.reduce((a, b) => a + b, 0) <= 1111, `the plan is ${widths.reduce((a, b) => a + b, 0)}px, the panel 1111px`)
})

test('a follow-up row keeps the server agency when it is shown as a daily row', () => {
  const item = { id: 'f1', applicationId: 'f1', candidateId: 'c1', ownerId: 'u1', stage: 4, agency: 'Lotus Overseas', remark: null }
  const row = m.followUpAsDaily(item)
  assert.equal(row.agency, 'Lotus Overseas')
  assert.equal(m.agencyLabel(row), 'Lotus Overseas')
  assert.equal(m.agencyLabel({ ...row, agency: null }), '', 'the server said there is no agency: a dash, not the empty referral label')
})

test('a placement moved on from stage 2 to 11 today counts as stage moved today (Follow-up rows carry when a stage was last finished)', () => {
  const zone = 'Asia/Kolkata'
  const base = { kind: 'stage', status: 'open', waiting: false, completedAt: null, referral: { source: '', label: '', detail: null }, remark: null, stage: 9 }
  const follow = [
    { ...base, id: 'f1', applicationId: 'f1', candidateId: 'c1', ownerId: 'u1', stageMovedAt: '2026-11-18T05:00:00Z' },  // moved today (Asia/Kolkata)
    { ...base, id: 'f2', applicationId: 'f2', candidateId: 'c2', ownerId: 'u1', stageMovedAt: '2026-11-17T05:00:00Z' },  // moved yesterday
    { ...base, id: 'f3', applicationId: 'f3', candidateId: 'c3', ownerId: 'u1' },                                       // no stage moved at all
  ]
  const rows = m.taskRows([], follow, '2026-11-18', zone)
  assert.deepEqual(Object.fromEntries(rows.map((r) => [r.candidateId, r.today.moved])), { c1: true, c2: false, c3: false })
  const work = m.recruiterWork(rows).get('u1')
  assert.equal(work.moved, 1)
  assert.equal(work.called, 1, 'a stage moved today counts as a call')
  assert.equal(work.untouched, 2)
})
