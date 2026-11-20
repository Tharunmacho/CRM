import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

/**
 * The Reports tables, the Client tracker, the Recruiters board, the weekly staff report and the Attendance tables all wear the approved Tasks table look
 * (blue 44px header, thin 1px frame with a 6px curve, faint row lines, light column lines, wrapping at spaces, no sideways scroll). The geometry was
 * measured in the browser (1440, 1366, 1280, 1024, 768 and 390px, light and dark); these tests pin the rules that produce it.
 */
const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('the figure tables have no inline min-width: they fit their panel, columns are measured percentages and the name column takes the rest', async () => {
  const table = await read('features/daily-report/report-table.tsx')
  assert.ok(!/minWidth|style=\{\{ minWidth/.test(table), 'no min-width in the code')
  assert.match(table, /<col style=\{\{ width: 'auto' \}\} \/>/) // the name column is the remainder
  assert.match(table, /headWidth\(/) // each figure column is as wide as its own heading
  assert.match(table, /const TABLE_MIN_WIDTH = 1080/) // the Tasks list's breakpoint: a table from 1080px, cards below
  const views = await read('features/daily-report/report-views.tsx')
  assert.ok(!/colWidth=/.test(views))
})

test('the figure tables use the Tasks look inside the 1080px container query; below it they are cards', async () => {
  const css = await read('styles/daily-report.css')
  const wide = css.slice(css.indexOf('@container dw (min-width: 1080px)'))
  assert.match(wide, /thead th \{[^}]*height: 44px;[^}]*background: var\(--primary\); color: var\(--on-primary\);[^}]*text-transform: uppercase;/)
  assert.match(wide, /border-bottom: var\(--table-line-width\) solid var\(--table-line\)/) // a line under every row
  assert.match(wide, /border-right: var\(--table-line-width\) solid var\(--table-column-line\)/) // a light line between the body columns
  assert.match(wide, /thead th:not\(:last-child\) \{ border-right: var\(--table-line-width\) solid var\(--table-header-line\)/)
  assert.match(wide, /height: 56px;/)
  assert.match(wide, /\.dwr-who \{[^}]*white-space: normal; overflow-wrap: break-word;/) // names wrap at spaces, never an ellipsis
  assert.ok(!/overflow-wrap: anywhere/.test(wide))
  assert.match(css, /@container dw \(max-width: 1079px\)/) // the card layout
  // the thin 6px frame is the scroll box's (daily-work.css), with the table never wider than it
  assert.match(wide, /:root \.dwr \.tk-table-wrap \{ overflow: hidden; \}/)
  const base = await read('styles/daily-work.css')
  assert.match(base, /\.dw \.tk-table-wrap \{ box-sizing: border-box; overflow-x: auto; border: var\(--table-frame-width\) solid var\(--table-line-strong\); border-radius: var\(--table-frame-radius, 6px\); \}/)
})

test('Lead status now is a small table in the same look at every width', async () => {
  const css = await read('styles/daily-report.css')
  assert.match(css, /\.dwr-status > table\.dw-table thead th \{[^}]*background: var\(--primary\); color: var\(--on-primary\);[^}]*font-size: 11\.5px;/)
  assert.match(css, /\.dwr-status > table\.dw-table \{ display: table; \}/)
  const views = await read('features/daily-report/report-views.tsx')
  assert.match(views, /<table className="dw-table"><colgroup>/)
})

test('the Recruiters board has no chrome of its own at any width, so its cards share the table frame edges; names wrap', async () => {
  const css = await read('styles/daily-work-board.css')
  assert.match(css, /\.dw-panel\.dwb-board \{ overflow: visible; border-width: 0; border-radius: 0; background: transparent; \}/)
  assert.match(css, /\.dw-panel\.dwb-board :is\(\.dwb-cards, \.dwb-none\) \{ padding-inline: 0; \}/)
  assert.match(css, /\.dwb-card-name b \{ overflow: visible; text-overflow: clip; white-space: normal; overflow-wrap: break-word; \}/)
})

test('the Client tracker is a table from 1080px of card width and labelled cards below it, never a scroller', async () => {
  const css = await read('styles/client-tracker.css')
  assert.match(css, /\.ct-table \{ container: ct \/ inline-size; \}/)
  const cards = css.slice(css.indexOf('@container ct (max-width: 1079px)'))
  assert.match(cards, /max-height: none; overflow: visible;/)
  assert.match(cards, /td:first-child \{ display: none; \}/) // the serial number is not shown on a card
  assert.match(cards, /td\[data-label='Name'\] \{ grid-column: 1 \/ -1; order: -1;/)
})

test('the weekly staff report tables use the same look', async () => {
  const css = await read('styles/staff-report.css')
  assert.match(css, /\.sw-table thead th \{[^}]*height: 44px;[^}]*background: var\(--primary\); color: var\(--on-primary\);[^}]*text-transform: uppercase;/)
  assert.match(css, /\.sw-scroll \{ border-radius: var\(--table-frame-radius, 6px\); \}/)
})

test('Attendance: no heading is cut and the matrix names wrap', async () => {
  const css = await read('features/attendance/attendance-overview.css')
  assert.match(css, /\.att-mx-name \{ min-width: 0; font-size: 13\.5px; line-height: 1\.3; overflow-wrap: break-word;/)
  assert.ok(!/\.att-mx-name \{[^}]*text-overflow: ellipsis/.test(css))
  // each column is at least as wide as its heading at the narrowest table (a 950px panel): about 7.7px a letter plus the 28px padding
  const files = ['attendance-overview.tsx', 'attendance-presence.tsx', 'attendance-records.tsx']
  for (const file of files) {
    const source = await read(`features/attendance/components/${file}`)
    // the action columns (a button) are sized by the table, not by their percentage
    for (const line of source.split('\n').filter((text) => !text.includes('actions: true'))) {
      for (const [, header, width] of line.matchAll(/header: '([^']+)', width: '(\d+)%'/g)) {
        const need = Math.ceil(header.length * 7.7 + 28)
        assert.ok((Number(width) / 100) * 950 >= need - 2, `${file}: "${header}" needs about ${need}px, has ${(Number(width) / 100) * 950}px`)
      }
    }
  }
})
