import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const load = async (path) => {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}
const { stageByNo, fieldVisible } = await load('../src/shared/workflow/stages.ts')
const { advanceStatusOf, advanceSummary, shortAmount, withAdvanceStatus } = await load('../src/shared/workflow/advance.ts')
const feedback = 'Spoke to the candidate about the advance.'
const stage8 = stageByNo(8)

test('stage 8 offers the status, the amount and an optional total service charge, in that order', () => {
  const names = stage8.fields.map((field) => field.name)
  assert.deepEqual(names.slice(0, 4), ['feedback', 'advanceStatus', 'amount', 'serviceChargeTotal'])
  const status = stage8.fields.find((field) => field.name === 'advanceStatus')
  assert.deepEqual(status.options, [{ value: 'collected', label: 'Collected' }, { value: 'pending', label: 'Pending' }])
  assert.equal(stage8.fields.find((field) => field.name === 'serviceChargeTotal').label, 'Total service charge (optional)')
  assert.equal(stage8.fields.find((field) => field.name === 'serviceChargeTotal').required, false)
})

test('the amounts show for both statuses, the payment details only for a collected advance', () => {
  const shown = (data) => stage8.fields.filter((field) => fieldVisible(field, data)).map((field) => field.name)
  assert.deepEqual(shown({}), ['feedback', 'advanceStatus'])
  assert.deepEqual(shown({ advanceStatus: 'pending' }), ['feedback', 'advanceStatus', 'amount', 'serviceChargeTotal', 'amountCurrency'])
  assert.deepEqual(shown({ advanceStatus: 'collected' }), ['feedback', 'advanceStatus', 'amount', 'serviceChargeTotal', 'amountCurrency', 'date', 'mode', 'receiptNo', 'receipt'])
})

test('nothing new is required to complete stage 8, and the outcome names the status', () => {
  const run = (data) => stage8.evaluate({ feedback, ...data })
  assert.equal(run({}).outcome, 'Completed manually')
  assert.equal(run({ collected: 'yes', amount: 20 }).outcome, 'Collected')
  assert.equal(run({ advanceStatus: 'collected' }).outcome, 'Collected')
  assert.equal(run({ advanceStatus: 'pending' }).outcome, 'Advance pending')
  assert.equal(run({ advanceStatus: 'pending', amount: 10000, serviceChargeTotal: 50000 }).canComplete, true)
  assert.equal(run({ advanceStatus: 'pending', stop: 'not_paid' }).closes.reason, 'advance_not_paid')
})

test('the amounts must be positive, and the total can never be below the advance', () => {
  const run = (data) => stage8.evaluate({ feedback, ...data })
  for (const data of [{ serviceChargeTotal: -5 }, { serviceChargeTotal: 0.001 }, { serviceChargeTotal: true }, { advanceStatus: 'later' }]) {
    assert.equal(run(data).canComplete, false, JSON.stringify(data))
  }
  const low = run({ advanceStatus: 'collected', amount: 60000, serviceChargeTotal: 50000 })
  assert.equal(low.canComplete, false)
  assert.deepEqual(low.missing, ['Total service charge cannot be less than the advance'])
  assert.equal(run({ advanceStatus: 'pending', amount: 60000, serviceChargeTotal: 50000 }).canComplete, false)
  assert.equal(run({ advanceStatus: 'collected', amount: 50000, serviceChargeTotal: 50000 }).canComplete, true)
  assert.equal(run({ advanceStatus: 'pending', serviceChargeTotal: 45000 }).canComplete, true)
})

test('an advance saved before the status existed reads as collected and shows in the form', () => {
  assert.equal(advanceStatusOf({ amount: 5000 }), 'collected')
  assert.equal(advanceStatusOf({ collected: 'yes' }), 'collected')
  assert.equal(advanceStatusOf({ collected: 'no' }), 'pending')
  assert.equal(advanceStatusOf({}), null)
  assert.equal(advanceStatusOf({ collected: 'no', stop: 'not_paid' }), null)
  assert.deepEqual(withAdvanceStatus({ amount: 5000 }), { amount: 5000, advanceStatus: 'collected' })
  assert.deepEqual(withAdvanceStatus({}), {})
  const explicit = { advanceStatus: 'pending', amount: 5000 }
  assert.equal(withAdvanceStatus(explicit), explicit)
})

test('amounts are shortened to k only when they are round thousands', () => {
  assert.deepEqual([60000, 10000, 12500, 1000, 1100, 500, 12345, 10050, 1500.75, 0, null, 'x'].map(shortAmount),
    ['60k', '10k', '12.5k', '1k', '1.1k', '500', '12345', '10050', '1500.75', '', '', ''])
})

test('the history reads "Advance 10k collected / 50k total"', () => {
  assert.equal(advanceSummary({ advanceStatus: 'collected', amount: 10000, serviceChargeTotal: 50000, amountCurrency: 'INR' }, 'INR'), 'Advance 10k collected / 50k total')
  assert.equal(advanceSummary({ advanceStatus: 'collected', amount: 60000 }, 'INR'), 'Advance 60k collected')
  assert.equal(advanceSummary({ amount: 5000, amountCurrency: 'INR' }, 'INR'), 'Advance 5k collected')
  assert.equal(advanceSummary({ advanceStatus: 'collected' }, 'INR'), 'Advance collected')
  assert.equal(advanceSummary({ advanceStatus: 'pending', amount: 10000 }, 'INR'), 'Advance pending, 10k expected')
  assert.equal(advanceSummary({ advanceStatus: 'pending', amount: 10000, serviceChargeTotal: 45000 }, 'INR'), 'Advance pending, 10k expected / 45k total')
  assert.equal(advanceSummary({ advanceStatus: 'pending' }, 'INR'), 'Advance pending')
  assert.equal(advanceSummary({ advanceStatus: 'collected', amount: 600, amountCurrency: 'USD' }, 'INR'), 'Advance 600 collected USD')
  assert.equal(advanceSummary({}, 'INR'), null)
  assert.equal(advanceSummary(null), null)
})
