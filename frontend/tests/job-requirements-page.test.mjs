import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

/* Registration of the Job requirements page: the menu entry (directly above Clients), the lazy route, the prefetch entry, the dashboard card. */
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
const shell = await read('../src/app/layout/app-shell.tsx')
const routes = await read('../src/app/routes.tsx')
const prefetch = await read('../src/app/prefetch.ts')
const dashboard = await read('../src/screens/dashboard/dashboard.tsx')
const page = await read('../src/screens/job-requirements/job-requirements.tsx')

const navLines = shell.slice(shell.indexOf('const NAV: NavItem[] = ['), shell.indexOf('const GROUPS')).split('\n').filter((line) => line.includes("{ to: '"))
const order = navLines.map((line) => /to: '([^']+)'/.exec(line)[1])

test('Job requirements is in the Recruitment group directly above Clients, with no role or capability gate', () => {
  const at = order.indexOf('/job-requirements')
  assert.ok(at >= 0)
  assert.equal(order[at + 1], '/clients')
  const line = navLines[at]
  assert.match(line, /label: 'Job requirements'/)
  assert.match(line, /group: 'Recruitment'/)
  assert.doesNotMatch(line, /capability|supervisorsOnly|extension/)
  assert.match(navLines[at + 1], /group: 'Recruitment'/)
})

test('the route is lazy loaded and listed in the first-screen and prefetch tables', () => {
  assert.match(routes, /'job-requirements\/job-requirements': \(\) => import\('@\/screens\/job-requirements\/job-requirements'\)/)
  assert.match(routes, /const JobRequirementsPage = lazy\(loaders\['job-requirements\/job-requirements'\]\)/)
  assert.match(routes, /<Route path="job-requirements" element=\{<JobRequirementsPage \/>\} \/>/)
  assert.ok(routes.includes("'job-requirements/job-requirements'],"), 'the first-screen table names it')
  assert.ok(prefetch.includes('[code.jobRequirements]'))
  assert.match(prefetch, /jobRequirements: \(\) => import\('@\/screens\/job-requirements\/job-requirements'\)/)
})

test('the page uses the shared pieces and the admin-level helper, never the literal role', () => {
  assert.match(page, /<PageHeader title="Job requirements"/)
  assert.match(page, /isAdminLevel\(store\.me\.role\)/)
  assert.doesNotMatch(page, /role === 'admin'/)
  assert.match(page, /Export Excel/)
  assert.match(page, /New requirement/)
  assert.match(page, /label: 'Overdue'/)
  assert.match(page, /Assigned to/)
})

test('the dashboard has a Job requirements card that opens the page', () => {
  assert.match(dashboard, /key: 'requirements', label: 'Job requirements'/)
  assert.match(dashboard, /to: '\/job-requirements'/)
  assert.ok(dashboard.includes("'/job-requirements?status=active&limit=1'"))
})

const cells = await read('../src/screens/job-requirements/job-requirement-cells.tsx')
const styles = await read('../src/styles/job-requirements.css')

test('Status is a real switch: role=switch, aria-checked, green when on, a label, a focus ring and a 44px touch size', () => {
  assert.match(cells, /role="switch" aria-checked=\{item\.status === 'active'\}/)
  assert.match(cells, /jr-switch-label">\{statusLabel\(item\.status\)\}/)
  assert.doesNotMatch(cells, /jr-status-dot/)
  assert.match(styles, /\.jr \.jr-switch\.is-on \.jr-switch-track \{ background: var\(--success-ink\)/)
  assert.match(styles, /\.jr \.jr-switch:focus-visible \{ outline: 2px solid/)
  assert.match(styles, /\.jr \.jr-switch \{[^}]*min-height: 44px/)
  assert.match(page, /if \(item\.status === 'active'\) setDialog\(\{ kind: 'close'/, 'turning it off still asks why')
  assert.match(page, /else keep\(item\.id, \{ status: flipStatus\(item\.status\) \}\)/, 'turning it on reopens')
})

test('the table is as wide as its box when the columns fit (no scroll bar) and every cell wraps instead of overflowing', () => {
  assert.match(page, /style=\{\{ width: widths\.total \}\}/)
  assert.match(page, /ref=\{boxRef\}/)
  assert.match(styles, /\.jr \.jr-details-sum \{[^}]*-webkit-line-clamp: 2/)
  assert.match(styles, /\.jr \.jr-colhead-name \{[^}]*white-space: normal/)
  assert.match(styles, /\.jr \.jr-upload \{[^}]*width: 44px; height: 44px/)
})

test('Country and the details summary wrap (three lines), the owner select shows a long name in full', () => {
  assert.match(page, /<InlineCell multiline centered saved=\{item\.country\}/)
  assert.match(cells, /function SuggestArea/)
  assert.match(styles, /\.jr \.jr-details-sum \{[^}]*-webkit-line-clamp: 3/)
  assert.match(styles, /\.jr \.jr-owner > span \{[^}]*white-space: normal/)
})
