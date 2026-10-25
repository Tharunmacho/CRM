import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const lib = await load('../src/shared/lib/agency.ts')
const { agencyName, agencyShortName, documentTitle, homeDigits, activeCurrencies, currencyChoices, agencyFormErrors, agencyFormChanged,
  formFromSettings, currencyFormErrors, serverFieldErrors, EMPTY_SETTINGS, NEUTRAL_NAME } = lib

const currencies = [
  { code: 'USD', name: 'US Dollar', symbol: null, active: true, sortOrder: 20 },
  { code: 'AED', name: 'UAE Dirham', symbol: null, active: true, sortOrder: 10 },
  { code: 'GBP', name: 'British Pound', symbol: null, active: false, sortOrder: 30 },
]
const settings = { ...EMPTY_SETTINGS, name: 'Example Agency', shortName: 'Example', defaultOrderCurrency: 'AED', defaultAdvanceCurrency: 'USD',
  homeCallingCode: '+44', acknowledgementText: 'Thank you for writing to us.', currencies, editable: true }

test('names come from the settings, then the sign-in answer, then a neutral label', () => {
  assert.equal(agencyName(settings), 'Example Agency')
  assert.equal(agencyName(EMPTY_SETTINGS, { name: 'Signed Out Agency' }), 'Signed Out Agency')
  assert.equal(agencyName(EMPTY_SETTINGS, null), NEUTRAL_NAME)
  assert.equal(agencyShortName(settings), 'Example')
  assert.equal(agencyShortName({ name: 'Long Name', shortName: null }), 'Long Name')
  assert.equal(documentTitle('Example Agency'), 'Example Agency | Recruitment CRM')
  assert.equal(documentTitle(NEUTRAL_NAME), 'CRM')
})

test('only active currencies are offered, in the admin sort order', () => {
  assert.deepEqual(activeCurrencies(currencies).map((c) => c.code), ['AED', 'USD'])
})

test('a record keeps a retired or unknown currency in its own picker', () => {
  assert.deepEqual(currencyChoices(currencies, 'AED').map((c) => c.code), ['AED', 'USD'])
  const retired = currencyChoices(currencies, 'GBP')
  assert.deepEqual(retired.map((c) => c.code), ['AED', 'USD', 'GBP'])
  assert.match(retired[2].name, /not in use/)
  assert.deepEqual(currencyChoices(currencies, 'JPY').map((c) => c.code), ['AED', 'USD', 'JPY'])
  assert.deepEqual(currencyChoices([], null), [])
})

test('the calling code is reduced to digits only when it is well formed', () => {
  assert.equal(homeDigits('+91'), '91')
  assert.equal(homeDigits('91'), '')
  assert.equal(homeDigits('+12345'), '')
  assert.equal(homeDigits(null), '')
})

test('the profile form applies the same rules as the server', () => {
  const form = formFromSettings(settings)
  assert.deepEqual(agencyFormErrors(form, currencies), {})
  assert.ok(agencyFormErrors({ ...form, name: '  ' }, currencies).name)
  assert.ok(agencyFormErrors({ ...form, name: 'x'.repeat(121) }, currencies).name)
  assert.ok(agencyFormErrors({ ...form, defaultOrderCurrency: 'GBP' }, currencies).defaultOrderCurrency)
  assert.ok(agencyFormErrors({ ...form, defaultAdvanceCurrency: '' }, currencies).defaultAdvanceCurrency)
  for (const bad of ['91', '+', '+12345', '+9a']) assert.ok(agencyFormErrors({ ...form, homeCallingCode: bad }, currencies).homeCallingCode, bad)
  assert.deepEqual(agencyFormErrors({ ...form, homeCallingCode: '+9 1' }, currencies), {})
  assert.ok(agencyFormErrors({ ...form, acknowledgementText: 'short' }, currencies).acknowledgementText)
})

test('a changed form is detected, ignoring surrounding spaces', () => {
  const form = formFromSettings(settings)
  assert.equal(agencyFormChanged(form, settings), false)
  assert.equal(agencyFormChanged({ ...form, name: ' Example Agency ' }, settings), false)
  assert.equal(agencyFormChanged({ ...form, defaultNationality: 'Kenyan' }, settings), true)
})

test('the currency form checks the code only when adding', () => {
  assert.ok(currencyFormErrors({ code: 'jp', name: 'Yen', symbol: '' }, currencies, true).code)
  assert.ok(currencyFormErrors({ code: 'AED', name: 'Dirham', symbol: '' }, currencies, true).code)
  assert.deepEqual(currencyFormErrors({ code: 'jpy', name: 'Yen', symbol: '¥' }, currencies, true), {})
  assert.deepEqual(currencyFormErrors({ code: '', name: 'Dirham', symbol: '' }, currencies, false), {})
  assert.ok(currencyFormErrors({ code: 'JPY', name: ' ', symbol: '' }, currencies, true).name)
  assert.ok(currencyFormErrors({ code: 'JPY', name: 'Yen', symbol: '123456789' }, currencies, true).symbol)
})

test('server field errors are mapped by field name', () => {
  assert.deepEqual(serverFieldErrors({ message: 'x', errors: [{ field: 'name', message: 'Enter the agency name.' }, { nope: 1 }] }), { name: 'Enter the agency name.' })
  assert.deepEqual(serverFieldErrors('plain text'), {})
  assert.deepEqual(serverFieldErrors(null), {})
})
