import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const filters = await load('../src/features/clients/agency-filters.ts')
const form = await load('../src/features/clients/client-form.ts')
const {
  SPEC_OPTIONS, COUNTRY_OPTIONS, SPEC_LABELS, COUNTRY_LABELS, NO_FILTERS, parseAgencyFilters, agencyFilterParams, hasAgencyFilters, describeAgencyFilters,
  matchesSpecialisation, matchesCountry, matchesActivity, matchesAgencyFilters, activeFirst,
} = filters

const params = (query) => new URLSearchParams(query)
const agency = (name, extra = {}) => ({ name, specialisations: [], preferredCountries: [], activeAgency: false, ...extra })
const names = (list) => list.map((item) => item.name)

test('the filter options are the same lists, in the same order, as the agency form', () => {
  assert.deepEqual([...SPEC_LABELS], [...form.SPECIALISATIONS])
  assert.deepEqual([...COUNTRY_LABELS], [...form.PREFERRED_COUNTRIES])
  assert.deepEqual(SPEC_OPTIONS.map((option) => option.value), ['oil-and-gas', 'hospitality', 'construction', 'general-worker', 'medical', 'all-sectors'])
  assert.deepEqual(COUNTRY_OPTIONS.map((option) => option.value), ['singapore', 'malaysia', 'europe', 'gulf', 'others'])
})

test('a specific sector matches an exact tag, and an All sectors agency counts under every sector', () => {
  const oil = agency('Oil', { specialisations: ['Oil and gas'] })
  const medical = agency('Medical', { specialisations: ['Medical', 'Hospitality'] })
  const everything = agency('Everything', { specialisations: ['All sectors'] })
  const none = agency('None')
  assert.equal(matchesSpecialisation(oil, 'oil-and-gas'), true)
  assert.equal(matchesSpecialisation(medical, 'oil-and-gas'), false)
  assert.equal(matchesSpecialisation(medical, 'hospitality'), true)
  assert.equal(matchesSpecialisation(none, 'oil-and-gas'), false)
  for (const option of SPEC_OPTIONS) assert.equal(matchesSpecialisation(everything, option.value), true, option.label)
  // The All sectors filter itself is an exact match: a specific sector does not count as All sectors.
  assert.equal(matchesSpecialisation(everything, 'all-sectors'), true)
  assert.equal(matchesSpecialisation(oil, 'all-sectors'), false)
  assert.equal(matchesSpecialisation(medical, 'all-sectors'), false)
  assert.equal(matchesSpecialisation(none, ''), true)
  assert.equal(matchesSpecialisation({}, 'medical'), false) // an older server sends no list
})

test('preferred country and activity match exactly; a missing flag reads as not active', () => {
  assert.equal(matchesCountry(agency('A', { preferredCountries: ['Gulf', 'Europe'] }), 'gulf'), true)
  assert.equal(matchesCountry(agency('A', { preferredCountries: ['Gulf'] }), 'malaysia'), false)
  assert.equal(matchesCountry(agency('A'), ''), true)
  assert.equal(matchesActivity(agency('A', { activeAgency: true }), 'active'), true)
  assert.equal(matchesActivity(agency('A', { activeAgency: true }), 'inactive'), false)
  assert.equal(matchesActivity({}, 'inactive'), true)
  assert.equal(matchesActivity({}, 'active'), false)
  assert.equal(matchesActivity({ activeAgency: true }, ''), true)
})

