import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

/*
 * Table alignment: the Tasks table rule, as SHARED defaults every table inherits.
 *   1. every heading and every cell is centred (horizontally; vertically middle), numbers too, with tabular figures
 *   2. the ONLY exceptions keep the CELL left (the heading stays centred): long multi-line free text (.is-text / .ui-c-text, the named notes, remarks,
 *      address, email, file, description cells) and the group rows that span the columns ("Active today (5)")
 *   3. a figure column is as wide as the longest word of its own heading (headings wrap at spaces) and the name column takes the rest, from the columns that
 *      are shown (measured / auto layout, no per-screen magic numbers)
 * The stylesheets are read as text; the real geometry (alignment of every th/td, centres of header and body cells, no clipped text, no sideways scroll)
 * was measured in a browser at 1440, 1225, 1100, 768 and 390px, light and dark.
 */
const root = fileURLToPath(new URL('../', import.meta.url))
const read = (relative) => readFile(path.join(root, 'src', relative), 'utf8').then((text) => text.replace(/\r\n/g, '\n'))

test('theme.css: the two alignment tokens, in the light and the dark theme', async () => {
  const css = await read('styles/theme.css')
  const block = css.slice(css.indexOf('Table lines'))
  const light = block.slice(block.indexOf(':root {'), block.indexOf('[data-theme="dark"] {'))
  const dark = block.slice(block.indexOf('[data-theme="dark"] {'))
  for (const [mode, text] of [['light', light], ['dark', dark]]) {
    assert.match(text, /--table-align: center;/, `${mode}: every heading and cell is centred`)
    assert.match(text, /--table-align-text: left;/, `${mode}: the free-text exception is left`)
  }
})

test('the shared DataTable centres every heading and cell; only a text column keeps its cell left', async () => {
  const css = await read('styles/data-table.css')
  assert.match(css, /:root:root \.ui-data-table\.is-grid :is\(th, td\) \{ text-align: var\(--table-align, center\); \}/)
  assert.match(css, /:root:root \.ui-data-table\.is-grid td\.ui-c-text \{ text-align: var\(--table-align-text, left\); \}/)
  assert.match(css, /:root:root \.ui-data-table\.is-grid td \{ font-variant-numeric: tabular-nums; \}/)
  assert.doesNotMatch(css, /\.ui-c-right \{ text-align: right/, 'no right-aligned figures any more')
  assert.doesNotMatch(css, /\.ui-c-left \{ text-align: left/, 'no left-aligned default any more')
  assert.match(css, /td:not\(\.ui-c-text\) > :where\(div, p, ul, \.record-identity\) \{ width: fit-content; max-width: 100%; margin-inline: auto; \}/, 'block content sits on the column centre')
  // vertical: header and body cells are middle
  assert.match(css, /thead th \{[^}]*vertical-align: middle;/)
  assert.match(css, /tbody td \{[^}]*vertical-align: middle;/)
  // the stacked cards keep their own left layout
  assert.match(css, /ui-table-stack-1280 tbody td:is\(\.ui-c-center, \.ui-c-right, \.ui-c-left\) \{ text-align: left; \}/)
})

test('DataTable column.align: left, center and right all centre, only "text" keeps the cell left', async () => {
  const table = await read('shared/ui/table.tsx')
  assert.match(table, /align\?: 'left' \| 'right' \| 'center' \| 'text'/)
  assert.match(table, /!col\.actions && col\.align === 'text' \? 'text' : 'center'/)
  assert.doesNotMatch(table, /text-right/, 'no right-aligned cell in the plain variant either')
  const theme = await read('styles/table-theme.css')
  assert.doesNotMatch(theme, /text-align: right/)
  assert.doesNotMatch(theme, /text-align: left; (text-overflow|border-bottom)/, 'the .tb-blue header and body cells are centred')
  assert.match(theme, /td\.ui-c-text \{ text-align: var\(--table-align-text, left\); \}/)
})

test('table-default.css: every hand-written table is centred, with the documented exceptions (cell only) and the group rows', async () => {
  const css = await read('styles/table-default.css')
  assert.match(css, /table:where\(:not\(\.ui-data-table\.is-grid table\)\) :is\(td, th\) \{ text-align: var\(--table-align, center\); \}/)
  assert.match(css, /:is\(td, th\):is\(\.num, \.is-num, \.is-right, \.ui-c-right\) \{ text-align: var\(--table-align, center\); font-variant-numeric: tabular-nums; \}/)
  const exceptions = /td:is\(([^)]*)\) \{ text-align: var\(--table-align-text, left\); \}/.exec(css)
  assert.ok(exceptions, 'the exceptions rule exists')
  const list = exceptions[1].split(',').map((item) => item.trim()).sort()
  assert.deepEqual(list, ['.is-address', '.is-description', '.is-email', '.is-file', '.is-notes', '.is-remarks', '.is-text', '.ui-c-text'], 'the documented exceptions, and nothing else')
  assert.match(css, /tr:is\(\.dw-group, \.is-group\) > :is\(td, th\) \{ text-align: var\(--table-align-text, left\); \}/, 'group rows that span the columns stay left')
  assert.doesNotMatch(css, /text-align: right/)
})

test('Reports people table, By client, Lead sheet and Candidate monitoring: centred, columns from the longest heading word, name column the rest', async () => {
  const table = await read('features/daily-report/report-table.tsx')
  assert.doesNotMatch(table, /is-left/, 'no left name column')
  assert.match(table, /export function headWordWidth/)
  assert.match(table, /split\(\/\\s\+\/\)/, 'a heading is measured by its words, not its whole text')
  assert.match(table, /<col style=\{\{ width: 'auto' \}\} \/>/, 'the name column is the remainder')
  assert.match(table, /nameWordWidth\(/, 'the name column is never narrower than its longest word')
  assert.match(table, /const colWidths = wanted\(pad\)\.map\(width => width \+ 'px'\)/, 'figure columns are px from the measured words, not percentages of a fixed number')
  const css = await read('styles/daily-report.css')
  const wide = css.slice(css.indexOf('@container dw (min-width: 1080px)'), css.indexOf('/* "Lead status now"'))
  assert.match(wide, /thead th \{[^}]*text-align: var\(--table-align, center\);[^}]*white-space: normal;/, 'centred headings that wrap at spaces')
  assert.match(wide, /:is\(tbody, tfoot\) :is\(td, th\) \{[^}]*text-align: var\(--table-align, center\);[^}]*vertical-align: middle;/)
  assert.match(wide, /th\.dwr-name \{[^}]*text-align: var\(--table-align, center\);/, 'names are centred')
  assert.match(wide, /td\.is-tail \{ padding: 8px; text-align: var\(--table-align, center\); \}/, 'the Day closed pills are centred')
  assert.match(wide, /\.dwr-pct \{ justify-content: center;/, 'the Contacted % meter is centred')
  assert.match(wide, /\.dw-group > th \{[^}]*text-align: var\(--table-align-text, left\);/, 'the Active today / No activity bands stay left')
  assert.doesNotMatch(wide, /text-align: (right|left);(?![^}]*dw-group)/, 'no right- or left-aligned figure or name in the wide table')
  // the stacked cards (under 1080px) keep their left layout
  assert.match(css, /@container dw \(max-width: 1079px\)/)
  // the drill-down tables: centred, the free-text detail cell left
  assert.match(css, /\.dr-drill-table td \{[^}]*text-align: var\(--table-align, center\); vertical-align: middle;/)
  assert.match(css, /\.dr-drill-table td\.dr-detail \{ text-align: var\(--table-align-text, left\); \}/)
})

