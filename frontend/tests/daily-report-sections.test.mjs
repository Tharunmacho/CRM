import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { test } from 'node:test'
import ts from 'typescript'

/**
 * The real views rendered to static HTML with the fixture, then read back: the Daily report holds the client's seven
 * figures (and Day closed) and nothing else, Lead sheet monitoring holds only leads, Candidate monitoring only candidates
 * and tasks, and each table's rows have exactly as many cells as its heading row.
 */
const require = createRequire(import.meta.url)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
globalThis.React = React

/** Replace `import { a, type B } from './x'` by `const { a } = globalThis.__x` (type-only imports are dropped). */
function stubImports(source, globals) {
  return source.replace(/import\s+(type\s+)?(?:\{([\s\S]*?)\}|(\w+))\s+from\s+'([^']+)'\n?/g, (_all, typeOnly, names, _def, from) => {
    if (typeOnly) return ''
    const key = from.replace(/^.*\//, '')
    const list = (names ?? '').split(',').map(n => n.trim()).filter(n => n && !n.startsWith('type ')).map(n => n.replace(/\s+as\s+/, ': '))
    if (!list.length) return ''
    return `const { ${list.join(', ')} } = ${globals[key] ?? '{}'}\n`
  })
}
const compile = async (path, globals = {}) => {
  const raw = await readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
  const out = ts.transpileModule(stubImports(raw, globals), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(out).toString('base64')}`)
}
const h = React.createElement
const view = await compile('features/daily-report/daily-report-view.ts')
globalThis.__view = view
globalThis.__react = { Fragment: React.Fragment }
globalThis.__cn = { cn: (...a) => a.flat().filter(Boolean).join(' ') }
globalThis.__format = { initials: name => view.initialsFor(name) }
globalThis.__lucide = new Proxy({}, { get: () => () => null })
globalThis.__display = { Badge: ({ children, tone }) => h('span', { className: 'ui-badge', 'data-tone': tone }, children) }
globalThis.__primitives = { Select: ({ children, ...rest }) => h('select', rest, children) }
globalThis.__parts = { useChangedCells: () => new Set() }
globalThis.__dailyTable = {
  ScrollEdges: ({ children }) => h('div', { className: 'dw-scroll' }, children),
  columnLayout: cols => { const total = cols.reduce((s, c) => s + c.width, 0); return { minWidth: total, share: c => `${((c.width / total) * 100).toFixed(3)}%` } },
}
globalThis.__strip = await compile('screens/tasks/daily-stat-strip.tsx', { cn: 'globalThis.__cn', 'lucide-react': 'globalThis.__lucide' })
const G = { 'daily-report-view': 'globalThis.__view', react: 'globalThis.__react', cn: 'globalThis.__cn', format: 'globalThis.__format', 'lucide-react': 'globalThis.__lucide', 'report-parts': 'globalThis.__parts', 'daily-table': 'globalThis.__dailyTable' }
globalThis.__table = await compile('features/daily-report/report-table.tsx', G)
globalThis.__sections = await compile('features/daily-report/report-sections.tsx', G)
// The attendance panel is another file's job (it reads the server): a recognisable stand-in that records what it was given.
globalThis.__attendance = { AttendancePanel: props => h('aside', { 'data-attendance': JSON.stringify(props, (key, value) => (typeof value === 'function' ? 'fn' : value)) }) }
globalThis.__people = await compile('features/daily-report/report-people.tsx', G)
const views = await compile('features/daily-report/report-views.tsx', { ...G, display: 'globalThis.__display', primitives: 'globalThis.__primitives', 'daily-stat-strip': 'globalThis.__strip', 'report-table': 'globalThis.__table', 'report-people': 'globalThis.__people', 'report-sections': 'globalThis.__sections', 'report-attendance': 'globalThis.__attendance' })

const fixture = JSON.parse(await readFile(new URL('./fixtures/daily-work-report.json', import.meta.url), 'utf8'))
const report = fixture.team
const noop = () => {}

function render(View, overrides = {}) {
  const base = overrides.report ?? report
  const props = { report: base, roster: base.staff, today: '2026-11-17', timeZone: 'Asia/Kolkata', scope: 't', staff: '', onPickStaff: noop, onDrill: noop, ...overrides }
  return renderToStaticMarkup(h(View, props))
}
const count = (html, pattern) => (html.match(pattern) || []).length
const text = html => html.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim()
const tileLabels = html => [...html.matchAll(/class="dwb-stat-label">([^<]*)</g)].map(m => m[1])

/** The server's thirteen-stage answer: candidates now at each stage (0 = not on an order yet) and, next to it, the period's flow per stage. */
const NAMES = ['Prescreening', 'Forwarded', 'Interview scheduling', 'Interview result', 'Offer letter', 'Advance payment', 'Passport', 'Medical', 'PCC', 'Visa arrival', 'Google review', 'Visa stamping', 'Air ticket']
const NOW = [5, 15, 9, 5, 3, 1, 0, 2, 0, 1, 0, 0, 4, 0] // stage 0, then 1 to 13
const withStages = () => ({
  ...report,
  stages: { totals: NOW.map((count, no) => ({ no, key: no === 0 ? 'none' : `stage${no}`, label: no === 0 ? 'Not on an order yet' : NAMES[no - 1], count })), perStaff: [] },
  stageFlow: NAMES.map((label, i) => ({ no: i + 1, key: `stage${i + 1}`, label, completed: i === 0 ? 11 : i === 1 ? 7 : 0, stopped: i === 1 ? 2 : 0 })),
})
/** The section's table: the heading cells, each body row as [name, ...figures], and the totals row. */
function sectionTable(html, label) {
  const start = html.indexOf(`aria-label="${label}"`)
  const section = html.slice(start, html.indexOf('</section>', start))
  const cells = row => [...row.matchAll(/<(?:th|td)\b[^>]*>([\s\S]*?)<\/(?:th|td)>/g)].map(m => text(m[1]))
  const head = cells(section.slice(section.indexOf('<thead>'), section.indexOf('</thead>')))
  const rows = section.slice(section.indexOf('<tbody>'), section.indexOf('</tbody>')).split('<tr ').slice(1).map(cells)
  const foot = cells(section.slice(section.indexOf('<tfoot>'), section.indexOf('</tfoot>')))
  return { section, head, rows, foot }
}
const withoutSections = html => html.replace(/<section[^>]*aria-label="(?:Task progress by stage|Lead sheet by status)"[\s\S]*?<\/section>/g, '')
const teamNames = html => {
  const start = html.indexOf('class="dw-panel dwb-board"')
  return [...html.slice(start, html.indexOf('</section>', start)).matchAll(/<b title="([^"]+)">/g)].map(m => m[1])
}

test('picking one recruiter: only their Team card, the heading is "<Name>\'s figures", only their row; the filter still lists everyone and All recruiters brings the team back', () => {
  const meera = report.staff[0]
  const html = render(views.DailyView, { report: { ...report, staff: [meera] }, roster: report.staff, staff: meera.id })
  assert.deepEqual(teamNames(html), ['Meera Nair'], 'the Team board is the one person')
  const board = html.slice(html.indexOf('class="dw-panel dwb-board"'), html.indexOf('</section>', html.indexOf('class="dw-panel dwb-board"')))
  assert.match(board, /aria-pressed="true"/)
  assert.ok(!/%<\/em>/.test(board), 'a lone person has no share of the team')
  const t = text(html)
  assert.match(t, /Meera Nair's figures/)
  assert.ok(!/Everyone's figures/.test(t))
  assert.ok(html.includes('aria-label="Meera Nair&#x27;s figures"'))
  assert.equal(withoutSections(html).split('<tr class="dw-staff-row').length - 1, 1, 'one row in the people table')
  // the dropdown is built from the roster, so the others can still be chosen
  const select = html.slice(html.indexOf('<select'), html.indexOf('</select>'))
  assert.deepEqual([...select.matchAll(/<option[^>]*>([^<]*)</g)].map(m => m[1]), ['All recruiters', 'Anil Kumar', 'Meera Nair', 'Zoya Khan'])
  // All recruiters: the whole team and the original heading
  const all = render(views.DailyView, { staff: '' })
  assert.match(text(all), /Everyone's figures/)
  assert.equal(teamNames(all).length, 3)
  // the picked person is found in the roster when the answer has not caught up yet
  const stale = render(views.DailyView, { report: { ...report, staff: [] }, roster: report.staff, staff: report.staff[1].id })
  assert.match(text(stale), /Anil Kumar's figures/)
  assert.deepEqual(teamNames(stale), ['Anil Kumar'])
})

test('Task progress by stage: 13 numbered rows, the three figure columns and a share bar, a totals row, the first row only when candidates are not on an order', () => {
  const html = render(views.DailyView, { report: withStages() })
  const { head, rows, foot, section } = sectionTable(html, 'Task progress by stage')
  assert.deepEqual(head, ['Stage', 'Now at this stage', 'Completed in this period', 'Stopped here in this period', 'Share now'])
  assert.deepEqual(rows.map(row => row[0]), ['0 · Not on an order yet', ...NAMES.map((name, i) => `${i + 1} · ${name}`)])
  assert.equal(rows.length, 14)
  assert.deepEqual(rows[1], ['1 · Prescreening', '15', '11', '–', '33%']) // 15 of the 45 candidates now at a stage or not on an order
  assert.deepEqual(rows[2], ['2 · Forwarded', '9', '7', '2', '20%'])
  assert.deepEqual(rows[6], ['6 · Advance payment', '–', '–', '–', '–']) // nothing: a light dash, never a zero
  assert.deepEqual(foot, ['Total', '45', '18', '2', '100%']) // the rows added up
  assert.equal(count(section, /class="dwb-meter"/g), 10) // a slim bar on the nine rows with candidates, and on the total
  // a number with candidates behind it is a button; the others are plain
  assert.ok(/<button type="button" class="dwr-link"[^>]*>15</.test(section))
  assert.ok(!/(completed|stopped) at [^"]*. Show the list/.test(section), 'completed and stopped have no list to open')
  assert.equal(count(section, /<button type="button" class="dwr-link"/g), 9, 'a button on each of the nine "now" figures with candidates')
  // the same table style as the report tables
  assert.match(section, /<table class="dw-table dw-main dwr-table"/)
  assert.match(section, /<th scope="col" class="is-left dwr-name-head">Stage</)
  assert.equal(count(section, /<th scope="col" class="is-num"/g), 4)
  assert.equal(count(section, /<td data-label="[^"]+" class="is-num">/g), 14 * 4 + 4)
  assert.ok(section.includes('dw-staff-row is-alt'))
  assert.ok(section.includes('class="dw-scroll"')) // the Tasks scroll frame (ScrollEdges)
  // no candidates outside an order: no "0 ·" row, 13 rows
  const none = withStages(); none.stages.totals[0].count = 0
  assert.equal(sectionTable(render(views.DailyView, { report: none }), 'Task progress by stage').rows.length, 13)
})

test('Task progress by stage: a number that has data opens the stage list; an older server without stageFlow still shows the table, with dashes in those two columns', () => {
  const html = render(views.DailyView, { report: withStages() })
  assert.ok(html.includes('aria-label="15 now at 1 · Prescreening. Show the list"'))
  assert.ok(!html.includes('completed at 1 · Prescreening. Show the list'))
  const old = withStages(); delete old.stageFlow
  const { rows, foot } = sectionTable(render(views.DailyView, { report: old }), 'Task progress by stage')
  assert.equal(rows.length, 14)
  assert.deepEqual(rows[1], ['1 · Prescreening', '15', '–', '–', '33%'])
  assert.deepEqual(foot, ['Total', '45', '–', '–', '100%'])
  // the oldest server: no stages either; still a table (13 rows), all dashes, no error
  const oldest = withStages(); delete oldest.stageFlow; delete oldest.stages
  const bare = sectionTable(render(views.DailyView, { report: oldest }), 'Task progress by stage')
  assert.equal(bare.rows.length, 13)
  assert.deepEqual(bare.rows[0], ['1 · Prescreening', '–', '–', '–', '–'])
  assert.deepEqual(bare.foot, ['Total', '–', '–', '–', '–'])
})

test('the Daily report order: Team, Task progress by stage, the people table, By client, Lead sheet by status, Attendance last', () => {
  const html = render(views.DailyView, { report: withStages(), onDrillClient: noop })
  const order = ['aria-label="Team"', 'aria-label="Task progress by stage"', 'aria-label="Everyone&#x27;s figures"', 'aria-label="By client"', 'aria-label="Lead sheet by status"', 'data-attendance=']
  const at = order.map(piece => html.indexOf(piece))
  assert.ok(at.every(index => index > 0), order.filter((_, i) => at[i] < 0).join(', '))
  assert.deepEqual([...at].sort((a, b) => a - b), at)
  assert.ok(html.indexOf('class="dwb-stats') < at[0], 'the tiles come first')
})

test('Lead sheet by status: one row per status with Leads and Share %, a total row, centred figures in the report table style', () => {
  const html = render(views.DailyView)
  const { head, rows, foot, section } = sectionTable(html, 'Lead sheet by status')
  assert.deepEqual(head, ['Status', 'Leads', 'Share %'])
  assert.equal(rows.length, 9) // all nine statuses of the fixture, zeros included
  assert.deepEqual(rows[0], ['Not contacted', '70', '47%'])
  assert.deepEqual(rows[5], ['Converted', '10', '7%'])
  assert.deepEqual(foot, ['Total', '150', '100%'])
  assert.match(section, /<table class="dw-table dw-main dwr-table"/)
  assert.match(section, /<th scope="col" class="is-num"[^>]*>Leads</)
  assert.match(section, /<td data-label="Leads" class="is-num">/)
  // the recruiter filter narrows it: the server answers with that person's leads and so does the table
  const narrowed = JSON.parse(JSON.stringify(report))
  narrowed.leadSheet.byStatus = [{ key: 'new', label: 'Not contacted', count: 3 }, { key: 'interested', label: 'Interested', count: 1 }]
  narrowed.leadSheet.totalLeads = 4
  const small = sectionTable(render(views.DailyView, { report: narrowed, staff: report.staff[0].id }), 'Lead sheet by status')
  assert.deepEqual(small.rows, [['Not contacted', '3', '75%'], ['Interested', '1', '25%']])
  assert.deepEqual(small.foot, ['Total', '4', '100%'])
  // an older server with no lead sheet: one short line instead of the section
  const old = JSON.parse(JSON.stringify(report)); delete old.leadSheet
  const oldHtml = render(views.DailyView, { report: old })
  assert.ok(!oldHtml.includes('aria-label="Lead sheet by status"'))
  assert.match(text(oldHtml), /This part needs the latest server version\./)
})

test('the attendance panel is last and gets the single day of the period (never after today; Total and ranges use today), the person filter and the team flag', () => {
  const props = html => {
    const start = html.indexOf('data-attendance="') + 17
    return JSON.parse(html.slice(start, html.indexOf('"', start)).replace(/&quot;/g, '"'))
  }
  assert.deepEqual(props(render(views.DailyView)), { day: '2026-11-17', staff: '', team: true, timeZone: 'Asia/Kolkata', onPickStaff: 'fn' })
  const yesterday = { ...report, period: { ...report.period, from: '2026-11-16', to: '2026-11-16' } }
  assert.equal(props(render(views.DailyView, { report: yesterday })).day, '2026-11-16')
  const range = { ...report, period: { ...report.period, from: '2026-11-10', to: '2026-11-16' } }
  assert.equal(props(render(views.DailyView, { report: range })).day, '2026-11-17') // a range: today
  assert.equal(props(render(views.DailyView, { report: yesterday, allTime: true })).day, '2026-11-17') // Total: today
  const future = { ...report, period: { ...report.period, from: '2026-11-20', to: '2026-11-20' } }
  assert.equal(props(render(views.DailyView, { report: future })).day, '2026-11-17') // never after today
  assert.equal(props(render(views.DailyView, { staff: 'u-anil' })).staff, 'u-anil')
  const own = { ...report, scope: 'self', compliance: null, staff: [report.staff[0]] }
  assert.equal(props(render(views.DailyView, { report: own })).team, false)
  const html = render(views.DailyView)
  assert.ok(html.indexOf('data-attendance=') > html.indexOf('aria-label="Lead sheet by status"'))
})

// ------------------------------------------------------------------------------ the source and the style sheets

const read = path => readFile(new URL('../src/' + path, import.meta.url), 'utf8')

test('the source: the roster only fills the recruiter filter; the Team board, the table and the heading follow the picked person', async () => {
  const src = await read('features/daily-report/report-views.tsx')
  const daily = src.slice(src.indexOf('export function DailyView'), src.indexOf('/* ---------------------------------------------------------------- Lead sheet monitoring'))
  assert.match(daily, /<PeopleBoard staff=\{shown\}/)
  assert.ok(!/<PeopleBoard staff=\{roster\}/.test(daily), 'the Team board is no longer built from the roster')
  assert.match(daily, /figureGroups\(shown, /)
  assert.match(daily, /<PanelHead title=\{figuresTitle\} staff=\{staff\} roster=\{roster\}/)
  assert.ok(daily.includes("'s figures`") && daily.includes("Everyone's figures") && daily.includes("'Your figures'"))
  // the order of the page
  const at = ['<Tiles', '<PeopleBoard', '<StageProgressPanel', '<FigureTable', 'aria-label="By client"', '<LeadStatusPanel', '<AttendancePanel'].map(piece => daily.indexOf(piece))
  assert.ok(at.every(index => index > 0), String(at))
  assert.deepEqual([...at].sort((a, b) => a - b), at)
  assert.match(daily, /<AttendancePanel day=\{attendanceDay\(report\.period, allTime, today\)\} staff=\{staff\} team=\{team\} timeZone=\{timeZone\} onPickStaff=\{onPickStaff\} \/>/)
  assert.match(src, /import \{ AttendancePanel \} from '\.\/report-attendance'/)
  const page = await read('features/daily-report/daily-report-page.tsx')
  assert.match(page, /const roster = everyone\.current\.length \? everyone\.current : data\?\.staff \?\? \[\]/) // everyone stays in the dropdown while one person is picked
  assert.match(page, /rosterRead = useDailyReport\(\{ \.\.\.query, staff: 'all', client: 'all' \}/) // opened on one person from a link: everyone is read once for the list
})

