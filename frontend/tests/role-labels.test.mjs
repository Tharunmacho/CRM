import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { roleLabelsUrl } from './fixtures/role-labels-url.mjs'

/**
 * The owner's rule: the role stored as `manager` is shown as "MD" everywhere, and an MD holds every privilege an
 * Admin holds. One label table and one `isAdminLevel` rule (src/shared/model/role-labels.ts) serve the whole app.
 */
const labels = await import(roleLabelsUrl)
const root = fileURLToPath(new URL('../src/', import.meta.url))
const read = (path) => readFile(join(root, path), 'utf8')

async function sourceFiles(directory = root) {
  const found = []
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) found.push(...(await sourceFiles(path)))
    else if (/\.(ts|tsx)$/.test(entry.name)) found.push(path)
  }
  return found
}
const relative = (path) => path.slice(root.length).replaceAll('\\', '/')
const withoutComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1')

test('the one label table: manager reads as MD, admin as Admin, staff as Recruiter', () => {
  assert.deepEqual(labels.ROLE_LABELS, { admin: 'Admin', manager: 'MD', staff: 'Recruiter' })
  assert.deepEqual(labels.ROLE_TITLES, { admin: 'Administrator', manager: 'MD', staff: 'Recruiter' })
  assert.equal(labels.roleLabel('manager'), 'MD')
  assert.equal(labels.roleLabel('manager', true), 'MD')
  assert.equal(labels.roleLabel('admin'), 'Admin')
  assert.equal(labels.roleLabel('admin', true), 'Administrator')
  assert.equal(labels.roleLabel('staff'), 'Recruiter')
  assert.equal(labels.roleLabel('md'), 'MD', 'the word md (any case) is the same role')
  assert.equal(labels.roleLabel('MD'), 'MD')
  assert.equal(labels.roleLabel(''), '')
  assert.equal(labels.roleLabel(undefined), '')
  assert.equal(labels.roleLabel('guest'), 'guest', 'an unknown value is shown as it came')
  assert.ok(!Object.values(labels.ROLE_LABELS).concat(Object.values(labels.ROLE_TITLES)).some((word) => /manager/i.test(word)))
})

test('a role picker offers Admin, MD, Recruiter in that order', () => {
  assert.deepEqual([...labels.ROLE_ORDER], ['admin', 'manager', 'staff'])
  assert.deepEqual(labels.ROLE_ORDER.map((role) => labels.roleLabel(role)), ['Admin', 'MD', 'Recruiter'])
})

test('role words inside a sentence keep MD as an acronym', () => {
  assert.equal(labels.roleWord('manager'), 'MD')
  assert.equal(labels.roleWord('admin'), 'admin')
  assert.equal(labels.roleWord('staff'), 'recruiter')
})

test('isAdminLevel is true for admin and MD (manager, md) and nothing else', () => {
  for (const role of ['admin', 'manager', 'md', 'MD', 'Manager', ' admin ']) assert.equal(labels.isAdminLevel(role), true, role)
  for (const role of ['staff', 'recruiter', 'guest', '', null, undefined]) assert.equal(labels.isAdminLevel(role), false, String(role))
  assert.deepEqual([...labels.ADMIN_LEVEL_ROLES], ['admin', 'manager'])
  assert.equal(labels.normaliseRole('MD'), 'manager')
})

test('only a real admin counts for the keep-one-admin wording, never an MD', () => {
  assert.equal(labels.isRealAdmin('admin'), true)
  for (const role of ['manager', 'md', 'staff', '', undefined]) assert.equal(labels.isRealAdmin(role), false, String(role))
})

test('every capability an admin holds is held by an MD, and a recruiter holds none of the admin-only ones', async () => {
  const store = await read('shared/state/crm-store.tsx')
  const table = store.slice(store.indexOf('const MATRIX'), store.indexOf('type Toast'))
  const entries = [...table.matchAll(/^\s*(\w+): (.+?),\s*$/gm)].map((match) => [match[1], match[2]])
  assert.ok(entries.length >= 13, 'the capability table was found')
  for (const [name, roles] of entries) {
    assert.ok(roles === 'ADMIN_LEVEL_ROLES' || /'admin'/.test(roles) === /'manager'/.test(roles), `${name} gives MD whatever it gives admin: ${roles}`)
    assert.ok(!/^\['admin'\]$/.test(roles), `${name} must not be admin-only`)
  }
  const adminOnly = entries.filter(([, roles]) => roles === 'ADMIN_LEVEL_ROLES').map(([name]) => name)
  for (const name of ['connect_sources', 'reassign', 'reopen_stage', 'user_management', 'export', 'staff_report', 'view_invoices']) {
    assert.ok(adminOnly.includes(name), `${name} is an admin-level capability`)
  }
  assert.match(store, /import \{[^}]*ADMIN_LEVEL_ROLES[^}]*\} from '@\/shared\/model\/role-labels'/)
})

