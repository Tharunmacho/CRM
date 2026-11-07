import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(...relatives) {
  const parts = await Promise.all(relatives.map(async (relative) => (await readFile(new URL(relative, import.meta.url), 'utf8')).replace(/^import .*$/gm, '')))
  const { outputText } = ts.transpileModule(parts.join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

// The flag module has its own fold helper, so it is loaded on its own and handed to the pure helpers.
const { COUNTRY_NAMES, countryCode } = await load('../src/shared/lib/country-flag.ts')
const { canonicalCountry, countryOptions } = await load('../src/features/clients/client-search.ts', '../src/features/jobs/country-options.ts')
const proper = (text) => canonicalCountry(text, COUNTRY_NAMES, countryCode)

test('any case of a country is kept as its proper name', () => {
  assert.equal(proper('KUWAIT'), 'Kuwait')
  assert.equal(proper('  kuwait '), 'Kuwait')
  assert.equal(proper('india'), 'India')
  assert.equal(proper('united   arab emirates'), 'United Arab Emirates')
})

test('common short forms become the proper name', () => {
  assert.equal(proper('UAE'), 'United Arab Emirates')
  assert.equal(proper('uk'), 'United Kingdom')
  assert.equal(proper('KSA'), 'Saudi Arabia')
})

test('a name that is not a country stays as typed, tidied, and blank stays blank', () => {
  assert.equal(proper('Atlantis  Island'), 'Atlantis Island')
  assert.equal(proper('   '), '')
  assert.equal(proper(null), '')
})

test('the list has every country, the used ones first and spelled properly, each once', () => {
  const list = countryOptions(['India', 'KUWAIT', 'UAE', 'kuwait'], COUNTRY_NAMES, countryCode)
  assert.deepEqual(list.slice(0, 3), ['India', 'Kuwait', 'United Arab Emirates'])
  assert.equal(list.length, new Set(list.map((n) => n.toLowerCase())).size)
  assert.equal(list.length, COUNTRY_NAMES.length)
  const rest = list.slice(3)
  assert.deepEqual(rest, [...rest].sort((a, b) => a.localeCompare(b)))
  assert.ok(rest.includes('Qatar') && rest.includes('Afghanistan') && rest.includes('Zimbabwe'))
  assert.ok(!rest.includes('India') && !rest.includes('Kuwait'))
})

test('with nothing used yet the list is every country A to Z', () => {
  const list = countryOptions([], COUNTRY_NAMES, countryCode)
  assert.equal(list[0], COUNTRY_NAMES[0])
  assert.equal(list.length, COUNTRY_NAMES.length)
})

test('a used name that is not a country is still listed first', () => {
  assert.equal(countryOptions(['Atlantis'], COUNTRY_NAMES, countryCode)[0], 'Atlantis')
})
