import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'
import { withRoleLabels } from './fixtures/role-labels-url.mjs'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(withRoleLabels(js)).toString('base64')}`)
}
const model = await load('../src/features/leads/lead-model.ts')

// The server sends the cleaned state (a real state, the one the place gives, or null); the column only lays it out.
test('Location column: the city, and under it the state the server sent', () => {
  assert.deepEqual(model.locationLines({ city: 'Pollachi', state: 'Tamil Nadu' }), { first: 'Pollachi', second: 'Tamil Nadu', title: 'Pollachi, Tamil Nadu' })
  assert.deepEqual(model.locationLines({ city: 'Chennai', state: ' tamil nadu ' }).second, 'tamil nadu')
})

test('Location column: no second line without a state, or when it only repeats the city', () => {
  assert.deepEqual(model.locationLines({ city: 'Qwertyville', state: null }), { first: 'Qwertyville', second: '', title: 'Qwertyville' })
  assert.deepEqual(model.locationLines({ city: 'Qwertyville' }), { first: 'Qwertyville', second: '', title: 'Qwertyville' })
  assert.equal(model.locationLines({ city: 'Delhi', state: ' delhi ' }).second, '')
})

test('Location column: a lead with no city shows its state alone, and nothing at all shows a dash', () => {
  assert.deepEqual(model.locationLines({ city: null, state: 'Kerala' }), { first: 'Kerala', second: '', title: 'Kerala' })
  assert.equal(model.locationLines({ city: '', state: '' }).first, '')
  assert.equal(model.locationLines({}).first, '')
})

test('the Location cell and the edit form use what the model says (the form starts from the stored text, not the cleaned one)', async () => {
  const cells = await readFile(new URL('../src/features/leads/components/lead-columns.tsx', import.meta.url), 'utf8')
  assert.match(cells, /locationLines\(lead\)/)
  const dialog = await readFile(new URL('../src/features/leads/components/lead-edit-dialog.tsx', import.meta.url), 'utf8')
  assert.match(dialog, /state: lead\.stateRaw \?\? lead\.state \?\? ''/)
  assert.match(dialog, /city: lead\.cityRaw \?\? lead\.city \?\? ''/)
})
