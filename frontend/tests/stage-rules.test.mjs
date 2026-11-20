import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/shared/workflow/stages.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { stageByNo, parseStageNumber, stopExits, STAGES, TOTAL_STAGES, PCC_STAGE, ADVANCE_STAGE, PASSPORT_STAGE, closureLabels } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const file = { id: 'stored-file' }
const feedback = 'Reviewed with the candidate and approved moving to the next stage.'

test('the workflow has twelve stages, in order, with Medical at 6 and the new PCC at 7', () => {
  assert.equal(TOTAL_STAGES, 12)
  assert.deepEqual(STAGES.map((stage) => stage.no), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
  assert.deepEqual(STAGES.map((stage) => stage.key), ['prescreening', 'forwarded', 'interview_scheduling', 'interview_result', 'offer_letter', 'medical', 'pcc',
    'passport', 'advance', 'visa_arrival', 'visa_stamping', 'air_ticket'])
  assert.deepEqual(STAGES.map((stage) => stage.title), ['Prescreening', 'Forwarded to client', 'Interview scheduling', 'Interview result', 'Offer letter', 'Medical', 'PCC',
    'Passport', 'Advance payment', 'Visa arrival + Google review', 'Visa stamping', 'Air ticket'])
  assert.equal(new Set(STAGES.map((stage) => stage.taskTitle)).size, 12, 'every stage names its own task')
  assert.equal(stageByNo(6).taskTitle, 'Complete the medical')
  assert.equal(stageByNo(7).taskTitle, 'Collect the police clearance (PCC)')
  assert.equal(stageByNo(8).taskTitle, 'Collect original passport')
  assert.equal(stageByNo(9).taskTitle, 'Collect the advance')
  assert.equal(stageByNo(12).taskTitle, 'Book the air ticket')
  assert.deepEqual([PCC_STAGE, PASSPORT_STAGE, ADVANCE_STAGE], [7, 8, 9])
})

test('Medical keeps only the medical fields and PCC has only the PCC fields, both rendered from the config like every stage', () => {
  const names = (no) => stageByNo(no).fields.map((field) => field.name)
  assert.deepEqual(names(6), ['feedback', 'applied', 'appliedDate', 'result', 'reapplying', 'retestDate'])
  assert.deepEqual(names(7), ['feedback', 'pccStatus', 'pccAppliedDate', 'pccReceivedDate', 'file'])
  const pcc = stageByNo(7)
  assert.deepEqual(pcc.fields.find((field) => field.name === 'pccStatus').options.map((option) => option.value), ['received', 'pending'])
  assert.equal(pcc.fields.find((field) => field.name === 'file').type, 'file')
  assert.equal(pcc.fields.find((field) => field.name === 'feedback').required, true)
  assert.ok(pcc.fields.filter((field) => field.name !== 'feedback').every((field) => field.required === false), 'a PCC detail is never a gate')
  assert.equal(pcc.decision, undefined)
  assert.equal(pcc.closer, true)
})

test('PCC records Received, Pending or a manual move, and needs only the feedback', () => {
  const run = (data) => stageByNo(7).evaluate({ feedback, ...data })
  assert.equal(run({}).outcome, 'Completed manually')
  assert.equal(run({ pccStatus: 'received' }).outcome, 'Received')
  assert.equal(run({ pccStatus: 'pending' }).outcome, 'PCC pending')
  assert.equal(run({ pccStatus: 'received', pccAppliedDate: '2026-11-01', pccReceivedDate: '2026-11-10', file }).canComplete, true)
  for (const data of [{ pccStatus: 'done' }, { pccStatus: 'received', pccReceivedDate: '2026-02-30' }, { file: 'fake.pdf' }]) assert.equal(run(data).canComplete, false, JSON.stringify(data))
  assert.equal(stageByNo(7).evaluate({ pccStatus: 'received' }).canComplete, false, 'no feedback')
  for (const result of [run({}), run({ pccStatus: 'pending' })]) {
    assert.equal(result.closes, undefined)
    assert.equal(result.repeats, undefined)
  }
})

test('all twelve stages retain their recorded outcomes with feedback', () => {
  const values = [{ result: 'passed' }, { forwarded: 'yes' }, { scheduled: 'yes', date: '2026-11-02' },
    { result: 'selected', remarks: 'Approved' }, { result: 'agreed', file }, { result: 'fit' }, { pccStatus: 'received', file },
    { collected: 'yes' }, { collected: 'yes', amount: 20, receipt: file }, { arrived: 'yes' }, { done: 'yes', file }, { file, travelDate: '2026-11-15' }]
  assert.equal(values.length, TOTAL_STAGES)
  values.forEach((data, index) => assert.equal(stageByNo(index + 1).evaluate({ ...data, feedback }).canComplete, true, `stage ${index + 1}`))
})

test('the first eleven stages can be moved manually except the three decisions, while deployment needs an actual ticket and travel date', () => {
  for (let stage = 1; stage < TOTAL_STAGES; stage++) {
    if (stageByNo(stage).decision) continue
    const result = stageByNo(stage).evaluate({ feedback })
    assert.equal(result.canComplete, true, `stage ${stage}`)
    assert.equal(result.outcome, 'Completed manually')
    assert.equal(result.closes, undefined)
    assert.equal(result.repeats, undefined)
    assert.ok(stageByNo(stage).fields.every((field) => field.type !== 'file' || !field.required))
  }
  const missing = stageByNo(12).evaluate({ feedback })
  assert.equal(missing.canComplete, false)
  assert.deepEqual(missing.missing, ['Air ticket', 'Travel date'])
  assert.equal(stageByNo(12).evaluate({ feedback, file }).canComplete, false)
  assert.equal(stageByNo(12).evaluate({ feedback, travelDate: '2026-11-15' }).canComplete, false)
  assert.equal(stageByNo(12).evaluate({ feedback, file, travelDate: '2026-11-15' }).outcome, 'Booked')
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
  assert.equal(stageByNo(5).evaluate({ feedback, result: 'declined' }).closes.reason, 'offer_declined')
  assert.equal(stageByNo(5).evaluate({ feedback, result: 'not_received' }).closes.reason, 'offer_not_received')
  assert.equal(stageByNo(6).evaluate({ feedback, result: 'unfit' }).closes.reason, 'medical_unfit')
})

// Interview result, Offer letter and Medical: each choice is explicit, needs feedback, and only the positive one moves on.
const decisions = [
  [4, { result: 'selected' }, 'Selected', null],
  [4, { result: 'rejected' }, 'Rejected', 'interview_rejected'],
  [5, { result: 'agreed' }, 'Agreed', null],
  [5, { result: 'declined' }, 'Declined', 'offer_declined'],
  [5, { result: 'not_received' }, 'Not received', 'offer_not_received'],
  [6, { result: 'fit' }, 'Fit', null],
  [6, { result: 'unfit' }, 'Unfit', 'medical_unfit'],
]

test('each decision records its outcome and only Selected, Agreed and Fit let the candidate go on', () => {
  for (const [stage, data, outcome, closes] of decisions) {
    const result = stageByNo(stage).evaluate({ feedback, ...data })
    assert.equal(result.canComplete, true, `${stage} ${outcome}`)
    assert.equal(result.outcome, outcome)
    assert.equal(result.closes?.reason ?? null, closes)
    assert.equal(stageByNo(stage).decision.advances.includes(data.result), closes === null, `${stage} ${outcome}`)
  }
})

test('a decision stage cannot be moved until the choice is made, whatever else is filled in', () => {
  const labels = { 4: 'Interview result', 5: 'Offer letter result', 6: 'Medical result' }
  const details = { 4: { remarks: 'Went well' }, 5: { obtained: 'yes', file }, 6: { applied: 'yes', appliedDate: '2026-11-02' } }
  for (const stage of [4, 5, 6]) {
    for (const data of [{}, details[stage]]) {
      const result = stageByNo(stage).evaluate({ feedback, ...data })
      assert.equal(result.canComplete, false, `stage ${stage}`)
      assert.deepEqual(result.missing, [labels[stage]])
    }
    assert.equal(stageByNo(stage).decision.prompt, labels[stage])
    assert.match(stageByNo(stage).decision.message, /^Choose the .* before moving this candidate\.$/)
  }
})

test('every choice on a decision stage still needs written feedback', () => {
  for (const [stage, data] of decisions) {
    for (const value of [undefined, '', '   ']) {
      const result = stageByNo(stage).evaluate({ feedback: value, ...data })
      assert.equal(result.canComplete, false, `stage ${stage} ${data.result}`)
      assert.ok(result.missing.some((message) => message.startsWith('Stage feedback')))
    }
  }
})

test('the decision is a required field of its stage, offered as listed choices only', () => {
  const offers = { 4: ['selected', 'rejected'], 5: ['agreed', 'declined', 'not_received'], 6: ['fit', 'unfit'] }
  for (const stage of [4, 5, 6]) {
    const def = stageByNo(stage)
    const field = def.fields.find((f) => f.name === def.decision.field)
    assert.equal(field.required, true)
    assert.deepEqual(field.options.map((o) => o.value), offers[stage])
    assert.equal(def.closer, true)
    assert.equal(def.evaluate({ feedback, result: 'maybe' }).canComplete, false)
  }
  // The older "offer obtained" answer is not a way past the offer letter.
  assert.equal(stageByNo(5).evaluate({ feedback, obtained: 'yes', file }).canComplete, false)
})

test('medical retry records an attempt without closing or advancing', () => {
  const result = stageByNo(6).evaluate({ feedback, result: 'unfit', reapplying: 'yes', retestDate: '2026-11-02' })
  assert.equal(result.canComplete, true)
  assert.equal(result.repeats, true)
  assert.equal(result.closes, undefined)
  assert.equal(stageByNo(6).evaluate({ feedback, result: 'unfit', reapplying: 'yes' }).canComplete, false)
  assert.equal(stageByNo(6).evaluate({ feedback, result: 'unfit', reapplying: 'no' }).closes.reason, 'medical_unfit')
  assert.equal(stageByNo(6).evaluate({ feedback, result: 'unfit' }).repeats, undefined)
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
    [5, { result: 'agreed', file: 'fake.pdf' }], [5, { result: 'accepted' }], [9, { collected: 'yes', amount: -1, receipt: file }],
    [9, { collected: 'yes', amount: 1.111, receipt: file }], [10, { arrived: 'yes', googleReview: 'yes' }], [7, { pccStatus: 'maybe' }],
    [6, { result: 'unfit', reapplying: 'yes', appliedDate: '2026-11-03', retestDate: '2026-11-02' }]]
  for (const [stage, data] of cases) assert.equal(stageByNo(stage).evaluate({ ...data, feedback }).canComplete, false, JSON.stringify(data))
})

