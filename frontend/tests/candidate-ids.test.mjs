import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')

const ids = await load('../src/features/candidates/candidate-ids.ts')
const both = { ref: 'CND-01092', careId: 'CI-26-27-001', travellerId: 'CI-T-26-27-001' }

test('the Care India ID is the primary identifier and the CRM reference is the second one', () => {
  assert.equal(ids.primaryId(both), 'CI-26-27-001')
  assert.equal(ids.secondaryId(both), 'CND-01092')
  assert.equal(ids.idLine(both), 'CI-26-27-001 · CND-01092')
})

test('before a Care India ID exists the CRM reference leads, as before', () => {
  for (const careId of [undefined, null, '', '   ']) {
    const candidate = { ref: 'CND-01092', careId }
    assert.equal(ids.primaryId(candidate), 'CND-01092')
    assert.equal(ids.secondaryId(candidate), '')
    assert.equal(ids.idLine(candidate), 'CND-01092')
  }
})

test('the same text is never shown twice, and missing candidates and fields do not throw', () => {
  assert.equal(ids.secondaryId({ ref: 'ci-26-27-001', careId: 'CI-26-27-001' }), '')
  assert.equal(ids.idLine({ careId: 'CI-26-27-001' }), 'CI-26-27-001')
  for (const empty of [null, undefined, {}]) {
    assert.equal(ids.primaryId(empty), '')
    assert.equal(ids.idLine(empty), '')
    assert.equal(ids.travellerLabel(empty), '')
    assert.equal(ids.idSearchText(empty), '')
    assert.equal(ids.paperIdLine(empty), '')
  }
})

test('the Traveller ID shows only for a candidate who has one', () => {
  assert.equal(ids.travellerLabel(both), 'Traveller ID CI-T-26-27-001')
  assert.equal(ids.travellerIdOf(both), 'CI-T-26-27-001')
  for (const travellerId of [undefined, null, '', '  ']) assert.equal(ids.travellerLabel({ ref: 'CND-1', travellerId }), '')
})

test('search finds a candidate by either ID or the CRM reference, in any case', () => {
  for (const text of ['ci-26-27-001', 'CI-T-26', 'cnd-01092', '27-001']) assert.equal(ids.matchesIdText(both, text), true, text)
  assert.equal(ids.matchesIdText(both, 'CI-27-28'), false)
  assert.equal(ids.matchesIdText(both, ''), false)
  assert.equal(ids.matchesIdText({ ref: 'CND-1' }, 'CI-T'), false)
})

test('the paper form header names the Care India ID, the reference and the Traveller ID', () => {
  assert.equal(ids.paperIdLine(both), 'Care India ID CI-26-27-001 · Reference CND-01092 · Traveller ID CI-T-26-27-001')
  assert.equal(ids.paperIdLine({ ref: 'CND-01092', careId: 'CI-26-27-001' }), 'Care India ID CI-26-27-001 · Reference CND-01092')
  assert.equal(ids.paperIdLine({ ref: 'CND-01092' }), 'Reference CND-01092')
  const readable = (reference) => (reference.length <= 14 ? reference : '')
  assert.equal(ids.paperIdLine({ ref: 'CND-E-f238e22d89f44a5a8fd49f6be920a7a9', careId: 'CI-26-27-001' }, readable), 'Care India ID CI-26-27-001')
})

test('every screen that showed the CRM reference now leads with the Care India ID', async () => {
  const cell = await read('../src/features/candidates/components/data-bank-columns.tsx')
  assert.match(cell, /careIdOf\(candidate\)/)
  assert.match(cell, /className="cid-main"/)
  assert.match(cell, /secondaryId\(candidate\)/)

  const profile = await read('../src/screens/candidates/candidate-profile.tsx')
  assert.match(profile, /primaryId\(cand\)/)
  assert.match(profile, /<TravellerIdChip candidate=\{cand\} \/>/)

  const form = await read('../src/screens/candidates/application-form.tsx')
  assert.match(form, /careIdOf\(ids\)/)
  assert.match(form, /<TravellerIdChip candidate=\{ids\} \/>/)
  const letterhead = await read('../src/features/application-form/components/letterhead.tsx')
  assert.match(letterhead, /paperIdLine\(candidate, readableReference\)/)

  const travelled = await read('../src/screens/travelled/travelled.tsx')
  assert.match(travelled, /<TravellerIdChip candidate=\{row\} \/>/)
  assert.match(travelled, /primaryId\(row\)/)    // Traveller ID chip, then the Care India ID, then the CRM reference muted
  assert.match(travelled, /secondaryId\(row\)/)

  const palette = await read('../src/app/layout/command-search.tsx')
  assert.match(palette, /idSearchText\(candidate\)/)
  assert.match(palette, /idLine\(candidate\)/)
})

test('the API types and the form reader carry both IDs, optional and null-safe', async () => {
  assert.match(await read('../src/shared/model/types.ts'), /travellerId\?: string \| null/)
  assert.match(await read('../src/features/application-form/types.ts'), /careId\?: string \| null; travellerId\?: string \| null/)
  assert.match(await read('../src/features/application-form/form-model.ts'), /travellerId: candidate\.travellerId == null \? null : str\(candidate\.travellerId\)/)
  assert.match(await read('../src/features/travelled/travelled-model.ts'), /travellerId\?: string \| null/)
})

test('the ID styles let a long ID shrink instead of overflowing a 360px screen', async () => {
  const css = await read('../src/styles/candidate-ids.css')
  assert.match(css, /\.cid-chip \{[^}]*max-width: 100%/)
  assert.match(css, /\.cid-chip > b \{[^}]*text-overflow: ellipsis/)
  assert.match(await read('../src/styles/application-form.css'), /\.af-bar-nameline \{ flex-wrap: wrap; \}/)
})
