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
globalThis.__people = await compile('features/daily-report/report-people.tsx', G)
const views = await compile('features/daily-report/report-views.tsx', { ...G, display: 'globalThis.__display', primitives: 'globalThis.__primitives', 'daily-stat-strip': 'globalThis.__strip', 'report-table': 'globalThis.__table', 'report-people': 'globalThis.__people' })

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
const headings = html => [...html.slice(0, html.indexOf('Lead status now') < 0 ? html.length : html.indexOf('Lead status now')).matchAll(/<th scope="col"[^>]*>([^<]*)</g)].map(m => m[1])
/** The cells of every person row: the row heading plus its td cells. */
const personRows = html => html.split('<tr class="dw-staff-row').slice(1).map(row => row.split('</tr>')[0]).map(row => count(row, /<th scope="row"/g) + count(row, /<td\b/g))

test('Daily report: the client wording as tiles, in the client order, then Day closed; nothing printed under a number', () => {
  const html = render(views.DailyView)
  assert.deepEqual(tileLabels(html), [
    'Resumes forwarded to clients', 'Resumes prescreened today', 'Workers shortlisted by clients', 'Online interviews conducted',
    'Offer letters received', 'Visas received', 'Workers travelled', 'Day closed',
  ])
  assert.equal(count(html, /class="dwb-stats has-4"/g), 2) // two rows of four, like the Tasks strip
  assert.equal(count(html, /class="dwb-stat-detail"/g), 0) // no caption line under any card
  const t = text(html)
  assert.match(t, /Day closed 1 of 3/) // the value text stays in the number line, with its progress bar
  assert.ok(html.includes('class="dwb-meter"'))
  assert.ok(!/vs yesterday|no change|of 5 interviews|staff have submitted|Not submitted yet —/.test(t.replace(/\bNot submitted\b/g, '')), 'the captions are not printed')
  assert.ok(!/Assigned|Overdue|Pending|Stage|Not contacted|Contacted|Call back/.test(t), 'nothing from the lead sheet, tasks or stages')
  // yesterday (a single day that is not today) says plain "prescreened"
  assert.ok(tileLabels(render(views.DailyView, { report: { ...report, period: { ...report.period, from: '2026-11-16', to: '2026-11-16' } } })).includes('Resumes prescreened'))
})

test('the information that is not printed is in each tile\'s hover text', () => {
  const html = render(views.DailyView)
  const tile = label => html.slice(html.lastIndexOf('<li>', html.indexOf(label)), html.indexOf(label))
  assert.match(tile('Resumes forwarded to clients'), /title="no change"/)
  assert.match(tile('Resumes prescreened today'), /title="−2 vs yesterday"/)
  assert.match(tile('Workers shortlisted by clients'), /title="\+4 vs yesterday"/)
  assert.match(tile('Online interviews conducted'), /title="of 5 interviews\. 2 interviews were recorded without a mode and are not counted as online\."/)
  assert.match(tile('Offer letters received'), /title="−1 vs yesterday"/)
  assert.match(tile('Visas received'), /title="\+1 vs yesterday"/)
  assert.match(tile('Day closed'), /title="1 of 3 recruiters submitted\. Not submitted yet — Anil Kumar, Zoya Khan"/)
})

