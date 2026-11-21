import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = fileURLToPath(new URL('../', import.meta.url))
const css = (name) => readFile(path.join(root, 'src/styles', name), 'utf8')
const src = (relative) => readFile(path.join(root, 'src', relative), 'utf8')

test('every table follows one set of rules: 44 px header, single-line uppercase 11.5px labels, 56 px rows, 14 px side padding', async () => {
  const travelled = await css('travelled.css')
  assert.match(travelled, /\.tv-table th \{[^}]*position: sticky;[^}]*height: 44px;[^}]*padding: 0 8px;[^}]*font-size: 11\.5px;[^}]*text-transform: uppercase;[^}]*white-space: nowrap;/)
  assert.match(travelled, /\.tv-table td \{[^}]*height: 56px; padding: 8px;[^}]*vertical-align: middle;/)
  const jobs = await css('jobs-simple.css')
  assert.match(jobs, /\.js-jobs-table \.ui-data-table\.is-grid thead th \{ height: 44px; padding: 0 14px; font-size: 11\.5px;[^}]*text-transform: uppercase; white-space: nowrap; \}/)
  assert.match(jobs, /\.js-jobs-table \.ui-data-table\.is-grid tbody td \{ height: 56px; padding: 8px 14px; vertical-align: middle;/)
  const settings = await css('agency-settings.css')
  assert.match(settings, /\.as-currencies \.ui-data-table\.is-grid thead th \{ height: 44px; padding: 0 14px; font-size: 11\.5px;[^}]*text-transform: uppercase; white-space: nowrap; \}/)
  assert.match(settings, /\.as-currencies \.ui-data-table\.is-grid tbody td \{ height: 56px; padding: 8px 14px; vertical-align: middle;/)
  assert.match(settings, /@media \(min-width: 1024px\) \{\s*:root:root \.as-currencies \.ui-data-table\.is-grid :is\(thead th, tbody td, :is\(th, td\)\.ui-actions-cell\) \{ text-align: center; \}/, 'heading and cells centred from the table width')
})

test('the card-style stacking of the shared table is left alone: the desktop table rules apply only above the stacking width', async () => {
  const jobs = await css('jobs-simple.css')
  assert.match(jobs, /@media \(min-width: 1280px\) \{\s*:root:root \.jobs-simple \.js-jobs-table \.ui-data-table\.is-grid thead th/)
  const clients = await css('clients-simple.css')
  assert.match(clients, /@media \(min-width: 1024px\) \{\s*:root:root \.clients-simple \.cs-agency-table \.ui-data-table\.is-grid thead th/)
  const settings = await css('agency-settings.css')
  assert.match(settings, /@media \(min-width: 768px\) \{\s*:root:root \.as-currencies \.ui-data-table\.is-grid thead th/)
  const screen = await src('screens/clients/clients.tsx')
  assert.match(screen, /<DataTable compact stackBelow=\{1024\}/)
  assert.doesNotMatch(screen, /sticky maxHeight/, 'the page scrolls, never an inner scroll box')
})

test('the currencies list is a table from 1024px with no inner scroll box and shows a dash, not the word None', async () => {
  const card = await src('screens/users/currency-card.tsx')
  assert.match(card, /<DataTable compact stackBelow=\{1024\} pageSize=\{50\}/)
  assert.doesNotMatch(card, /sticky/)
  assert.doesNotMatch(card, />None</)
  assert.match(card, /aria-label="None">—</)
})

test('dashboard cards sit on the 4 / 8 / 12 / 16 / 20 / 24 spacing scale and in a 4 x 2 grid', async () => {
  const dash = await css('dashboard-simple.css')
  assert.match(dash, /\.ds-grid \{[^}]*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); gap: 16px;/)
  assert.match(dash, /\.dashsimple \{[^}]*gap: 24px;/)
  assert.match(dash, /\.ds-card \{[^}]*padding: 20px;[^}]*border-radius: 16px;/)
  assert.match(dash, /\.sg-card \{[^}]*padding: 20px;[^}]*border-radius: 16px;/)
  const tracker = await css('dashboard-tracker.css')
  assert.match(tracker, /\.dt-card \{[^}]*padding: 20px;[^}]*border-radius: 16px;/)
  assert.match(tracker, /\.dt-chip \{[^}]*min-height: 32px;[^}]*line-height: 20px;/)
})

test('the stage 8 form: every box is 40 px (44 on touch), the grid gap is 16 px, labels are 12.5px/600 muted', async () => {
  const modal = await css('stage-modal.css')
  assert.match(modal, /:root:root \.sm-form :is\(\.ui-input:not\(textarea\), select, \.custom-select-trigger, \.date-picker-trigger\) \{ box-sizing: border-box; height: 40px; min-height: 40px; \}/)
  assert.match(modal, /\.sm-form \{ display: grid; grid-template-columns: repeat\(12, minmax\(0, 1fr\)\); gap: 16px;/)
  assert.match(modal, /\.sm-form \.ui-field > span:first-child \{[^}]*font-size: 12\.5px; font-weight: 600; color: var\(--text-muted\);/)
  assert.match(modal, /:root:root \.sm-form :is\(\.ui-input:not\(textarea\), select, \.custom-select-trigger, \.date-picker-trigger\) \{ height: 44px; min-height: 44px; \}/)
})

test('touch screens: one rule makes buttons, selects, inputs and chips 44 px on every screen of this pass, and fields 16px', async () => {
  const touch = await css('touch-targets.css')
  assert.match(touch, /@media \(max-width: 1023px\), \(pointer: coarse\)/)
  for (const root of ['.ct', '.tv', '.clients-simple', '.jobs-simple', '.dashsimple', '.sm-form', '.jv-form', '.as']) assert.ok(touch.includes(root), root)
  assert.match(touch, /\{ min-height: 44px; \}/)
  assert.match(touch, /font-size: 16px/)
  for (const file of ['screens/travelled/travelled.tsx', 'features/reports/client-tracker.tsx', 'screens/clients/clients.tsx', 'screens/jobs/jobs.tsx', 'screens/dashboard/dashboard.tsx', 'features/workflow/components/stage-modal-body.tsx', 'features/jobs/components/job-dialogs.tsx', 'screens/users/agency-settings.tsx']) {
    assert.match(await src(file), /import '@\/styles\/touch-targets\.css'/, file)
  }
})

test('the Client tracker table: sticky 44 px header, 56 px centred rows, right-aligned tabular numbers, ellipsis cells with the full text in the title', async () => {
  const view = await src('features/reports/client-placement-sheet.tsx')
  assert.match(view, /<span className="ct-cell" title=\{cellText\(row, column\.key\) \|\| undefined\}>/)
  assert.match(view, /<colgroup>/)
  assert.match(view, /className="rm-filters ct-filters"/)
})
