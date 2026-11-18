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
  // Fee collections belongs to the admin alone (the third argument is the view_invoices capability), not to managers.
  assert.equal(t.reportTabFromParams(q('tab=collections'), true, true), 'collections')
  assert.equal(t.reportTabFromParams(q('tab=collections'), true, false), 'funnel')
  assert.equal(t.reportTabFromParams(q('tab=collections'), true), 'funnel')
  assert.equal(t.reportTabFromParams(q('tab=collections'), false), 'funnel')
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

test('the former /staff-report address opens the Daily report filtered to that person and week (the Staff performance tab only while legacy reports are on)', () => {
  assert.equal(t.staffReportRedirect(''), '/reports')
  assert.equal(t.staffReportRedirect('?from=2026-11-02&to=2026-11-08&staff=abc'), '/reports?from=2026-11-02&to=2026-11-08&staff=abc')
  assert.equal(t.staffReportRedirect('?tab=orders&staff=abc'), '/reports?staff=abc')
  assert.equal(t.staffReportRedirect('', true), '/reports?tab=staff')
  assert.equal(t.staffReportRedirect('?from=2026-11-02&to=2026-11-08&staff=abc', true), '/reports?from=2026-11-02&to=2026-11-08&staff=abc&tab=staff')
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

test('the page tabs are exactly the three report views; Accounts and the legacy reports are behind constants', () => {
  const three = ['Daily report', 'Lead sheet monitoring', 'Candidate monitoring']
  assert.deepEqual(t.pageTabs(false).map(x => x.label), three)
  assert.deepEqual(t.pageTabs(true).map(x => x.label), three) // even for the admin: Accounts is not a visible tab
  assert.equal(t.SHOW_ACCOUNTS_TAB, false)
  assert.equal(t.SHOW_LEGACY_REPORTS, false)
  // switched on, they come back
  assert.deepEqual(t.pageTabs(true, false, true).map(x => x.key), ['daily', 'lead', 'stages', 'accounts'])
  assert.deepEqual(t.pageTabs(true, true, true).map(x => x.key), ['daily', 'lead', 'stages', 'accounts', 'legacy'])
  assert.deepEqual(t.pageTabs(false, false, true).map(x => x.key), ['daily', 'lead', 'stages']) // never for a non-admin
})

test('which tab an address opens: no tab is the Daily report; old Fee/Advance collections links still open for the admin; old report keys fall back', () => {
  assert.equal(t.pageTabFromParams(q(''), true, false), 'daily')
  assert.equal(t.pageTabFromParams(q('tab=lead'), false, false), 'lead')
  assert.equal(t.pageTabFromParams(q('tab=stages'), false, false), 'stages')
  for (const tab of ['accounts', 'collections', 'advance']) {
    assert.equal(t.pageTabFromParams(q(`tab=${tab}`), true, false), 'accounts', tab)
    assert.equal(t.pageTabFromParams(q(`tab=${tab}`), false, false), 'daily', tab)
  }
  // the removed reports open the Daily report (older bookmarks keep working) unless the legacy flag is on
  for (const tab of ['funnel', 'staff', 'source', 'orders', 'rejections']) {
    assert.equal(t.pageTabFromParams(q(`tab=${tab}`), true, false), 'daily', tab)
    assert.equal(t.pageTabFromParams(q(`tab=${tab}`), true, true), 'legacy', tab)
  }
  assert.equal(t.pageTabFromParams(q('staff=abc&from=2026-10-01&to=2026-10-30'), true, false), 'daily') // the person filter is applied by the page
  assert.equal(t.pageTabFromParams(q('tab=nonsense'), true, false), 'daily')
})

test('choosing a page tab writes ?tab= and drops dates and person from the address', () => {
  assert.equal(t.paramsForPageTab(q('from=2026-11-02&to=2026-11-08&staff=abc'), 'lead').toString(), 'tab=lead')
  assert.equal(t.paramsForPageTab(q('tab=stages&staff=abc'), 'daily').toString(), '')
  assert.equal(t.paramsForPageTab(q(''), 'accounts').toString(), 'tab=accounts')
  assert.equal(t.paramsForPageTab(q(''), 'legacy').toString(), 'tab=funnel')
})

test('the code of the removed reports is kept: only their tabs are gated by one constant', async () => {
  const other = await readFile(new URL('../src/screens/reports/other-reports.tsx', import.meta.url), 'utf8')
  assert.match(other, /SHOW_LEGACY_REPORTS/)
  assert.match(other, /next\.set\('tab', key\)/)
  assert.ok(t.ACCOUNT_TABS.includes('collections') && t.ACCOUNT_TABS.includes('advance') && t.ACCOUNT_TABS.length === 2)
})
