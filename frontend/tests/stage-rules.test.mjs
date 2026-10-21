import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/shared/workflow/stages.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { stageByNo, parseStageNumber, TOTAL_STAGES } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const file = { id: 'stored-file' }

test('all eleven stages accept their PRD completion minimums', () => {
  const values = [{ result: 'passed' }, { forwarded: 'yes' }, { scheduled: 'yes', date: '2026-11-02' },
    { result: 'selected', remarks: 'Approved' }, { obtained: 'yes', file }, { result: 'fit' },
    { collected: 'yes' }, { collected: 'yes', amount: 20, receipt: file }, { arrived: 'yes' }, { done: 'yes', file }, { file }]
  values.forEach((data, index) => assert.equal(stageByNo(index + 1).evaluate(data).canComplete, true, `stage ${index + 1}`))
})

test('medical retry records an attempt without closing or advancing', () => {
  const result = stageByNo(6).evaluate({ result: 'unfit', reapplying: 'yes', retestDate: '2026-11-02' })
  assert.equal(result.canComplete, true)
  assert.equal(result.repeats, true)
  assert.equal(result.closes, undefined)
  assert.equal(stageByNo(6).evaluate({ result: 'unfit', reapplying: 'yes' }).canComplete, false)
  assert.equal(stageByNo(6).evaluate({ result: 'unfit', reapplying: 'no' }).closes.reason, 'medical_unfit')
})

test('a Fit result is not blocked by a retained date from a previous re-medical selection', () => {
  const data = { applied: 'yes', appliedDate: '2026-11-03', result: 'unfit', reapplying: 'yes', retestDate: '2026-11-02' }
  assert.equal(stageByNo(6).evaluate(data).canComplete, false)
  const result = stageByNo(6).evaluate({ ...data, result: 'fit' })
  assert.equal(result.canComplete, true)
  assert.equal(result.outcome, 'Fit')
  assert.equal(result.repeats, undefined)
  assert.equal(result.closes, undefined)
})

test('stage route filters accept existing stages and ignore malformed or out-of-range values', () => {
  for (let stage = 1; stage <= TOTAL_STAGES; stage++) {
    const parsed = parseStageNumber(String(stage))
    assert.equal(parsed, stage)
    assert.ok(stageByNo(parsed).title)
  }
  for (const value of [null, '', '0', String(TOTAL_STAGES + 1), '-1', 'abc', '2.5', 'Infinity', '1e0', '0x1', ' 1 ']) {
    assert.equal(parseStageNumber(value), null, `invalid stage: ${value}`)
  }
})

test('malformed enum, dates, money and fabricated attachment strings cannot complete', () => {
  const cases = [[1, { result: 'anything' }], [3, { scheduled: 'yes', date: '2026-02-30' }],
    [3, { scheduled: 'yes', date: '2026-11-02', time: '25:00' }], [4, { result: 'selected', remarks: '   ' }],
    [5, { obtained: 'yes', file: 'fake.pdf' }], [8, { collected: 'yes', amount: -1, receipt: file }],
    [8, { collected: 'yes', amount: 1.111, receipt: file }], [9, { arrived: 'yes', googleReview: 'yes' }],
    [6, { result: 'unfit', reapplying: 'yes', appliedDate: '2026-11-03', retestDate: '2026-11-02' }]]
  for (const [stage, data] of cases) assert.equal(stageByNo(stage).evaluate(data).canComplete, false, JSON.stringify(data))
})
