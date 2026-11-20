import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const read = (relative) => readFile(new URL(`../src/${relative}`, import.meta.url), 'utf8')

test('the Data Bank and Matched candidates tables use the blue header theme (the Tasks look)', async () => {
  const css = await read('styles/candidate-tables.css')
  assert.match(css, /thead th \{[^}]*height: 44px;[^}]*padding: 0 8px;[^}]*white-space: nowrap;[^}]*background: var\(--primary\); color: var\(--on-primary\);[^}]*font-size: 11\.5px; font-weight: 600; letter-spacing: \.04em;[^}]*text-transform: uppercase;/)
  assert.match(css, /tbody tr:not\(\.db-xrow\) > td \{[^}]*height: 60px;[^}]*padding: 8px;[^}]*vertical-align: middle;[^}]*overflow-wrap: break-word;[^}]*border-bottom: var\(--table-line-width\) solid var\(--table-line\);/)
  assert.match(css, /td:not\(:last-child\) \{ border-right: var\(--table-line-width\) solid var\(--table-column-line\); \}/, 'a LIGHT line between body columns, none after the last')
  assert.match(css, /thead th:not\(:last-child\) \{ border-right: var\(--table-line-width\) solid var\(--table-header-line\); \}/)
  assert.match(css, /:last-child > td \{ border-bottom: var\(--table-line-width\) solid var\(--table-line\); \}/, 'the last row keeps its line')
  assert.match(css, /\.db-th-caret \{[^}]*color: inherit;/, 'sort carets are white on the blue')
  assert.match(css, /thead \.custom-checkbox:is\(:checked, :indeterminate\) \{[^}]*var\(--on-primary\)/, 'select-all is white on the blue')
  assert.match(css, /\.db-tbl\.is-grid thead th/, 'one rule for every grid table, the Lead sheet included')
  assert.doesNotMatch(css, /overflow-wrap: anywhere/, 'words wrap at spaces, never mid-word')
  assert.doesNotMatch(css, /#[0-9a-f]{3,6}\b/i, 'tokens only')
  assert.match(await read('features/candidates/components/db-table.tsx'), /import '@\/styles\/candidate-tables\.css'/)
})

test('Matched candidates is a fit table: no sideways scroll, the least important columns hide first, no flags', async () => {
  const screen = await read('screens/workflow/mapped-candidates.tsx')
  assert.match(screen, /<DbTable[\s\S]*?\sfit\s/)
  assert.match(screen, /fitColumns\(allColumns, boxWidth, columnChoices\)/)
  assert.doesNotMatch(screen, /sticky: '/, 'nothing is pinned: the table fits')
  assert.doesNotMatch(screen, /CountryFlag/, 'no country flags')
  for (const key of ['source', 'mapped', 'passport', 'order']) assert.match(screen, new RegExp(`key: '${key}'[^\\n]*optional: \\d+`), `${key} can be hidden to fit`)
})

test('the Data Bank carries no explanatory captions around the table', async () => {
  const screen = await read('screens/candidates/data-bank.tsx')
  assert.doesNotMatch(screen, /ld-fitnote|hidden to fit the screen|db-hint|Candidates from every source|dbt-scope-note|myNewNote/)
  assert.doesNotMatch(screen, /title="Finds candidate details as you type/)
  assert.match(screen, /placeholder="Search candidates, phone, email… \(commas combine words\)"/)
  assert.match(screen, /role="status">\{searching && !loaded/, 'the count line is data and stays')
})
