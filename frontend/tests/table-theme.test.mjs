import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = fileURLToPath(new URL('../', import.meta.url))
const read = (relative) => readFile(path.join(root, 'src', relative), 'utf8')
const rule = (css, selector) => {
  const at = css.indexOf(selector)
  assert.ok(at >= 0, selector)
  return css.slice(at, css.indexOf('}', at))
}

test('the shared DataTable header is the primary blue: 44px, 700, uppercase 11.5px, .05em, one line, on-primary text', async () => {
  const css = await read('styles/data-table.css')
  const head = rule(css, ':root:root .ui-data-table.is-grid thead th {')
  for (const part of ['height: 44px', 'padding: 0 14px', 'font-size: 11.5px', 'font-weight: 700', 'letter-spacing: .05em', 'text-transform: uppercase', 'white-space: nowrap', 'color: var(--on-primary)', 'background: var(--primary)']) assert.ok(head.includes(part), part)
  assert.match(css, /thead tr \{ border: 0; background: var\(--primary\); \}/)
  assert.match(css, /\.ui-data-table\.is-grid\.is-compact thead th \{ height: 44px;/)
  assert.match(css, /thead :is\(button, a, input, \[tabindex\]\):focus-visible \{ outline: 2px solid var\(--on-primary\)/)
  assert.match(css, /thead \.custom-checkbox:is\(:checked, :indeterminate\) \{[^}]*var\(--on-primary\)/)
})

test('the shared DataTable rows: 56px, 14px padding, a 1px line under every row including the last, hover tint, a column line between every pair of columns', async () => {
  const css = await read('styles/data-table.css')
  const cell = rule(css, ':root:root .ui-data-table.is-grid tbody td {')
  for (const part of ['height: 56px', 'padding: 8px 14px', 'vertical-align: middle', 'border-bottom: var(--table-line-width) solid var(--table-line)', 'border-right: var(--table-line-width) solid var(--table-line)']) assert.ok(cell.includes(part), part)
  assert.doesNotMatch(css, /tbody tr:last-child > td \{ border-bottom: 0/)
  assert.match(css, /tbody tr:hover \{ background: color-mix\(in srgb, var\(--primary\) 5%/)
  assert.match(css, /td \.ui-button \{ height: 36px; min-height: 36px; \}/)
  assert.match(css, /:is\(th, td\):first-child \{ padding-left: 14px; \}/)
})

test('the stacked cards keep no band and no stray lines, and the buttons stay 40px', async () => {
  const css = await read('styles/data-table.css')
  for (const n of [768, 1024, 1280]) {
    assert.match(css, new RegExp(`ui-table-stack-${n} tbody td \{ height: auto; padding: 0; border: 0;`))
    assert.match(css, new RegExp(`ui-table-stack-${n} tbody td \.ui-button \{ height: 40px;`))
  }
  const depth = await read('styles/depth.css')
  assert.match(depth, /ui-table-stack-768 thead \{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect\(0 0 0 0\); \}/)
})

test('the shared table has no explanatory captions: no "Showing" word in the pager, no help line under an empty filter, titles on cut headers', async () => {
  const table = await read('shared/ui/table.tsx')
  assert.doesNotMatch(table, /pagination-label/)
  assert.doesNotMatch(table, /Remove a filter to see more results/)
  assert.match(table, /title=\{col\.header\}/)
  assert.match(table, /col\.actions \? 'center' : col\.align \?\? 'left'/)
})

test('table-theme.css: the safety net for every other table, in tokens, after the old skin, low specificity', async () => {
  const css = await read('styles/table-default.css')
  const head = rule(css, 'thead th {')
  for (const part of ['height: 44px', 'padding: 0 14px', 'background: var(--primary)', 'color: var(--on-primary)', 'font-size: 11.5px', 'font-weight: 700', 'letter-spacing: .05em', 'text-transform: uppercase', 'white-space: nowrap']) assert.ok(head.includes(part), part)
  assert.match(css, /height: 56px;[^}]*border-bottom: var\(--table-line-width\) solid var\(--table-line\)/)
  assert.match(css, /@media \(min-width: 1280px\)/)
  assert.match(css, /:where\(:not\(\.ui-data-table\.is-grid table\)\)/)
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,6}\b/)
  const main = await read('main.tsx')
  assert.ok(main.indexOf("./styles/table-default.css") > main.indexOf("./styles/skin.css"))
  assert.ok(main.indexOf("./styles/table-default.css") > main.indexOf("./styles/depth.css"))
})
