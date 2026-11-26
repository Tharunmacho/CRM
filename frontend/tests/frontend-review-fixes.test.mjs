import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
const load = async (path, patch = (text) => text) => {
  const js = ts.transpileModule(patch(await read(path)), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

// ---------------------------------------------------------------- the call dialog (#2)
const call = await load('features/candidates/call-outcome.ts')

test('the call dialog starts clean for every candidate, so nothing typed for one person reaches another', async () => {
  assert.deepEqual(call.initialCallForm(''), { called: 'yes', interested: '', remarks: '', error: '' })
  assert.deepEqual(call.initialCallForm('rejected'), { called: 'yes', interested: 'rejected', remarks: '', error: '' })
  const text = await read('features/candidates/components/call-dialog.tsx')
  assert.match(text, /\}, \[open, candidateId, initialInterested\]\)/, 'reset on every opening and when the candidate changes')
  assert.match(text, /initialCallForm\(initialInterested\)/)
})

test('"Called: No" can never be posted with a decline or a rejection (the server would not complete the call task)', () => {
  assert.deepEqual(call.callBody('no', 'no', 'x'), { called: false, interested: 'unreachable', remarks: 'x' })
  assert.deepEqual(call.callBody('no', 'rejected', ''), { called: false, interested: 'unreachable', remarks: '' })
  assert.deepEqual(call.callBody('yes', 'rejected', 'not suitable'), { called: true, interested: 'rejected', remarks: 'not suitable' })
  assert.equal(call.callCanSave('yes', ''), false, 'an answer is needed when the call was made')
  assert.equal(call.callCanSave('no', ''), true, 'when the call was not made the answer is fixed')
  assert.equal(call.callCanSave('yes', 'no'), true)
})

test('"Rejected" is worded as Rejected on the profile, in the stage window and in the Data Bank column (never "Not reachable")', () => {
  assert.equal(call.callAnswerWords('rejected').badge, 'Rejected')
  assert.equal(call.callAnswerWords('rejected').fact, 'Rejected')
  assert.equal(call.callAnswerWords('rejected').column, 'Rejected')
  assert.equal(call.callAnswerWords('rejected').tone, 'stamp')
  assert.deepEqual([call.callAnswerWords('yes').badge, call.callAnswerWords('no').badge, call.callAnswerWords('unreachable').badge, call.callAnswerWords(undefined).badge], ['Interested', 'Not interested', 'Not reachable', 'Not reachable'])
  assert.equal(call.callAnswerWords('unreachable').column, 'Unreachable')
})

// ---------------------------------------------------------------- the closed-row tab (#5)
const stages = await load('shared/workflow/stages.ts')
const REASONS = ['prescreen_failed', 'not_interested', 'resume_not_received', 'interview_rejected', 'medical_unfit', 'unmapped', 'changed_job', 'offer_declined', 'offer_not_received',
  'stage_rejected', 'stage_declined', 'interview_not_possible', 'passport_not_available', 'advance_not_paid', 'visa_rejected', 'visa_stamping_refused', 'not_travelling', 'pcc_not_cleared']

test('every closure reason names the same Tasks tab the server puts the candidate on', async () => {
  const types = await read('shared/model/types.ts')
  const declared = [...types.slice(types.indexOf('export type ClosureReason')).split('export interface StageRecord')[0].matchAll(/'([a-z_]+)'/g)].map((m) => m[1])
  assert.deepEqual([...declared].sort(), [...REASONS].sort(), 'the test list is the full ClosureReason type')
  const expected = { stage_declined: 'Declined', offer_declined: 'Declined', not_interested: 'Declined', not_travelling: 'Declined', unmapped: null, changed_job: null }
  for (const reason of REASONS) assert.equal(stages.closureTab(reason), reason in expected ? expected[reason] : 'Rejected', reason)
  assert.equal(stages.closureTab(undefined), null)
  // the server's sets, mirrored (services/daily_work.py DECLINE_CLOSURES / NOT_OUTCOMES)
  const python = await readFile(new URL('../../backend/app/services/daily_work.py', import.meta.url), 'utf8')
  const set = (name) => [...python.match(new RegExp(`${name} = \\(([^)]*)\\)`))[1].matchAll(/ClosureReason\.([a-z_]+)/g)].map((m) => m[1]).sort()
  assert.deepEqual([...stages.DECLINED_CLOSURES].sort(), set('DECLINE_CLOSURES'))
  assert.deepEqual([...stages.NO_OUTCOME_CLOSURES].sort(), set('NOT_OUTCOMES'))
})

