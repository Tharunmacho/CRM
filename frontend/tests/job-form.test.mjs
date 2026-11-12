import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(...relatives) {
  const parts = await Promise.all(relatives.map(async (relative) => (await readFile(new URL(relative, import.meta.url), 'utf8')).replace(/^import .*$/gm, '')))
  const { outputText } = ts.transpileModule(parts.join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const f = await load('../src/features/clients/client-search.ts', '../src/features/jobs/job-search.ts', '../src/features/jobs/job-form.ts')

const designations = [{ id: 'd1', title: 'Welder (6G)' }, { id: 'd2', title: 'Cook' }]

test('a title that is saved is reused, whatever its case or spacing', () => {
  assert.deepEqual(f.decideTitle('cook', designations), { kind: 'existing', id: 'd2', title: 'Cook' })
  assert.deepEqual(f.decideTitle('  WELDER   (6G) ', designations), { kind: 'existing', id: 'd1', title: 'Welder (6G)' })
})

test('a title that is not saved is new, with its spaces collapsed, and an empty one is empty', () => {
  assert.deepEqual(f.decideTitle('  Tower   Crane Operator ', designations), { kind: 'new', title: 'Tower Crane Operator' })
  assert.deepEqual(f.decideTitle('   ', designations), { kind: 'empty' })
  assert.deepEqual(f.decideTitle('', []), { kind: 'empty' })
})

test('a title this dialog already added is reused on the next try, never added twice', () => {
  const created = new Map([['tower crane operator', 'd9']])
  assert.deepEqual(f.decideTitle('Tower crane  operator', designations, created), { kind: 'existing', id: 'd9', title: 'Tower crane operator' })
  assert.equal(f.cleanTitle(' a   b '), 'a b')
  assert.equal(f.titleKey(' A   B '), 'a b')
})

const clients = [
  { id: 'cl1', name: 'Khalid Rahman', status: 'active', country: 'UAE', city: 'Dubai' },
  { id: 'cl2', name: 'Anita Rao', status: 'active' },
  { id: 'cl3', name: 'Zoe Hart', status: 'active' },
  { id: 'cl4', name: 'Old Contact', status: 'inactive' },
  { id: 'cl5', name: 'No Company', status: 'active' },
]
const companies = [
  { id: 'co1', clientId: 'cl1', name: 'Gulf Build', status: 'active', country: 'UAE', city: 'Dubai' },
  { id: 'co2', clientId: 'cl2', name: 'Kerala Foods', status: 'active' },
  { id: 'co3', clientId: 'cl2', name: 'Kerala Catering', status: 'active', country: 'India', city: 'Kochi' },
  { id: 'co4', clientId: 'cl3', name: 'Zed Marine', status: 'active' },
  { id: 'co5', clientId: 'cl4', name: 'Dormant Co', status: 'active' },
  { id: 'co6', clientId: 'cl3', name: 'Zed Closed', status: 'inactive' },
]
const orders = [{ companyId: 'co1' }, { companyId: 'co1' }, { companyId: 'co1' }, { companyId: 'co2' }, { companyId: 'co3' }, { companyId: 'co3' }, { companyId: 'co5' }]

test('every company is its own entry: "Company (contact)", sorted by company', () => {
  const options = f.buildClientOptions({ clients, companies, orders })
  assert.deepEqual(options.map((o) => f.optionLabel(o)), ['Gulf Build (Khalid Rahman)', 'Kerala Catering (Anita Rao)', 'Kerala Foods (Anita Rao)', 'Zed Marine (Zoe Hart)'])
  assert.deepEqual(options.map((o) => o.companyId), ['co1', 'co3', 'co2', 'co4'])
})

test('a client with several companies appears once per company', () => {
  const options = f.buildClientOptions({ clients, companies, orders })
  assert.equal(options.filter((o) => o.clientId === 'cl2').length, 2)
})

test('the job count is shown per entry and a client with jobs stays selectable (0, 1 and many)', () => {
  const options = f.buildClientOptions({ clients, companies, orders })
  const by = Object.fromEntries(options.map((o) => [o.companyId, o]))
  assert.equal(by.co4.jobs, 0)
  assert.equal(by.co2.jobs, 1)
  assert.equal(by.co1.jobs, 3)
  assert.equal(by.co3.jobs, 2)
  // the client's own total spans its companies
  assert.equal(by.co2.clientJobs, 3)
  assert.equal(by.co3.clientJobs, 3)
  assert.equal(by.co4.clientJobs, 0)
  assert.equal(f.jobsLabel(0), 'No jobs yet')
  assert.equal(f.jobsLabel(1), '1 job')
  assert.equal(f.jobsLabel(3), '3 jobs')
})

test('inactive clients and companies, and clients without a company, are left out unless already chosen', () => {
  const ids = (keep) => f.buildClientOptions({ clients, companies, orders, keepCompanyId: keep }).map((o) => o.companyId)
  assert.deepEqual(ids(undefined).sort(), ['co1', 'co2', 'co3', 'co4'])
  assert.ok(ids('co5').includes('co5'))
  assert.ok(ids('co6').includes('co6'))
})

test('the place of an entry falls back from the company to the client', () => {
  const options = f.buildClientOptions({ clients, companies, orders })
  const plain = options.find((x) => x.companyId === 'co2')
  assert.deepEqual([plain.country, plain.city], ['', ''])
  const gulf = options.find((x) => x.companyId === 'co1')
  assert.deepEqual([gulf.country, gulf.city], ['UAE', 'Dubai'])
})

test('typing narrows the clients by company, contact person, country or city, case and accent blind, every word', () => {
  const options = f.buildClientOptions({ clients, companies, orders })
  const names = (q) => f.filterClientOptions(options, q).map((o) => o.company)
  assert.deepEqual(names(''), ['Gulf Build', 'Kerala Catering', 'Kerala Foods', 'Zed Marine'])
  assert.deepEqual(names('KERALA'), ['Kerala Catering', 'Kerala Foods'])
  assert.deepEqual(names('anita'), ['Kerala Catering', 'Kerala Foods'])
  assert.deepEqual(names('kerala cat'), ['Kerala Catering'])
  assert.deepEqual(names('kochi'), ['Kerala Catering'])
  assert.deepEqual(names('rahman, dubai'), ['Gulf Build'])
  assert.deepEqual(names('nothing'), [])
})

test('the client filter lists only the companies that have jobs, inactive ones too', () => {
  const options = f.clientFilterOptions({ clients, companies, orders })
  assert.deepEqual(options.map((o) => o.companyId), ['co5', 'co1', 'co3', 'co2'])
  assert.deepEqual(options.map((o) => o.jobs), [1, 3, 2, 1])
  assert.deepEqual(f.clientFilterOptions({ clients, companies, orders: [] }), [])
})

test('the form validates in plain English', () => {
  const empty = f.emptyJobForm('', 'AED')
  const errors = f.validateJob(empty)
  assert.deepEqual(Object.keys(errors).sort(), ['companyId', 'title'])
  assert.match(errors.companyId, /client/)
  assert.match(errors.title, /job title/)
  const ok = { ...empty, companyId: 'co1', title: 'Cook' }
  assert.deepEqual(f.validateJob(ok), {})
  assert.match(f.validateJob({ ...ok, openings: '0' }).openings, /at least 1/)
  assert.match(f.validateJob({ ...ok, openings: '2.5' }).openings, /whole number/)
  assert.match(f.validateJob({ ...ok, openings: '' }).openings, /whole number/)
  assert.match(f.validateJob({ ...ok, salary: '-5' }).salary, /salary/i)
  assert.deepEqual(f.validateJob({ ...ok, salary: '1800.50' }), {})
  assert.match(f.validateJob({ ...ok, currency: '' }).currency, /currency/i)
  assert.match(f.validateJob({ ...ok, interviewDate: '2026-12-10', deadline: '2026-12-01' }).interviewDate, /on or before/)
  assert.deepEqual(f.validateJob({ ...ok, interviewDate: '2026-12-01', deadline: '2026-12-01' }), {})
})

test('editing: openings cannot go below the deployed seats, Filled and Closed follow the candidates', () => {
  const ok = { ...f.emptyJobForm('co1', 'AED'), title: 'Cook', openings: '2' }
  assert.match(f.validateJob(ok, { deployed: 3, active: 0 }).openings, /3 people are already deployed/)
  assert.match(f.validateJob({ ...ok, openings: '5', status: 'filled' }, { deployed: 3, active: 0 }).status, /every opening/)
  assert.deepEqual(f.validateJob({ ...ok, openings: '3', status: 'filled' }, { deployed: 3, active: 0 }), {})
  assert.match(f.validateJob({ ...ok, status: 'closed' }, { deployed: 0, active: 1 }).status, /active candidates/)
})

test('the request body is trimmed, with empty salary and dates sent as null and benefits as a list', () => {
  const body = f.buildJobPayload({
    companyId: 'co1', title: 'Cook', openings: '4', country: ' UAE ', city: ' Dubai ', salary: '', currency: 'AED', deadline: '', benefits: 'Food,  Accommodation , ',
    contractPeriod: ' 2 years ', interviewDate: '', status: 'open',
  }, 'd2')
  assert.deepEqual(body, {
    companyId: 'co1', designationId: 'd2', openings: 4, country: 'UAE', city: 'Dubai', salary: null, currency: 'AED', benefits: ['Food', 'Accommodation'],
    contractPeriod: '2 years', interviewDate: null, deadline: null, status: 'open',
  })
  assert.equal(f.buildJobPayload({ ...f.emptyJobForm('co1', 'AED'), salary: '1800.5' }, 'd1').salary, 1800.5)
  assert.equal(f.buildJobPayload({ ...f.emptyJobForm('co1', 'AED'), deadline: '2026-12-31' }, 'd1').deadline, '2026-12-31')
  assert.equal(f.buildJobPayload({ ...f.emptyJobForm('co1', 'AED'), interviewDate: '2026-12-10' }, 'd1').interviewDate, '2026-12-10')
})

test('each new job starts blank and cannot inherit values or skills from an earlier draft', () => {
  const previous = f.emptyJobForm('co1', 'AED')
  Object.assign(previous, { title: 'Cook', country: 'UAE', city: 'Dubai', salary: '1800', benefits: 'Food', interviewDate: '2026-12-01' })
  previous.skills.push('Cooking')
  const next = f.emptyJobForm('', 'AED')
  for (const key of ['companyId', 'title', 'country', 'city', 'salary', 'benefits', 'contractPeriod', 'interviewDate', 'deadline']) assert.equal(next[key], '')
  assert.deepEqual(next.skills, [])
  assert.equal(next.openings, '1')
  assert.equal(next.currency, 'AED')
})

test('server errors land on the right box', () => {
  const unprocessable = [
    { loc: ['body', 'openings'], msg: 'Input should be greater than or equal to 1' },
    { loc: ['body', 'designationId'], msg: 'Field required' },
    { loc: ['body', 'interviewDate'], msg: 'Value error, The interview date must be on or before the order deadline' },
    { loc: ['body', 'surprise'], msg: 'Something else' },
  ]
  const a = f.jobErrorsFromApi(unprocessable)
  assert.equal(a.fields.openings, 'Input should be greater than or equal to 1.')
  assert.equal(a.fields.title, 'Field required.')
  assert.equal(a.fields.interviewDate, 'The interview date must be on or before the order deadline.')
  assert.equal(a.general, 'Something else.')
  assert.equal(f.jobErrorsFromApi('Activate the company and its client before opening an order').fields.companyId, 'Activate the company and its client before opening an order.')
  assert.ok(f.jobErrorsFromApi('This order already has 3 deployments; openings cannot be lower').fields.openings)
  assert.ok(f.jobErrorsFromApi('All openings are filled. Add more openings before you reopen this order').fields.openings)
  assert.ok(f.jobErrorsFromApi('Unmap or finish the active candidates before closing this order').fields.status)
  assert.deepEqual(f.jobErrorsFromApi('The record could not be saved. Please try again.'), { fields: {}, general: 'The record could not be saved. Please try again.' })
  assert.deepEqual(f.jobErrorsFromApi(undefined, ''), { fields: {}, general: '' })
  assert.equal(f.jobErrorsFromApi(undefined, 'Network down').general, 'Network down.')
})
