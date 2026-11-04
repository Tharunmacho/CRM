import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/form-options/form-options-model.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const model = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const { FALLBACK_OPTIONS, FORM_OPTION_CATEGORIES, normalizeFormOptions, activeOptions, choicesFor, labelFor, toneStyle, slugValue, reorder, optionLabelError } = model

const served = {
  categories: [
    { key: 'interest_level', label: 'Interest level', strict: true, fixedValues: false, allowCustom: false, options: [
      { id: 'i2', value: 'interested', label: 'Keen to go', hint: 'Renamed', tone: 'info', sortOrder: 20, isActive: true, isSystem: true },
      { id: 'i1', value: 'more_interested', label: 'More interested', hint: null, tone: 'success', sortOrder: 10, isActive: true, isSystem: true },
      { id: 'i3', value: 'maybe_later', label: 'Maybe later', tone: 'warning', sortOrder: 30, isActive: false, isSystem: false },
    ] },
    { key: 'candidate_source', label: 'Source', strict: true, fixedValues: true, allowCustom: false, options: [] },
    { key: 'marital_status', label: 'Marital status', strict: false, fixedValues: false, allowCustom: true, options: [{ id: 'm1', value: 'Single', label: 'Single', sortOrder: 10, isActive: true }, { value: '', label: 'broken' }, null] },
  ],
}

test('the served lists are read by category with their rules, and broken rows are skipped', () => {
  const { options, meta } = normalizeFormOptions(served)
  assert.deepEqual(Object.keys(options), ['interest_level', 'candidate_source', 'marital_status'])
  assert.equal(options.marital_status.length, 1)
  assert.deepEqual(options.interest_level[0], { id: 'i2', category: 'interest_level', value: 'interested', label: 'Keen to go', hint: 'Renamed', tone: 'info', sort: 20, active: true, system: true })
  assert.equal(meta.candidate_source.fixedValues, true)
  assert.equal(meta.interest_level.strict, true)
  assert.equal(meta.marital_status.allowCustom, true)
  assert.deepEqual(normalizeFormOptions(null), { options: {}, meta: {} })
  assert.deepEqual(normalizeFormOptions({ categories: 'nope' }), { options: {}, meta: {} })
})

test('a list shows its active options in the saved order, never the hidden ones', () => {
  const { options } = normalizeFormOptions(served)
  assert.deepEqual(activeOptions(options, 'interest_level').map((option) => option.value), ['more_interested', 'interested'])
  assert.deepEqual(activeOptions(options, 'unknown'), [])
})

test('a value a candidate already has stays visible even when its option is hidden or was never in the list', () => {
  const { options } = normalizeFormOptions(served)
  assert.deepEqual(choicesFor(options, 'interest_level', 'interested').map((option) => option.value), ['more_interested', 'interested'])
  assert.deepEqual(choicesFor(options, 'interest_level', 'maybe_later').map((option) => [option.value, option.label]), [['more_interested', 'More interested'], ['interested', 'Keen to go'], ['maybe_later', 'Maybe later']])
  const unknown = choicesFor(options, 'marital_status', 'Engaged')
  assert.deepEqual(unknown.map((option) => option.value), ['Single', 'Engaged'])
  assert.equal(unknown[1].label, 'Engaged')
  assert.deepEqual(choicesFor(options, 'marital_status', '  ').map((option) => option.value), ['Single'])
})

test('labels follow what an admin set, and fall back to the stored value', () => {
  const { options } = normalizeFormOptions(served)
  assert.equal(labelFor(options, 'interest_level', 'interested'), 'Keen to go')
  assert.equal(labelFor(options, 'interest_level', 'something_else'), 'something_else')
})

test('the built-in lists exist for every category and are used only as a fallback shape', () => {
  for (const key of Object.values(FORM_OPTION_CATEGORIES)) assert.ok(Array.isArray(FALLBACK_OPTIONS[key]), key)
  assert.deepEqual(FALLBACK_OPTIONS.interest_level.map((option) => option.value), ['more_interested', 'interested', 'somewhat_interested', 'not_interested'])
  assert.ok(FALLBACK_OPTIONS.interest_level.every((option) => option.tone && option.active))
})

test('tones map to card colours: the server names, a plain colour name or a hex colour', () => {
  assert.deepEqual(toneStyle('success'), { name: 'green' })
  assert.deepEqual(toneStyle('info'), { name: 'blue' })
  assert.deepEqual(toneStyle('warning'), { name: 'amber' })
  assert.deepEqual(toneStyle('danger'), { name: 'red' })
  assert.deepEqual(toneStyle('neutral'), { name: 'gray' })
  assert.deepEqual(toneStyle('#0ea5e9'), { color: '#0ea5e9' })
  assert.deepEqual(toneStyle('not-a-tone'), {})
  assert.deepEqual(toneStyle(null), {})
})

test('a new option gets a stable code from its name', () => {
  assert.equal(slugValue('Somewhat keen'), 'somewhat_keen')
  assert.equal(slugValue('  Very  Keen! '), 'very_keen')
  assert.equal(slugValue('Café time'), 'cafe_time')
  assert.equal(slugValue('???'), '')
})

test('moving an option renumbers the whole list and stops at the ends', () => {
  const rows = [{ value: 'a', sort: 10 }, { value: 'b', sort: 20 }, { value: 'c', sort: 30 }]
  assert.deepEqual(reorder(rows, 'b', -1).map((row) => [row.value, row.sort]), [['b', 10], ['a', 20], ['c', 30]])
  assert.deepEqual(reorder(rows, 'b', 1).map((row) => row.value), ['a', 'c', 'b'])
  assert.deepEqual(reorder(rows, 'a', -1).map((row) => row.value), ['a', 'b', 'c'])
  assert.deepEqual(reorder(rows, 'c', 1).map((row) => row.value), ['a', 'b', 'c'])
  assert.deepEqual(reorder([{ value: 'x', sort: 55 }, { value: 'y', sort: 7 }], 'x', -1).map((row) => [row.value, row.sort]), [['x', 10], ['y', 20]])
})

test('an option name must be new, short and not empty', () => {
  const rows = [{ value: 'a', label: 'Agreed' }, { value: 'b', label: 'Pending' }]
  assert.equal(optionLabelError('', rows), 'Enter a name.')
  assert.equal(optionLabelError('x'.repeat(81), rows), 'Use at most 80 characters.')
  assert.equal(optionLabelError(' agreed ', rows), 'This name is already in the list.')
  assert.equal(optionLabelError('Agreed', rows, 'a'), undefined, 'renaming to its own name is fine')
  assert.equal(optionLabelError('Declined', rows), undefined)
})