test('every stage offers a stopping exit that closes the application and needs feedback', () => {
  const expected = {
    1: ['rejected', 'declined'], 2: ['rejected', 'declined'], 3: ['rejected', 'declined', 'not_possible'], 4: ['rejected', 'declined'],
    5: ['rejected', 'declined'], 6: ['rejected', 'declined'], 7: ['rejected', 'declined', 'not_cleared'], 8: ['rejected', 'declined', 'not_available'],
    9: ['rejected', 'declined', 'not_paid'], 10: ['rejected', 'declined', 'visa_rejected'], 11: ['rejected', 'declined', 'refused'], 12: ['not_travelling'],
  }
  for (let stage = 1; stage <= TOTAL_STAGES; stage++) {
    const def = stageByNo(stage)
    assert.equal(def.closer, true, `stage ${stage}`)
    assert.deepEqual(def.stops.map((option) => option.value), expected[stage], `stage ${stage}`)
    for (const { value } of def.stops) {
      const result = def.evaluate({ feedback, stop: value })
      assert.equal(result.canComplete, true, `stage ${stage} ${value}`)
      assert.ok(result.closes?.reason && result.closes.label && result.outcome, `stage ${stage} ${value}`)
      assert.equal(result.repeats, undefined)
      // Feedback is required for the exit too.
      assert.equal(def.evaluate({ stop: value }).canComplete, false, `stage ${stage} ${value} without feedback`)
    }
  }
})

