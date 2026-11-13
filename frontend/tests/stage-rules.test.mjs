import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/shared/workflow/stages.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { stageByNo, parseStageNumber, TOTAL_STAGES } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const file = { id: 'stored-file' }
const feedback = 'Reviewed with the candidate and approved moving to the next stage.'

test('all eleven stages retain their recorded outcomes with feedback', () => {
  const values = [{ result: 'passed' }, { forwarded: 'yes' }, { scheduled: 'yes', date: '2026-11-02' },
    { result: 'selected', remarks: 'Approved' }, { obtained: 'yes', file }, { result: 'fit' },
    { collected: 'yes' }, { collected: 'yes', amount: 20, receipt: file }, { arrived: 'yes' }, { done: 'yes', file }, { file, travelDate: '2026-11-15' }]
  values.forEach((data, index) => assert.equal(stageByNo(index + 1).evaluate({ ...data, feedback }).canComplete, true, `stage ${index + 1}`))
})

test('the first ten stages can be moved manually, while deployment needs an actual ticket and travel date', () => {
  for (let stage = 1; stage < TOTAL_STAGES; stage++) {
    const result = stageByNo(stage).evaluate({ feedback })
    assert.equal(result.canComplete, true, `stage ${stage}`)
    assert.equal(result.outcome, 'Completed manually')
    assert.equal(result.closes, undefined)
    assert.equal(result.repeats, undefined)
    assert.ok(stageByNo(stage).fields.every((field) => field.type !== 'file' || !field.required))
  }
  const missing = stageByNo(11).evaluate({ feedback })
  assert.equal(missing.canComplete, false)
  assert.deepEqual(missing.missing, ['Air ticket', 'Travel date'])
  assert.equal(stageByNo(11).evaluate({ feedback, file }).canComplete, false)
  assert.equal(stageByNo(11).evaluate({ feedback, travelDate: '2026-11-15' }).canComplete, false)
  assert.equal(stageByNo(11).evaluate({ feedback, file, travelDate: '2026-11-15' }).outcome, 'Booked')
})

test('every stage requires new written feedback even when every other detail is filled', () => {
  for (let stage = 1; stage <= TOTAL_STAGES; stage++) {
    for (const value of [undefined, null, '', '   ', {}, 123, 'x'.repeat(10001)]) {
      const result = stageByNo(stage).evaluate({ feedback: value, file })
      assert.equal(result.canComplete, false, `stage ${stage}, feedback ${String(value).slice(0, 20)}`)
      assert.ok(result.missing.some((message) => message.startsWith('Stage feedback')))
    }
  }
})

test('explicit negative outcomes still close or repeat, never silently advance', () => {
  assert.equal(stageByNo(1).evaluate({ feedback, result: 'failed' }).closes.reason, 'prescreen_failed')
  assert.equal(stageByNo(2).evaluate({ feedback, forwarded: 'no' }).canComplete, false)
  assert.equal(stageByNo(2).evaluate({ feedback, forwarded: 'no', reason: 'resume_not_received' }).closes.reason, 'resume_not_received')
  assert.equal(stageByNo(4).evaluate({ feedback, result: 'rejected' }).closes.reason, 'interview_rejected')
  assert.equal(stageByNo(6).evaluate({ feedback, result: 'unfit' }).canComplete, false)
})

test('medical retry records an attempt without closing or advancing', () => {
  const result = stageByNo(6).evaluate({ feedback, result: 'unfit', reapplying: 'yes', retestDate: '2026-11-02' })
  assert.equal(result.canComplete, true)
  assert.equal(result.repeats, true)
  assert.equal(result.closes, undefined)
  assert.equal(stageByNo(6).evaluate({ feedback, result: 'unfit', reapplying: 'yes' }).canComplete, false)
  assert.equal(stageByNo(6).evaluate({ feedback, result: 'unfit', reapplying: 'no' }).closes.reason, 'medical_unfit')
})

test('a Fit result is not blocked by a retained date from a previous re-medical selection', () => {
  const data = { feedback, applied: 'yes', appliedDate: '2026-11-03', result: 'unfit', reapplying: 'yes', retestDate: '2026-11-02' }
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
    [3, { scheduled: 'yes', date: '2026-11-02', time: '25:00' }],
    [5, { obtained: 'yes', file: 'fake.pdf' }], [8, { collected: 'yes', amount: -1, receipt: file }],
    [8, { collected: 'yes', amount: 1.111, receipt: file }], [9, { arrived: 'yes', googleReview: 'yes' }],
    [6, { result: 'unfit', reapplying: 'yes', appliedDate: '2026-11-03', retestDate: '2026-11-02' }]]
  for (const [stage, data] of cases) assert.equal(stageByNo(stage).evaluate({ ...data, feedback }).canComplete, false, JSON.stringify(data))
})
