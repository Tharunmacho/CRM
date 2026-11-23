import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/*
 * The Task report's main table is WHAT WAS DONE in the chosen period (stage records completed, recruiter by stage); where candidates are right now is a
 * different table with its own title. The owner's complaint: with "Today" chosen the table showed 12 candidates at Advance for a recruiter who had done no
 * advance payment that day, because it was a snapshot of where people stand.
 */
const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
const load = async (path) => {
  const js = ts.transpileModule(await read(path), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const view = await load('features/daily-report/daily-report-view.ts')

const stageMap = (entries) => Object.fromEntries(Array.from({ length: 13 }, (_, i) => [String(i + 1), entries[i + 1] ?? 0]))
const person = (id, completed, stopped = {}) => {
  const done = stageMap(completed), lost = stageMap(stopped)
  return { id, completed: done, stopped: lost, total: Object.values(done).reduce((a, b) => a + b, 0), lost: Object.values(lost).reduce((a, b) => a + b, 0) }
}
const staff = (id, name, role = 'staff') => ({ id, name, role })
const report = (extra = {}) => ({
  staff: [staff('y', 'Yuvaraj'), staff('g', 'Gayathri'), staff('a', 'Admin', 'admin')],
  stages: { totals: [{ no: 6, key: 'advance', label: 'Advance payment', count: 12 }], perStaff: [{ id: 'y', counts: { 6: 12 }, total: 12 }] },
  stageFlow: [],
  taskInsight: {
    workDone: { perStaff: [person('g', { 1: 2, 2: 1 }, { 1: 1 }), person('y', { 4: 1 })], totals: {} },
    completed: { value: 4 }, stopped: { value: 1 },
  },
  ...extra,
})

test('the Work done matrix counts the stage records completed in the period, not where candidates stand now', () => {
  const matrix = view.buildWorkDoneMatrix(report())
  assert.deepEqual(matrix.rows.map((row) => row.name), ['Gayathri', 'Yuvaraj'])   // alphabetical, the Administrator is no row
  const yuvaraj = matrix.rows.find((row) => row.id === 'y')
  assert.equal(yuvaraj.cells['6'], 0, 'twelve candidates WAIT at Advance, but no advance payment was completed today')
  assert.equal(yuvaraj.cells['4'], 1)
  assert.equal(matrix.rows.find((row) => row.id === 'g').lost, 1)
  assert.equal(matrix.total.total, 4)
  assert.equal(matrix.total.lost, 1)
  assert.equal(matrix.total.total, report().taskInsight.completed.value, 'the Total row equals the Stages completed card')
  assert.equal(matrix.total.lost, report().taskInsight.stopped.value, 'the last column equals the Stopped or lost card')
  assert.equal(matrix.stages.length, 13)
  assert.equal(matrix.total.cells['1'], 2)
})

test('one person picked narrows the table; a person the report does not list is kept in an Other row; no reading, no table', () => {
  const one = view.buildWorkDoneMatrix(report(), 'g')
  assert.deepEqual(one.rows.map((row) => row.name), ['Gayathri'])
  assert.equal(one.total.total, 3)
  const stranger = report({ taskInsight: { workDone: { perStaff: [person('g', { 1: 1 }), person('gone', { 2: 2 })], totals: {} }, completed: { value: 3 }, stopped: { value: 0 } } })
  const all = view.buildWorkDoneMatrix(stranger)
  assert.deepEqual(all.rows.map((row) => row.name).slice(-1), ['Other'])
  assert.equal(all.total.total, 3)
  assert.equal(view.buildWorkDoneMatrix(stranger, 'g').rows.length, 1)
  assert.equal(view.buildWorkDoneMatrix({ ...report(), taskInsight: { completed: { value: 0 }, stopped: { value: 0 } } }), null)
})

test('the period is named in words over the table, and the work-done lists are period lists, not snapshots', () => {
  const today = '2026-11-23'
  assert.equal(view.periodTitle({ from: today, to: today }, today), 'Today')
  assert.equal(view.periodTitle({ from: '2026-11-22', to: '2026-11-22' }, today), 'Yesterday')
  assert.equal(view.periodTitle({ from: '2026-11-05', to: '2026-11-05' }, today), 'Thu 5 Nov')
  assert.equal(view.periodTitle({ from: '2026-11-02', to: '2026-11-05' }, today), 'Mon 2 Nov to Thu 5 Nov')
  assert.equal(view.periodTitle({ from: '2000-01-01', to: today, allTime: true }, today), 'All time')
  for (const metric of ['done:1', 'done:13', 'done:all', 'lost:5', 'lost:all']) {
    assert.equal(view.isSnapshotMetric(metric), false, `${metric} follows the chosen period`)
    assert.equal(view.drillWhenHeading(metric), 'When')
  }
  assert.equal(view.drillTitle('done:all'), 'Stages completed')
  assert.equal(view.drillTitle('lost:all'), 'Stopped or lost')
  assert.match(view.drillTitle('done:6', report()), /^Stages completed at stage 6: Advance payment$/)
  assert.match(view.drillTitle('lost:2'), /^Stopped or lost at stage 2: /)
})

test('the Task report shows Work done first and Waiting now second, with distinct titles, and the first table never speaks of waiting', async () => {
  const source = await read('features/daily-report/report-task.tsx')
  const strip = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '')
  const body = strip(source)
  const workStart = body.indexOf('aria-label="Work done, recruiter by recruiter"')
  const waitStart = body.indexOf('aria-label="Waiting now, recruiter by recruiter"')
  assert.ok(workStart > 0 && waitStart > workStart, 'Work done comes first')
  const work = body.slice(workStart, waitStart)
  assert.ok(!/waiting/i.test(work), 'the work-done table says nothing about waiting')
  assert.match(work, /'Work done'/)
  assert.match(work, /\(\{periodText\}\)/)
  assert.match(body.slice(waitStart), /'Waiting now'/)
  assert.match(body.slice(waitStart), /where candidates are at this moment \(not limited to the chosen period\)/)
  assert.ok(!body.includes('Candidates waiting at each stage</h2>') && !body.includes("'Candidates waiting at each stage'"), 'the old title is gone')
  // each cell of Work done opens exactly its records
  assert.match(body, /doneDrill\('done', String\(stage\.no\), item\.id/)
  assert.match(body, /doneDrill\('done', 'all', item\.id, item\.total\)/)
  assert.match(body, /doneDrill\('lost', 'all', item\.id, item\.lost\)/)
  assert.match(work, /doneDrill\('done', String\(stage\.no\), null, work\.total/)
  // the headings: Recruiter, the 13 stages, Total, Lost
  assert.match(work, /Recruiter<\/th>/)
  assert.match(work, /Lost<\/span>/)
})

test('changing the period swaps the Work done figures: it is built from the report of that period, nothing is kept between periods', () => {
  const thursday = view.buildWorkDoneMatrix(report())
  const wednesday = view.buildWorkDoneMatrix(report({ taskInsight: { workDone: { perStaff: [person('g', { 1: 1 }), person('y', {})], totals: {} }, completed: { value: 1 }, stopped: { value: 0 } } }))
  assert.equal(thursday.total.total, 4)
  assert.equal(wednesday.total.total, 1)
  assert.notDeepEqual(thursday.total.cells, wednesday.total.cells)
})
