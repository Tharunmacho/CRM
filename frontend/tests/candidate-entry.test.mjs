import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/candidates/entry-model.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { EMPTY_CANDIDATE_FORM, candidateEntryErrors, restoredCandidateDraft } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const valid = { ...EMPTY_CANDIDATE_FORM, name: 'Reviewed candidate', phone: '+91 90000 00020' }

test('entry validation keeps backend identity, experience and date constraints visible before save', () => {
  assert.deepEqual(candidateEntryErrors(valid, '2026-10-22'), {})
  const errors = candidateEntryErrors({ ...valid, phone: '123', dob: '2026-10-23', totalExperience: '2', gulfExperience: '3',
    passportIssue: '2025-05-01', passportExpiry: '2025-04-30', expectedSalary: '-1' }, '2026-10-22')
  assert.deepEqual(Object.keys(errors).sort(), ['dob', 'expectedSalary', 'gulfExperience', 'passportExpiry', 'phone'])
  assert.ok(candidateEntryErrors({ ...valid, totalExperience: '81' }).totalExperience)
  assert.ok(candidateEntryErrors({ ...valid, gulfExperience: '1.5' }).gulfExperience)
  assert.deepEqual(candidateEntryErrors({ ...valid, phone: '９０００００００２０', totalExperience: '0', gulfExperience: '0' }), {})
})

test('restoring an intake uses only its saved form and preserves its own deliberately cleared fields', () => {
  const saved = { form: { name: 'Candidate B', phone: '', skills: 'TIG welding', unexpected: 'ignored' }, editedFields: ['name', 'phone', 'unexpected'] }
  const restored = restoredCandidateDraft(saved)
  assert.equal(restored.form.name, 'Candidate B')
  assert.equal(restored.form.phone, '')
  assert.equal(restored.form.skills, 'TIG welding')
  assert.equal(restored.form.nationality, '')
  assert.equal(restored.form.unexpected, undefined)
  assert.deepEqual([...restored.edited], ['name', 'phone'])
  assert.equal(EMPTY_CANDIDATE_FORM.name, '')
  assert.deepEqual(restoredCandidateDraft().form, EMPTY_CANDIDATE_FORM)
})
