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
/** The two section tables of the Daily report (stages, lead status) have their own tests: the figure-table helpers below leave them out. */
const withoutSections = html => html.replace(/<section[^>]*aria-label="(?:Task progress by stage|Lead sheet by status)"[\s\S]*?<\/section>/g, '')
const headings = html => [...withoutSections(html).slice(0, html.indexOf('Lead status now') < 0 ? html.length : html.indexOf('Lead status now')).matchAll(/<th scope="col"[^>]*>([^<]*)</g)].map(m => m[1])
/** The cells of every person row: the row heading plus its td cells. */
const personRows = html => withoutSections(html).split('<tr class="dw-staff-row').slice(1).map(row => row.split('</tr>')[0]).map(row => count(row, /<th scope="row"/g) + count(row, /<td\b/g))

test('Lead sheet monitoring: five tiles tied to the Lead sheet screen, a table with Unassigned and a Total row, headings only', () => {
  const html = render(views.LeadSheetView)
  assert.deepEqual(tileLabels(html), ['Total leads', 'New leads', 'Assigned', 'Contacted', 'Not contacted'])
  assert.equal(count(html, /class="dwb-stats has-5"/g), 1) // one row of five
  const t = text(html)
  assert.match(t, /Total leads 150 New leads 12 Assigned 20 Contacted 31 Not contacted 180/)
  assert.ok(html.includes('title="All the leads on the sheet, whatever the period"'))
  assert.ok(html.includes('title="Leads never contacted, whatever the period: the same number as the Lead sheet screen"')) // the all-time tiles say so
  assert.ok(html.includes('title="+2 vs yesterday"')) // new leads 12 against 10
  assert.ok(html.includes('title="+7 vs yesterday"')) // contacted 31 against 24
  assert.deepEqual(headings(html), ['Recruiter', 'Assigned', 'Contacted', 'Interested', 'Call back', 'Not interested', 'Wrong number', 'No response', 'Not contacted', 'Contacted %'])
  assert.deepEqual(personRows(html), [10, 10, 10, 10]) // three recruiters and Unassigned
  assert.match(t, /Meera Nair 8 20 4 5 2 1 6 55 33%/) // assigned today, contacted, ..., never contacted, contacted of the workload (20 of 60)
  assert.match(t, /Unassigned [–\s]+30/) // leads nobody owns: Assigned is a dash, Not contacted is theirs
  const foot = html.slice(html.indexOf('<tfoot>'), html.indexOf('</tfoot>'))
  assert.match(text(foot), /^Total 20 31/) // the Total row: assigned and contacted equal the tiles
  assert.match(text(foot), /180/) // and its Not contacted is the tile's 180
  assert.ok(foot.includes('font') || foot.includes('dwr-total')) // pinned, bold by CSS
  assert.ok(!/not contacted yet|not assigned|Leads called or updated|select a number|Where every lead/.test(t), 'no captions or subtitles')
  assert.match(t, /Lead status now Status Leads Share/)
  assert.equal(count(html, /<tr><th scope="row" class="dwr-status-name">/g), 9) // all nine statuses
  assert.ok(!/Stage|Overdue|Pending|Candidates|Prescreened|Forwarded|Day closed/.test(t), 'nothing about candidates, stages or the daily figures')
})

test('Lead sheet tiles open their lists with the period keys', async () => {
  const html = render(views.LeadSheetView)
  assert.equal(count(html, /class="dwb-stat is-action"/g), 5) // all five have something behind them
  const source = await readFile(new URL('../src/features/daily-report/report-views.tsx', import.meta.url), 'utf8')
  for (const key of ['leadsTotal', 'leadsNewInPeriod', 'leadsAssignedInPeriod', 'leadsContacted', 'leadsNotContacted', 'leadsBacklog']) assert.ok(source.includes(`onDrill('${key}', null)`), key)
})

test('an older server: the lead tiles go back to the four-tile set', () => {
  const old = JSON.parse(JSON.stringify(report))
  for (const key of ['neverContacted']) delete old.leadSheet[key]
  for (const row of old.staff) delete row.leads.neverContacted
  const html = render(views.LeadSheetView, { report: old })
  assert.deepEqual(tileLabels(html), ['New leads', 'Assigned', 'Contacted', 'Still to contact']) // period-aware but no never-contacted figure
  for (const key of ['newInPeriod', 'assignedInPeriod', 'notContactedInPeriod', 'backlog', 'previous']) delete old.leadSheet[key]
  old.leadSheet.unassigned = 30
  for (const row of old.staff) { delete row.leads.assignedInPeriod; delete row.leads.notContactedInPeriod }
  const oldest = render(views.LeadSheetView, { report: old })
  assert.deepEqual(tileLabels(oldest), ['Total leads on the sheet', 'Assigned', 'Contacted in this period', 'Contacted of assigned'])
  assert.match(text(oldest), /Total leads on the sheet 150 Assigned 120/)
  assert.ok(!oldest.includes('<tfoot>')) // no Total row without the period figures
})

