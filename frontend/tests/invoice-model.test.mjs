import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/application-form/invoice-model.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { feePaise, feePreview, paymentPaise } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('only staff-entered fees from 5000 through 30000 with up to two decimal places are accepted', () => {
  assert.equal(feePaise('5000'), 500000)
  assert.equal(feePaise('30000.00'), 3000000)
  assert.equal(feePaise(' 12345.67 '), 1234567)
  for (const value of ['', '0', '4999.99', '30000.01', '-5000', '5000.001', '5e3', 'Infinity', 'NaN', '5,000']) assert.equal(feePaise(value), null, value)
})

test('the confirmed CGST and SGST are calculated separately in paise before showing the total', () => {
  assert.deepEqual(feePreview('5000'), { subtotal: 500000, cgst: 45000, sgst: 45000, total: 590000 })
  assert.deepEqual(feePreview('30000'), { subtotal: 3000000, cgst: 270000, sgst: 270000, total: 3540000 })
  assert.deepEqual(feePreview('12345.67'), { subtotal: 1234567, cgst: 111111, sgst: 111111, total: 1456789 })
  assert.equal(feePreview(''), null)
})

test('a preview follows server tax rates instead of a previously configured rate', () => {
  assert.deepEqual(feePreview('5000', '6.00', '6.00'), { subtotal: 500000, cgst: 30000, sgst: 30000, total: 560000 })
  assert.equal(feePreview('5000', 'not configured', '9.00'), null)
})

test('partial receipts can be smaller than the service-fee minimum, with positive whole-paise values', () => {
  assert.equal(paymentPaise('0.01'), 1)
  assert.equal(paymentPaise('2000.50'), 200050)
  for (const value of ['', '0', '-5', '10.001', '1e3']) assert.equal(paymentPaise(value), null)
})
