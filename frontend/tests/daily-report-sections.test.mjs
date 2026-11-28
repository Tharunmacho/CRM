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
const NAMES = ['Prescreening', 'Forwarded', 'Interview scheduling', 'Interview result', 'Offer letter', 'Advance payment', 'Passport', 'Medical', 'PCC', 'Visa', 'Google review', 'Visa stamping', 'Air ticket']
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

// ------------------------------------------------------------------------------ the source and the style sheets

const read = path => readFile(new URL('../src/' + path, import.meta.url), 'utf8')

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
