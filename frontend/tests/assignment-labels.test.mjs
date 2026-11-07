import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const labels = await load('../src/features/candidates/assignment-labels.ts')

test('an empty recruiter reads Not assigned and is flagged as muted', () => {
  assert.equal(labels.recruiterText(null), 'Not assigned')
  assert.equal(labels.recruiterText('   '), 'Not assigned')
  assert.equal(labels.recruiterText('Asha Nair'), 'Asha Nair')
  assert.equal(labels.isUnassigned(undefined), true)
  assert.equal(labels.isUnassigned('Asha Nair'), false)
})

test('the recruiter dropdown lists active staff and managers only, A to Z, never an admin', () => {
  const choices = labels.recruiterChoices([
    { id: 'a', name: 'Zed Staff', role: 'staff', active: true },
    { id: 'b', name: 'Amy Manager', role: 'manager', active: true },
    { id: 'c', name: 'Root Admin', role: 'admin', active: true },
    { id: 'd', name: 'Gone Staff', role: 'staff', active: false },
    { id: 'e', name: 'Lee Staff', role: 'staff', active: true, onLeave: true },
  ])
  assert.deepEqual(choices.map((c) => c.id), ['b', 'e', 'a'])
  assert.equal(choices[0].label, 'Amy Manager (manager)')
  assert.equal(choices[1].label, 'Lee Staff · on leave')
  assert.equal(labels.NOT_ASSIGNED_YET, 'Not assigned yet')
})

test('the save button says Save candidate until a recruiter is picked', () => {
  assert.equal(labels.saveButtonLabel(true, null), 'Save candidate')
  assert.equal(labels.saveButtonLabel(true, '  '), 'Save candidate')
  assert.equal(labels.saveButtonLabel(true, 'Asha Nair'), 'Save and assign to Asha')
  // A staff member never picks, so the label stays plain whatever name is passed.
  assert.equal(labels.saveButtonLabel(false, 'Asha Nair'), 'Save candidate')
})

test('the footer says who will own the candidate, or that nobody is chosen', () => {
  assert.equal(labels.savebarStatus(false), 'You will own this candidate.')
  assert.equal(labels.savebarStatus(true, null), 'No recruiter is chosen. An admin can assign one later.')
  assert.equal(labels.savebarStatus(true, 'Asha Nair'), 'Asha Nair will own this candidate.')
})

test('the follow-up hint mentions the recruiter to-do list only when a recruiter is chosen', () => {
  assert.doesNotMatch(labels.followUpHint(true, null), /recruiter’s to-do list/)
  assert.match(labels.followUpHint(true, 'Asha Nair'), /recruiter’s to-do list/)
  assert.match(labels.followUpHint(false), /your to-do list/)
})

test('the saved notice and step never talk about an automatic or free recruiter', () => {
  const all = [
    labels.savedNotice(true, null).text, labels.savedNotice(true, 'Asha Nair').text, labels.savedNotice(false, 'Me').text,
    labels.savedOwnerNote(true, null, false), labels.savedOwnerNote(true, 'Asha', false), labels.savedOwnerNote(false, 'Me', false), labels.savedOwnerNote(true, 'Asha', true),
    labels.saveStep(true, null).detail, labels.saveStep(true, 'Asha').detail, labels.saveStep(false).detail,
  ]
  for (const text of all) assert.doesNotMatch(text, /automatic|free recruiter/i)
  assert.equal(labels.savedNotice(true, null).tone, 'warn')
  assert.equal(labels.savedNotice(true, 'Asha').tone, undefined)
})

test('the automatic assignment switch is off by default and needs the latest server to be used', () => {
  assert.equal(labels.AUTO_ASSIGN_LABEL, 'Assign new candidates automatically')
  assert.equal(labels.AUTO_ASSIGN_HELPER, 'When off, new candidates wait for you to assign a recruiter.')
  const off = labels.autoAssignToggle({ autoAssign: false, editable: true }, true)
  assert.deepEqual([off.checked, off.disabled, off.note], [false, false, ''])
  const on = labels.autoAssignToggle({ autoAssign: true, editable: true }, true)
  assert.deepEqual([on.checked, on.disabled], [true, false])
  const old = labels.autoAssignToggle({ editable: true }, true)
  assert.deepEqual([old.checked, old.disabled, old.note], [false, true, 'Needs the latest server'])
  const staff = labels.autoAssignToggle({ autoAssign: true, editable: true }, false)
  assert.equal(staff.disabled, true)
  assert.match(staff.note, /admin/)
  assert.equal(labels.autoAssignValue(undefined), undefined)
  assert.equal(labels.autoAssignValue({ autoAssign: 'yes' }), undefined)
})

test('the per-person flag is muted with a note while automatic assignment is off', () => {
  assert.deepEqual(labels.receivesFlagText(true), { label: 'Can be given new candidates automatically', muted: false, note: '' })
  for (const state of [false, undefined]) {
    const flag = labels.receivesFlagText(state)
    assert.equal(flag.muted, true)
    assert.equal(flag.note, 'Automatic assignment is off')
  }
})

test('no screen the setting touches still promises an automatic recruiter', async () => {
  for (const file of ['../src/screens/candidates/add-candidate.tsx', '../src/screens/users/users.tsx', '../src/screens/users/user-dialog.tsx', '../src/features/candidates/data-bank-pools.ts']) {
    const text = await readFile(new URL(file, import.meta.url), 'utf8')
    assert.doesNotMatch(text, /Leave on automatic|a free recruiter|Automatic: a free/, file)
  }
})
