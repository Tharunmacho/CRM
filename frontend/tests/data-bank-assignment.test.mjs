import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/candidates/data-bank-assignment.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { bankApplication, bankFormApplication } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
const application = (id, status, createdAt) => ({ id, status, createdAt, orderId: `order-${id}` })

test('a current active placement takes priority over a newer deployment', () => {
  const active = application('a', 'active', '2026-09-01T10:00:00Z')
  const deployed = application('b', 'deployed', '2026-10-01T10:00:00Z')
  const applications = [deployed, active]
  assert.equal(bankApplication(applications), active)
  assert.equal(bankFormApplication(applications), active)
  assert.deepEqual(applications, [deployed, active], 'display selection must not reorder shared store data')
})

test('deployed candidates show the latest created placement independently of API array order or updates', () => {
  const older = { ...application('a', 'deployed', '2026-09-01T10:00:00Z'), updatedAt: '2026-11-12T10:00:00Z' }
  const latest = application('b', 'deployed', '2026-10-01T10:00:00Z')
  const closed = application('c', 'closed', '2026-11-01T10:00:00Z')
  for (const applications of [[latest, older, closed], [closed, older, latest]]) {
    assert.equal(bankApplication(applications), latest)
    assert.equal(bankFormApplication(applications), latest)
  }
})

test('equal timestamps choose the descending id just like the server assignment ranking', () => {
  const lower = application('00000000-0000-0000-0000-000000000001', 'deployed', '2026-10-01T10:00:00Z')
  const higher = application('00000000-0000-0000-0000-000000000002', 'deployed', lower.createdAt)
  assert.equal(bankApplication([lower, higher]), higher)
  assert.equal(bankApplication([higher, lower]), higher)
})

test('closed-only history is unplaced but its latest application form remains available', () => {
  const latest = application('a', 'closed', '2026-10-01T10:00:00Z')
  const older = application('b', 'closed', '2026-09-01T10:00:00Z')
  assert.equal(bankApplication([latest, older]), undefined)
  assert.equal(bankFormApplication([latest, older]), latest)
  assert.equal(bankApplication([]), undefined)
  assert.equal(bankFormApplication([]), undefined)
})
