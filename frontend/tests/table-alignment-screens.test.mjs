import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

/*
 * The per-screen tables of the candidate and operations screens follow the Tasks table: every heading AND every cell is CENTRED (numbers
 * too), vertically centred, words wrap at spaces, and the columns size to what they hold. The documented exceptions (long free text such as a
 * file name, a custom-question answer, a caption) read from the left in their CELLS only; their headings stay centred. Cards (below a table's
 * fit width) keep their left labels.
 */
const read = (relative) => readFile(new URL(`../src/${relative}`, import.meta.url), 'utf8')
const rules = (css, selector) => css.split('\n').filter((line) => line.includes(selector))

test('DbTable grid tables centre the heading, the sort label, the cells and the right-aligned columns', async () => {
  const css = await read('styles/candidate-tables.css')
  assert.match(css, /\.db-tbl\.is-grid thead th \{[^}]*text-align: center;/)
  assert.match(css, /\.db-tbl\.is-grid thead th\.is-right \{ text-align: center; \}/)
  assert.match(css, /\.db-th-sort \{[^}]*justify-content: center;[^}]*text-align: center;/)
  assert.match(css, /tbody tr:not\(\.db-xrow\) > td \{[^}]*text-align: center;/)
  assert.match(css, /td\.is-right \{ text-align: center; \}/)
  assert.doesNotMatch(css, /text-align: (left|right)/, 'no left or right alignment is left in the shared candidate-table rules')
  // the sort caret hangs in the padding so the heading's WORDS stay centred over the centred cells
  assert.match(css, /\.db-th-caret \{ position: absolute; top: 50%; right: 3px;/)
  const table = await read('features/candidates/components/db-table.tsx')
  assert.match(table, /column\.align === 'left' && 'is-left'/, 'only a column that asks for it reads from the left')
  assert.match(table, /\(column\.align \?\? 'center'\) !== 'left' && 'is-center'/)
  assert.match(table, /column\.align !== undefined/, 'a table whose columns all state an alignment is the flush (Data Bank) table')
})

