import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { cleanCategoryName, sortedCategories, usedCategories, categorySuggestions, categoryNameError } = await load('../src/shared/lib/job-categories.ts')
const { EMPTY_SETTINGS } = await load('../src/shared/lib/agency.ts')

const categories = [
  { id: 'c', name: 'Security', active: true, sortOrder: 30 },
  { id: 'a', name: 'Construction', active: true, sortOrder: 10 },
  { id: 'b', name: 'Oil and gas', active: false, sortOrder: 20 },
]

test('names are trimmed and their inner spaces collapsed', () => {
  assert.equal(cleanCategoryName('  Oil   and  gas '), 'Oil and gas')
  assert.equal(cleanCategoryName('   '), '')
})

test('the list is shown in the agency order, then by name', () => {
  assert.deepEqual(sortedCategories(categories).map((item) => item.id), ['a', 'b', 'c'])
  assert.deepEqual(sortedCategories([{ id: 'x', name: 'B', active: true, sortOrder: 1 }, { id: 'y', name: 'A', active: true, sortOrder: 1 }]).map((item) => item.id), ['y', 'x'])
  assert.deepEqual(sortedCategories(undefined), [])
  assert.deepEqual(sortedCategories(null), [])
})

test('categories already used on job titles are listed once, most used first, ignoring case', () => {
  assert.deepEqual(usedCategories(['Test 1', 'test 1', null, ' ', 'Alpha', 'Alpha', 'alpha', 'Beta', undefined]), ['Alpha', 'Test 1', 'Beta'])
  assert.deepEqual(usedCategories([]), [])
})

test('the suggestions are the active managed categories first, then other categories already in use', () => {
  const used = ['Test 1', 'security', 'Test 1', 'Oil and gas', 'Welding']
  // Security and Construction are offered in the agency order; Oil and gas is switched off so it is not offered again
  // even though a job title still uses it; "security" is the managed one under another spelling.
  assert.deepEqual(categorySuggestions(categories, used), ['Construction', 'Security', 'Test 1', 'Welding'])
})

test('an older server without a category list falls back to the categories used on job titles', () => {
  assert.deepEqual(categorySuggestions(undefined, ['Test 1', 'Welding', 'Welding']), ['Welding', 'Test 1'])
  assert.deepEqual(categorySuggestions(null, []), [])
  assert.deepEqual(categorySuggestions([], ['Test 1']), ['Test 1'])
})

test('a category the agency switched off is not offered but a job title can still keep it', () => {
  const only = [{ id: 'z', name: 'Aviation', active: false, sortOrder: 1 }]
  assert.deepEqual(categorySuggestions(only, ['Aviation']), [])
})

test('the name form applies the same rules as the server', () => {
  assert.equal(categoryNameError('Hospitality', categories), undefined)
  assert.ok(categoryNameError('   ', categories))
  assert.ok(categoryNameError('A', categories))
  assert.ok(categoryNameError('x'.repeat(121), categories))
  assert.equal(categoryNameError('x'.repeat(120), categories), undefined)
  assert.equal(categoryNameError('  construction ', categories), 'That category is already in the list.')
  assert.equal(categoryNameError('Oil  AND gas', categories), 'That category is already in the list.')
  // Renaming a category to its own name in another case is allowed.
  assert.equal(categoryNameError('SECURITY', categories, 'c'), undefined)
  assert.equal(categoryNameError('security', categories, 'a'), 'That category is already in the list.')
})

test('the empty settings carry an empty, read-only category list', () => {
  assert.deepEqual(EMPTY_SETTINGS.categories, [])
  assert.equal(EMPTY_SETTINGS.categoriesEditable, false)
})