test('every table uses one column template: header and row cells line up, and numeric columns are right-aligned in both', () => {
  const tables = {
    lead: render(views.LeadSheetView),
  }
  for (const [name, html] of Object.entries(tables)) {
    for (const table of html.split('<table').slice(1).filter(piece => piece.includes('dwr-table'))) {
      const head = table.slice(table.indexOf('<thead>'), table.indexOf('</thead>'))
      const headCells = [...head.matchAll(/<th scope="col"( class="([^"]*)")?/g)].map(m => m[2] ?? '')
      const cols = (table.match(/<col /g) || []).length
      assert.equal(headCells.length, cols, name + ': one <col> per heading')
      const rows = table.split('<tr class="dw-staff-row').slice(1).map(row => row.split('</tr>')[0])
      assert.ok(rows.length > 0, name)
      for (const row of rows) {
        const cells = [...row.matchAll(/<(?:th scope="row"|td)( [^>]*?)?>/g)].map(m => m[0])
        assert.equal(cells.length, headCells.length, name + ': a row has as many cells as the heading row')
        cells.forEach((cell, i) => {
          if (i === 0) return
          // the heading and the cell of a column carry the same alignment class
          const numeric = /class="[^"]*\bis-num\b/.test(cell)
          assert.equal(numeric, /\bis-num\b/.test(headCells[i]), name + ' column ' + i)
        })
      }
    }
  }
})

test('headings are the Tasks look: the primary blue band, bold uppercase 11.5px, one line; group bands bold', async () => {
  const css = await readFile(new URL('../src/styles/daily-report.css', import.meta.url), 'utf8')
  const rule = /:root \.dwr \.dwr-table\.dw-table\.dw-main thead th \{([^}]*)\}/.exec(css)
  assert.ok(rule, 'no heading rule')
  assert.match(rule[1], /height: 44px/)
  assert.match(rule[1], /background: var\(--primary\); color: var\(--on-primary\)/)
  assert.match(rule[1], /font-size: 11\.5px; font-weight: 700/)
  assert.match(rule[1], /text-transform: uppercase/)
  assert.match(rule[1], /white-space: normal/) // headings wrap at spaces (never cut), 44px at least
  assert.match(rule[1], /text-align: var\(--table-align, center\)/)
  const weightOf = selector => {
    const found = new RegExp('(?:^|\\n)' + selector.replace(/[.*+?^$\{}()|[\]\\]/g, '\\$&') + ' \\{([^}]*)\\}').exec(css)
    assert.ok(found, 'no rule for ' + selector)
    return { weight: Number(/font-weight: (\d+)/.exec(found[1])?.[1] ?? 0), colour: /color: ([^;]+);/.exec(found[1])?.[1] }
  }
  for (const selector of ['.dwr .dw-group th, .dwr .dw-group-title', '.dr-drill .dr-drill-table thead th, .dr-drill .dr-mini thead th']) {
    assert.ok(weightOf(selector).weight >= 600, selector)
  }
  assert.equal(weightOf('.dr-drill .dr-drill-table thead th, .dr-drill .dr-mini thead th').colour, 'var(--text-main)')
})

test('without the additive keys the views say so in one short sentence', () => {
  const old = JSON.parse(JSON.stringify(report))
  delete old.leadSheet; delete old.tasks; delete old.stages
  for (const row of old.staff) { delete row.leads; delete row.tasks }
  assert.match(text(render(views.LeadSheetView, { report: old })), /This part needs the latest server version\./)
})

