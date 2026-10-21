import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/attendance/model.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { inferredPresence, PRESENCE_IDLE_MS, activeAttendanceSession, durationLabel } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('presence treats hidden tabs as offline and recent interaction as online only before the idle boundary', () => {
  assert.equal(inferredPresence(false, 1000, 1001), 'offline')
  assert.equal(inferredPresence(true, 1000, 1000 + PRESENCE_IDLE_MS - 1), 'online')
  assert.equal(inferredPresence(true, 1000, 1000 + PRESENCE_IDLE_MS), 'idle')
})

test('checkout targets the active server session even if it began outside the selected day', () => {
  assert.equal(activeAttendanceSession({ attendance: { status: 'checked_in', sessionId: 'yesterday', sessions: [] } }), 'yesterday')
  assert.equal(activeAttendanceSession({ attendance: { status: 'checked_in', sessions: [{ id: 'closed', checkedOutAt: '2026-01-01' }, { id: 'open', checkedOutAt: null }] } }), 'open')
  assert.equal(activeAttendanceSession({ attendance: { status: 'checked_out', sessionId: 'stale', sessions: [] } }), null)
})

test('attendance totals retain hours over a day and never render negative or invalid durations', () => {
  assert.equal(durationLabel(27 * 3600 + 120), '27h 02m')
  assert.equal(durationLabel(-5), '0h 00m')
  assert.equal(durationLabel(NaN), '0h 00m')
})
