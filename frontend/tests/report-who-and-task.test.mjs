import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

/*
 * The owner's Reports rules, read from the source of the screens: the Task report is only "candidates waiting now" (no Completed view),
 * its period and client row is hidden while the Lead sheet report keeps it, and every recruiter list goes through the one shared reportPeople.
 */
const read = (path) => readFile(new URL(`../src/features/daily-report/${path}`, import.meta.url), 'utf8')
const withoutComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1')

test('the Task report has no Completed view: no switch, no completed or stopped tiles or columns', async () => {
  const task = withoutComments(await read('report-task.tsx'))
  for (const gone of ['Completed in this period', 'Stopped in this period', 'Stopped here', 'Stages completed', 'STAGE_MODES', 'StageMode', 'dwr-seg', 'aria-pressed', 'localStorage', 'OctagonX', 'CheckCircle2', 'flow.total.completed', 'item.completed'])
    assert.ok(!task.includes(gone), `${gone} must be gone from the Task report`)
  assert.ok(task.includes('Candidates waiting at each stage'))
  assert.ok(task.includes('Submitted reports'))
  assert.deepEqual([...task.matchAll(/key: '(\w+)', label: '([^']+)'/g)].map((x) => x[2]), ['Waiting now', 'Overdue tasks'])
  const heads = [...task.slice(task.indexOf('aria-label="Stage by stage"')).matchAll(/<th scope="col"[^>]*>([^<]+)<\/th>/g)].map((x) => x[1])
  assert.deepEqual(heads, ['Stage', 'Waiting now', 'Share'])
})

test('the period row is hidden on the Task report only, and the client is not applied there', async () => {
  const page = await read('daily-report-page.tsx')
  assert.match(page, /const periodMatters = view !== 'daily'/)
  assert.match(page, /<PeriodRow filters=\{periodMatters\}/)
  assert.match(page, /client: periodMatters \? client : 'all'/)
  const bar = await read('daily-report-toolbar.tsx')
  assert.match(bar, /\{filters && <div className="dwr-period-main">/)
  assert.match(bar, /\{filters && props\.clients\.length > 0/)
  assert.ok(bar.includes('{props.live}'), 'the Live label stays on every tab')
})

test('every recruiter list of the report tabs goes through reportPeople, and no screen compares a role with the admin literal', async () => {
  for (const file of ['report-task.tsx', 'report-lead.tsx', 'report-attendance-tab.tsx']) assert.match(await read(file), /reportPeople\(/, file)
  for (const file of ['daily-report-view.ts', 'lead-matrix.ts', 'report-task.tsx', 'report-lead.tsx', 'report-attendance-tab.tsx'])
    assert.ok(!/role\s*(===|!==)\s*'admin'/.test(withoutComments(await read(file))), file)
})

test('the matrix fits its panel: fixed layout, a slim Recruiter column, short stage headings', async () => {
  const task = await read('report-task.tsx')
  assert.match(task, /const NAME_WIDTH = 1[0-7]\d\b/)
  assert.match(task, /Array\.from\(\{ length: 14 \}/)
  assert.ok(task.includes('is-matrix'))
  assert.ok((await read('report-lead.tsx')).includes('is-matrix'))
  const css = await readFile(new URL('../src/styles/report-tables.css', import.meta.url), 'utf8')
  assert.match(css, /table\.dwm-table \{[^}]*table-layout: fixed/)
})
