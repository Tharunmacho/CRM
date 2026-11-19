import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const read = (relative) => readFile(new URL(`../src/${relative}`, import.meta.url), 'utf8')

test('the Data Bank and Matched candidates tables use the blue header theme', async () => {
  const css = await read('styles/candidate-tables.css')
  assert.match(css, /thead th \{[^}]*height: 44px;[^}]*padding: 0 14px;[^}]*white-space: nowrap;[^}]*background: var\(--primary\); color: var\(--on-primary\);[^}]*font-size: 11\.5px; font-weight: 700; letter-spacing: \.05em;[^}]*text-transform: uppercase;/)
  assert.match(css, /tbody tr:not\(\.db-xrow\) > td \{[^}]*height: 60px;[^}]*padding: 8px 14px;[^}]*vertical-align: middle;[^}]*border-bottom: var\(--table-line-width\) solid var\(--table-line\);/)
  assert.match(css, /:last-child > td \{ border-bottom: var\(--table-line-width\) solid var\(--table-line\); \}/, 'the last row keeps its line')
  assert.match(css, /\.db-th-caret \{[^}]*color: inherit;/, 'sort carets are white on the blue')
  assert.match(css, /thead \.custom-checkbox:is\(:checked, :indeterminate\) \{[^}]*var\(--on-primary\)/, 'select-all is white on the blue')
  assert.match(css, /\.db-tbl\.is-grid:not\(\.ld \.db-tbl\)/, 'the Lead sheet keeps its own sheet')
  assert.doesNotMatch(css, /#[0-9a-f]{3,6}\b/i, 'tokens only')
  for (const file of ['screens/candidates/data-bank.tsx', 'screens/workflow/mapped-candidates.tsx']) {
    assert.match(await read(file), /import '@\/styles\/candidate-tables\.css'/)
  }
})

test('the Data Bank carries no explanatory captions around the table', async () => {
  const screen = await read('screens/candidates/data-bank.tsx')
  assert.doesNotMatch(screen, /ld-fitnote|hidden to fit the screen|db-hint|Candidates from every source|dbt-scope-note|myNewNote/)
  assert.doesNotMatch(screen, /title="Finds candidate details as you type/)
  assert.match(screen, /placeholder="Search candidates, phone, email… \(commas combine words\)"/)
  assert.match(screen, /role="status">\{searching && !loaded/, 'the count line is data and stays')
})
