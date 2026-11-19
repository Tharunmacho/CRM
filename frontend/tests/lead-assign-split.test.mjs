import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'
import { withRoleLabels } from './fixtures/role-labels-url.mjs'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(withRoleLabels(js)).toString('base64')}`)
}
const text = (path) => readFile(new URL(path, import.meta.url), 'utf8')

const b = await load('../src/features/leads/lead-bulk-model.ts')
const model = await load('../src/features/leads/lead-model.ts')

// The same rule as backend/app/services/lead_import.py split_equally: everyone gets total // n, the rest go one each.

test('the split helper gives equal shares that differ by at most one, biggest first', () => {
  assert.deepEqual(b.splitSizes(10, 3), [4, 3, 3])
  assert.deepEqual(b.splitSizes(7, 2), [4, 3])
  assert.deepEqual(b.splitSizes(9, 3), [3, 3, 3])
  assert.deepEqual(b.splitSizes(2, 3), [1, 1, 0])
  assert.deepEqual(b.splitSizes(0, 3), [0, 0, 0])
  assert.deepEqual(b.splitSizes(5, 1), [5])
  assert.deepEqual(b.splitSizes(5, 0), [])
  for (let total = 0; total < 60; total += 1) {
    for (let people = 1; people < 9; people += 1) {
      const sizes = b.splitSizes(total, people)
      assert.equal(sizes.reduce((a, c) => a + c, 0), total)
      assert.ok(Math.max(...sizes) - Math.min(...sizes) <= 1)
      assert.deepEqual(sizes, [...sizes].sort((x, y) => y - x))
    }
  }
})

test('the live summary line says how the leads split', () => {
  assert.equal(b.splitSummary(10, 3, 5), '10 leads over 3 recruiters: 4, 3, 3')
  assert.equal(b.splitSummary(1, 2, 5), '1 lead over 2 recruiters: 1, 0')
  assert.equal(b.splitSummary(10, 1, 5), '10 leads to 1 recruiter.')
  assert.equal(b.splitSummary(10, 0, 4), 'Nobody ticked, so 10 leads will be spread between all 4 available recruiters, balancing their current workload.')
  assert.equal(b.splitSummary(10, 0, 0), 'No recruiter is available right now.')
  assert.equal(b.splitSummary(null, 3, 5), 'The leads will be shared equally between 3 recruiters.')
  assert.equal(b.splitSummary(3000, 3, 5), '3,000 leads over 3 recruiters: 1,000, 1,000, 1,000')
  for (const line of [b.splitSummary(10, 3, 5), b.splitSummary(10, 0, 4), b.splitSummary(10, 0, 0), b.splitSummary(10, 1, 4)]) assert.doesNotMatch(line, /staff/i, 'visible text says recruiter')
})

test('ticking, clearing and the available list', () => {
  assert.deepEqual(b.toggleRecruiter([], 'a', true), ['a'])
  assert.deepEqual(b.toggleRecruiter(['a'], 'a', true), ['a'], 'never twice')
  assert.deepEqual(b.toggleRecruiter(['a', 'b'], 'a', false), ['b'])
  const users = [
    { id: 'a', role: 'staff', active: true, onLeave: false }, { id: 'away', role: 'staff', active: true, onLeave: true },
    { id: 'm', role: 'manager', active: true, onLeave: false }, { id: 'gone', role: 'staff', active: false, onLeave: false },
  ]
  assert.deepEqual(b.availableRecruiters(users).map((u) => u.id), ['a'])
})

test('the request: one or more recruiters ticked are sent as assigneeIds, nothing ticked keeps the evenly shortcut', () => {
  assert.deepEqual(b.assignChoice([]), { ownerId: 'auto' })
  assert.deepEqual(b.assignChoice(['a']), { assigneeIds: ['a'] })
  assert.deepEqual(b.assignChoice(['b', 'a', 'b']), { assigneeIds: ['b', 'a'] })
  const filter = { q: 'x' }
  assert.deepEqual(b.bulkBody({ ids: ['l1', 'l2'], allMatching: false }, filter, 2, 'assign', { assigneeIds: ['a', 'b'] }), { action: 'assign', ids: ['l1', 'l2'], assigneeIds: ['a', 'b'] })
  assert.deepEqual(b.bulkBody({ ids: [], allMatching: true }, filter, 3000, 'assign', { assigneeIds: ['a', 'b', 'c'] }), { action: 'assign', filter, expectedCount: 3000, assigneeIds: ['a', 'b', 'c'] })
  assert.deepEqual(b.bulkBody({ ids: ['l1'], allMatching: false }, filter, 1, 'assign', { ownerId: 'auto' }), { action: 'assign', ids: ['l1'], ownerId: 'auto' }, 'the old payload still works')
  assert.deepEqual(b.bulkBody({ ids: ['l1'], allMatching: false }, filter, 1, 'assign', { ownerId: b.UNASSIGNED }), { action: 'assign', ids: ['l1'], ownerId: 'none' })
})

test('the upload sends the recruiters ticked as a JSON list, and one id or auto as before', () => {
  assert.deepEqual(b.uploadAssignment(true, true, []), {}, 'nobody yet')
  assert.deepEqual(b.uploadAssignment(true, true, ['a']), {}, 'nobody yet wins over stale ticks')
  assert.deepEqual(b.uploadAssignment(true, false, []), { assignTo: 'auto' })
  assert.deepEqual(b.uploadAssignment(true, false, ['a', 'b']), { assigneeIds: ['a', 'b'] })
  assert.deepEqual(b.uploadAssignment(false, false, ['a']), {}, 'only an admin hands out leads')
  assert.deepEqual(model.importFormFields({ dryRun: false, ...b.uploadAssignment(true, false, ['a', 'b', 'c']) }), { dry_run: 'false', assignee_ids: '["a","b","c"]' })
  assert.deepEqual(model.importFormFields({ dryRun: false, assignTo: 'auto' }), { dry_run: 'false', assign_to: 'auto' })
  assert.deepEqual(model.importFormFields({ dryRun: false, assignTo: 'u1' }), { dry_run: 'false', assign_to: 'u1' })
  assert.deepEqual(model.importFormFields({ dryRun: true, assigneeIds: [] }), { dry_run: 'true' })
})

test('the confirmation and the toast name who got how many', () => {
  const copy = b.confirmCopy({ action: 'assign', count: 10, allMatching: false, ownerName: 'Asha', recruiterNames: ['Asha', 'Ravi', 'Mina'] })
  assert.equal(copy.lines[0], '10 leads will be assigned to Asha, Ravi and Mina, shared equally (4, 3, 3).')
  assert.equal(b.confirmCopy({ action: 'assign', count: 10, allMatching: false, ownerName: 'Asha', recruiterNames: ['Asha'] }).lines[0], '10 leads will be assigned to Asha.')
  assert.match(b.confirmCopy({ action: 'assign', count: 10, allMatching: false, ownerName: 'auto', recruiterNames: [] }).lines[0], /the available recruiters, spread evenly/)
  assert.equal(b.confirmCopy({ action: 'assign', count: 2, allMatching: false, ownerName: 'none' }).title, 'Make 2 leads unassigned')
  assert.equal(b.ownersLine([{ name: 'Sangeetha', count: 4 }, { name: 'Yuvaraj', count: 3 }, { name: 'Mina', count: 0 }]), 'Sangeetha 4, Yuvaraj 3')
  assert.equal(b.assignToast({ updated: 7, skipped: 0, owners: [{ name: 'Sangeetha', count: 4 }, { name: 'Yuvaraj', count: 3 }] }), '7 leads assigned. Sangeetha 4, Yuvaraj 3.')
  assert.equal(b.assignToast({ updated: 3, skipped: 0, owners: [{ name: 'Asha', count: 3 }] }), '3 leads assigned.')
  assert.equal(b.joinNames(['A']), 'A')
  assert.equal(b.joinNames(['A', 'B']), 'A and B')
})

test('the dialogs use the multi-select picker, send the payload from the model and keep the on-leave note', async () => {
  const assign = await text('../src/features/leads/components/assign-dialog.tsx')
  const bulk = await text('../src/features/leads/components/bulk-dialogs.tsx')
  const upload = await text('../src/features/leads/components/upload-dialog.tsx')
  const picker = await text('../src/features/leads/components/recruiter-picker.tsx')
  const api = await text('../src/features/leads/lead-api.ts')
  assert.match(assign, /<RecruiterPicker /)
  assert.match(assign, /leadApi\.assign\(ids, assignChoice\(chosen\)\)/)
  assert.doesNotMatch(assign.split('export function UnassignLeadDialog')[0], /<Select/, 'the single select is gone from the assign dialog')
  assert.match(assign, /Converted leads are never moved/)
  assert.match(assign, /Now with \$\{fromName\}/, 'reassigning from a recruiter keeps its wording')
  assert.match(bulk, /<RecruiterPicker /)
  assert.match(bulk, /Nobody: make unassigned/)
  assert.doesNotMatch(bulk, /Distribute evenly between available recruiters/)
  assert.match(upload, /<RecruiterPicker /)
  assert.match(upload, /Nobody yet \(assign later\)/)
  assert.match(upload, /uploadAssignment\(canAssign, nobody, recruiters\)/)
  assert.match(picker, /Select all available/)
  assert.match(picker, />Clear</)
  assert.match(picker, /\(on leave\)/)
  assert.match(picker, /user\.onLeave/)
  assert.match(picker, /initials\(user\.name\)/)
  assert.match(api, /assigneeIds: string\[\] \}/)
  for (const source of [assign, bulk, upload, picker]) assert.doesNotMatch(source, /\bstaff\b[^.\n]*recruiters? /i.source ? /visible-staff-word/ : /x/)
})
