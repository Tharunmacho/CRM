import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/*
 * The owner's rule: changing the period or the recruiter must never show the PREVIOUS period's numbers (a "Today" that reads yesterday's work); a
 * different question shows the loading state. A live refresh of the same question keeps its numbers. The report's live topics cover every table the
 * figures are read from, so an action on the Tasks page reaches the Reports page within the live cycle.
 */
const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
const load = async (path) => {
  const js = ts.transpileModule(await read(path), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const view = await load('features/daily-report/daily-report-view.ts')
const live = await load('features/daily-report/daily-report-live.ts')

test('a report is drawn only when it answers the question now asked', () => {
  assert.equal(view.showsReport({ data: null, current: false }), false, 'nothing yet: loading')
  assert.equal(view.showsReport({ data: { period: 'yesterday' }, current: false }), false, 'the previous period\'s answer must not be drawn under the new heading')
  assert.equal(view.showsReport({ data: { period: 'today' }, current: true }), true)
  assert.equal(view.showsReport({ data: null, current: true }), false)
})

test('the page draws the report through showsReport, never dims old numbers, and shows an error instead of another period\'s numbers', async () => {
  const page = await read('features/daily-report/daily-report-page.tsx')
  assert.match(page, /const answers = showsReport\(state\)/)
  assert.match(page, /\{!answers && !error && <Skeleton \/>\}/, 'a different question shows the loading state')
  assert.match(page, /\{answers && data && common && /, 'the body is drawn only for an answer to the current question')
  assert.doesNotMatch(page, /is-stale|const stale\b/, 'old numbers are never kept on screen, dimmed or not')
  assert.match(page, /const blocking = !answers && !!error && !outdated/, 'an error for a new question blocks; it never falls back to the previous question\'s figures')
})

test('the report listens to every topic its figures are read from', () => {
  for (const topic of ['leads', 'candidates', 'applications', 'tasks', 'users', 'reports', 'daily_report']) assert.ok(live.LIVE_TOPICS.includes(topic), topic)
  assert.ok(live.POLL_SAFETY_MS <= 20_000 && live.POLL_DOWN_MS <= 15_000, 'a missed event is caught within about 20 seconds')
})

test('no report screen invents numbers: no random, demo or sample data in the report path', async () => {
  for (const file of ['features/daily-report/report-task.tsx', 'features/daily-report/report-lead.tsx', 'features/daily-report/report-sections.tsx', 'features/daily-report/daily-report-view.ts',
    'features/daily-report/report-management.ts', 'features/reports/client-tracker.tsx', 'features/reports/report-views.tsx']) {
    const source = await read(file)
    assert.doesNotMatch(source, /Math\.random|faker|lorem|\bdemo\b|\bdummy\b|\bmock\b/i, file)
  }
})
