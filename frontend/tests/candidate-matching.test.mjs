import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

function moduleUrl(source) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}

const skillsUrl = moduleUrl(await readFile(new URL('../src/features/candidates/candidate-skills.ts', import.meta.url), 'utf8'))
const source = await readFile(new URL('../src/features/jobs/candidate-matching.ts', import.meta.url), 'utf8')
const { matchesCandidate } = await import(moduleUrl(source.replace('@/features/candidates/candidate-skills', skillsUrl)))
const candidate = {
  name: 'Ravi Kumar', ref: 'CAN-009', phone: '9876543210', email: 'ravi@example.test',
  passportNo: 'P123456', currentCity: 'New Delhi', designations: ['TIG Welder'],
  totalExperience: 3.5, skills: ['TIG Welding', 'Pipe Fitting'],
  ownerId: 'staff-2', resumeText: 'Experience operating a crane.',
}
const filters = {
  designation: '', minExperience: '', passport: 'any', owner: 'all', location: '', skills: [], skillMode: 'all',
}

test('all order filters apply together using recorded facts and normalized labels', () => {
  const selected = { ...filters, designation: ' tig  welder ', minExperience: '3.5', passport: 'present', owner: 'staff-2', location: ' DELHI ', skills: ['tig welding', 'pipe fitting'] }
  assert.equal(matchesCandidate(candidate, selected, 'CAN-009'), true)
  for (const changed of [
    { designation: 'Welder' }, { minExperience: '4' }, { passport: 'missing' },
    { owner: 'staff-1' }, { location: 'Mumbai' }, { skills: ['TIG Welding', 'CNC'] },
  ]) assert.equal(matchesCandidate(candidate, { ...selected, ...changed }, ''), false)
})

test('skill matching distinguishes exact skills from resume search and supports any/all', () => {
  assert.equal(matchesCandidate(candidate, { ...filters, skills: ['Welding'] }, ''), false)
  assert.equal(matchesCandidate(candidate, { ...filters, skills: ['crane'] }, ''), false)
  assert.equal(matchesCandidate(candidate, filters, 'crane'), true)
  assert.equal(matchesCandidate(candidate, { ...filters, skills: ['tig welding', 'CNC'], skillMode: 'any' }, ''), true)
  assert.equal(matchesCandidate(candidate, { ...filters, skills: ['tig welding', 'CNC'], skillMode: 'all' }, ''), false)
})

test('blank passport values are missing and invalid experience filters cannot match', () => {
  const missingPassport = { ...candidate, passportNo: '   ' }
  assert.equal(matchesCandidate(missingPassport, { ...filters, passport: 'missing' }, ''), true)
  assert.equal(matchesCandidate(missingPassport, { ...filters, passport: 'present' }, ''), false)
  for (const minimum of ['-1', 'invalid', 'Infinity']) {
    assert.equal(matchesCandidate(candidate, { ...filters, minExperience: minimum }, ''), false)
  }
})

test('contact, passport, designation, and location are searchable without inventing facts', () => {
  for (const query of ['ravi@example.test', 'P123456', 'tig   welder', 'new delhi', '9876543210']) {
    assert.equal(matchesCandidate(candidate, filters, query), true)
  }
  assert.equal(matchesCandidate(candidate, filters, 'Java developer'), false)
})