// ---------------------------------------------------------------- the requirements sheet (#1, #6)
const jr = await load('features/job-requirements/job-requirements-model.ts')
const item = (id, patch = {}) => ({ id, clientName: id, details: {}, custom: {}, status: 'active', ...patch })

test('the requirement form diffs against the baseline it opened with, so another persons newer value is never written back', async () => {
  const opened = jr.formFor({ details: { deadline: '2026-12-01', remarks: 'a' } })
  const form = { ...opened, remarks: 'mine' }
  assert.deepEqual(jr.buildPatch(opened, form), { details: { remarks: 'mine' } })
  const live = jr.formFor({ details: { deadline: '2026-12-15', remarks: 'a' } })
  assert.notDeepEqual(jr.buildPatch(live, form), { details: { remarks: 'mine' } }, 'against the live row the stale deadline WOULD have been sent back (the bug)')
  const text = await read('screens/job-requirements/job-requirement-form.tsx')
  assert.match(text, /const \[start\] = useState\(\(\) => formFor\(item, options\)\)/)
  assert.match(text, /disabled=\{saving \|\| gone\}/)
  assert.match(text, /<div className="jrf" ref=\{root\} onKeyDown=\{enterMoves\}>/, 'a div, not a form: Enter can never submit and close the dialog')
  assert.doesNotMatch(text, /<form[\s>]/)
})

test('a silent read waits for a save or create, and a row created a moment ago survives a read that started before it existed', () => {
  assert.equal(jr.readBlocked(0, 0), false)
  assert.equal(jr.readBlocked(1, 0), true)
  assert.equal(jr.readBlocked(0, 2), true)
  const page = [item('a', { clientName: 'server A' }), item('b')]
  const current = [item('new'), item('a', { clientName: 'typed A' }), item('b')]
  const merged = jr.mergeRead(page, { a: { clientName: 'typed A' } }, current, new Set(['new']))
  assert.deepEqual(merged.map((row) => row.id), ['new', 'a', 'b'], 'the new row stays on top')
  assert.equal(merged.find((row) => row.id === 'a').clientName, 'typed A', 'unsaved text wins over the server copy')
  assert.deepEqual(jr.mergeRead(page, {}, current, new Set()).map((row) => row.id), ['a', 'b'], 'a row that is not new and not on the server is gone')
  assert.deepEqual(jr.mergeRead([item('new'), ...page], {}, current, new Set(['new'])).map((row) => row.id), ['new', 'a', 'b'], 'no duplicate once the server has it')
})

test('the sheet counts a create as sending and runs the skipped read afterwards', async () => {
  const hook = await read('features/job-requirements/use-job-requirements.ts')
  assert.match(hook, /inFlight\.current \+= 1   \/\/ a read that lands while the row is being created/)
  assert.match(hook, /rerun\.current = true/)
  assert.match(hook, /const flush = useCallback/)
})

// ---------------------------------------------------------------- busy topics, CSV, drawer, menu (#8, #10)
const events = await load('shared/state/live-events.ts')
test('a stream of the same busy topic is recognised, anything else is ordinary news', () => {
  const busy = ['documents']
  assert.equal(events.isBusyOnly({ topics: ['documents'], resync: false }, busy), true)
  assert.equal(events.isBusyOnly({ topics: ['documents', 'reports'], resync: false }, busy), true)
  assert.equal(events.isBusyOnly({ topics: ['documents', 'mail'], resync: false }, busy), false, 'mail announced with it is real news')
  assert.equal(events.isBusyOnly({ topics: ['documents'], resync: true }, busy), false, 'a resync is never noise')
  assert.equal(events.isBusyOnly({ topics: ['mail'], resync: false }, busy), false)
  assert.equal(events.isBusyOnly({ topics: ['documents'], resync: false }, undefined), false)
})

