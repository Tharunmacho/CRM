import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/candidates/candidate-skills.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
})
const { normalizedSkills, skillFacets, matchesSkills } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('skill normalization merges casing, spacing and Unicode forms while preserving trade names', () => {
  assert.deepEqual(normalizedSkills([' TIG   Welding ', 'tig welding', 'ＴＩＧ Welding', '', 'C++', 'C#']), ['TIG Welding', 'C++', 'C#'])
})

test('facet counts are per candidate, including candidates with more than one skill', () => {
  assert.deepEqual(skillFacets([
    { skills: ['Welding', 'welding', 'Pipe fitting'] },
    { skills: [' WELDING '] },
    { skills: [] },
  ]), [
    { key: 'pipe fitting', label: 'Pipe fitting', count: 1 },
    { key: 'welding', label: 'Welding', count: 2 },
  ])
})

test('all and any modes use exact recorded skills, without substring industry guesses', () => {
  const candidate = { skills: ['JavaScript', 'TIG welding', 'C++'] }
  assert.equal(matchesSkills(candidate, ['tig WELDING', 'C++']), true)
  assert.equal(matchesSkills(candidate, ['Java']), false)
  assert.equal(matchesSkills(candidate, ['C']), false)
  assert.equal(matchesSkills(candidate, ['TIG welding', 'Pipe fitting'], 'all'), false)
  assert.equal(matchesSkills(candidate, ['TIG welding', 'Pipe fitting'], 'any'), true)
  assert.equal(matchesSkills({ skills: [] }, ['Welding']), false)
  assert.equal(matchesSkills({ skills: [] }, [], 'any'), true)
})
