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
  assert.match(screen, /navigate\(applicationFormHref\(item\.candidateId\)\)/)
  assert.match(table, /Open candidate profile/)
  assert.match(table, /Open stage/)
  assert.match(table, /Add remark/)
})

test('the screen has no morning/evening, postponed, quota, goals or hourly checkpoints, and says "recruiters" not "staff"', async () => {
  const screen = await code('../src/screens/tasks/daily-work.tsx')
  const board = await code('../src/screens/tasks/daily-staff-board.tsx')
  for (const text of [screen, board]) {
    assert.doesNotMatch(text, /Morning|Evening|Postponed|carried over|quota|GoalsPanel|GoalsDialog|daily-goals|Checkpoint|Next deadline|overdue/i)
    assert.doesNotMatch(text, /All staff|Filter by staff/)
  }
  assert.match(screen, /Today's candidates/)
  assert.match(screen, /listTitle\(person\)/)
  assert.match(screen, /All recruiters/)
  assert.match(screen, /Done today/)
  assert.match(screen, /summaryLine\(summary\)/)
  assert.match(board, /staffByAttention/)
  assert.match(board, /recruiterStatus/)
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

test('the remark box starts at one line, grows with the text to about four lines and then scrolls inside', () => {
  assert.deepEqual(m.remarkBox(0), { height: m.REMARK_MIN, scroll: false })
  assert.deepEqual(m.remarkBox(20), { height: m.REMARK_MIN, scroll: false })
  assert.deepEqual(m.remarkBox(60.2), { height: 61, scroll: false })
  assert.deepEqual(m.remarkBox(m.REMARK_MAX), { height: m.REMARK_MAX, scroll: false })
  assert.deepEqual(m.remarkBox(300), { height: m.REMARK_MAX, scroll: true })
  assert.deepEqual(m.remarkBox(Number.NaN), { height: m.REMARK_MIN, scroll: false })
  assert.ok(m.remarkBox(48).height < m.remarkBox(90).height, 'grows with the text and shrinks back when it is deleted')
})

test("the three buckets: Today's candidates and Follow-up carry counts in their tab label, Travelled links to its own screen", () => {
  assert.deepEqual(m.bucketTabs({ today: 12, followUp: 7 }), [
    { key: 'daily', label: "Today's candidates (12)" }, { key: 'follow-up', label: 'Follow-up (7)' }, { key: 'travelled', label: 'Travelled' }])
  assert.deepEqual(m.bucketTabs({ today: null, followUp: null }).map((t) => t.label), ["Today's candidates", 'Follow-up', 'Travelled'])
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

test('the Tasks page has the Follow-up view and a Travelled tab that links to /travelled, and nothing is duplicated', async () => {
  const tasks = await code('../src/screens/tasks/tasks.tsx')
  assert.match(tasks, /wantsFollowUp/)
  assert.match(tasks, /bucketTabs\(bucketCounts\)/)
  const tabs = await code('../src/screens/tasks/tasks-tabs.tsx')
  assert.match(tabs, /navigate\('\/travelled'\)/)
  assert.match(tabs, /next\.set\('view', key\)/)
  const followUp = await code('../src/screens/tasks/follow-up.tsx')
  assert.match(followUp, /All recruiters/)
  assert.match(followUp, /useFollowUp/)
  assert.doesNotMatch(followUp, /Deadline|overdue|Postponed|Morning|Evening|All staff/i)
  // Follow-up reuses the Today's candidates cells instead of copying them.
  const table = await code('../src/screens/tasks/follow-up-table.tsx')
  assert.match(table, /from '\.\/daily-table'/)
  assert.match(table, /TASK_COLUMNS/) // one column plan for both tables, so their headers and widths cannot drift apart
  assert.doesNotMatch(table, /const COLUMNS/)
  assert.match(table, /AmountCell/)
  assert.match(table, /ReviewCell/)
  assert.match(table, /Open stage/)
  assert.match(table, /Open candidate profile/)
  assert.match(table, /applicationFormHref|onOpen/)
  assert.match(followUp, /navigate\(applicationFormHref\(item\.candidateId\)\)/)
})

test('a stage finished or an application closed refreshes both lists without a reload (the live topics they follow)', async () => {
  const hook = await code('../src/features/daily-work/use-daily-work.ts')
  assert.match(hook, /\['tasks', 'users', 'candidates', 'applications'\]/)
  assert.match(hook, /useFollowUp/)
  assert.match(hook, /'\/follow-up'/)
})

test("a recruiter's Tasks badge counts today's candidates to work; no overdue or deadline wording and no banner", async () => {
  const shell = await code('../src/app/layout/app-shell.tsx')
  assert.match(shell, /summary\.toWork/)
  assert.match(shell, /to work today/)
  assert.doesNotMatch(shell, /Complete your tasks first/)
})

test('AMOUNT is the advance collected, as money; blank when none is recorded', () => {
  assert.equal(m.advanceLabel({ advanceAmount: 5000, advanceCurrency: 'INR' }), '₹5,000.00')
  assert.equal(m.advanceLabel({ advanceAmount: 125000.5, advanceCurrency: null }), '₹1,25,000.50')
  assert.equal(m.advanceLabel({ advanceAmount: null, advanceCurrency: null }), null)
  assert.equal(m.advanceLabel({ advanceAmount: 0, advanceCurrency: 'INR' }), null)
  assert.match(m.advanceLabel({ advanceAmount: 100, advanceCurrency: 'AED' }), /100\.00/)
})

test('the AMOUNT and GOOGLE REVIEW cells show Yes, Pending or a dash, and never read invoice figures', async () => {
  const table = await code('../src/screens/tasks/daily-table.tsx')
  assert.match(table, /googleReview === 'obtained'[\s\S]*>Yes</)
  assert.match(table, /googleReview === 'pending'[\s\S]*>Pending</)
  assert.match(table, /advanceLabel\(item\)/)
  const model = await code('../src/features/daily-work/daily-work-model.ts')
  assert.match(model, /advanceAmount: number \| null/)
  assert.match(model, /googleReview: GoogleReview/)
  for (const text of [table, model]) assert.doesNotMatch(text, /invoice|cgst|gst/i)
})

test('the Tasks tables carry no explanatory filler: no group sentence, no "Not saved", "pref.", "from location" or "No order yet" notes', async () => {
  for (const path of ['daily-table.tsx', 'follow-up-table.tsx', 'daily-work.tsx', 'follow-up.tsx']) {
    const text = await code(`../src/screens/tasks/${path}`)
    assert.doesNotMatch(text, /Candidates with prescreening waiting|Work them in the order|Finished candidates are in Follow-up|Finished candidates move to Travelled|Not saved|from location|from skills|No order yet|'pref\.'|dw-sub|dw-pref|dw-add/, path)
  }
  const table = await code('../src/screens/tasks/daily-table.tsx')
  assert.match(table, /const bands = groups\.length > 1/) // a single group has no band
  assert.match(table, /placeholder="Add remark"/)
  assert.match(table, /useAutoGrow/)
  assert.match(table, /remarkBox\(/)
})
