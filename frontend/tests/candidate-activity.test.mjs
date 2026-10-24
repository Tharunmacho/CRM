import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

function moduleUrl(source) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}

const { buildActivity } = await import(moduleUrl(await readFile(new URL('../src/features/candidates/activity.ts', import.meta.url), 'utf8')))
const { passportExpiryNotice } = await import(moduleUrl(await readFile(new URL('../src/features/candidates/passport-expiry.ts', import.meta.url), 'utf8')))

const look = {
  stageTitle: (no) => `Stage-${no}`,
  closureLabel: (reason) => `closed:${reason}`,
  orderLabel: (id) => `Order ${id}`,
  userName: (id) => (id ? `User ${id}` : ''),
  sourceLabel: (source) => source.toUpperCase(),
}
const candidate = { id: 'c1', source: 'email', sourceDetail: 'hr@x', addedAt: '2026-10-01T09:00:00Z', ownerId: 'u1', callOutcome: null }

test('activity lists the add, the call, mapping, every stage and the ending, newest first', () => {
  const cand = { ...candidate, callOutcome: { called: true, interested: 'yes', remarks: 'Keen', recordedAt: '2026-10-02T09:00:00Z' } }
  const apps = [{
    id: 'a1', orderId: 'o1', status: 'closed', closureReason: 'interview_rejected', createdAt: '2026-10-03T09:00:00Z', updatedAt: '2026-10-06T09:00:00Z',
    stages: [
      { stage: 1, attempt: 1, outcome: 'Passed', completedAt: '2026-10-04T09:00:00Z', completedBy: 'u1', data: {} },
      { stage: 4, attempt: 1, outcome: 'Rejected', completedAt: '2026-10-05T09:00:00Z', completedBy: 'u2', data: {} },
    ],
  }]
  const events = buildActivity(cand, apps, look)
  assert.deepEqual(events.map((event) => event.tone), ['bad', 'bad', 'ok', 'info', 'ok', 'plain'])
  assert.equal(events[0].title, 'Order o1 closed: closed:interview_rejected')
  assert.equal(events[1].title, 'Stage 4, Stage-4: Rejected')
  assert.equal(events[1].by, 'User u2')
  assert.equal(events.at(-1).detail, 'EMAIL · hr@x')
  assert.equal(events.find((event) => event.id === 'call-c1').title, 'Initial call: interested')
})

test('activity marks reopened and repeated attempts', () => {
  const apps = [{
    id: 'a1', orderId: 'o1', status: 'active', createdAt: '2026-10-03T09:00:00Z', updatedAt: '2026-10-09T09:00:00Z',
    stages: [
      { stage: 6, attempt: 1, outcome: 'Unfit', completedAt: '2026-10-04T09:00:00Z', completedBy: 'u1', data: {} },
      { stage: 6, attempt: 2, outcome: 'Fit', completedAt: '2026-10-08T09:00:00Z', completedBy: 'u1', data: {}, reopenedReason: 'Wrong report' },
    ],
  }]
  const events = buildActivity(candidate, apps, look)
  const second = events.find((event) => event.title.includes('attempt 2'))
  assert.equal(second.tone, 'warn')
  assert.equal(second.detail, 'Reopened later: Wrong report')
  assert.ok(events.find((event) => event.title.includes('attempt 1')))
})

test('passport notice: expired, soon, and quiet when far away or unreadable', () => {
  const now = new Date('2026-10-24T00:00:00Z')
  assert.equal(passportExpiryNotice('2026-10-01', now).tone, 'expired')
  assert.equal(passportExpiryNotice('2027-01-01', now).text, 'Passport expires in 69 days')
  assert.equal(passportExpiryNotice('2026-10-25', now).text, 'Passport expires in 1 day')
  assert.equal(passportExpiryNotice('2030-06-08', now), null)
  assert.equal(passportExpiryNotice('not a date', now), null)
  assert.equal(passportExpiryNotice(undefined, now), null)
})
