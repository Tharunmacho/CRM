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

const designations = [
  { id: 'd1', title: 'Welder (6G)', category: 'Trades', description: 'Pipe welding', requiredSkills: ['TIG', 'MIG'], minExperience: 3 },
  { id: 'd2', title: 'Cook', category: null, description: null, requiredSkills: [], minExperience: 0 },
]
const reqOf = (id) => f.requirementsOf(designations.find((d) => d.id === id))

test('choosing a saved title fills in its requirements; a new title starts empty', () => {
  assert.deepEqual(reqOf('d1'), { skills: ['TIG', 'MIG'], minExperience: '3', description: 'Pipe welding' })
  assert.deepEqual(reqOf('d2'), { skills: [], minExperience: '0', description: '' })
  assert.deepEqual(f.requirementsOf(null), { skills: [], minExperience: '0', description: '' })
})

test('skills are tidied and suggestions come from the skills already in use', () => {
  assert.deepEqual(f.cleanSkills([' TIG ', 'tig', 'Blueprint   reading', '']), ['TIG', 'Blueprint reading'])
  const list = f.skillSuggestions([{ requiredSkills: ['TIG', 'MIG'] }, { requiredSkills: ['tig', 'Rigging'] }, { requiredSkills: undefined }])
  assert.deepEqual(list, ['TIG', 'MIG', 'Rigging'])
})

test('requirements are compared ignoring skill case and order', () => {
  const same = { skills: ['mig', 'tig'], minExperience: '3', description: ' Pipe welding ' }
  assert.equal(f.sameRequirements(reqOf('d1'), same), true)
  assert.equal(f.sameRequirements(reqOf('d1'), { ...same, skills: ['tig'] }), false)
  assert.equal(f.sameRequirements(reqOf('d1'), { ...same, minExperience: '4' }), false)
  assert.equal(f.sameRequirements(reqOf('d1'), { ...same, description: 'Other' }), false)
})

test('saving a saved title unchanged just uses it', () => {
  const form = { title: 'welder (6g)', ...reqOf('d1') }
  assert.deepEqual(f.planDesignation(form, designations), { kind: 'use', id: 'd1' })
})

test('changing the requirements of a saved title updates that title with the full body', () => {
  const form = { title: 'WELDER (6G)', skills: ['TIG', 'MIG', ' Rigging '], minExperience: '5', description: ' New text ' }
  assert.deepEqual(f.planDesignation(form, designations), {
    kind: 'update', id: 'd1',
    body: { title: 'Welder (6G)', category: 'Trades', description: 'New text', requiredSkills: ['TIG', 'MIG', 'Rigging'], minExperience: 5 },
  })
})

test('a new title is created with the requirements typed', () => {
  const form = { title: '  Tower   Crane Operator ', skills: ['Crane'], minExperience: '2', description: '' }
  assert.deepEqual(f.planDesignation(form, designations), {
    kind: 'create', body: { title: 'Tower Crane Operator', category: null, description: '', requiredSkills: ['Crane'], minExperience: 2 },
  })
  assert.deepEqual(f.planDesignation({ ...form, title: '  ' }, designations), { kind: 'none' })
})

test('a title added by this dialog is used on the next try, not added again', () => {
  const created = new Map([['tower crane operator', 'd9']])
  assert.deepEqual(f.planDesignation({ title: 'Tower Crane Operator', skills: [], minExperience: '0', description: '' }, designations, created), { kind: 'use', id: 'd9' })
})

test('minimum experience and skills are validated', () => {
  const ok = { ...f.emptyJobForm('co1', 'AED'), title: 'Cook' }
  assert.deepEqual(f.validateJob(ok), {})
  assert.match(f.validateJob({ ...ok, minExperience: '61' }).minExperience, /0 to 60/)
  assert.match(f.validateJob({ ...ok, minExperience: '-1' }).minExperience, /0 to 60/)
  assert.match(f.validateJob({ ...ok, minExperience: '1.5' }).minExperience, /whole years/)
  assert.match(f.validateJob({ ...ok, minExperience: '' }).minExperience, /whole years/)
  assert.match(f.validateJob({ ...ok, skills: Array.from({ length: 101 }, (_, i) => `s${i}`) }).skills, /100/)
})

test('"Save and add another job" clears the requirements with the rest', () => {
  const next = f.nextJobForm({ ...f.emptyJobForm('co1', 'AED'), title: 'Cook', skills: ['Knife'], minExperience: '4', description: 'x' })
  assert.deepEqual([next.skills, next.minExperience, next.description, next.companyId], [[], '0', '', 'co1'])
})

test('server errors on the requirements land on their boxes', () => {
  const a = f.jobErrorsFromApi([
    { loc: ['body', 'requiredSkills'], msg: 'List should have at most 100 items' },
    { loc: ['body', 'minExperience'], msg: 'Input should be less than or equal to 60' },
    { loc: ['body', 'title'], msg: 'String should have at least 1 character' },
  ])
  assert.deepEqual(Object.keys(a.fields).sort(), ['minExperience', 'skills', 'title'])
})