test('the three filters combine with AND', () => {
  const all = [
    agency('Gulf oil, active', { specialisations: ['Oil and gas'], preferredCountries: ['Gulf'], activeAgency: true }),
    agency('Gulf oil, quiet', { specialisations: ['Oil and gas'], preferredCountries: ['Gulf'] }),
    agency('Asia oil, active', { specialisations: ['Oil and gas'], preferredCountries: ['Singapore'], activeAgency: true }),
    agency('Gulf medical, active', { specialisations: ['Medical'], preferredCountries: ['Gulf'], activeAgency: true }),
    agency('Gulf anything', { specialisations: ['All sectors'], preferredCountries: ['Gulf', 'Others'] }),
  ]
  const pick = (spec, country, activity) => names(all.filter((item) => matchesAgencyFilters(item, { spec, country, activity })))
  assert.equal(pick('', '', '').length, 5)
  assert.deepEqual(pick('oil-and-gas', '', ''), ['Gulf oil, active', 'Gulf oil, quiet', 'Asia oil, active', 'Gulf anything'])
  assert.deepEqual(pick('oil-and-gas', 'gulf', ''), ['Gulf oil, active', 'Gulf oil, quiet', 'Gulf anything'])
  assert.deepEqual(pick('oil-and-gas', 'gulf', 'active'), ['Gulf oil, active'])
  assert.deepEqual(pick('', 'gulf', 'inactive'), ['Gulf oil, quiet', 'Gulf anything'])
  assert.deepEqual(pick('all-sectors', '', ''), ['Gulf anything'])
  assert.deepEqual(pick('medical', 'singapore', ''), [])
})

test('the filters are read from the address and written back without leaving empty entries', () => {
  assert.deepEqual(parseAgencyFilters(params('?spec=oil-and-gas&country=gulf&active=1')), { spec: 'oil-and-gas', country: 'gulf', activity: 'active' })
  assert.deepEqual(parseAgencyFilters(params('active=0')), { spec: '', country: '', activity: 'inactive' })
  assert.deepEqual(parseAgencyFilters(params('')), NO_FILTERS)
  assert.deepEqual(parseAgencyFilters(params('?spec=Oil-And-Gas&country=%20GULF%20')), { spec: 'oil-and-gas', country: 'gulf', activity: '' }) // case and spaces forgiven
  // Hand-edited nonsense reads as "All" instead of hiding every agency.
  assert.deepEqual(parseAgencyFilters(params('?spec=banking&country=mars&active=maybe')), NO_FILTERS)
  assert.deepEqual(agencyFilterParams({ spec: 'medical', country: '', activity: 'inactive' }), { spec: 'medical', country: null, active: '0' })
  assert.deepEqual(agencyFilterParams(NO_FILTERS), { spec: null, country: null, active: null })
  const roundTrip = { spec: 'all-sectors', country: 'others', activity: 'active' }
  const written = new URLSearchParams()
  for (const [key, value] of Object.entries(agencyFilterParams(roundTrip))) if (value) written.set(key, value)
  assert.equal(written.toString(), 'spec=all-sectors&country=others&active=1')
  assert.deepEqual(parseAgencyFilters(written), roundTrip)
})

test('hasAgencyFilters and the words shown in the result count', () => {
  assert.equal(hasAgencyFilters(NO_FILTERS), false)
  assert.equal(hasAgencyFilters({ ...NO_FILTERS, activity: 'inactive' }), true)
  assert.equal(describeAgencyFilters(NO_FILTERS), '')
  assert.equal(describeAgencyFilters({ spec: 'oil-and-gas', country: '', activity: '' }), 'Oil and gas')
  assert.equal(describeAgencyFilters({ spec: 'oil-and-gas', country: 'gulf', activity: 'active' }), 'Oil and gas · Gulf · Active')
  assert.equal(describeAgencyFilters({ spec: '', country: '', activity: 'inactive' }), 'Not active')
})

test('active agencies come first, the others last, and each group keeps its incoming order', () => {
  const list = [agency('a'), agency('b', { activeAgency: true }), agency('c'), agency('d', { activeAgency: true }), { name: 'e' }]
  assert.deepEqual(names(activeFirst(list)), ['b', 'd', 'a', 'c', 'e'])
  assert.deepEqual(names(list), ['a', 'b', 'c', 'd', 'e']) // the input is not changed
  assert.deepEqual(activeFirst([]), [])
})

test('the agency form sends the Active agency choice and maps a server error to it', () => {
  const fields = { name: 'Priya', agencyName: 'Blue Ocean', phone: '', email: '', address: '', state: '' }
  assert.equal(form.buildAgencyPayload(fields, []).activeAgency, false)
  assert.equal(form.buildAgencyPayload(fields, [], form.emptyFocus(), true).activeAgency, true)
  const mapped = form.fieldErrorsFromApi([{ loc: ['body', 'activeAgency'], msg: 'Value error, Active agency: use yes or no.' }])
  assert.match(mapped.fields.activeAgency, /yes or no/)
})
