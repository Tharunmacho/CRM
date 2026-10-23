import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const skills = await readFile(new URL('../src/features/candidates/candidate-skills.ts', import.meta.url), 'utf8')
const evidence = await readFile(new URL('../src/features/jobs/order-profile-evidence.ts', import.meta.url), 'utf8')
const source = `${skills}\n${evidence.replace(/^import .*$/gm, '')}`
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { orderProfileEvidence } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const designation = { title: 'Welder', requiredSkills: ['TIG welding', 'Blueprint reading'], minExperience: 3 }

test('matching designation does not imply skill or experience suitability', () => {
  const result = orderProfileEvidence({ designations: ['Welder'], skills: [], totalExperience: 1 }, designation)
  assert.equal(result.designationMatches, true)
  assert.equal(result.experienceMeets, false)
  assert.deepEqual(result.matchedSkills, [])
  assert.deepEqual(result.missingSkills, designation.requiredSkills)
})

test('saved skill evidence normalizes case, Unicode and whitespace but never guesses aliases or substrings', () => {
  const result = orderProfileEvidence({ designations: ['  WELDER  '], skills: ['ＴＩＧ   welding', 'Blueprint'], totalExperience: 3 }, { ...designation, requiredSkills: [' TIG welding ', 'tig WELDING', 'Blueprint reading'] })
  assert.equal(result.designationMatches, true)
  assert.equal(result.experienceMeets, true)
  assert.deepEqual(result.matchedSkills, ['TIG welding'])
  assert.deepEqual(result.missingSkills, ['Blueprint reading'])
  assert.equal(result.requiredSkills.length, 2)
})

test('no listed requirements stays distinct from matching all recorded skills', () => {
  const result = orderProfileEvidence({ designations: [], skills: ['Welding'], totalExperience: 0 }, { title: 'Welder', requiredSkills: [], minExperience: 0 })
  assert.equal(result.designationMatches, false)
  assert.deepEqual(result.requiredSkills, [])
  assert.deepEqual(result.matchedSkills, [])
  assert.equal(result.experienceMeets, true)
})