test('no screen decides a privilege with role === admin: only the listed people filters and employee rules remain', async () => {
  // Each remaining use is about WHO is listed or whether someone is a clocking employee, never about what the viewer may do.
  const allowed = new Set([
    'features/attendance/attendance-context.tsx',            // an Admin has no own attendance; an MD clocks in like a recruiter
    'features/attendance/components/attendance-clock.tsx',
    'features/attendance/components/attendance-workspace.tsx',
    'features/attendance/components/attendance-presence.tsx',
    'features/candidates/components/data-bank-filters.tsx',  // lists who can own candidates (not admins)
    'features/leads/components/lead-filters.tsx',
    'screens/tasks/tasks.tsx',
    'screens/staff/staff-detail.tsx',                        // the employee being looked at is not an admin
    'shared/model/role-labels.ts',
  ])
  const offenders = []
  for (const file of await sourceFiles()) {
    const text = withoutComments(await readFile(file, 'utf8'))
    if (/\brole\s*(===|!==)\s*'admin'/.test(text) && !allowed.has(relative(file))) offenders.push(relative(file))
  }
  assert.deepEqual(offenders, [])
})

test('no visible "Manager" wording is left in the screens (identifiers and API values excepted)', async () => {
  // A visible string is any quoted or JSX text that is not exactly the stored value 'manager'. Allowed: a client contact's job
  // title placeholder ("HR Manager"), which is data about the client's staff and not a CRM role.
  const offenders = []
  for (const file of await sourceFiles()) {
    const text = withoutComments(await readFile(file, 'utf8'))
    for (const line of text.split('\n')) {
      const visible = line.replace(/(['"`])manager\1/g, '').replace(/\.manager\b/g, '').replace(/\bmanager:/g, '').replace(/Role\.manager/g, '')
      if (/\bmanagers?\b/i.test(visible) && !/HR Manager/.test(visible)) offenders.push(`${relative(file)}: ${line.trim().slice(0, 120)}`)
    }
  }
  assert.deepEqual(offenders, [])
})

test('the Users screens pick roles from the one table and say MD', async () => {
  const dialog = await read('screens/users/user-dialog.tsx')
  assert.match(dialog, /ROLE_ORDER\.map\(\(value\) => <option key=\{value\} value=\{value\}>\{roleLabel\(value\)\}<\/option>\)/)
  const list = await read('screens/users/users.tsx')
  assert.match(list, /ROLE_ORDER\.map\(\(value\) => <option key=\{value\} value=\{value\}>\{roleLabel\(value\)\}<\/option>\)/)
  const ui = await read('screens/users/user-ui.tsx')
  assert.match(ui, /export const roleLabels: Record<Role, string> = ROLE_LABELS/)
  assert.match(ui, /manager: 'Everything an Admin can do/)
  assert.match(dialog, /Your own role cannot be changed\./)
  const shell = await read('app/layout/app-shell.tsx')
  assert.match(shell, /isAdminLevel\(store\.me\.role\)/)
  assert.doesNotMatch(shell, /ROLE_LABEL\b/)
})

test('the server error wording the screens mirror says MD, not Manager', async () => {
  const access = await import(`data:text/javascript;base64,${Buffer.from((await import('typescript')).default.transpileModule(
    (await read('features/candidates/candidate-access.ts')).replace("'@/shared/model/role-labels'", `'${roleLabelsUrl}'`),
    { compilerOptions: { module: 99, target: 9 } }).outputText).toString('base64')}`)
  assert.equal(access.workRefusal({ id: 'r', role: 'staff' }, { ownerId: null }), 'This candidate has no recruiter yet. Only an MD or an admin can work on it.')
  assert.equal(access.canWorkCandidate({ id: 'm', role: 'manager' }, { ownerId: 'someone' }), true)
  assert.equal(access.canWorkCandidate({ id: 'm', role: 'md' }, { ownerId: 'someone' }), true)
})
