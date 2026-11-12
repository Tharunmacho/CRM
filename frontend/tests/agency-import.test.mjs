import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/clients/agency-import-model.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { agencyImportFileError, sameAgencyImportOptions, agencyImportReadyCount } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('agency imports accept only nonempty bounded Excel/CSV files', () => {
  assert.equal(agencyImportFileError({ name: 'AGENCIES.XLSX', size: 5 * 1024 * 1024 }), '')
  assert.equal(agencyImportFileError({ name: 'agencies.csv', size: 40 }), '')
  assert.match(agencyImportFileError({ name: 'agencies.xlsx', size: 0 }), /empty/)
  assert.match(agencyImportFileError({ name: 'agencies.xlsx', size: 5 * 1024 * 1024 + 1 }), /5 MB/)
  assert.match(agencyImportFileError({ name: 'agencies.png', size: 100 }), /Excel.*CSV/)
  assert.match(agencyImportFileError({ name: 'agencies.xls', size: 100 }), /Excel.*CSV/)
})

test('confirmation becomes stale after any mapped column, sheet or header change', () => {
  const reviewed = { mapping: { name: 0, phone: 1, place: 2 }, headerRow: 1, sheetName: 'Agencies' }
  assert.equal(sameAgencyImportOptions(reviewed, { ...reviewed, mapping: { place: 2, phone: 1, name: 0 } }), true)
  assert.equal(sameAgencyImportOptions(reviewed, { ...reviewed, headerRow: 0 }), false)
  assert.equal(sameAgencyImportOptions(reviewed, { ...reviewed, sheetName: 'Another sheet' }), false)
  assert.equal(sameAgencyImportOptions(reviewed, { ...reviewed, mapping: { name: 0, phone: 2, place: 1 } }), false)
  assert.equal(sameAgencyImportOptions(reviewed, { ...reviewed, mapping: { name: 0, phone: 1 } }), false)
})

test('only new agencies and new contacts contribute to the import action count', () => {
  assert.equal(agencyImportReadyCount(null), 0)
  assert.equal(agencyImportReadyCount({ counts: { create: 2, add_contact: 3, duplicate: 7, invalid: 4, conflict: 1, section: 2 } }), 5)
  assert.equal(agencyImportReadyCount({ counts: { create: 0, add_contact: 0, duplicate: 7, invalid: 4, conflict: 1, section: 2 } }), 0)
})
