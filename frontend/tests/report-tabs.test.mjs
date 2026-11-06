import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/reports/report-tabs.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const t = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const config = await readFile(new URL('../src/features/reports/report-config.ts', import.meta.url), 'utf8')

const q = text => new URLSearchParams(text)

test('every tab named here is a report in the configuration, and the other way round', () => {
  const keys = [...config.matchAll(/^\s+key: '(\w+)', label:/gm)].map(match => match[1])
  assert.deepEqual([...t.REPORT_TABS].sort(), keys.sort())
})

test('a known ?tab= opens that tab; unknown or missing falls back to Pipeline', () => {
  assert.equal(t.reportTabFromParams(q('tab=staff'), true), 'staff')
  assert.equal(t.reportTabFromParams(q('tab=advance'), false), 'advance')
  assert.equal(t.reportTabFromParams(q('tab=nonsense'), true), 'funnel')
  assert.equal(t.reportTabFromParams(q(''), true), 'funnel')
})

test('an older link that names only a staff member opens Staff performance for a supervisor, not for staff', () => {
  assert.equal(t.reportTabFromParams(q('staff=abc&from=2026-10-01&to=2026-10-30'), true), 'staff')
  assert.equal(t.reportTabFromParams(q('staff=abc'), false), 'funnel')
  assert.equal(t.reportTabFromParams(q('tab=orders&staff=abc'), true), 'orders') // an explicit tab always wins
})

test('choosing a tab writes ?tab= and drops the dates and person that belonged to the old tab', () => {
  assert.equal(t.paramsForTab(q('tab=staff&from=2026-11-02&to=2026-11-08&staff=abc'), 'source').toString(), 'tab=source')
  assert.equal(t.paramsForTab(q('tab=staff&from=2026-11-02&to=2026-11-08'), 'funnel').toString(), '')
  assert.equal(t.paramsForTab(q('x=1'), 'staff').toString(), 'x=1&tab=staff')
})

test('the former /staff-report address lands on the Staff performance tab and keeps the week and person', () => {
  assert.equal(t.staffReportRedirect(''), '/reports?tab=staff')
  assert.equal(t.staffReportRedirect('?from=2026-11-02&to=2026-11-08&staff=abc'), '/reports?from=2026-11-02&to=2026-11-08&staff=abc&tab=staff')
  assert.equal(t.staffReportRedirect('?tab=orders'), '/reports?tab=staff')
})

test('the sidebar has one Reports item and /staff-report only redirects', async () => {
  const shell = await readFile(new URL('../src/app/layout/app-shell.tsx', import.meta.url), 'utf8')
  assert.equal([...shell.matchAll(/to: '\/reports'/g)].length, 1)
  assert.doesNotMatch(shell, /Staff Report|\/staff-report/)
  const routes = await readFile(new URL('../src/app/routes.tsx', import.meta.url), 'utf8')
  assert.match(routes, /path="staff-report" element=\{<StaffReportRedirect \/>\}/)
  const page = await readFile(new URL('../src/screens/reports/reports.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(page, /Weekly staff report|navigate\('\/staff-report'\)/)
})