test('the stopping exit uses the same closure reasons as the server and wins over the stage answers', () => {
  assert.equal(stageByNo(3).evaluate({ feedback, stop: 'not_possible' }).closes.reason, 'interview_not_possible')
  assert.equal(stageByNo(7).evaluate({ feedback, stop: 'not_cleared' }).closes.reason, 'pcc_not_cleared')
  assert.equal(closureLabels.pcc_not_cleared, 'PCC not cleared')
  assert.equal(stageByNo(8).evaluate({ feedback, stop: 'not_available' }).closes.reason, 'passport_not_available')
  assert.equal(stageByNo(9).evaluate({ feedback, stop: 'not_paid' }).closes.reason, 'advance_not_paid')
  assert.equal(stageByNo(10).evaluate({ feedback, stop: 'visa_rejected' }).closes.reason, 'visa_rejected')
  assert.equal(stageByNo(11).evaluate({ feedback, stop: 'refused' }).closes.reason, 'visa_stamping_refused')
  assert.equal(stageByNo(12).evaluate({ feedback, stop: 'not_travelling' }).closes.reason, 'not_travelling')
  // Each exit stayed with its own stage when PCC was inserted: the old Passport exit is not offered at the new stage 7, nor at Medical.
  assert.deepEqual(stopExits(7).map((exit) => exit.value), ['rejected', 'declined', 'not_cleared'])
  assert.ok(!stopExits(6).some((exit) => exit.value === 'not_available'))
  assert.equal(stageByNo(7).evaluate({ feedback, stop: 'not_available' }).canComplete, false)
  assert.equal(stageByNo(2).evaluate({ feedback, stop: 'declined' }).closes.reason, 'stage_declined')
  assert.equal(stageByNo(4).evaluate({ feedback, stop: 'rejected' }).closes.reason, 'interview_rejected')
  assert.equal(stageByNo(5).evaluate({ feedback, stop: 'declined' }).closes.reason, 'offer_declined')
  assert.equal(stageByNo(4).evaluate({ feedback, result: 'selected', stop: 'declined' }).closes.reason, 'stage_declined')
  assert.equal(stageByNo(6).evaluate({ feedback, result: 'unfit', reapplying: 'yes', retestDate: '2026-12-01', stop: 'declined' }).repeats, undefined)
  // Not offered at the stage: refused. Stage 12 has no Rejected or Declined, and deployment still needs the ticket and date.
  assert.equal(stageByNo(12).evaluate({ feedback, stop: 'declined' }).canComplete, false)
  assert.equal(stageByNo(5).evaluate({ feedback, stop: 'not_paid' }).canComplete, false)
  assert.equal(stageByNo(12).evaluate({ feedback }).canComplete, false)
})