test('Client tracker, weekly staff report, Fee collections and the report DataTables: centred, auto layout, remarks the only left cell', async () => {
  const ct = await read('styles/client-tracker.css')
  assert.match(ct, /table-layout: auto;/, 'the tracker sizes by content')
  assert.match(ct, /\.ct-col-sNo, \.ct-col-referral, \.ct-col-advance \{ width: 1%; \}/, 'the short columns are as wide as their longest word')
  assert.match(ct, /tbody td \{[^}]*text-align: var\(--table-align, center\);[^}]*vertical-align: middle;/)
  assert.match(ct, /tbody td\.is-remarks \{ text-align: var\(--table-align-text, left\); \}/, 'Remarks is the one left cell')
  const sw = await read('styles/staff-report.css')
  assert.match(sw, /\.sw-table :is\(thead th, tbody td, tbody th, tfoot td, tfoot th\) \{ text-align: var\(--table-align, center\); vertical-align: middle; \}/)
  assert.match(sw, /\.sw-table thead th\.num \{ width: 1%; \}/)
  const fees = await read('styles/fee-collections.css')
  assert.match(fees, /\.fee-collection-table\.ops-fees table \{ table-layout: auto; width: 100%; \}/)
  assert.match(fees, /ops-fees tbody td \{ padding: 8px; text-align: var\(--table-align, center\);/)
  const legacy = await read('styles/reports-v2.css')
  assert.match(legacy, /\.rv-table \.ui-data-table\.is-grid table\.is-fixed \{ table-layout: auto; \}/)
  const views = await read('features/reports/report-views.tsx')
  assert.match(views, /width: '1%', sortable: true, align: 'center'/, 'figure columns of the legacy tables are as wide as their heading word')
})
