import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { countryCode } = await load('../src/shared/lib/country-flag.ts')
const { frequentValues } = await load('../src/shared/lib/suggestions.ts')

test('country names and common spellings resolve to one ISO code', () => {
  assert.equal(countryCode('United Arab Emirates'), 'ae')
  assert.equal(countryCode(' UAE '), 'ae')
  assert.equal(countryCode('u.a.e.'), 'ae')
  assert.equal(countryCode('Saudi Arabia'), 'sa')
  assert.equal(countryCode('KSA'), 'sa')
  assert.equal(countryCode('UK'), 'gb')
  assert.equal(countryCode('USA'), 'us')
  assert.equal(countryCode('India'), 'in')
  assert.equal(countryCode('Côte d’Ivoire'), 'ci')
  assert.equal(countryCode('Dubai, UAE'), 'ae')
})

test('countries beyond the Gulf are recognised, not only the ones with flag files', () => {
  for (const [name, code] of [['Kenya', 'ke'], ['Nigeria', 'ng'], ['Sri Lanka', 'lk'], ['Philippines', 'ph'], ['Bangladesh', 'bd'], ['Ethiopia', 'et'], ['Japan', 'jp']]) {
    assert.equal(countryCode(name), code, name)
  }
})

test('whole-word matching keeps look-alike names apart', () => {
  assert.equal(countryCode('Romania'), 'ro')
  assert.equal(countryCode('Niger'), 'ne')
  assert.equal(countryCode('Nigeria'), 'ng')
  assert.equal(countryCode('Papua New Guinea'), 'pg')
  assert.equal(countryCode('Guinea'), 'gn')
  assert.equal(countryCode('South Sudan'), 'ss')
  assert.equal(countryCode('Sudan'), 'sd')
})

test('an unknown or empty country has no code', () => {
  assert.equal(countryCode(''), null)
  assert.equal(countryCode(null), null)
  assert.equal(countryCode(undefined), null)
  assert.equal(countryCode('Atlantis'), null)
})

test('suggestions are the distinct saved values, most used first', () => {
  assert.deepEqual(frequentValues(['Transport', 'medical', 'Transport', ' Medical ', '', null, undefined, 'Food', 'transport']), ['Transport', 'medical', 'Food'])
  assert.deepEqual(frequentValues(['b', 'a', 'c', 'a'], 2), ['a', 'b'])
  assert.deepEqual(frequentValues([]), [])
})
