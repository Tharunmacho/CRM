import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const read = (relative) => readFile(new URL(`../src/${relative}`, import.meta.url), 'utf8')

test('Travelled is a fit table with the Tasks look: no scroll, light column lines, 6px frame, cards under 900px', async () => {
  const css = await read('styles/travelled.css')
  assert.match(css, /\.tv-table-wrap \{ max-height: 72vh; overflow-x: hidden;/, 'no sideways scroll')
  assert.match(css, /\.tv-table \{ width: 100%; min-width: 0; table-layout: fixed;/)
  assert.doesNotMatch(css, /min-width: 820px/)
  assert.doesNotMatch(css, /overflow-wrap: anywhere/, 'words wrap at spaces, never mid-word')
  assert.match(css, /border-radius: var\(--table-frame-radius, 6px\)/)
  const widths = [...css.matchAll(/\.tv-c-[a-z]+ \{ width: (\d+)%; \}/g)].reduce((sum, match) => sum + Number(match[1]), 0)
  assert.equal(widths, 100, 'the column shares add up to the whole frame')
})

test('every candidate table card (Lead sheet, Data Bank, Matched) has one thin border with the 6px curve', async () => {
  const css = await read('styles/candidate-tables.css')
  assert.match(css, /\.db-mini \{ border-width: var\(--table-line-width, 1px\); border-radius: var\(--table-frame-radius, 6px\); \}/)
})

test('the Lead sheet and the Data Bank never squeeze a column: the least width is stated per column', async () => {
  const bank = await read('features/candidates/components/data-bank-columns.tsx')
  assert.match(bank, /BANK_COLUMN_LEAST/)
  const fit = await read('features/candidates/components/column-fit.ts')
  assert.match(fit, /export const COMPACT_RATIO = 1/)
})
