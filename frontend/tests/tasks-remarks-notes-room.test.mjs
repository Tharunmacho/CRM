import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/*
 * REMARKS and NOTES are what people read and write on the Tasks list: they take the room the panel has beyond the minimum plan, and a note that
 * was typed but not saved stays in its box when the table is drawn again.
 */

const moduleUrl = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
const m = await import(moduleUrl(await read('../src/features/daily-work/daily-work-model.ts')))
const drafts = await import(moduleUrl(await read('../src/features/daily-work/note-drafts.ts')))
const sum = (widths) => Object.values(widths).reduce((total, width) => total + width, 0)

test('on a panel at or below the plan every column keeps its measured width', () => {
  const columns = m.taskColumnsFor(true, false)
  const plan = m.taskPlanWidth(true, false)
  for (const panel of [0, plan]) {   // (the plan is 1057px since the DOT column went, so 1080px already has spare room)
    assert.deepEqual(m.widthsWithSpare(columns, panel), Object.fromEntries(columns.map((c) => [c.key, c.width])), `${panel}px`)
  }
})

test('spare room goes to REMARKS and NOTES (2 : 3) and nowhere else, up to their limits', () => {
  const columns = m.taskColumnsFor(true, false)
  const base = Object.fromEntries(columns.map((c) => [c.key, c.width]))
  const plan = m.taskPlanWidth(true, false)
  const wider = m.widthsWithSpare(columns, plan + 100)
  assert.equal(wider.remarks, base.remarks + 40)
  assert.equal(wider.notes, base.notes + 60)
  for (const key of Object.keys(base)) if (key !== 'remarks' && key !== 'notes') assert.equal(wider[key], base[key], `${key} does not change`)
  const huge = m.widthsWithSpare(columns, plan + 2000)
  assert.equal(huge.remarks, m.REMARKS_MAX)
  assert.equal(huge.notes, m.NOTES_MAX)
  assert.ok(sum(huge) < plan + 2000, 'what is beyond the limits is spread over every column by the table (shares), not added here')
})

test('the table uses the widths with spare room, and the Notes box keeps what was typed', async () => {
  const table = await read('../src/screens/tasks/task-table.tsx')
  assert.match(table, /widthsWithSpare\(columns, panelWidth\)/)
  assert.match(table, /<colgroup>\{effective\.map/)
  assert.match(table, /useState\(\(\) => getNoteDraft\(draftKey\)\)/, 'the box starts with the saved draft')
  assert.match(table, /setNoteDraft\(draftKey, value\)/, 'every keystroke is kept')
  assert.match(table, /else setNoteDraft\(draftKey, ''\)/, 'a saved note leaves the draft store')
})

test('drafts are kept per writer and candidate, empty ones are dropped, and the oldest go first', () => {
  drafts.resetNoteDrafts()
  const a = drafts.noteDraftKey('Asha', 'c1')
  const b = drafts.noteDraftKey('Ravi', 'c1')
  assert.notEqual(a, b, 'two people never see each other\'s draft')
  drafts.setNoteDraft(a, 'Candidate will send the resume tomorrow')
  assert.equal(drafts.getNoteDraft(a), 'Candidate will send the resume tomorrow')
  assert.equal(drafts.getNoteDraft(b), '')
  drafts.setNoteDraft(a, '   ')
  assert.equal(drafts.getNoteDraft(a), '', 'a blank note is no draft')
  for (let i = 0; i < 250; i += 1) drafts.setNoteDraft(drafts.noteDraftKey('Asha', `c${i}`), `note ${i}`)
  assert.equal(drafts.getNoteDraft(drafts.noteDraftKey('Asha', 'c0')), '', 'the oldest draft was dropped')
  assert.equal(drafts.getNoteDraft(drafts.noteDraftKey('Asha', 'c249')), 'note 249')
})
