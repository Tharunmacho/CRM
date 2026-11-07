import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const view = await load('../src/features/candidates/data-bank-view.ts')

test('a written preferred country splits on commas, slashes, semicolons and bars, without blanks or repeats', () => {
  assert.deepEqual(view.splitCountries('Saudi Arabia, UAE'), ['Saudi Arabia', 'UAE'])
  assert.deepEqual(view.splitCountries(' Qatar / Kuwait ;  oman | qatar,, '), ['Qatar', 'Kuwait', 'oman'])
  assert.deepEqual(view.splitCountries(''), [])
  assert.deepEqual(view.splitCountries(null), [])
  assert.deepEqual(view.splitCountries('United   Arab  Emirates'), ['United Arab Emirates'])
})

test('a country keeps one pill colour whatever its letter case or spacing, always inside the palette', () => {
  assert.equal(view.countryTone('Qatar'), view.countryTone('  QATAR '))
  assert.equal(view.countryTone('Saudi Arabia'), view.countryTone('saudi-arabia'))
  assert.equal(view.countryTone('UAE', 'ae'), view.countryTone('United Arab Emirates', 'ae'))
  for (const name of ['Qatar', 'Kuwait', 'Oman', 'Bahrain', 'Saudi Arabia', 'Canada', 'Germany', 'مصر', '']) {
    const tone = view.countryTone(name)
    assert.ok(Number.isInteger(tone) && tone >= 0 && tone < view.COUNTRY_TONES, `${name} -> ${tone}`)
  }
  // The Gulf countries are not all lumped into one colour.
  assert.ok(new Set(['Qatar', 'Kuwait', 'Oman', 'Bahrain', 'Saudi Arabia', 'United Arab Emirates'].map((name) => view.countryTone(name))).size >= 3)
})

test('option colours: an admin tone wins, then the built-in meaning of the value, then grey', () => {
  assert.deepEqual(view.optionTone({ name: 'green' }, 'pending', view.SERVICE_CHARGE_TONE), { tone: 'ok' })
  assert.deepEqual(view.optionTone({ name: 'red' }, 'agreed', view.SERVICE_CHARGE_TONE), { tone: 'bad' })
  assert.deepEqual(view.optionTone({ color: '#7c3aed' }, 'agreed', view.SERVICE_CHARGE_TONE), { tone: 'quiet', color: '#7c3aed' })
  assert.deepEqual(view.optionTone({}, 'agreed', view.SERVICE_CHARGE_TONE), { tone: 'ok' })
  assert.deepEqual(view.optionTone({}, 'pending', view.SERVICE_CHARGE_TONE), { tone: 'warn' })
  assert.deepEqual(view.optionTone({}, 'declined', view.SERVICE_CHARGE_TONE), { tone: 'bad' })
  assert.deepEqual(view.optionTone({}, 'not_discussed', view.SERVICE_CHARGE_TONE), { tone: 'quiet' })
  assert.deepEqual(view.optionTone({}, 'something_new', view.SERVICE_CHARGE_TONE), { tone: 'quiet' })
  assert.deepEqual(view.optionTone({}, 'somewhat_interested', view.INTEREST_TONE), { tone: 'warn' })
  assert.deepEqual(view.optionTone({}, null, view.INTEREST_TONE), { tone: 'quiet' })
})

test('the Created cell reads as a day-and-month line over a year line', () => {
  assert.deepEqual(view.createdParts(new Date(2026, 10, 4, 9).toISOString()), { day: '04 Nov', year: '2026' })
  assert.deepEqual(view.createdParts(new Date(2025, 0, 21, 12).toISOString()), { day: '21 Jan', year: '2025' })
  assert.equal(view.createdParts('not a date'), null)
  assert.equal(view.createdParts(undefined), null)
})

test('a date of birth is read without a time-zone shift, with the age today', () => {
  const now = new Date(2026, 10, 4)
  assert.deepEqual(view.dobParts('1994-03-12', now), { label: '12 Mar 1994', age: 32 })
  assert.deepEqual(view.dobParts('1994-11-05', now), { label: '05 Nov 1994', age: 31 })
  assert.deepEqual(view.dobParts('1994-11-04', now), { label: '04 Nov 1994', age: 32 })
  assert.equal(view.dobParts('2090-01-01', now), null)
  assert.equal(view.dobParts('', now), null)
  assert.equal(view.dobParts(null, now), null)
  assert.equal(view.dobParts('1994-13-01', now), null)
})

test('a WhatsApp link needs a plausible number and keeps only its digits', () => {
  assert.equal(view.whatsappHref('+91 98765 43210'), 'https://wa.me/919876543210')
  assert.equal(view.whatsappHref('98765-43210'), 'https://wa.me/9876543210')
  assert.equal(view.whatsappHref('12345'), null)
  assert.equal(view.whatsappHref(''), null)
  assert.equal(view.whatsappHref(undefined), null)
  assert.equal(view.whatsappHref('1'.repeat(16)), null)
})

test('the optional registry columns sort on keys the server knows', async () => {
  const server = await readFile(new URL('../../backend/app/services/candidate_search.py', import.meta.url), 'utf8')
  const fields = await readFile(new URL('../src/features/candidates/data-bank-fields.ts', import.meta.url), 'utf8')
  const keys = [...fields.matchAll(/sortKey: '(\w+)'/g)].map((match) => match[1])
  assert.ok(keys.length >= 8)
  for (const key of new Set(keys)) assert.match(server, new RegExp(`"${key}":`), `server cannot sort on "${key}"`)
})
