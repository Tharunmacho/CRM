import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

/*
 * Table borders: ONE source of truth (theme.css) and every table on the site draws its lines from it.
 *   --table-line          the line under every row (the last one too), 1px; there are NO lines between columns in the body
 *   --table-line-strong   the outer frame (2px) and the header's bottom edge
 *   --table-header-line   the very faint column divider inside the primary-blue header band (the body has none)
 * The stylesheets are read as text; the real colours and widths were measured in the browser (see the task report).
 */
const root = fileURLToPath(new URL('../', import.meta.url))
const read = (relative) => readFile(path.join(root, 'src', relative), 'utf8').then((text) => text.replace(/\r\n/g, '\n'))

/** The rule (selector + block) that starts with `selector`, first match. */
const rule = (css, selector) => {
  const at = css.indexOf(selector)
  assert.ok(at >= 0, `${selector} exists`)
  return css.slice(at, css.indexOf('}', at))
}
/** Every one-line rule whose selector mentions a table part, with its text. */
const tableRules = (css, selectorPattern) => css.split('\n').filter((line) => {
  const brace = line.indexOf('{')
  return brace > 0 && selectorPattern.test(line.slice(0, brace))
})

const FAINT = /\b[0-9.]+px solid var\(--border-(?:main|faint|control)\)/
const HARD = /border(?:-(?:top|right|bottom|left))?: [0-9.]+px solid (?:#[0-9a-f]{3,8}\b|rgba?\()/i

test('theme.css defines the table line variables, in the light and the dark theme', async () => {
  const css = await read('styles/theme.css')
  const at = css.indexOf('Table lines')
  assert.ok(at >= 0, 'the table lines block exists')
  const block = css.slice(at)
  const light = block.slice(block.indexOf(':root {'), block.indexOf('[data-theme="dark"] {'))
  const dark = block.slice(block.indexOf('[data-theme="dark"] {'))
  assert.match(light, /--table-line-width: 1px;/)
  assert.match(light, /--table-frame-width: 2px;/)
  assert.match(light, /--table-line: color-mix\(in srgb, var\(--text-main\) (?:30|31|32|33|34|35)%, var\(--bg-card\)\);/, 'a clearly visible neutral, about a third of the text colour')
  assert.match(light, /--table-line-strong: color-mix\(in srgb, var\(--text-main\) (?:4\d|50)%, var\(--bg-card\)\);/, 'the frame is stronger than the inside lines')
  assert.match(light, /--table-header-line: color-mix\(in srgb, var\(--on-primary\) (?:1\d|2\d)%, var\(--primary\)\);/, 'a very faint divider in the blue header')
  assert.match(dark, /--table-line: color-mix\(in srgb, var\(--text-main\) \d+%, var\(--bg-card\)\);/)
  assert.match(dark, /--table-line-strong: color-mix\(in srgb, var\(--text-main\) \d+%, var\(--bg-card\)\);/)
  assert.match(dark, /--table-header-line: color-mix\(in srgb, var\(--on-primary\) (?:1\d|2\d)%, var\(--primary\)\);/)
  const pct = (text, name) => Number(text.match(new RegExp(`${name}: color-mix\\(in srgb, var\\(--text-main\\) (\\d+)%`))[1])
  assert.ok(pct(light, '--table-line-strong') > pct(light, '--table-line'), 'light: strong is stronger')
  assert.ok(pct(dark, '--table-line-strong') > pct(dark, '--table-line'), 'dark: strong is stronger')
})

test('the shared DataTable: a 2px strong frame around the box, a 1px line under every row (the last too), no column lines in the body', async () => {
  const css = await read('styles/data-table.css')
  const frame = rule(css, ':root:root .ui-data-table:is(.is-grid, .is-plain) {')
  assert.match(frame, /border: var\(--table-frame-width\) solid var\(--table-line-strong\)/)
  assert.match(frame, /overflow: hidden/)
  assert.match(frame, /border-radius: var\(--radius-xl, 10px\)/, 'the radius is kept')
  const cell = rule(css, ':root:root .ui-data-table.is-grid tbody td {')
  assert.match(cell, /border-bottom: var\(--table-line-width\) solid var\(--table-line\)/)
  assert.doesNotMatch(cell, /border-(right|left)/, 'no vertical line between body cells')
  assert.doesNotMatch(css, /tbody td:last-child \{ border-right/, 'no column-line rule to switch off on the last cell')
  const head = rule(css, ':root:root .ui-data-table.is-grid thead th {')
  assert.match(head, /border-bottom: var\(--table-frame-width\) solid var\(--table-line-strong\)/, 'the header bottom edge')
  assert.match(css, /thead th:not\(:last-child\) \{ border-right: var\(--table-line-width\) solid var\(--table-header-line\); \}/, 'the lighter divider in the blue header')
  // the compact / dense variants keep the same lines (they only change padding and font size)
  assert.doesNotMatch(rule(css, ':root:root .ui-data-table.is-grid.is-compact tbody td, '), /border/)
})

test('the shared DataTable stacked cards: no frame, no column lines, one var(--table-line) card border', async () => {
  const css = await read('styles/data-table.css')
  for (const n of [768, 1024, 1280]) {
    assert.match(css, new RegExp(`\\.ui-data-table\\.ui-table-stack-${n}:is\\(\\.is-grid, \\.is-plain\\) \\{ overflow: visible; border: 0; border-radius: 0; \\}`), `stack-${n} drops the frame`)
    assert.match(css, new RegExp(`ui-table-stack-${n} tbody td \\{ height: auto; padding: 0; border: 0;`), `stack-${n} cells have no column lines`)
    assert.match(css, new RegExp(`ui-table-stack-${n} tbody tr \\{ height: auto; background: var\\(--bg-card\\); border-bottom: var\\(--table-line-width\\) solid var\\(--table-line\\); \\}`))
  }
  // a panel around a table gives up its border only while the table is a table; as cards it keeps one border in the same colour
  assert.match(css, /\.ui-panel:not\(\.p-5\):has\(> \.ui-data-table:is\(\.is-grid, \.is-plain\)\)[^{]*\{ border-color: var\(--table-line\); \}/)
  assert.match(css, /\.ui-data-table:is\(\.is-grid, \.is-plain\):not\(\[class\*='ui-table-stack-'\]\)\)[^{]*\{ border-color: transparent; \}/)
})

test('table-default.css: the catch-all tables get the frame and the row line from the tokens, and no column lines', async () => {
  const css = await read('styles/table-default.css')
  assert.match(css, /table:where\(:not\([^\n]*\.db-scroll table[^\n]*\)\) \{\n\s+box-sizing: border-box; border-collapse: separate; border-spacing: 0; border: var\(--table-frame-width\) solid var\(--table-line-strong\); border-radius: var\(--radius-xl, 10px\); overflow: hidden;/)
  const head = rule(css, 'table:where(:not(.ui-data-table.is-grid table)) thead th {')
  assert.match(head, /border-bottom: var\(--table-frame-width\) solid var\(--table-line-strong\)/)
  assert.match(css, /thead th:not\(:last-child\) \{ border-right: var\(--table-line-width\) solid var\(--table-header-line\); \}/)
  assert.match(css, /tbody td:where\(:not\(\[data-label\]\)\),[\s\S]*?border-bottom: var\(--table-line-width\) solid var\(--table-line\);/)
  assert.doesNotMatch(css, /tbody[^{]*\{[^}]*border-right/, 'no vertical line between body cells, with or without a card label')
  // a table with card labels only has the frame and the column lines from 1280px (below that it is stacked cards)
  assert.match(css, /@media \(min-width: 1280px\) \{\n\s+:root:root :is\(\.workspace, \.workspace-dialog, \.workspace-sheet\) table:where\(:not\([^)]*\)\):has\(td\[data-label\]\) \{/)
  assert.doesNotMatch(css, FAINT)
})

test('table-theme.css (.tb-blue): the same lines as the shared DataTable', async () => {
  const css = await read('styles/table-theme.css')
  assert.match(css, /thead th \{[^}]*border-bottom: var\(--table-frame-width\) solid var\(--table-line-strong\)/)
  assert.match(css, /thead th:not\(:last-child\) \{ border-right: var\(--table-line-width\) solid var\(--table-header-line\); \}/)
  assert.match(css, /tbody td \{ height: 56px; padding: 8px 14px; vertical-align: middle; text-align: left; border-bottom: var\(--table-line-width\) solid var\(--table-line\); \}/, 'a line under every row only')
  assert.doesNotMatch(css, /tbody td[^{]*\{[^}]*border-right/, 'no vertical line between body cells')
  assert.doesNotMatch(css, FAINT)
})

test('Tasks: the table is framed (on its scroll box, a thin square frame), every row has the 1px line, no column lines, the header edge is strong', async () => {
  const work = await read('styles/daily-work.css')
  const frame = rule(work, '  .dw .tk-table-wrap {')
  assert.match(frame, /border: var\(--table-frame-width\) solid var\(--table-line-strong\)/)
  assert.match(frame, /border-radius: 0/)
  assert.match(work, /@container dw \(min-width: 1080px\) \{\n\s+\.dw \.tk-table-wrap \{/, 'only where it is a table (a panel of 1080px or more), under that the rows are cards')
  assert.match(work, /\.dw-table thead th \{[^}]*border-bottom: var\(--table-frame-width\) solid var\(--table-line-strong\)/)
  const tasks = await read('styles/tasks-table.css')
  const P = ':root .dw .tw-table.dw-table.dw-main'
  const body = rule(tasks, `  ${P} tbody td {`)
  assert.doesNotMatch(body, /border-(right|left)/, 'no vertical line between body cells')
  assert.match(body, /border-bottom: var\(--table-line-width\) solid var\(--table-line\)/)
  assert.match(rule(tasks, `  ${P} thead th {`), /border-bottom: var\(--table-frame-width\) solid var\(--table-line-strong\)/)
  assert.match(tasks, /thead th:not\(:last-child\) \{ border-right: var\(--table-line-width\) solid var\(--table-header-line\); \}/)
  assert.doesNotMatch(tasks, /border-bottom: 1px solid var\(--border-control\)/)
  // the panel around the framed table keeps one border in the same colour as cards; from 1360px it has no chrome at all, so the heading, the cards and the frame share the page's edges
  assert.match(work, /\.dw-panel:has\(> \.dw-scroll, > \.tk-table-wrap\) \{ border: var\(--table-line-width\) solid var\(--table-line\); border-radius: 0; \}/)
  assert.match(work, /@media \(min-width: 1360px\) \{\n\s+\.dw-panel:is\(\.dwb-board, \.dwr-myday, :has\(> \.dw-scroll, > \.tk-table-wrap\)\) \{ overflow: visible; border-width: 0; border-radius: 0; background: transparent; \}/)
  // the Reports people tables (.dwr) share .dw-table: their row lines are the token, and they have no column lines in the body either
  assert.match(work, /\.dw-table td \{ border-bottom: var\(--table-line-width\) solid var\(--table-line\); \}/)
  assert.doesNotMatch(work, /\.dw-table td \+ td/, 'no column line between body cells')
  assert.match(work, /\.dw-table tbody > tr:last-child > td \{ border-bottom: var\(--table-line-width\) solid var\(--table-line\); \}/)
})

test('Lead sheet, Data Bank and Matched candidates: the scroll box is framed, rows have the line, columns have none', async () => {
  const base = await read('styles/data-bank-v2.css')
  assert.match(base, /\.db-scroll:has\(> \.db-tbl\.is-grid\) \{ box-sizing: border-box; border: var\(--table-frame-width\) solid var\(--table-line-strong\); border-radius: var\(--radius-xl, 10px\); \}/)
  assert.match(base, /\.db-panel:has\(> \.db-box \.db-scroll > \.db-tbl\.is-grid\)[^{]*\{ border-color: transparent; \}/, 'one frame, never two')
  const leads = await read('styles/leads-table.css')
  const head = rule(leads, ':root:root .ld .db-tbl.is-grid.is-fit thead th {')
  assert.match(head, /border-bottom: var\(--table-frame-width\) solid var\(--table-line-strong\)/)
  assert.match(head, /border-right: var\(--table-line-width\) solid var\(--table-header-line\)/)
  const lbody = rule(leads, ':root:root .ld .db-tbl.is-grid.is-fit tbody td {')
  assert.match(lbody, /border-right: 0; border-bottom: var\(--table-line-width\) solid var\(--table-line\)/, 'a line under every row, no column line')
  assert.doesNotMatch(leads, FAINT)
  const cands = await read('styles/candidate-tables.css')
  assert.match(rule(cands, ':root:root:root .db-tbl.is-grid:not(.ld .db-tbl) thead th {'), /border-bottom: var\(--table-frame-width\) solid var\(--table-line-strong\)/)
  assert.match(cands, /thead th:not\(:last-child\) \{ border-right: var\(--table-line-width\) solid var\(--table-header-line\); \}/)
  const cbody = rule(cands, ':root:root:root .db-tbl.is-grid:not(.ld .db-tbl) tbody tr:not(.db-xrow) > td {')
  assert.match(cbody, /border: 0; border-bottom: var\(--table-line-width\) solid var\(--table-line\);/)
  assert.doesNotMatch(cbody, /border-(right|left)/)
  assert.doesNotMatch(cands, FAINT)
  const bank = await read('styles/data-bank-table.css')
  assert.match(bank, /border-right: var\(--table-line-width\) solid var\(--table-header-line\); border-bottom: var\(--table-frame-width\) solid var\(--table-line-strong\);/)
  assert.match(bank, /background: var\(--bg-card\); border-right: 0; border-bottom: var\(--table-line-width\) solid var\(--table-line\);/, 'Data Bank body: row line only')
  assert.match(base, /background: var\(--bg-card\); border-bottom: var\(--table-line-width\) solid var\(--table-line\); border-right: 0;/, 'Matched candidates body: row line only')
})

test('Agencies, Currencies, Jobs, Clients: the DataTable box carries the frame, their row rules use the tokens', async () => {
  const clients = await read('styles/clients-simple.css')
  assert.match(clients, /\.cs-agency-table \.ui-data-table\.is-grid thead th:not\(:last-child\) \{ border-right: var\(--table-line-width\) solid var\(--table-header-line\); \}/)
  assert.doesNotMatch(rule(clients, ':root:root .clients-simple .cs-agency-table .ui-data-table.is-grid thead th {'), /border-color: var\(--primary\)/, 'the header must not repaint the strong bottom edge blue')
  for (const file of ['clients-simple.css', 'agency-settings.css', 'jobs-simple.css']) {
    const css = await read(`styles/${file}`)
    assert.match(css, /tbody td \{[^}]*border-bottom: var\(--table-line-width\) solid var\(--table-line\);/, file)
    assert.match(css, /tbody tr:last-child > td \{ border-bottom: var\(--table-line-width\) solid var\(--table-line\); \}/, file)
    assert.doesNotMatch(tableRules(css, /\b(?:td|th|tr|tbody|thead)\b|-table|\.ui-data-table/).join('\n'), FAINT, `${file} keeps no faint table line`)
  }
})

test('Travelled, Client tracker, Fee collections, Weekly report: the scroll box is framed, the old faint lines are the tokens', async () => {
  const tv = await read('styles/travelled.css')
  assert.match(tv, /@media \(min-width: 721px\) \{\n\s+:root:root \.tv-table-wrap \{ box-sizing: border-box; border: var\(--table-frame-width\) solid var\(--table-line-strong\); border-radius: var\(--radius-xl, 10px\); \}/)
  assert.match(tv, /@media \(max-width: 720px\) \{[\s\S]*:root:root:root \.tv-table \{ overflow: visible; border: 0; border-radius: 0; \}/, 'the phone cards have no frame')
  const ct = await read('styles/client-tracker.css')
  assert.match(ct, /:root:root \.ct-table \.ct-scroll \{ box-sizing: border-box; border: var\(--table-frame-width\) solid var\(--table-line-strong\); border-radius: var\(--radius-xl, 10px\); \}/)
  assert.match(ct, /\.ct-table tr\.is-first-of-client td \{ border-top: var\(--table-frame-width\) solid var\(--table-line-strong\); \}/)
  assert.match(await read('styles/fee-collections.css'), /\.fee-collection-scroll \{ box-sizing: border-box; border: var\(--table-frame-width\) solid var\(--table-line-strong\);/)
  assert.match(await read('styles/staff-report.css'), /\.sw-scroll \{ box-sizing: border-box; border: var\(--table-frame-width\) solid var\(--table-line-strong\);/)
  const catchAll = await read('styles/table-default.css')
  for (const wrapper of ['.tk-table-wrap table', '.ct-scroll table', '.tv-table-wrap table', '.fee-collection-scroll table', '.sw-scroll table', '.db-scroll table']) assert.ok(catchAll.includes(wrapper), `${wrapper} is framed by its wrapper, not twice`)
})

test('the stacked-card tables drop the catch-all frame (a card list has no frame)', async () => {
  for (const [file, selector] of [['clients.css', '.cd-cands'], ['import-candidates.css', '.ic-files'], ['tasks-page.css', '.tk-table'], ['travelled.css', '.tv-table']]) {
    const css = await read(`styles/${file}`)
    assert.ok(css.includes(`:root:root:root ${selector} { overflow: visible; border: 0; border-radius: 0; }`), `${file}: ${selector}`)
  }
})

test('application form rows and the profile lists use the same lines', async () => {
  const form = await read('styles/application-form-tabs.css')
  assert.match(form, /\.aff-row-list \{ gap: 0; border: var\(--table-frame-width\) solid var\(--table-line-strong\); border-radius: 12px; overflow: hidden; \}/)
  assert.match(form, /\.aff-table-head \{[^}]*border-bottom: var\(--table-frame-width\) solid var\(--table-line-strong\)/)
  assert.match(form, /\.aff-row \{[^}]*border-top: var\(--table-line-width\) solid var\(--table-line\)/)
  const profile = await read('styles/candidate-v2.css')
  assert.match(profile, /:root:root \.cp-stack \{[^}]*border: var\(--table-frame-width\) solid var\(--table-line-strong\)/)
  assert.match(profile, /:root:root \.cp-stack > \.cp-item \{[^}]*border-bottom: var\(--table-line-width\) solid var\(--table-line\)/)
  assert.match(profile, /:root:root \.cp-docs \{[^}]*border: var\(--table-frame-width\) solid var\(--table-line-strong\)/)
  assert.match(profile, /:root:root \.cp-docs > \* \{[^}]*border-bottom: var\(--table-line-width\) solid var\(--table-line\)/)
})

test('no table rule in the table stylesheets keeps a hard-coded or faint border', async () => {
  const files = [
    'data-table.css', 'table-default.css', 'table-theme.css', 'tasks-table.css', 'leads-table.css', 'candidate-tables.css', 'clients-simple.css', 'agency-settings.css',
    'jobs-simple.css', 'travelled.css', 'client-tracker.css', 'daily-work.css', 'data-bank-v2.css', 'data-bank-table.css', 'staff-report.css', 'fee-collections.css',
    'clients.css', 'depth.css', 'skin.css', 'leads.css', 'tasks-page.css',
  ]
  const tablePart = /\b(?:td|th|tr|tbody|thead|tfoot)\b|\.ui-data-table|\.db-tbl|\.dw-table|\.tw-table|\.tk-table|\.tv-table|\.ct-table|\.sw-table|\.cd-cands|\.cs-agency-table|\.ld-map|\.fee-collection-table|\.dr-drill-table/
  for (const file of files) {
    const css = await read(`styles/${file}`)
    // pagination, menus and chips share some selectors: only the cell / row / header lines matter here
    const lines = tableRules(css, tablePart).filter((line) => /(?:^|[ ,])(?:[^{]*\b(?:td|th|tr)\b|[^{]*thead|[^{]*tbody)[^{]*\{/.test(line))
    for (const line of lines) {
      assert.doesNotMatch(line, FAINT, `${file}: ${line.trim().slice(0, 120)}`)
      assert.doesNotMatch(line, HARD, `${file}: ${line.trim().slice(0, 120)}`)
    }
  }
})
