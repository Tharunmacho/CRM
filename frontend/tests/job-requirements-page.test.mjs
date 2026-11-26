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

test('Country and the details summary wrap (three lines), the owners button shows long names in full', async () => {
  assert.match(page, /<InlineCell multiline centered saved=\{item\.country\}/)
  assert.match(cells, /function SuggestArea/)
  assert.match(styles, /\.jr \.jr-details-sum \{[^}]*-webkit-line-clamp: 3/)
  assert.match(await read('../src/styles/job-requirement-owners.css'), /\.jro-names \{[^}]*overflow-wrap: break-word/)
})

const form = await read('../src/screens/job-requirements/job-requirement-form.tsx')
const formStyles = await read('../src/styles/job-requirement-form.css')
const dialogs = await read('../src/screens/job-requirements/job-requirement-dialogs.tsx')
const api = await read('../src/features/job-requirements/job-requirements-api.ts')

test('the table column is Job positions, shows the titles as chips, and the row menu downloads the client\'s form as Excel', () => {
  assert.match(page, /<th scope="col">Job positions<\/th>/)
  assert.doesNotMatch(page, /Requirements details/)
  assert.match(page, /positionsSummary\(item\)/)
  assert.match(page, /label: 'Download form \(Excel\)'/)
  assert.match(page, /downloadRequirementForm\(item\.id, item\.clientName\)/)
  assert.match(page, /The form could not be created/, 'a failed download is reported')
  assert.match(api, /\/api\/job-requirements\/\$\{encodeURIComponent\(id\)\}\/form/)
  assert.match(cells, /jr-pos-chip/)
  assert.match(cells, /deadlineChip\(item, today\)/, 'the deadline chip stays on the cell')
  assert.doesNotMatch(dialogs, /DetailsDialog/, 'the old 16-box form is gone')
  assert.match(page, /<JobRequirementForm item=/)
})

test('the form has the client\'s four sections, labelled boxes on every row, and keeps the Save and Cancel footer', () => {
  for (const heading of ['Job positions', 'Job requirement details', 'General employment details', 'Deadline and client confirmation']) assert.ok(form.includes(`title="${heading}"`), heading)
  for (const label of ['Job title of position ${index + 1}', 'No. of vacancies of position ${index + 1}', 'Salary of position ${index + 1}', 'Requirement field ${at + 1} of ${name}', 'Details of requirement ${at + 1} of ${name}', 'Remove position ${index + 1}']) assert.ok(form.includes(label), label)
  assert.match(form, /<Dialog open onClose=\{requestClose\} width="xl"/)
  assert.match(form, /Add position/)
  assert.match(form, /Add requirement/)
  assert.match(form, /Add particular/)
  assert.match(form, /Client confirmation \/ special conditions/)
  assert.match(form, /<DatePicker /)
  assert.match(form, /\{saving \? 'Saving\.\.\.' : 'Save'\}/)
})

test('the form is careful: unsaved changes are confirmed before closing, a nameless row is flagged on Save, focus follows a new row, Enter only moves on', () => {
  assert.match(form, /Discard what you changed\?/)
  assert.match(form, /const requestClose = \(\) => \{ if \(saving\) return; if \(dirty\) setAsking/)
  assert.match(form, /formProblems\(form\)/)
  assert.match(form, /setTried\(true\)/)
  assert.match(form, /role="alert"/)
  assert.match(form, /focusKey\.current = `\$\{created\.id\}-title`/)
  assert.match(form, /useLayoutEffect/)
  assert.match(form, /event\.key !== 'Enter'/)
  assert.match(form, /target\.dataset\.multiline/, 'only the long confirmation box takes a new line')
  assert.match(form, /className="jr-gone"/, 'the deleted-by-someone-else banner is kept')
})

test('the form is responsive and follows the palette: stacked labelled rows under 700px, tokens only', () => {
  assert.match(formStyles, /@media \(max-width: 699px\)/)
  assert.match(formStyles, /\.jrf-m \{ display: block/)
  assert.match(formStyles, /\.jrf-band \{[^}]*background: var\(--primary\)/)
  assert.doesNotMatch(formStyles, /#[0-9a-fA-F]{3,6}\b/, 'no hard-coded colours: dark mode follows the tokens')
  assert.match(formStyles, /font-size: 16px/, 'inputs are 16px on a phone so it does not zoom')
})
