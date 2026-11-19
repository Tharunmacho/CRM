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
const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
const model = await load('../src/features/staff/delete-user-model.ts')

const admin = { id: 'root', role: 'admin' }
const md = { id: 'maya', role: 'manager' }
const recruiter = { id: 'asha', role: 'staff' }

test('the Delete user item is offered only to Admin and MD, only for a Recruiter, never for yourself', () => {
  assert.equal(model.canDeleteUser(admin, { id: 'ravi', role: 'staff' }), true)
  assert.equal(model.canDeleteUser(md, { id: 'ravi', role: 'staff' }), true)
  assert.equal(model.canDeleteUser(recruiter, { id: 'ravi', role: 'staff' }), false, 'a recruiter deletes nobody')
  assert.equal(model.canDeleteUser(admin, { id: 'other', role: 'admin' }), false, 'an Admin account cannot be deleted')
  assert.equal(model.canDeleteUser(admin, { id: 'maya', role: 'manager' }), false, 'an MD account cannot be deleted')
  assert.equal(model.canDeleteUser(md, { id: 'other', role: 'admin' }), false)
  assert.equal(model.canDeleteUser(md, md), false, 'nobody deletes themselves')
  assert.equal(model.canDeleteUser(admin, { id: 'gone', role: 'staff', deleted: true }), false)
  assert.equal(model.canDeleteUser(null, { id: 'ravi', role: 'staff' }), false)
  assert.equal(model.canDeleteUser(admin, undefined), false)
})

test('the live summary reads "29 candidates and 26 open tasks" and says so when there is nothing to move', () => {
  assert.equal(model.workSummaryText({ candidates: 29, openTasks: 26, leads: 0 }), '29 candidates and 26 open tasks')
  assert.equal(model.workSummaryText({ candidates: 1, openTasks: 1, leads: 0 }), '1 candidate and 1 open task')
  assert.equal(model.workSummaryText({ candidates: 3, openTasks: 3, leads: 2 }), '3 candidates, 3 open tasks and 2 leads')
  assert.equal(model.workSummaryText({ candidates: 0, openTasks: 0, leads: 0 }), 'Nothing to move')
  assert.equal(model.workSummaryText(null), 'Nothing to move')
  assert.equal(model.hasWorkToMove({ candidates: 0, openTasks: 0, leads: 1 }), true)
  assert.equal(model.hasWorkToMove({ candidates: 0, openTasks: 0, leads: 0 }), false)
})

test('the red button stays disabled until a choice is made (none needed when there is nothing to move)', () => {
  const work = { candidates: 29, openTasks: 26, leads: 0 }
  assert.equal(model.deleteReady(null, '', false), false, 'still loading what the person holds')
  assert.equal(model.deleteReady(work, '', false), false, 'no default choice')
  assert.equal(model.deleteReady(work, 'ravi', false), true)
  assert.equal(model.deleteReady(work, model.LEAVE_UNASSIGNED, false), true, 'leave unassigned is an explicit second option')
  assert.equal(model.deleteReady(work, 'ravi', true), false, 'busy')
  assert.equal(model.deleteReady({ candidates: 0, openTasks: 0, leads: 0 }, '', false), true, 'a deactivated person with 0 candidates')
})

test('the request carries the chosen recruiter or the explicit leave-unassigned flag', () => {
  assert.equal(model.deleteQuery('ravi 1'), '?reassignTo=ravi%201')
  assert.equal(model.deleteQuery(model.LEAVE_UNASSIGNED), '?leaveUnassigned=true')
  assert.equal(model.deleteQuery(''), '')
})

test('the success toast names who took the work, or says it is unassigned', () => {
  const work = { candidates: 29, openTasks: 26, leads: 0 }
  assert.equal(model.successText('Asha', work, 'Ravi'), 'Asha was deleted. 29 candidates and 26 open tasks moved to Ravi.')
  assert.equal(model.successText('Asha', work, null), 'Asha was deleted. 29 candidates and 26 open tasks are now unassigned.')
  assert.equal(model.successText('Tharun .v', { candidates: 0, openTasks: 0, leads: 0 }, null), 'Tharun .v was deleted.')
})

test('the screen wires the item last in the row menu, separated and destructive, and the dialog asks the required question', async () => {
  const users = await read('screens/users/users.tsx')
  assert.match(users, /canDeleteUser\(store\.me, u\) \? \[\{ label: 'Delete user', icon: Trash2, tone: 'danger' as const, separated: true,/)
  const menu = users.slice(users.indexOf("<RowMenu label={'Actions for '"), users.indexOf("<RowMenu label={'Actions for '") + 1800)
  assert.ok(menu.lastIndexOf("label: 'Delete user'") > menu.lastIndexOf("label: 'Deactivate account'"), 'Delete user is the last item')
  const dialog = await read('screens/users/delete-user-dialog.tsx')
  for (const words of ['`Delete ${user.name}?`', 'Move their candidates and tasks to', 'Leave unassigned (goes to the Data Bank for the next assignment)',
    'Deactivate instead', 'variant="danger"', 'Choose what happens…']) assert.ok(dialog.includes(words), words)
  assert.match(dialog, /disabled=\{!ready\}/)
  assert.match(dialog, /api\.del\('\/users\/' \+ user\.id \+ deleteQuery\(validChoice\)\)/)
  assert.match(dialog, /'\/delete-summary'/)
})

test('the row menu supports a divider, never wraps a label, and keeps 40 px items (44 px on touch)', async () => {
  const table = await read('shared/ui/table.tsx')
  assert.match(table, /separated\?: boolean/)
  assert.match(table, /separated && 'is-separated'/)
  const css = await read('styles/data-table.css')
  const item = css.match(/\.ui-row-menu-item \{[^}]*\}/)[0]
  assert.match(item, /height: 40px/)
  assert.match(item, /white-space: nowrap/)
  assert.match(css.match(/\.ui-row-menu \{[^}]*\}/)[0], /width: max-content/)
  assert.match(css, /\.ui-row-menu-item\.is-separated/)
  assert.match(css, /@media \(pointer: coarse\) \{[^\n]*\.ui-row-menu-item \{ height: 44px/)
})

test('a deleted account is kept out of every list and picker but not out of name lookups', async () => {
  const store = await read('shared/state/crm-store.tsx')
  assert.match(store, /users: data\.users\.filter\(\(person\) => !person\.deleted\)/)
  assert.match(store, /user: \(id\) => \(id \? index\.users\.get\(id\) : undefined\)/)
})
