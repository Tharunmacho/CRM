import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/screens/tasks/doc-input.tsx', import.meta.url), 'utf8')
const pure = source.slice(source.indexOf('/** "2026-11-19"'), source.indexOf('/**\n * The DOC (date of commencement) typed'))
const { outputText } = ts.transpileModule(pure, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { docToText, docFromText, shapeDocText } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('a saved ISO date is shown as dd/mm/yyyy, anything else as nothing', () => {
  assert.equal(docToText('2026-11-19'), '19/11/2026')
  assert.equal(docToText('2026-11-19T00:00:00Z'), '19/11/2026')
  assert.equal(docToText(null), '')
  assert.equal(docToText('soon'), '')
})

test('typing adds the slashes as the person goes', () => {
  assert.equal(shapeDocText('1'), '1')
  assert.equal(shapeDocText('191'), '19/1')
  assert.equal(shapeDocText('19112026'), '19/11/2026')
  assert.equal(shapeDocText('19/11/2026999'), '19/11/2026')
  assert.equal(shapeDocText('ab19-11.2026'), '19/11/2026')
})

test('only a complete real day between 2000 and 2100 is sent', () => {
  assert.equal(docFromText('19/11/2026'), '2026-11-19')
  assert.equal(docFromText('19-11-2026'), '2026-11-19')
  assert.equal(docFromText('01012030'), '2030-01-01')
  assert.equal(docFromText('29/02/2028'), '2028-02-29')
  assert.equal(docFromText('29/02/2027'), null)
  assert.equal(docFromText('31/04/2026'), null)
  assert.equal(docFromText('19/11/26'), null)
  assert.equal(docFromText('19/11/1999'), null)
  assert.equal(docFromText(''), null)
})
