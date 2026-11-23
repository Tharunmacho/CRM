import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('the clock says that signing in starts the day and signing out ends it (the old "signing out does not check you out" is gone)', async () => {
  const clock = await read('features/attendance/components/attendance-clock.tsx')
  assert.match(clock, /Signing in starts your day and signing out ends it/)
  assert.match(clock, /30 minutes/)
  assert.doesNotMatch(clock, /does not check you out/)
})

test('a session carries who opened and closed it, so reports can tell a sign-in from a button press', async () => {
  const model = await read('features/attendance/model.ts')
  assert.match(model, /checkInSource\?: 'login' \| 'heartbeat' \| 'manual' \| null/)
  assert.match(model, /checkOutSource\?: 'logout' \| 'inactivity' \| 'missed' \| 'manual' \| null/)
})

test('nothing in the screens still claims that only the Check in button starts attendance', async () => {
  for (const file of ['features/attendance/components/attendance-clock.tsx', 'features/daily-report/attendance-rows.ts', 'features/daily-report/report-attendance.tsx']) {
    const text = await read(file)
    assert.doesNotMatch(text, /attendance only starts with Check in/i, file)
    assert.doesNotMatch(text, /never creates attendance/i, file)
  }
})