test('Data Bank: every column is centred, the row buttons and the Actions heading share one centre, the e-mail never overflows its cell', async () => {
  const columns = await read('features/candidates/data-bank-table.ts')
  assert.doesNotMatch(columns, /align: '(left|right)' }/)
  assert.equal([...columns.matchAll(/align: 'center'/g)].length, 7)
  const defs = await read('features/candidates/components/data-bank-columns.tsx')
  assert.match(defs, /ROW_BUTTONS_WIDTH, align: 'center'/)
  assert.match(defs, /align: 'left' as const, optional: 20 \+ index/, 'registry fields are free text: they read from the left')
  const css = await read('styles/data-bank-table.css')
  assert.match(css, /\.db-tbl\.is-grid\.is-bank :is\(th, td\) \{ text-align: center; \}/)
  assert.match(css, /\.db-tbl\.is-bank \.db-selcell > :is\(\.db-th-plain, \.db-acts\.is-grid\) \{ flex: 1 1 auto;/)
  assert.match(css, /\.dbt-sub\.is-email \{ max-width: 100%; min-width: 0; \}/)
  assert.match(css, /\.db-tbl\.is-bank \.db-owner \{ justify-content: center;/)
  // the cards and the job groups keep their left labels
  assert.match(css, /:is\(\.db-tbl\.is-bank, \.dbp-row, \.db-mini\) \.db-owner \{[^}]*justify-content: flex-start;/)
  const screen = await read('screens/candidates/data-bank.tsx')
  assert.match(screen, /boxWidth < DATA_BANK_TABLE_MIN_WIDTH \|\| !fit\.fits/, 'cards as soon as the required columns cannot fit: a word is never cut')
})

test('the fit mechanism reports whether the required columns fit, and the three fit screens turn to cards when they do not', async () => {
  const fit = await read('features/candidates/components/column-fit.ts')
  assert.match(fit, /fits: boolean/)
  assert.equal([...fit.matchAll(/fits: true/g)].length, 3)
  assert.equal([...fit.matchAll(/fits: false/g)].length, 1)
  for (const screen of ['screens/leads/leads.tsx', 'screens/candidates/data-bank.tsx', 'screens/workflow/mapped-candidates.tsx']) {
    assert.match(await read(screen), /!fit\.fits/, screen)
  }
})

test('Lead sheet: heading, name block, phone line, status, resume and experience answers are all centred; free-text answers read from the left', async () => {
  const css = await read('styles/leads-table.css')
  assert.match(css, /thead th \{[^}]*text-align: center;/)
  assert.match(css, /tbody td \{[^}]*text-align: center;/)
  assert.match(css, /tbody td\.is-left \{ padding-inline: 8px; text-align: left; \}/)
  assert.match(css, /\.ld-identity-phone \{[^}]*justify-content: center;/)
  assert.match(css, /\.ld-statown \{[^}]*align-items: center;[^}]*text-align: center;/)
  assert.match(css, /\.ld-experience-cell \{ display: flex; justify-content: center;/)
  assert.match(css, /\.ld-resume \{[^}]*align-items: center;/)
  assert.match(css, /thead th\.is-select \{[^}]*text-align: left;/, 'the select-all box sits over the row boxes')
  assert.match(css, /\.db-th-caret \{ position: absolute;/)
  const leads = await read('styles/leads.css')
  assert.match(leads, /thead th\.ld-col-name \{ text-align: center; \}/)
  const defs = await read('features/leads/components/lead-column-definitions.tsx')
  const core = defs.slice(0, defs.indexOf('const fields: DbColumn<Lead>[]'))
  assert.doesNotMatch(core, /align: '(left|right)'/)
  assert.match(defs, /width: 112, align: 'center', group: 'Workflow'/, '"Administrator" fits the Assigned to column whole')
  assert.match(defs, /header: field\.label, width: field\.width, align: 'left'/, 'registry answers are free text')
  const history = await read('features/leads/components/import-history.tsx')
  assert.equal([...history.matchAll(/align: 'right'/g)].length, 0, 'the counts are centred, with tabular numerals')
  assert.match(history, /key: 'file', header: 'File', width: 300, align: 'left'/, 'a file name reads from the left')
})

test('Matched candidates, Travelled and the plain DbTable tables are centred; Travelled sizes its columns to their content', async () => {
  const matched = await read('screens/workflow/mapped-candidates.tsx')
  assert.doesNotMatch(matched, /align: '(left|right)'/)
  const v2 = await read('styles/data-bank-v2.css')
  assert.match(v2, /\.db-tbl th \{[^}]*text-align: center;/)
  assert.match(v2, /\.db-tbl td \{[^}]*text-align: center;/)
  assert.match(v2, /\.db-tbl td\.is-left \{ text-align: left; \}/)
  const travelled = await read('styles/travelled.css')
  assert.match(travelled, /\.tv-table th \{[^}]*text-align: center;/)
  assert.match(travelled, /\.tv-table td \{[^}]*text-align: center;/)
  assert.match(travelled, /table-layout: auto/)
  // the cards (under 900px) keep their left labels
  assert.match(travelled, /:root:root \.tv-table td \{ height: auto; padding: 6px 0; border: 0; font-size: 14px; text-align: left; \}/)
})

test('the DataTable screens (Clients, Agencies, Jobs, Users, Currencies, WhatsApp, agency import, Order detail) centre every column', async () => {
  for (const file of ['screens/jobs/jobs.tsx', 'screens/users/currency-card.tsx', 'screens/inbox/whatsapp.tsx', 'screens/jobs/order-detail.tsx', 'features/clients/components/agency-import-dialog.tsx']) {
    const source = await read(file)
    assert.doesNotMatch(source, /align: '(left|right)'/, file)
  }
  assert.match(await read('screens/users/users.tsx'), /align: 'center' as const/)
  const css = await read('styles/ops-tables.css')
  for (const width of [768, 1024, 1280]) {
    assert.match(css, new RegExp(`@media \\(min-width: ${width}px\\) \\{\\s*:root:root:root \\.ops-fit \\.ui-data-table\\.ui-table-stack-${width} :is\\(th, td\\) \\{ text-align: center; \\}`), `a table of stack ${width} is centred from ${width}px; below it the cards keep their left labels`)
  }
  assert.match(css, /td\[data-label='Message or document'\] \{ text-align: left; \}/, 'a file name and its caption are free text')
  assert.match(css, /ui-badge-chip, \.ui-badge-dot\) \{ white-space: nowrap; \}/, 'a role or status chip never breaks inside a word')
  assert.match(css, /\.cd-order-list \.cd-cands :is\(th, td\) \{ text-align: center; \}/)
  assert.match(css, /\.ops-fees tbody td \{[^}]*text-align: center;/)
  const wa = await read('styles/inbox-v2.css')
  assert.match(wa, /\.wa-doc-name \{ overflow: visible; text-overflow: clip; white-space: normal;/, 'a file name wraps, it is not cut')
  // each table-specific sheet that used to left-align its heading and cells now centres them
  const jobs = await read('styles/jobs-simple.css')
  assert.match(jobs, /\.js-jobs-table \.ui-data-table\.is-grid thead th \{[^}]*text-align: center;/)
  assert.match(jobs, /\.js-jobs-table \.ui-data-table\.is-grid tbody td \{ text-align: center; \}/)
  const clients = await read('styles/clients-simple.css')
  assert.match(clients, /\.cs-agency-table \.ui-data-table\.is-grid thead th \{[^}]*text-align: center;/)
  assert.match(clients, /\.cs-agency-table \.ui-data-table\.is-grid tbody td \{[^}]*text-align: center;/)
})

test('free-text exceptions are left in the cell only, and the other form and list tables are centred', async () => {
  const files = await read('styles/import-candidates.css')
  assert.match(files, /\.ic-files th \{[^}]*text-align: center;/)
  assert.match(files, /\.ic-files td:is\(\.ic-file-name, :nth-child\(4\)\) \{ text-align: left; \}/)
  const form = await read('styles/application-form.css')
  assert.match(form, /\.af-rowtable-head > span \{[^}]*justify-content: center;[^}]*text-align: center;/)
  assert.match(form, /\.af-rowtable-cell \{[^}]*align-items: center;[^}]*text-align: center;/)
  assert.match(form, /\.af-track-head > span, \.af-track-row > span \{[^}]*justify-content: center;/)
  const ocr = await read('features/candidates/components/ocr-sections.tsx')
  assert.match(ocr, /<table className="w-full min-w-\[30rem\] text-center/)
  assert.match(ocr, /<td className="break-words py-2 text-left text-ink-600">\{r\.reason\}<\/td>/, 'the note is free text')
  const users = await read('styles/users-v2.css')
  assert.match(users, /\.uv-directory \.ui-data-table \.uv-status \{ text-align: center; \}/)
  const extras = await read('styles/lead-extras.css')
  assert.match(extras, /\.lc-name \{[^}]*justify-items: center;[^}]*text-align: center;/)
  assert.match(extras, /\.lc-forms th, \.lc-forms td \{[^}]*text-align: center;/)
  assert.deepEqual(rules(extras, 'text-align: right').filter((line) => !line.includes('.lc-card-leads')), [], 'only a card line keeps a right edge')
})