const view = await load('features/daily-report/daily-report-view.ts')
test('the drill-down file says "Waiting since" for a snapshot figure and is named by its day, not by the chosen period', () => {
  assert.deepEqual(view.csvHead('stage:6').slice(0, 2), ['Waiting since (date)', 'Waiting since (time)'])
  assert.deepEqual(view.csvHead('completed').slice(0, 2), ['Date', 'Time'])
  assert.deepEqual(view.csvHead(), ['Date', 'Time', 'Recruiter', 'Name', 'Candidate ID', 'Type', 'Client', 'Job', 'Detail'])
  assert.equal(view.csvFileName('Advance', '2026-11-20', '2026-11-23', '2026-11-23'), 'advance-as-of-2026-11-23.csv')
  assert.equal(view.csvFileName('Stages completed', '2026-11-23', '2026-11-23'), 'stages-completed-2026-11-23.csv')
  const csv = view.buildCsv([{ at: '2026-11-05T10:00:00Z', staffName: 'A', name: 'N', careId: null, kind: 'candidate', client: null, job: null, detail: 'd' }], 'Asia/Kolkata', 'stage:6')
  assert.match(csv.split('\r\n')[0], /^Waiting since \(date\),Waiting since \(time\),Recruiter/)
})

test('the menu is right-anchored and sized from the real entry count; the drawer and the live feeds are rate-limited', async () => {
  const table = await read('screens/tasks/daily-table.tsx')
  assert.match(table, /style=\{\{ top: at\.top, right: at\.right \}\}/)
  assert.doesNotMatch(table, /box\.right - 190/)
  assert.match(table, /count \* \(touch \? 44 : 36\) \+ 10/)
  const css = await read('styles/daily-work.css')
  assert.match(css, /\.dw-menu \{[^}]*width: max-content;[^}]*max-width: calc\(100vw - 16px\)/)
  assert.match(css, /\.dw-menu-item \{[^}]*white-space: nowrap/)
  const drawer = await read('features/daily-report/drilldown-drawer.tsx')
  assert.match(drawer, /export const DRILL_REREAD_MS = 10_000/)
  const attendance = await read('features/daily-report/use-attendance-data.ts')
  assert.match(attendance, /MONTH_RATES: LiveRates = \{ downMs: 60_000, safetyMs: 60_000, eventGapMs: 30_000 \}/)
  assert.match(attendance, /DAY_RATES: LiveRates = \{ downMs: 15_000, safetyMs: 20_000, eventGapMs: 0 \}/)
  const dash = await read('screens/dashboard/dashboard.tsx')
  assert.match(dash, /noisyTopics: \['documents'\], noisyGapMs: 10_000/)
  const ignored = await read('features/whatsapp/ignored-numbers.tsx')
  assert.match(ignored, /waitMs: 5_000, leading: true/)
  assert.match(ignored, /mine === readNo\.current/, 'an older poll never puts the old list back')
})

test('DocInput keeps what is being typed while a change arrives, and the workspace poll backs off and skips needless writes', async () => {
  const doc = await read('screens/tasks/doc-input.tsx')
  assert.match(doc, /if \(focused\.current \|\| busy\.current\) return; setText\(saved\)/)
  assert.match(doc, /onFocus=\{\(\) => \{ focused\.current = true \}\}/)
  const store = await read('shared/state/crm-store.tsx')
  assert.match(store, /const reload = \(\): Promise<void> \| void => \{/)
  assert.match(store, /return attempt/)
  assert.match(store, /if \(!unchangedRef\.current\) persistSoon\('bootstrap'/)
})

test('every page that needs "today" takes it from the one hook, so they all agree and all roll over at midnight', async () => {
  for (const file of ['features/daily-report/report-attendance.tsx', 'features/reports/client-tracker.tsx', 'features/attendance/components/attendance-workspace.tsx', 'screens/reports/other-reports.tsx', 'screens/job-requirements/job-requirements.tsx']) {
    const text = await read(file)
    assert.match(text, /const today = useAgencyToday\(/, file)
    assert.doesNotMatch(text, /const today = agencyDay\(new Date\(\)/, file)
  }
})
