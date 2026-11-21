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
const both = { ref: 'CND-01092', careId: 'CI-078', travellerId: 'CI 94' }

test('the Data Bank ID is the ONE ID shown; the CND reference and the Traveller ID are never added to it', () => {
  assert.equal(ids.primaryId(both), 'CI-078')
  assert.equal(ids.idLine(both), 'CI-078')
  assert.equal(ids.secondaryId, undefined, 'there is no second ID to show any more')
  // Format-agnostic: whatever string the API sends is what is shown.
  for (const careId of ['CI-078', 'CI-26-27-078', 'DB/2027/78']) assert.equal(ids.idLine({ ref: 'CND-01092', careId }), careId)
})

test('only a very old row with no Data Bank ID falls back to the CRM reference', () => {
  for (const careId of [undefined, null, '', '   ']) {
    const candidate = { ref: 'CND-01092', careId }
    assert.equal(ids.primaryId(candidate), 'CND-01092')
    assert.equal(ids.idLine(candidate), 'CND-01092')
  }
})

test('missing candidates and fields do not throw', () => {
  assert.equal(ids.idLine({ careId: 'CI-078' }), 'CI-078')
  for (const empty of [null, undefined, {}]) {
    assert.equal(ids.primaryId(empty), '')
    assert.equal(ids.idLine(empty), '')
    assert.equal(ids.travellerIdOf(empty), '')
    assert.equal(ids.idSearchText(empty), '')
    assert.equal(ids.paperIdLine(empty), '')
  }
})

test('the Traveller ID is read as sent, and is empty for a candidate who has not travelled', () => {
  assert.equal(ids.travellerIdOf(both), 'CI 94')
  assert.equal(ids.travellerLabel, undefined, 'no Traveller ID label is added to other screens')
  for (const travellerId of [undefined, null, '', '  ']) assert.equal(ids.travellerIdOf({ ref: 'CND-1', travellerId }), '')
})

test('search still finds a candidate by the Data Bank ID, the Traveller ID or the hidden CND reference, in any case', () => {
  for (const text of ['ci-078', 'CI 9', 'ci 94', 'cnd-01092', '078']) assert.equal(ids.matchesIdText(both, text), true, text)
  assert.equal(ids.matchesIdText(both, 'CI-099'), false)
  assert.equal(ids.matchesIdText(both, ''), false)
  assert.equal(ids.matchesIdText({ ref: 'CND-1' }, 'CI-T'), false)
  // the stored numbers are zero-padded, a typed number is not
  assert.equal(ids.matchesIdText({ ref: 'CND-1', travellerId: 'CI 07' }, 'ci 7'), true)
  assert.equal(ids.matchesIdText({ ref: 'CND-1', travellerId: 'CI 07' }, 'ci7'), true)
  assert.equal(ids.matchesIdText({ ref: 'CND-1', travellerId: 'CI 07' }, 'ci 8'), false)
  assert.equal(ids.matchesIdText({ ref: 'CND-1', travellerId: 'CI 07' }, 'ci 00'), false)
})

test('the traveller number is CI, a space and at least two digits, growing wider after 99', () => {
  assert.deepEqual([1, 2, 9, 10, 99, 100, 101, 1234].map(ids.formatTravellerId), ['CI 01', 'CI 02', 'CI 09', 'CI 10', 'CI 99', 'CI 100', 'CI 101', 'CI 1234'])
  for (const bad of [0, -1, 1.5, Number.NaN]) assert.throws(() => ids.formatTravellerId(bad), RangeError)
  for (const serial of [1, 9, 10, 99, 100, 101, 5000]) assert.equal(ids.travellerSerialOf(ids.formatTravellerId(serial)), serial)
  for (const bad of ['CI-T-001', 'CI 1', 'CI 00', 'CI 001', 'ci 01', 'CI  01', 'CI 01 ', '', null, undefined, 7]) assert.equal(ids.travellerSerialOf(bad), null, String(bad))
})

test('the paper form header names the Data Bank ID only; the reference stands in only without one', () => {
  assert.equal(ids.paperIdLine(both), 'Care India ID CI-078')
  assert.equal(ids.paperIdLine({ ref: 'CND-01092', careId: 'CI-078' }), 'Care India ID CI-078')
  assert.equal(ids.paperIdLine({ ref: 'CND-01092' }), 'Reference CND-01092')
  const readable = (reference) => (reference.length <= 14 ? reference : '')
  assert.equal(ids.paperIdLine({ ref: 'CND-E-f238e22d89f44a5a8fd49f6be920a7a9', careId: 'CI-078' }, readable), 'Care India ID CI-078')
  assert.equal(ids.paperIdLine({ ref: 'CND-E-f238e22d89f44a5a8fd49f6be920a7a9' }, readable), '')
})

test('every screen shows only the Data Bank ID (Travelled only the Traveller ID), never the CND reference beside it', async () => {
  const cell = await read('../src/features/candidates/components/data-bank-columns.tsx')
  assert.match(cell, /careIdOf\(candidate\)/)
  assert.match(cell, /className="cid-main"/)
  assert.doesNotMatch(cell, /secondaryId|travellerLabel/)

  const profile = await read('../src/screens/candidates/candidate-profile.tsx')
  assert.match(profile, /primaryId\(cand\)/)
  assert.doesNotMatch(profile, /secondaryId|TravellerIdChip|CRM reference/)

  const form = await read('../src/screens/candidates/application-form.tsx')
  assert.match(form, /careIdOf\(ids\)/)
  assert.doesNotMatch(form, /TravellerIdChip|secondaryId|travellerId/)
  const letterhead = await read('../src/features/application-form/components/letterhead.tsx')
  assert.match(letterhead, /paperIdLine\(candidate, readableReference\)/)

  const travelled = await read('../src/screens/travelled/travelled.tsx')
  assert.doesNotMatch(travelled, /TravellerIdChip/, 'the table draws the number itself, not the wide chip')
  assert.doesNotMatch(travelled, /primaryId|careIdOf|secondaryId|candidate-ids/)

  const palette = await read('../src/app/layout/command-search.tsx')
  assert.match(palette, /idSearchText\(candidate\)/)
  assert.match(palette, /idLine\(candidate\)/)

  const stage = await read('../src/features/workflow/components/stage-modal-body.tsx')
  assert.doesNotMatch(stage, /label="Reference"|label="Traveller ID"/)
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
