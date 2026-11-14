import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function source(path) {
  return readFile(new URL(path, import.meta.url), 'utf8')
}

async function load(path) {
  const js = ts.transpileModule(await source(path), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const { canDeleteOrder, orderDeleteBlocker, orderDeletePath, serverAsksToConfirm } = await load('../src/features/jobs/order-delete.ts')
const { removeOrder } = await load('../src/shared/state/order-removal.ts')

test('only an admin is offered Delete job', () => {
  assert.equal(canDeleteOrder('admin'), true)
  for (const role of ['manager', 'staff', '', undefined]) assert.equal(canDeleteOrder(role), false)
})

test('a job with no candidates has no blocker', () => {
  assert.equal(orderDeleteBlocker([], 'ORD-1'), null)
})

test('a job with candidates can be deleted only after the admin confirms removing them', () => {
  const closedOnly = orderDeleteBlocker([{ status: 'closed', candidateId: 'c1' }], 'ORD-1')
  assert.deepEqual(closedOnly, { message: '1 candidate is on ORD-1 (all closed).', canWithdraw: true, candidates: 1, active: 0 })
  const mixed = orderDeleteBlocker([{ status: 'active', candidateId: 'c1' }, { status: 'closed', candidateId: 'c2' }, { status: 'closed', candidateId: 'c1' }], 'ORD-2')
  assert.deepEqual(mixed, { message: '2 candidates are on ORD-2 (1 active).', canWithdraw: true, candidates: 2, active: 1 })
})

test('a deployed candidate blocks the delete with a plain message and no override', () => {
  const blocked = orderDeleteBlocker([{ status: 'active', candidateId: 'c1' }, { status: 'deployed', candidateId: 'c2' }], 'ORD-3', (id) => (id === 'c2' ? 'Asha' : undefined))
  assert.equal(blocked.canWithdraw, false)
  assert.match(blocked.message, /^Asha was deployed through ORD-3, and a deployed placement is kept on record/)
  assert.match(blocked.message, /Close it instead\.$/)
  assert.match(orderDeleteBlocker([{ status: 'deployed', candidateId: 'x' }], 'ORD-3').message, /^A candidate was deployed through ORD-3/)
})

test('the delete request carries the confirmation only when the admin gave it', () => {
  assert.equal(orderDeletePath('o1'), '/orders/o1')
  assert.equal(orderDeletePath('o1', false), '/orders/o1')
  assert.equal(orderDeletePath('o1', true), '/orders/o1?withdrawApplications=true')
})

test('a refusal that says the admin can still confirm brings the confirmation up', () => {
  assert.equal(serverAsksToConfirm({ detail: { message: '2 candidates are on ORD-1', canWithdraw: true } }), true)
  assert.equal(serverAsksToConfirm({ detail: { message: 'x', canWithdraw: false } }), false)
  assert.equal(serverAsksToConfirm({ detail: 'Can\'t delete ORD-1: deployed' }), false)
  assert.equal(serverAsksToConfirm(null), false)
})

test('removing a job drops its applications, tasks, remarks and links, and nothing else', () => {
  const data = {
    orders: [{ id: 'o1' }, { id: 'o2' }],
    applications: [{ id: 'a1', orderId: 'o1' }, { id: 'a2', orderId: 'o2' }],
    tasks: [{ id: 't1', applicationId: 'a1' }, { id: 't2', applicationId: 'a2' }, { id: 't3' }],
    remarks: [{ id: 'r1', taskId: 't1' }, { id: 'r2', taskId: 't2' }],
    todos: [{ id: 'd1', linkTo: '/orders/o1', linkLabel: 'ORD-1' }, { id: 'd2', linkTo: '/orders/o2', linkLabel: 'ORD-2' }],
    notifications: [{ id: 'n1', linkTo: '/orders/o1' }, { id: 'n2', linkTo: '/tasks?task=t1' }, { id: 'n3', linkTo: '/tasks?task=t2' }, { id: 'n4' }],
    candidates: [{ id: 'c1' }],
  }
  const next = removeOrder(data, 'o1')
  assert.deepEqual(next.orders, [{ id: 'o2' }])
  assert.deepEqual(next.applications, [{ id: 'a2', orderId: 'o2' }])
  assert.deepEqual(next.tasks.map((task) => task.id), ['t2', 't3'])
  assert.deepEqual(next.remarks, [{ id: 'r2', taskId: 't2' }])
  assert.deepEqual(next.notifications.map((note) => note.id), ['n3', 'n4'])
  assert.deepEqual(next.todos, [{ id: 'd1', linkTo: undefined, linkLabel: undefined }, { id: 'd2', linkTo: '/orders/o2', linkLabel: 'ORD-2' }])
  assert.equal(next.candidates, data.candidates)
  assert.equal(data.orders.length, 2)
})

test('removing a job nobody refers to keeps the other lists as they are', () => {
  const data = { orders: [{ id: 'o1' }], applications: [], tasks: [], remarks: [], todos: [], notifications: [] }
  const next = removeOrder(data, 'o1')
  assert.deepEqual(next.orders, [])
  assert.equal(next.applications, data.applications)
  assert.equal(next.todos, data.todos)
})

test('the Jobs list and the job page offer the delete button to an admin only, in both views', async () => {
  const page = await source('../src/screens/jobs/jobs.tsx')
  assert.match(page, /const canDelete = canDeleteOrder\(store\.me\?\.role\)/)
  assert.match(page, /onDelete=\{canDelete \? setDeleting : undefined\}/g)
  assert.equal(page.match(/onDelete=\{canDelete \? setDeleting : undefined\}/g).length, 2)
  assert.match(page, /<DeleteJobDialog orderId=\{deleting\.id\}/)
  assert.match(page, /\{onDelete && <button type="button" className="cs-icon-act is-danger"/)
  assert.match(page, /aria-label=\{`Delete \$\{row\.title\} \$\{row\.order\.ref\}`\} title="Delete"/)
  const detail = await source('../src/screens/jobs/order-detail.tsx')
  assert.match(detail, /canDeleteOrder\(store\.me\?\.role\) && <MenuButton danger/)
  assert.match(detail, /<DeleteJobDialog\s+orderId=\{order\.id\}/)
})

test('the store sends the confirmation flag and removes the job from the lists once the server agrees', async () => {
  const store = await source('../src/shared/state/crm-store.tsx')
  assert.match(store, /await api\.del\(orderDeletePath\(id, options\?\.withdrawApplications\)\)/)
  assert.match(store, /setData\(\(current\) => removeOrder\(current, id\)\)/)
})
