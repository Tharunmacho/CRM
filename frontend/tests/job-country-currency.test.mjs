import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { countryCode, COUNTRY_NAMES } = await load('../src/shared/lib/country-flag.ts')
const { currencyForCountry, defaultCurrencyFor, CURRENCY_BY_COUNTRY_CODE } = await load('../src/features/jobs/country-currency.ts')

test('the countries the agency places people in map to their own currency', () => {
  const expected = {
    Malaysia: 'MYR', Kuwait: 'KWD', 'United Arab Emirates': 'AED', 'Saudi Arabia': 'SAR', Qatar: 'QAR', Oman: 'OMR', Bahrain: 'BHD',
    'Sri Lanka': 'LKR', Singapore: 'SGD', India: 'INR', Nepal: 'NPR', Bangladesh: 'BDT', Pakistan: 'PKR', 'United Kingdom': 'GBP',
    'United States': 'USD', Canada: 'CAD', Germany: 'EUR', France: 'EUR', Maldives: 'MVR', Jordan: 'JOD',
  }
  for (const [country, currency] of Object.entries(expected)) assert.equal(currencyForCountry(country, countryCode), currency, country)
})

test('common spellings, cities and case resolve like the country', () => {
  for (const typed of ['UAE', 'uae', 'Dubai', 'Abu Dhabi', 'Dubai, UAE']) assert.equal(currencyForCountry(typed, countryCode), 'AED', typed)
  assert.equal(currencyForCountry('KSA', countryCode), 'SAR')
  assert.equal(currencyForCountry('UK', countryCode), 'GBP')
  assert.equal(currencyForCountry('USA', countryCode), 'USD')
  assert.equal(currencyForCountry(' MALAYSIA ', countryCode), 'MYR')
})

test('an unknown or empty country has no currency', () => {
  for (const typed of ['', '   ', null, undefined, 'Atlantis', 'Nowhereland']) assert.equal(currencyForCountry(typed, countryCode), null, String(typed))
})

test('every country the app offers has a currency (Antarctica has none)', () => {
  const missing = COUNTRY_NAMES.filter((name) => name !== 'Antarctica' && !currencyForCountry(name, countryCode))
  assert.deepEqual(missing, [])
  for (const code of Object.values(CURRENCY_BY_COUNTRY_CODE)) assert.match(code, /^[A-Z]{3}$/)
})

test('the default is only offered when the currency list has it', () => {
  const list = ['INR', 'USD', 'EUR', 'AED', 'MYR']
  assert.equal(defaultCurrencyFor('Malaysia', countryCode, list), 'MYR')
  assert.equal(defaultCurrencyFor('Kuwait', countryCode, list), null) // KWD not on offer: the current value is kept
  assert.equal(defaultCurrencyFor('Atlantis', countryCode, list), null)
  assert.equal(defaultCurrencyFor('Malaysia', countryCode, []), null)
})
