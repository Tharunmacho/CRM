import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/** The helper imports the Clients page's fold and terms; the sources are joined into one module (imports dropped) so it loads on its own. */
async function load(...relatives) {
  const parts = await Promise.all(relatives.map(async (relative) => (await readFile(new URL(relative, import.meta.url), 'utf8')).replace(/^import .*$/gm, '')))
  const { outputText } = ts.transpileModule(parts.join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const m = await load('../src/features/clients/client-search.ts', '../src/features/jobs/job-search.ts')
const { matchesJob, statusWords, openingsText, openingsShare, legacyJobsQuery, matchesTerms, filterByText, STATUS_FILTERS, parseStatusFilter, passesFilters, hasJobFilters } = m

const job = {
  title: 'Welder (6G)', ref: 'ORD-2026-00002', contact: 'Khalid Rahman', company: 'Gulf Build Contracting LLC',
  country: 'United Arab Emirates', city: 'Dubai', statuses: statusWords('on_hold', false), skills: ['TIG', 'Blueprint reading'],
}

test('a blank query matches every job', () => {
  assert.equal(matchesJob(job, ''), true)
  assert.equal(matchesJob(job, '   '), true)
  assert.equal(matchesJob(job, undefined), true)
})

test('search is case-insensitive and finds the title, reference, contact, company, country, city and skills', () => {
  for (const query of ['WELDER', 'ord-2026-00002', '00002', 'khalid', 'gulf build', 'emirates', 'DUBAI', 'blueprint']) assert.equal(matchesJob(job, query), true, query)
  assert.equal(matchesJob(job, 'plumber'), false)
})

test('every term must match, separated by spaces or commas, in any field', () => {
  assert.equal(matchesJob(job, 'welder dubai'), true)
  assert.equal(matchesJob(job, 'welder, dubai, gulf'), true)
  assert.equal(matchesJob(job, 'welder qatar'), false)
  assert.equal(matchesJob(job, 'welder, qatar'), false)
})

test('accents are ignored in both directions', () => {
  assert.equal(matchesJob({ ...job, city: 'Zürich' }, 'zurich'), true)
  assert.equal(matchesJob({ ...job, city: 'Zurich' }, 'ZÜRICH'), true)
})

test('the status words the page shows can be searched', () => {
  const held = statusWords('on_hold', false)
  assert.deepEqual(statusWords('open', false), ['Open'])
  assert.deepEqual(held, ['On hold'])
  assert.equal(matchesJob({ title: 'Cook', statuses: held }, 'on hold'), true)
  assert.equal(matchesJob({ title: 'Cook', statuses: held }, 'open'), false)
  assert.equal(matchesJob({ title: 'Cook', statuses: statusWords('filled', false) }, 'filled'), true)
  assert.equal(matchesJob({ title: 'Cook', statuses: statusWords('closed', false) }, 'CLOSED'), true)
})

test('an open job past its deadline shows as overdue and still answers to open', () => {
  const late = statusWords('open', true)
  assert.deepEqual(late, ['Overdue', 'Open'])
  assert.equal(matchesJob({ title: 'Cook', statuses: late }, 'overdue'), true)
  assert.equal(matchesJob({ title: 'Cook', statuses: late }, 'open'), true)
})

test('openings read as "3 of 5 filled" with a share for the bar', () => {
  assert.equal(openingsText(3, 5), '3 of 5 filled')
  assert.equal(openingsText(0, 4), '0 of 4 filled')
  assert.equal(openingsText(9, 5), '5 of 5 filled')
  assert.equal(openingsShare(3, 5), 60)
  assert.equal(openingsShare(0, 4), 0)
  assert.equal(openingsShare(9, 5), 100)
  assert.equal(openingsShare(1, 0), 0)
})

test('old links become a search for the job title or the company', () => {
  const lookup = { designationTitle: (id) => ({ d1: 'Welder' })[id], companyName: (id) => ({ c1: 'Gulf Build' })[id] }
  assert.equal(legacyJobsQuery({ designation: 'd1' }, lookup), 'Welder')
  assert.equal(legacyJobsQuery({ company: 'c1' }, lookup), 'Gulf Build')
  assert.equal(legacyJobsQuery({ designation: 'd1', company: 'c1' }, lookup), 'Welder')
  assert.equal(legacyJobsQuery({ designation: 'gone', company: 'c1' }, lookup), 'Gulf Build')
  assert.equal(legacyJobsQuery({ designation: 'gone' }, lookup), '')
  assert.equal(legacyJobsQuery({}, lookup), '')
})

test('dropdown search: case and accent blind, every word, blank keeps everything in order', () => {
  const names = ['São Paulo', 'Dubai', 'Doha', 'Zürich']
  assert.deepEqual(filterByText(names, '', (n) => [n]), names)
  assert.deepEqual(filterByText(names, 'SAO', (n) => [n]), ['São Paulo'])
  assert.deepEqual(filterByText(names, 'zurich', (n) => [n]), ['Zürich'])
  assert.deepEqual(filterByText(names, 'd', (n) => [n]), ['São Paulo', 'Dubai', 'Doha'].filter((n) => /d/i.test(n)))
  assert.deepEqual(filterByText(names, 'nowhere', (n) => [n]), [])
  assert.equal(matchesTerms(['Gulf Build', 'Khalid Rahman'], 'rahman, gulf'), true)
  assert.equal(matchesTerms(['Gulf Build', 'Khalid Rahman'], 'rahman qatar'), false)
})

test('the status pills are the four statuses plus All, as the page labels them', () => {
  assert.deepEqual(STATUS_FILTERS.map((s) => s.label), ['All', 'Open', 'Filled', 'On hold', 'Closed'])
  assert.deepEqual(STATUS_FILTERS.map((s) => s.value), ['', 'open', 'filled', 'on_hold', 'closed'])
  assert.equal(parseStatusFilter('on_hold'), 'on_hold')
  assert.equal(parseStatusFilter('nonsense'), '')
  assert.equal(parseStatusFilter(null), '')
})

test('filters combine with each other and with the search text (AND)', () => {
  const jobs = [
    { title: 'Welder', status: 'open', companyId: 'co1' },
    { title: 'Welder', status: 'filled', companyId: 'co1' },
    { title: 'Cook', status: 'open', companyId: 'co2' },
    { title: 'Cook', status: 'on_hold', companyId: 'co1' },
  ]
  const run = (filters, query = '') => jobs.filter((j) => passesFilters(j, filters) && matchesJob({ title: j.title }, query)).map((j) => `${j.title}:${j.status}:${j.companyId}`)
  assert.equal(run({ status: '', client: '' }).length, 4)
  assert.deepEqual(run({ status: 'open', client: '' }), ['Welder:open:co1', 'Cook:open:co2'])
  assert.deepEqual(run({ status: '', client: 'co1' }), ['Welder:open:co1', 'Welder:filled:co1', 'Cook:on_hold:co1'])
  assert.deepEqual(run({ status: 'open', client: 'co1' }), ['Welder:open:co1'])
  assert.deepEqual(run({ status: 'open', client: 'co1' }, 'cook'), [])
  assert.deepEqual(run({ status: 'on_hold', client: 'co1' }, 'COOK'), ['Cook:on_hold:co1'])
  assert.deepEqual(run({ status: '', client: '' }, 'welder'), ['Welder:open:co1', 'Welder:filled:co1'])
})

test('a filter on its own shows the list; with none set the page stays search-first', () => {
  assert.equal(hasJobFilters({ status: '', client: '' }), false)
  assert.equal(hasJobFilters({ status: 'open', client: '' }), true)
  assert.equal(hasJobFilters({ status: '', client: 'co1' }), true)
})
