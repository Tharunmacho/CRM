import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/** split-source.ts is pure (no imports), so it is transpiled and loaded as it stands. */
const source = await readFile(new URL('../src/shared/lib/split-source.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { formatPageRanges, splitSourceCaption } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)

test('pages are listed as ranges where they are consecutive', () => {
  assert.equal(formatPageRanges([1, 8]), '1, 8')
  assert.equal(formatPageRanges([2, 3]), '2-3')
  assert.equal(formatPageRanges([4, 5, 6, 7]), '4-7')
  assert.equal(formatPageRanges([3]), '3')
  assert.equal(formatPageRanges([1, 2, 3, 5, 7, 8]), '1-3, 5, 7-8')
  assert.equal(formatPageRanges([8, 1, 2, 2]), '1-2, 8')
  assert.equal(formatPageRanges([]), '')
})

test('the caption says Page for one page and Pages for several, with the original file name', () => {
  assert.equal(splitSourceCaption({ fileName: 'Rashid Khan Nawazkhan-picker.pdf', pages: [1, 8] }), 'Pages 1, 8 of Rashid Khan Nawazkhan-picker.pdf')
  assert.equal(splitSourceCaption({ fileName: 'scan.pdf', pages: [2, 3] }), 'Pages 2-3 of scan.pdf')
  assert.equal(splitSourceCaption({ fileName: 'scan.pdf', pages: [3] }), 'Page 3 of scan.pdf')
  assert.equal(splitSourceCaption({ fileName: 'scan.pdf', pages: [3, 3] }), 'Page 3 of scan.pdf')
  assert.equal(splitSourceCaption(undefined), '')
  assert.equal(splitSourceCaption({ fileName: '', pages: [1] }), '')
  assert.equal(splitSourceCaption({ fileName: 'scan.pdf', pages: [] }), '')
})
