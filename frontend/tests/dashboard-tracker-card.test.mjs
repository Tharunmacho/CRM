import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/reports/tracker-summary.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { summarizeTracker, trackerLink, WAITING_STAGES } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

const row = (stageNo, travelled = false) => ({ stageNo, travelled })

test('in progress and travelled are counted from the rows, and they add up to the total', () => {
  const summary = summarizeTracker([row(5), row(5), row(13), row(13, true), row(13, true)])
  assert.equal(summary.total, 5)
  assert.equal(summary.inProgress, 3)
  assert.equal(summary.travelled, 2)
})

test('the waiting chips run Offer to Air ticket, count only placements still in progress, and show zero when nobody waits', () => {
  const summary = summarizeTracker([row(5), row(5), row(8), row(9), row(9), row(10), row(13), row(13, true), row(2), row(4)])
  assert.deepEqual(summary.waiting.map(stage => [stage.no, stage.label, stage.count]), [
    [5, 'Offer', 2], [6, 'Advance', 0], [7, 'Passport', 0], [8, 'Medical', 1], [9, 'PCC', 2], [10, 'Visa', 1], [11, 'Review', 0], [12, 'Stamp', 0], [13, 'Air ticket', 1],
  ])
  assert.equal(WAITING_STAGES.length, 9)
})

test('earlier stages are in progress but have no chip', () => {
  const summary = summarizeTracker([row(1), row(2), row(3), row(4)])
  assert.equal(summary.inProgress, 4)
  assert.equal(summary.waiting.reduce((sum, stage) => sum + stage.count, 0), 0)
})

test('an empty tracker is all zeros', () => {
  const summary = summarizeTracker([])
  assert.deepEqual([summary.total, summary.inProgress, summary.travelled], [0, 0, 0])
  assert.equal(summary.waiting.length, 9)
})

test('links open the tracker tab; the stage is added only when the tracker reads it', () => {
  assert.equal(trackerLink(), '/reports?tab=tracker')
  assert.equal(trackerLink(7), '/reports?tab=tracker&stage=7', 'the tracker reads ?stage=, so a chip opens it at that stage')
  assert.equal(trackerLink(7, false), '/reports?tab=tracker')
  assert.equal(trackerLink(7, true), '/reports?tab=tracker&stage=7')
  assert.equal(trackerLink(undefined, true), '/reports?tab=tracker')
})