test('Daily report table: short headings (full wording in the title), groups, avatars, drill buttons and pills', () => {
  const html = render(views.DailyView)
  assert.deepEqual(headings(html), ['Recruiter', 'Forwarded', 'Prescreened', 'Shortlisted', 'Online interviews', 'Offers', 'Visas', 'Travelled', 'Day closed'])
  assert.ok(html.includes('title="Resumes forwarded to clients. '))
  const t = text(html)
  assert.match(t, /Everyone's figures/)
  assert.ok(!/3 people|select a number to see/.test(t), 'no subtitle under the heading')
  assert.deepEqual([...html.matchAll(/class="dw-group-title">[\s\S]*?<\/span>/g)].map(m => text(`<span ${m[0]}`)), ['Active today 2', 'No activity 1'])
  assert.ok(html.includes('dw-group is-done'))
  assert.deepEqual(personRows(html), [9, 9, 9])
  assert.ok(/<button type="button" class="dwr-link"[^>]*>11</.test(html))
  assert.ok(!/<button[^>]*>0</.test(html)) // zeros are a light dash, never a button
  assert.equal(count(html, /data-tone="green"/g), 1) // Meera submitted
  assert.equal(count(html, /data-tone="amber"/g), 2) // Anil and Zoya have not
  assert.match(t, /Submitted 18:42/)
  assert.ok(!/<th scope="row"[^>]*>Total/.test(html)) // the tiles are the total: no Total row
})

test('no footer sentences: the day-close names, the interviews note and the legends are not printed; two small links sit in the card header', () => {
  const html = render(views.DailyView, { onHistory: noop })
  const t = text(html)
  assert.ok(!/Day close:|were recorded without a mode|Counted when a stage|not submitted yet/i.test(t.replace(/Not submitted/g, '')), 'no footer notes')
  assert.equal(count(html, /<footer/g), 0)
  const head = html.slice(html.indexOf('aria-label="Everyone&#x27;s figures"'), html.indexOf('<table'))
  assert.match(head, /Show the interviews/)
  assert.match(head, /Submitted reports/)
  assert.match(head, /class="dwr-textlink" title="2 interviews were recorded without a mode and are not counted as online\."/)
  assert.match(head, /Filter by recruiter/)
})

test('Team card: heading only, attention first, the Tasks status pills, a share bar, and "All recruiters"', () => {
  const html = render(views.DailyView)
  const board = html.slice(html.indexOf('class="dw-panel dwb-board"'), html.indexOf('</section>', html.indexOf('class="dw-panel dwb-board"')))
  const names = [...board.matchAll(/<b title="([^"]+)">/g)].map(m => m[1])
  assert.deepEqual(names, ['Anil Kumar', 'Zoya Khan', 'Meera Nair']) // not reported and active, then no activity, then reported
  assert.deepEqual([...board.matchAll(/class="dwb-chip is-[\w-]+">[\s\S]*?<\/span>/g)].map(m => text(`<span ${m[0]}`)), ['Not reported yet', 'No activity', 'Reported'])
  assert.ok(board.includes('dwb-card is-behind') && board.includes('dwb-card is-none') && board.includes('dwb-card is-on-track'))
  const t = text(board)
  assert.match(t, /12 actions today 32%/)
  assert.match(t, /25 actions today 68%/)
  assert.match(t, /Nothing recorded today/)
  assert.match(t, /All recruiters/)
  assert.ok(!/People who have not reported|Select one to see/.test(t), 'no subtitle')
})

test('a recruiter sees their own tiles, their own row, and no Team card', () => {
  const own = { ...report, scope: 'self', compliance: null, staff: [report.staff[0]] }
  const html = render(views.DailyView, { report: own })
  const t = text(html)
  assert.ok(!/Team|All recruiters/.test(t))
  assert.match(t, /Your figures/)
  assert.match(t, /Day closed Yes/)
  assert.ok(html.includes('title="Submitted 18:42"')) // the detail is the tile's hover text
  assert.ok(!/aria-pressed/.test(html)) // a name is not a filter for a recruiter
  assert.deepEqual(personRows(html), [9])
})

test('a period of several days has no Day closed column or tile', () => {
  const week = { ...report, period: { ...report.period, from: '2026-11-11', to: '2026-11-17' } }
  const html = render(views.DailyView, { report: week })
  assert.deepEqual(headings(html), ['Recruiter', 'Forwarded', 'Prescreened', 'Shortlisted', 'Online interviews', 'Offers', 'Visas', 'Travelled'])
  assert.ok(!tileLabels(html).includes('Day closed'))
  assert.deepEqual(personRows(html), [8, 8, 8])
  assert.ok(text(html).includes('Active in this period'))
})

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
  assert.equal(count(html, /<tr><th scope="row" class="is-left dwr-status-name">/g), 9) // all nine statuses
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
    daily: render(views.DailyView, { onDrillClient: noop }),
    lead: render(views.LeadSheetView),
    candidates: render(views.CandidateMonitoringView),
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

test('the Candidate table: the name column is the remainder, every figure column is as wide as its heading needs, all right-aligned', async () => {
  const html = render(views.CandidateMonitoringView)
  const table = html.slice(html.indexOf('<table'))
  assert.ok(!/min-width/.test(table.slice(0, table.indexOf('<thead>'))), 'no inline min-width: the table fits its panel')
  const widths = [...table.matchAll(/<col style="width:([\d.]+)%/g)].map(m => Number(m[1]))

  assert.equal(widths.length, 17) // Done, Pending, Overdue, No order, stages 1 to 12, Total; the Recruiter column has no width of its own (auto: it takes the rest)
  assert.ok(table.includes('<col style="width:auto"/>') || table.includes('<col style="width:auto" />'))
  const sum = widths.reduce((a, b) => a + b, 0)
  assert.ok(sum < 90 && sum > 55, 'the figure columns leave the Recruiter column at least 10% of the table (' + sum + ')')
  assert.ok(widths[3] > widths[4], 'No order (a longer heading) is wider than a stage number')
  assert.match(table, /<th scope="col" class="is-num">Total<\/th>/) // the Total heading is a figure heading: right-aligned like its numbers
  const css = await readFile(new URL('../src/styles/daily-report.css', import.meta.url), 'utf8')
  assert.match(css, /thead th\.is-num \{ padding: 0 10px 0 6px; text-align: right; \}/)
  assert.match(css, /td\.is-num \{ padding: 8px 10px 8px 6px; font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; text-align: right;/)
  assert.match(css, /\.dwr-table\.dw-table\.dw-main thead th \{[^}]*height: 44px;/)
  assert.match(css, /td\.is-num > \.dw-none[^{]*\{ display: block; margin-left: auto; text-align: right; \}/) // the dash is right-aligned like a number
})

test('headings are the Tasks look: the primary blue band, bold uppercase 11.5px, one line; group bands bold', async () => {
  const css = await readFile(new URL('../src/styles/daily-report.css', import.meta.url), 'utf8')
  const rule = /:root \.dwr \.dwr-table\.dw-table\.dw-main thead th \{([^}]*)\}/.exec(css)
  assert.ok(rule, 'no heading rule')
  assert.match(rule[1], /height: 44px/)
  assert.match(rule[1], /background: var\(--primary\); color: var\(--on-primary\)/)
  assert.match(rule[1], /font-size: 11\.5px; font-weight: 700/)
  assert.match(rule[1], /text-transform: uppercase/)
  assert.match(rule[1], /white-space: nowrap/)
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

test('Candidate monitoring: candidates and tasks only; the stage names are the header hover texts, not a legend line', () => {
  const html = render(views.CandidateMonitoringView)
  assert.deepEqual(tileLabels(html), ['Candidates in progress', 'Tasks done', 'Tasks pending', 'Tasks overdue'])
  assert.deepEqual(headings(html), ['Recruiter', 'Done', 'Pending', 'Overdue', 'No order', ...Array.from({ length: 12 }, (_, i) => String(i + 1)), 'Total'])
  assert.deepEqual(personRows(html), [18, 18, 18])
  const t = text(html)
  assert.match(t, /Candidates in progress 36/)
  assert.ok(!/Stages:|not on an order yet|1 Prescreening|select a number/.test(t), 'no legend line, no captions')
  assert.equal(count(html, /dwr-legend/g), 0)
  assert.ok(/<th scope="col" class="is-num" title="Stage 3: Interview scheduling\./.test(html)) // the stage header '3' carries the stage name
  assert.ok(/<th scope="col" class="is-num" title="Not on an order yet/.test(html))
  assert.ok(html.includes('title="Right now, whatever the period. 5 not on an order yet"'))
  assert.ok(!/Lead|Contacted|Interested|Assigned|Day closed/.test(t), 'nothing about leads')
  assert.ok(html.includes('dwr-table is-dense'))
})

test('without the additive keys the views say so in one short sentence', () => {
  const old = JSON.parse(JSON.stringify(report))
  delete old.leadSheet; delete old.tasks; delete old.stages
  for (const row of old.staff) { delete row.leads; delete row.tasks }
  assert.match(text(render(views.CandidateMonitoringView, { report: old })), /^This part needs the latest server version\.$/)
  assert.match(text(render(views.LeadSheetView, { report: old })), /This part needs the latest server version\./)
  assert.deepEqual(personRows(render(views.DailyView, { report: old })), [9, 9, 9]) // the Daily report needs none of them
})

test('the page: titles, the Tasks-style header with no subtitle, tabs and period row, and the Tasks CSS reused', async () => {
  const page = await readFile(new URL('../src/features/daily-report/daily-report-page.tsx', import.meta.url), 'utf8')
  assert.match(page, /daily: 'Report', lead: 'Lead sheet monitoring', stages: 'Candidate monitoring'/)
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
  assert.match(text(render(views.DailyView)), /All recruiters/)
  assert.ok(!/\bstaff\b/i.test(text(render(views.DailyView)) + text(render(views.LeadSheetView)) + text(render(views.CandidateMonitoringView))))
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

test('By client card: below Everyone\'s figures, the same table style, hidden while a client filter is on', () => {
  const html = render(views.DailyView, { onDrillClient: noop })
  const t = text(html)
  assert.match(t, /By client/)
  assert.ok(!/What was done for each client/.test(t), 'no subtitle')
  assert.ok(html.indexOf("aria-label=\"By client\"") > html.indexOf("aria-label=\"Everyone's figures\""))
  const card = html.slice(html.indexOf('aria-label="By client"'))
  assert.deepEqual([...card.matchAll(/<th scope="col"[^>]*>([^<]*)</g)].map(m => m[1]), ['Client', 'Forwarded', 'Prescreened', 'Shortlisted', 'Online interviews', 'Offers', 'Visas', 'Travelled', 'Total'])
  assert.match(text(card), /Al Noor Contracting[^G]*22/) // busiest first with its total
  assert.ok(text(card).indexOf('Al Noor') < text(card).indexOf('Gulf Build'))
  assert.ok(/<button type="button" class="dwr-link"[^>]*>10</.test(card)) // a number opens the drill-down
  assert.equal(count(card, /<tr class="dw-staff-row/g), 2)
  assert.ok(!html.includes('aria-label="By client"') === false)
  assert.ok(!render(views.DailyView, { onDrillClient: noop, clientFilter: 'co-1' }).includes('aria-label="By client"'))
  const none = { ...report }; delete none.byClient
  assert.ok(!render(views.DailyView, { report: none, onDrillClient: noop }).includes('aria-label="By client"'))
})

test('the Day closed pill opens the report, "changed" is flagged, and "Submitted reports" is a small link in the card header', () => {
  const html = render(views.DailyView, { onSubmission: noop, onHistory: noop })
  assert.equal(count(html, /class="dwr-pillbtn"/g), 1) // only the one who submitted
  assert.ok(/class="dwr-pillbtn"[^>]*aria-label="Meera Nair: Submitted 18:42\. Show their report"/.test(html))
  assert.equal(count(html, /class="dwr-changed"/g), 1)
  assert.match(html, /title="Numbers changed after this report was submitted \(now 6 forwarded, was 5\)"/)
  assert.match(text(html), /Submitted reports/)
  // without the handlers the pill is plain, as before
  assert.equal(count(render(views.DailyView), /class="dwr-pillbtn"/g), 0)
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