test('the page: titles, the Tasks-style header with no subtitle, tabs and period row, and the Tasks CSS reused', async () => {
  const page = await readFile(new URL('../src/features/daily-report/daily-report-page.tsx', import.meta.url), 'utf8')
  assert.match(page, /daily: 'Task report', lead: 'Lead sheet report'/)
  assert.ok(page.indexOf('<PageHeader') < page.indexOf('{tabs}') && page.indexOf('{tabs}') < page.indexOf('<PeriodRow'))
  assert.ok(!/subtitle|dwr-when|description=/.test(page), 'no subtitle under the title')
  assert.match(page, /Export Excel/)
  assert.match(page, /Refresh/)
  assert.match(page, /@\/styles\/daily-work\.css/)
  assert.match(page, /@\/styles\/daily-work-board\.css/)
  const views2 = await readFile(new URL('../src/features/daily-report/report-views.tsx', import.meta.url), 'utf8')
  assert.match(views2, /from '@\/screens\/tasks\/daily-stat-strip'/) // the Tasks summary strip itself
  assert.ok(!/<footer|dwr-foot|dwr-legend|<p>/.test(views2), 'no footers, legends or paragraphs of explanation')
  const table = await readFile(new URL('../src/features/daily-report/report-table.tsx', import.meta.url), 'utf8')
  assert.match(table, /from '@\/screens\/tasks\/daily-table'/) // the Tasks table's scroll edges and column layout
  assert.match(table, /dw-table dw-main/)
})

test('"staff" is "recruiter" in everything the person reads on the report screens', async () => {
  const read = name => readFile(new URL(`../src/features/daily-report/${name}`, import.meta.url), 'utf8')
  for (const name of ['report-views.tsx', 'report-people.tsx', 'report-table.tsx', 'daily-report-page.tsx', 'drilldown-drawer.tsx', 'submission-panels.tsx', 'my-day-card.tsx', 'reminder-banner.tsx', 'daily-report-toolbar.tsx']) {
    const source = await read(name)
    // string literals and JSX text that a person reads
    const literals = [...source.matchAll(/(?:'([^'\n]*)'|"([^"\n]*)"|>([^<>{}\n]+)<)/g)].map(m => m[1] ?? m[2] ?? m[3]).filter(Boolean)
    const offenders = literals.filter(s => /\bstaff\b/i.test(s) && !/^(?:\.\/|@\/|crm\.|(?:all|team|self|daily|lead|stages)$)/.test(s) && !/staff-row|dw-staff|\.staff|staff:|staffId|params|^staff$|View staff report|\/staff-report/.test(s))
    assert.deepEqual(offenders, [], name)
  }
  assert.ok((await read('daily-report-view.ts')).includes("'Recruiter'"))
  assert.ok(!/\bstaff\b/i.test(text(render(views.LeadSheetView))))
})

test('the colours are the app tokens: status pills only for status, no hard-coded hues', async () => {
  const css = await readFile(new URL('../src/styles/daily-report.css', import.meta.url), 'utf8')
  assert.doesNotMatch(css, /gradient|--chart-/)
  const hex = new Set((css.match(/#[0-9a-fA-F]{3,6}\b/g) || []).map(x => x.toLowerCase()))
  assert.deepEqual([...hex].sort(), ['#fff'])
  for (const name of ['report-table.tsx', 'report-people.tsx', 'report-views.tsx', 'report-parts.tsx', 'daily-report-page.tsx', 'daily-report-toolbar.tsx', 'my-day-card.tsx', 'drilldown-drawer.tsx']) {
    const source = await readFile(new URL(`../src/features/daily-report/${name}`, import.meta.url), 'utf8')
    assert.doesNotMatch(source, /#[0-9a-fA-F]{6}\b|rgb\(|hsl\(|text-(red|amber|green|orange|violet|rose|yellow)/, name)
  }
  assert.doesNotMatch(css, /overflow-y|max-height: calc/)
})

test('the panels: a report panel and a paged history list on the same drawer shell, a history call that degrades', async () => {
  const panels = await readFile(new URL('../src/features/daily-report/submission-panels.tsx', import.meta.url), 'utf8')
  assert.match(panels, /from '@\/shared\/ui\/overlay'/) // the drill-down's Sheet shell
  assert.match(panels, /changedSinceText\(row\)/)
  assert.match(panels, /dailyReportApi\.history\(/)
  assert.match(panels, /isOutdatedServer\(status\)/) // an older server: a quiet line, not a failure
  assert.match(panels, /This list needs the latest server version\./)
  const page = await readFile(new URL('../src/features/daily-report/daily-report-page.tsx', import.meta.url), 'utf8')
  assert.match(page, /client: drill\?\.client \?\? query\.client/) // a By client number drills with that client
  assert.match(page, /<SubmissionPanel/)
  assert.match(page, /<HistoryDrawer/)
  assert.ok(fixture.history.rows.length === 2 && fixture.history.rows[0].figures.forwarded === 5)
})
