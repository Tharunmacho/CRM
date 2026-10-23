import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function loadModule(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  const resolved = outputText.replace(/from (['"])date-fns\1/g, `from ${JSON.stringify(import.meta.resolve('date-fns'))}`)
  return import(`data:text/javascript;base64,${Buffer.from(resolved).toString('base64')}`)
}
const { taskViewFromParams, completedInPeriod, validDateFilter } = await loadModule('../src/features/staff/task-filters.ts')
const { canReceiveManualAssignment, manualAssignmentLabel } = await loadModule('../src/features/staff/availability-model.ts')
const { slaState } = await loadModule('../src/shared/lib/format.ts')

test('task overdue boundary matches the backend deadline strictly before now rule', () => {
  const start = '2026-10-20T10:00:00Z'
  const deadline = new Date('2026-10-22T10:00:00Z')
  assert.equal(slaState(start, new Date(deadline.getTime() - 1)).overdue, false)
  const atDeadline = slaState(start, deadline)
  assert.equal(atDeadline.overdue, false)
  assert.equal(atDeadline.urgency, 'due')
  assert.equal(atDeadline.label, '0m left')
  const afterDeadline = slaState(start, new Date(deadline.getTime() + 1))
  assert.equal(afterDeadline.overdue, true)
  assert.equal(afterDeadline.urgency, 'over')
})

test('directory task links retain the requested view and scope', () => {
  for (const view of ['open', 'overdue', 'escalated', 'initial', 'missing', 'completed']) {
    const params = new URLSearchParams({ staff: 'recruiter-2', view, from: '2026-10-01', to: '2026-10-21' })
    assert.equal(taskViewFromParams(params, true), view)
    params.set('staff', 'recruiter-3')
    assert.equal(taskViewFromParams(params, true), view, 'changing staff must not reset the selected tab')
  }
  assert.equal(taskViewFromParams(new URLSearchParams('view=escalated'), false), 'open')
  assert.equal(taskViewFromParams(new URLSearchParams('view=unknown'), true), 'open')
})

test('historical missing updates retain date semantics only for supervisors', () => {
  const params = new URLSearchParams('date=2026-10-20&view=missing')
  assert.equal(taskViewFromParams(params, true), 'history')
  assert.equal(taskViewFromParams(params, false), 'missing')
})

test('a completed notification task opens its completed view without overriding an explicit tab', () => {
  const closed = { completedAt: '2026-10-21T12:00:00Z' }
  assert.equal(taskViewFromParams(new URLSearchParams('task=closed-task'), true, closed), 'completed')
  assert.equal(taskViewFromParams(new URLSearchParams('task=closed-task&view=open'), true, closed), 'open')
  assert.equal(taskViewFromParams(new URLSearchParams('task=open-task'), true), 'open')
})

test('completed task period is inclusive in the agency timezone', () => {
  const matches = value => completedInPeriod(value, '2026-10-21', '2026-10-21', 'Asia/Kolkata')
  assert.equal(matches('2026-10-20T18:29:59Z'), false)
  assert.equal(matches('2026-10-20T18:30:00Z'), true)
  assert.equal(matches('2026-10-21T18:29:59Z'), true)
  assert.equal(matches('2026-10-21T18:30:00Z'), false)
  assert.equal(matches(undefined), false)
  assert.equal(completedInPeriod('2026-10-21T12:00:00Z', '', '', 'Asia/Kolkata'), true)
})

test('invalid and normalized-overflow dates cannot silently become completion filters', () => {
  assert.equal(validDateFilter('2026-02-30'), '')
  assert.equal(validDateFilter('10/21/2026'), '')
  assert.equal(validDateFilter(null), '')
  assert.equal(validDateFilter('2028-02-29'), '2028-02-29')
})

test('manual handover includes paused rotation but excludes approved leave, manual leave and inactive staff', () => {
  const person = status => ({ id: 'staff', name: 'Recruiter', role: 'staff', active: true, availability: { status, label: status, eligible: status === 'available' } })
  assert.equal(canReceiveManualAssignment(person('available')), true)
  assert.equal(canReceiveManualAssignment(person('paused')), true)
  for (const status of ['approved_leave', 'manual_leave', 'inactive', 'not_assigned']) assert.equal(canReceiveManualAssignment(person(status)), false)
  assert.equal(canReceiveManualAssignment({ ...person('available'), active: false }), false)
  assert.equal(canReceiveManualAssignment({ ...person('available'), role: 'manager' }), false)
  assert.match(manualAssignmentLabel(person('paused')), /available for manual assignment/)
})
