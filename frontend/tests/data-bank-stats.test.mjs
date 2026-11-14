import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import React from 'react'
import ts from 'typescript'


// data-bank-stats.ts imports data-bank-pools.ts: transpile that one to a temp module the import can reach.
const poolsSource = await readFile(new URL('../src/features/candidates/data-bank-pools.ts', import.meta.url), 'utf8')
const poolsJs = ts.transpileModule(poolsSource, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const poolsUrl = `data:text/javascript;base64,${Buffer.from(poolsJs).toString('base64')}`
const statsSource = await readFile(new URL('../src/features/candidates/data-bank-stats.ts', import.meta.url), 'utf8')
const statsJs = ts.transpileModule(statsSource, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText.replace("'./data-bank-pools'", `'${poolsUrl}'`)
const stats = await import(`data:text/javascript;base64,${Buffer.from(statsJs).toString('base64')}`)
const pools = await import(poolsUrl)

// Exercise the real filter component without loading its CSS or unrelated column dialogs.
const columnSource = await readFile(new URL('../src/features/candidates/components/data-bank-columns.tsx', import.meta.url), 'utf8')
const columnTree = ts.createSourceFile('columns.tsx', columnSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const sourceLabel = columnTree.statements.find(statement => ts.isVariableStatement(statement)
  && statement.declarationList.declarations.some(declaration => declaration.name.getText(columnTree) === 'SOURCE_LABEL'))
const labels = {}
new Function('exports', ts.transpileModule(sourceLabel.getText(columnTree), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText)(labels)
const filterSource = await readFile(new URL('../src/features/candidates/components/data-bank-filters.tsx', import.meta.url), 'utf8')
const filterTree = ts.createSourceFile('filters.tsx', filterSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const filterJs = ts.transpileModule(filterTree.statements.filter(statement => !ts.isImportDeclaration(statement))
  .map(statement => statement.getText(filterTree)).join('\n'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
}).outputText
const components = {}
const bindings = { ...stats, ...labels, PRD_COLUMNS: pools.PRD_COLUMNS, TRAVEL_CHOICES: pools.TRAVEL_CHOICES, parseTravel: pools.parseTravel, ChevronDown: 'svg', X: 'svg',
  Select: 'select', Field: 'label', Input: 'input', cn: (...values) => values.filter(Boolean).join(' '),
  stageByNo: number => ({ title: `Workflow ${number}` }) }
new Function('exports', 'React', ...Object.keys(bindings), filterJs)(components, React, ...Object.values(bindings))
const noop = () => {}
const filterProps = (overrides = {}) => ({
  me: { id: 'u1', role: 'staff' }, filters: {}, onFilter: noop, pool: 'all', onPool: noop,
  staff: '', onStaff: noop, travel: '', onTravel: noop, users: [], skills: [], onSkills: noop, skillMode: 'all', onSkillMode: noop,
  skillStatus: 'all', onSkillStatus: noop, coverage: { data: null, loading: false, error: '' },
  moreOpen: false, onMoreToggle: noop, canClear: false, onClear: noop, ...overrides,
})
const descendants = node => React.Children.toArray(node).flatMap(child => React.isValidElement(child)
  ? [child, ...descendants(child.props.children)] : [])
const findLabel = (tree, label) => descendants(tree).find(node => node.props['aria-label'] === label)

test('source is visible in the main filter bar and preserves the backend source values', () => {
  const changes = []
  const tree = components.DataBankFilters(filterProps({ filters: { source: 'Walk-in' }, onFilter: (...args) => changes.push(args) }))
  const bar = descendants(tree).find(node => node.props.className === 'dbt-bar')
  const source = findLabel(bar, 'Filter by Source')
  assert.ok(source)
  assert.equal(source.props.value, 'Walk-in')
  assert.deepEqual(descendants(source).filter(node => node.type === 'option').map(node => node.props.value),
    ['', 'Email', 'WhatsApp', 'Walk-in', 'Referral', 'Other'])
  source.props.onChange({ target: { value: 'Referral' } })
  assert.deepEqual(changes, [['source', 'Referral']])
  assert.equal(findLabel(bar, 'Filter by Stage'), undefined)
  assert.ok(findLabel(bar, 'Filter by Status'))
  assert.equal(descendants(tree).filter(node => node.props['aria-label'] === 'Filter by Source').length, 1)
  assert.equal(findLabel(tree, 'Some of these filters are active'), undefined)
})

test('an existing stage selection remains available in More filters and marks hidden filters active', () => {
  const changes = []
  const tree = components.DataBankFilters(filterProps({ filters: { stage: '11' }, onFilter: (...args) => changes.push(args) }))
  const more = descendants(tree).find(node => node.props.id === 'db-more-filters')
  const stage = findLabel(more, 'Filter by Stage')
  assert.equal(more.props.hidden, true)
  assert.equal(stage.props.value, '11')
  assert.ok(findLabel(tree, 'Some of these filters are active'))
  stage.props.onChange({ target: { value: '' } })
  assert.deepEqual(changes, [['stage', '']])
})

test('Travel is a select in the main filter bar for every role, with Any travel / Travelled / Not travelled', () => {
  for (const role of ['staff', 'manager', 'admin']) {
    const changes = []
    const tree = components.DataBankFilters(filterProps({ me: { id: 'u1', role }, travel: 'no', onTravel: value => changes.push(value) }))
    const bar = descendants(tree).find(node => node.props.className === 'dbt-bar')
    const travel = findLabel(bar, 'Filter by travel')
    assert.ok(travel, role)
    assert.equal(travel.props.value, 'no')
    assert.deepEqual(descendants(travel).filter(node => node.type === 'option').map(node => [node.props.value, node.props.children]),
      [['', 'Any travel'], ['yes', 'Travelled'], ['no', 'Not travelled']])
    travel.props.onChange({ target: { value: 'yes' } })
    travel.props.onChange({ target: { value: 'nonsense' } })   // a value the select never offers reads as no filter
    assert.deepEqual(changes, ['yes', ''])
  }
})

const counts = { all: 4, unassigned: 3, needs_review: 0 }

test('admin and manager see Resumes, Not assigned and Needs review; staff see no Not assigned card', () => {
  for (const role of ['admin', 'manager']) {
    assert.deepEqual(stats.statCards(counts, 'all', role).map(card => card.label), ['Resumes', 'Not assigned', 'Needs review'])
  }
  assert.deepEqual(stats.statCards(counts, 'all', 'staff').map(card => card.label), ['Resumes', 'Needs review'])
  assert.deepEqual(stats.statCards(counts, 'all', undefined).map(card => card.key), ['resumes', 'needs_review'])
})

test('the old Jobs, No order yet, Not interested and Deployed cards are gone', () => {
  const labels = stats.statCards({ all: 9, unassigned: 1, needs_review: 1 }, 'all', 'admin').map(card => card.label)
  for (const gone of ['Jobs', 'No order yet', 'Not interested', 'Deployed']) assert.ok(!labels.includes(gone), gone)
})

test('a card is active for its own pool and a zero reads muted unless it is the active filter', () => {
  const cards = stats.statCards(counts, 'all', 'admin')
  assert.deepEqual(cards.map(card => card.active), [true, false, false])
  assert.deepEqual(cards.map(card => card.muted), [false, false, true])
  const review = stats.statCards(counts, 'needs_review', 'admin')
  assert.equal(review[2].active, true)
  assert.equal(review[2].muted, false)
  assert.equal(review[0].active, false)
  assert.equal(stats.statCards({ all: 4, unassigned: 0, needs_review: 0 }, 'all', 'admin')[1].muted, true)
})

test('pressing Resumes shows everyone; pressing another card toggles its pool', () => {
  const [resumes, unassigned, review] = stats.statCards(counts, 'all', 'admin')
  assert.equal(stats.poolAfterCardPress('needs_review', resumes), 'all')
  assert.equal(stats.poolAfterCardPress('all', unassigned), 'unassigned')
  assert.equal(stats.poolAfterCardPress('unassigned', unassigned), 'all')
  assert.equal(stats.poolAfterCardPress('unassigned', review), 'needs_review')
})

test('the visible filters (Travel for everyone) follow the role and Staff is only for admin and manager', () => {
  assert.deepEqual(stats.visibleFilters('admin'), ['scope', 'source', 'status', 'travel', 'staff'])
  assert.deepEqual(stats.visibleFilters('manager'), ['scope', 'source', 'status', 'travel', 'staff'])
  assert.deepEqual(stats.visibleFilters('staff'), ['scope', 'source', 'status', 'travel'])
  assert.deepEqual([...stats.MORE_FILTER_SELECTS], ['pool', 'stage', 'passport'])
})

test('Everyone / My candidates maps to the staff filter', () => {
  assert.equal(stats.scopeValue('', 'u1'), 'everyone')
  assert.equal(stats.scopeValue('u1', 'u1'), 'mine')
  assert.equal(stats.scopeValue('u2', 'u1'), 'other')
  assert.equal(stats.staffForScope('mine', 'u1'), 'u1')
  assert.equal(stats.staffForScope('everyone', 'u1'), '')
})

test('the list choices offer Not assigned only to those who can assign, or while it is the active list', () => {
  const names = (role, current) => stats.poolChoices(role, current).map(([value]) => value)
  assert.deepEqual(names('admin', 'all'), ['all', 'unmapped', 'unassigned', 'needs_review', 'not_interested'])
  assert.deepEqual(names('staff', 'all'), ['all', 'unmapped', 'needs_review', 'not_interested'])
  assert.ok(names('staff', 'unassigned').includes('unassigned'))
})