test('the section tables reuse the report table classes and the Tasks scroll frame', async () => {
  const src = await read('features/daily-report/report-sections.tsx')
  assert.match(src, /import \{ ScrollEdges \} from '@\/screens\/tasks\/daily-table'/)
  assert.match(src, /className="dw-table dw-main dwr-table"/)
  assert.match(src, /<th scope="col" className="is-left dwr-name-head">/)
  assert.match(src, /className="dw-panel dwr-stages"/)
  assert.match(src, /className="dw-panel dwr-leadstatus"/)
  assert.match(src, /'dw-staff-row', index % 2 === 1 && 'is-alt'/) // the stripes and the hover of report-tables.css
  assert.match(src, /data-label=\{col\.head\}/) // a labelled card where the table does not fit
})

test('the report strip: the label above its figure, both centred in the card, only inside the Reports page (.dwr), the Tasks strip is untouched', async () => {
  const css = await read('styles/stat-cards.css')
  const block = css.slice(css.indexOf('Reports strip'), css.indexOf('Report mini cards'))
  assert.match(block, /\.dwr \.dwb-stat \{[^}]*flex-direction: column; align-items: center;[^}]*text-align: center;/)
  assert.match(block, /\.dwr \.dwb-stat-text \{[^}]*align-items: center;[^}]*text-align: center;/)
  assert.match(block, /\.dwr \.dwb-stat-label \{ text-align: center; \}/)
  assert.match(block, /\.dwr \.dwb-stat-figure \{ justify-content: center;/)
  for (const line of block.split('\n').filter(text => text.startsWith('html:root'))) assert.match(line, /\.dwr \.dwb-stat/, 'every rule of the block is scoped to the Reports page')
  assert.doesNotMatch(css.slice(0, css.indexOf('Reports strip')), /\.dwr/)
})

test('the report tables: names and their heading left, figures and figure headings centred, the share bar centred, a bold Total', async () => {
  const css = await read('styles/report-tables.css')
  assert.match(css, /:is\(thead th\.is-left, th\.dwr-name, th\.dwr-name-head\) \{ padding-left: 16px; text-align: left; \}/)
  assert.match(css, /:is\(th, td\)\.is-num,[^{]*\{ padding: 8px 10px; text-align: center; \}/)
  assert.match(css, /td\.is-num \.dwr-pct \{ justify-content: center; width: 100%; \}/)
  assert.match(css, /td\.is-num \.dwr-pct\.is-share \.dwb-meter \{[^}]*max-width: 120px;/)
  assert.match(css, /tfoot \.dwr-total th\.dwr-name \{ font-weight: 700; \}/)
})
