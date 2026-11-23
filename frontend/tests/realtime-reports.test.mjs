import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
const load = async (path, patch = (text) => text) => {
  const js = ts.transpileModule(patch(await read(path)), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const today = await load('features/daily-report/use-agency-today.ts', (text) => text
  .replace("import { useEffect, useState } from 'react'", 'const useEffect = () => {}, useState = () => [];')
  .replace("import { agencyDay } from '@/shared/lib/task-time'", "const agencyDay = (value, timeZone) => new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value))"))
const view = await load('features/daily-report/daily-report-view.ts')

test('a day or month that was "today" follows the new day at midnight; one picked by hand stays', () => {
  assert.equal(today.followDay('2026-11-22', '2026-11-22', '2026-11-23'), '2026-11-23')
  assert.equal(today.followDay('2026-11-10', '2026-11-22', '2026-11-23'), '2026-11-10')
  assert.equal(today.followDay('2026-11', '2026-11', '2026-12'), '2026-12')   // the month choice rolls over with the 1st
  assert.equal(today.followDay('2026-09', '2026-11', '2026-12'), '2026-09')
})

test('the agency day is read again every 15 seconds and when the tab comes back, and the Reports page and the attendance board use it', async () => {
  assert.equal(today.DAY_CHECK_MS, 15_000)
  const hook = await read('features/daily-report/use-agency-today.ts')
  assert.match(hook, /window\.setInterval\(check, DAY_CHECK_MS\)/)
  assert.match(hook, /addEventListener\('visibilitychange', check\)/)
  assert.match(hook, /addEventListener\('focus', check\)/)
  const page = await read('features/daily-report/daily-report-page.tsx')
  assert.match(page, /const today = useAgencyToday\(timeZone\)/)
  const board = await read('features/daily-report/report-attendance-tab.tsx')
  assert.match(board, /const today = useAgencyToday\(timeZone\)/)
  assert.match(board, /followDay\(current, previous, today\)/)
  assert.match(board, /followDay\(current, previous\.slice\(0, 7\), today\.slice\(0, 7\)\)/)
})

test('a figure that is a snapshot of now is labelled "Right now" with a "Waiting since" column, not the chosen period', async () => {
  for (const metric of ['stage:5', 'stage:0', 'stuck', 'stuck:3', 'tasksPending', 'tasksOverdue', 'leadsAssigned', 'leadsStale']) assert.equal(view.isSnapshotMetric(metric), true, metric)
  for (const metric of ['prescreened', 'forwarded', 'travelled', 'leadsContacted', 'tasksDone']) assert.equal(view.isSnapshotMetric(metric), false, metric)
  assert.equal(view.drillWhenHeading('stage:5'), 'Waiting since')
  assert.equal(view.drillWhenHeading('stuck:2'), 'Waiting since')
  assert.equal(view.drillWhenHeading('tasksOverdue'), 'Since')
  assert.equal(view.drillWhenHeading('forwarded'), 'When')
  const drawer = await read('features/daily-report/drilldown-drawer.tsx')
  assert.match(drawer, /isSnapshotMetric\(metric\) \? 'Right now \(not limited to the chosen period\)'/)
  assert.match(drawer, /drillWhenHeading\(metric\)/)
})

test('no screen can look at numbers older than about 30 seconds: the live reads keep a short safety poll even while the stream is up', async () => {
  const live = await read('shared/state/live.ts')
  assert.match(live, /LIVE_SAFETY_POLL_MS = 45_000/)
  const reports = await read('features/reports/report-api.ts')
  assert.match(reports, /const REFRESH_MS = 30_000/)
  assert.match(reports, /safetyMs: REFRESH_MS/)
  const attendance = await read('features/daily-report/use-attendance-data.ts')
  assert.match(attendance, /DAY_RATES: LiveRates = \{ downMs: 15_000, safetyMs: 20_000/)
  assert.match(attendance, /rates\.downMs, \['attendance', 'users'\], \{\s*safetyMs: rates\.safetyMs/)
  const tasks = await read('features/daily-work/use-daily-work.ts')
  assert.match(tasks, /15_000,\s*TOPICS,\s*\{ debounceMs: LIVE_WINDOW_MS, safetyMs: 20_000/)
})
