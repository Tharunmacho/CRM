import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

/*
 * The owner's Reports rules, read from the source of the screens: the Task report has no Completed / Stopped switch (its two period cards carry the
 * comparison), both reports share one lean toolbar with no client filter, and every recruiter list goes through the one shared reportPeople.
 */
const read = (path) => readFile(new URL(`../src/features/daily-report/${path}`, import.meta.url), 'utf8')
const withoutComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1')

test('the Task report has no Completed view switch: two period cards, the matrix with Stuck, the stage table, the longest waiting', async () => {
  const task = withoutComments(await read('report-task.tsx'))
  for (const gone of ['STAGE_MODES', 'StageMode', 'dwr-seg', 'aria-pressed', 'localStorage', 'OctagonX', 'flow.total.completed', 'item.completed', 'LostReasonsPanel', 'Where candidates are lost'])
    assert.ok(!task.includes(gone), `${gone} must be gone from the Task report`)
  assert.ok(task.includes('Candidates waiting at each stage'))
  assert.deepEqual([...task.matchAll(/label: '([^']+)', value: numberText\(summary\./g)].map((x) => x[1]), ['Stages completed', 'Stopped or lost'])
  const heads = [...task.slice(task.indexOf('<caption className="rp-sr">Stage by stage')).matchAll(/<th scope="col"[^>]*>([^<]+)<\/th>/g)].map((x) => x[1])
  assert.deepEqual(heads, ['Stage', 'Waiting now', 'Average wait', 'Stuck &gt;{line}d', 'Completed', 'Stopped'])
})

test('both reports share one toolbar: the period (with a custom range), the recruiter, Submitted reports and the Live label; no client filter, no explanation lines', async () => {
  const page = await read('daily-report-page.tsx')
  assert.ok(!/periodMatters|PeriodRow|clientOptions/.test(page))
  assert.match(page, /client: 'all'/)
  const bar = await read('daily-report-toolbar.tsx')
  assert.match(bar, /Custom range/)
  assert.match(bar, /<DatePicker/)
  assert.ok(bar.includes('{props.live}'), 'the Live label stays on every tab')
  assert.ok(bar.includes('Submitted reports'))
  assert.ok(!/rp-toolbar-note|Compared with/.test(bar), 'no explanation line under the toolbar')
  const header = await read('reports-header.tsx')
  assert.ok(!/<p/.test(header) && header.includes('title="Reports"'), 'the header is the title only')
})

test('the reports carry no explanatory paragraphs, legends or helper sentences', async () => {
  for (const file of ['report-task.tsx', 'report-lead.tsx', 'report-lists.tsx', 'daily-report-toolbar.tsx', 'reports-header.tsx']) {
    const source = withoutComments(await read(file))
    assert.ok(!/<p[ >]/.test(source.replace(/<p className="dwb-none[^>]*>[^<]*<\/p>/g, '').replace(/<p className="dwr-quiet">[^<]*<\/p>/g, '')), `${file}: no paragraph`)
    assert.ok(!/<footer|legend|rp-foot/.test(source), file)
  }
})

test('every recruiter list of the report tabs goes through reportPeople, and no screen compares a role with the admin literal', async () => {
  for (const file of ['report-task.tsx', 'lead-matrix.ts', 'daily-report-page.tsx', 'report-attendance-tab.tsx']) assert.match(await read(file), /reportPeople\(/, file)
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
