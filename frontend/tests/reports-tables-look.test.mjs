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
  assert.match(table, /headWordWidth\(/) // each figure column is as wide as the longest word of its own heading
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

test('the Client tracker is a table from 960px of card width (auto layout) and labelled cards below it, never a scroller', async () => {
  const css = await read('styles/client-tracker.css')
  assert.match(css, /\.ct-table \{ container: ct \/ inline-size; \}/)
  const cards = css.slice(css.indexOf('@container ct (max-width: 959px)'))
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

test('Reports (legacy tabs): the DataTable report tables wrap headings at spaces, lose the double rule and never break a word anywhere', async () => {
  const css = await read('styles/reports-v2.css')
  assert.match(css, /:root:root \.rv-card\.rv-table > header \{ border-bottom: 0;/) // one frame, never two
  assert.match(css, /:root:root \.rv-table\.tb-blue \.ui-data-table\.ui-data-table thead th \{ white-space: normal; text-overflow: clip; overflow-wrap: normal;/)
  assert.match(css, /\.rv-table\.tb-blue \.ui-data-table \.ui-th-inner > button \{[^}]*text-overflow: clip; white-space: normal;/)
  assert.match(css, /\.rv-cell \{[^}]*overflow-wrap: break-word; word-break: normal;/)
  assert.ok(!/overflow-wrap: anywhere/.test(css), 'no mid-word breaking in the report styles')
  const views = await read('features/reports/report-views.tsx')
  assert.match(views, /className=\{`rv-card rv-table tb-blue \$\{className\}`\}/) // the shared Tasks-look theme
  assert.match(views, /stackBelow/) // labelled cards below the fit width
})

test('Reports (legacy tabs): the weekly staff report tables fit their panel and become labelled cards below their fit width', async () => {
  const css = await read('styles/staff-report.css')
  assert.match(css, /\.sw-body \{ container: sw \/ inline-size; \}/)
  assert.match(css, /:root:root \.rv-card\.sw-card:has\(> \.sw-scroll\) \{ border-color: transparent; \}/) // one frame
  assert.match(css, /\.sw-staff \{ min-width: 0; \}/) // the 880px minimum is gone
  assert.match(css, /\.sw-staff td:not\(:first-child\) \{ white-space: normal; \}/)
  assert.match(css, /\.sw-table thead th \{ white-space: normal; text-overflow: clip; overflow-wrap: normal;/)
  for (const [name, width] of [['staff', 979], ['leads', 779], ['matrix', 719]]) {
    const block = css.slice(css.indexOf(`@container sw (max-width: ${width}px)`))
    assert.match(block, new RegExp(String.raw`\.sw-scroll:has\(> \.sw-${name}\) \{ overflow: visible; padding: 0 16px 16px; border: 0; border-radius: 0; \}`), `${name}: the cards have no frame`)
    assert.match(block, new RegExp(String.raw`\.sw-table\.sw-${name} :is\(tbody, tfoot\) td::before \{ content: attr\(data-label\)`), `${name}: every cell shows its label`)
  }
  assert.ok(!/overflow-wrap: anywhere/.test(css))
  const tsx = await read('features/staff-report/staff-weekly-report.tsx')
  for (const label of ['Recruiter', 'Days', 'Hours', 'Daily tasks', 'Stage moves', 'Sent to client', 'Visa', 'Deployed', 'Stuck', 'Score', 'Assigned', 'Interested', 'Calls', 'Resumes', 'Converted', 'Overdue']) {
    assert.ok(tsx.includes(`data-label="${label}"`), `${label} cell is labelled`)
  }
  assert.match(tsx, /data-label=\{`Stage \$\{stage\.stage\}`\}/)
})
