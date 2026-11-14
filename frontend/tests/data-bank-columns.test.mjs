import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const pools = await load('../src/features/candidates/data-bank-pools.ts')
const assignment = await load('../src/features/candidates/data-bank-assignment.ts')
const { candidateReviewIssues } = await load('../src/features/candidates/candidate-review.ts')

// Exercise the real status renderer without loading the unrelated store, router and dialogs in this column module.
const columnSource = await readFile(new URL('../src/features/candidates/components/data-bank-columns.tsx', import.meta.url), 'utf8')
const columnTree = ts.createSourceFile('data-bank-columns.tsx', columnSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const statusSource = columnTree.statements.filter(statement =>
  ts.isVariableStatement(statement) && statement.declarationList.declarations.some(declaration => declaration.name.getText(columnTree) === 'STATUS_VIEW')
  || ts.isFunctionDeclaration(statement) && ['candidateStatus', 'StatusText'].includes(statement.name?.text),
).map(statement => statement.getText(columnTree)).join('\n')
const statusJs = ts.transpileModule(statusSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
}).outputText
const status = {}
new Function('exports', 'React', 'StatusDot', 'candidateReviewIssues', statusJs)(status, React,
  ({ tone, title, children }) => React.createElement('span', { 'data-tone': tone, title }, children), candidateReviewIssues)
const renderStatus = candidate => renderToStaticMarkup(React.createElement(status.StatusText, { candidate: { name: 'Test Candidate', ownerId: 'recruiter', ...candidate } }))

// Render the real shared assignment cells used by table rows, cards and the field registry.
const placementSource = columnTree.statements.filter(statement =>
  ts.isFunctionDeclaration(statement) && ['bankContext', 'formOrderId', 'StageCell'].includes(statement.name?.text),
).map(statement => statement.getText(columnTree)).join('\n')
const placementJs = ts.transpileModule(placementSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
}).outputText
const placement = {}
new Function('exports', 'React', 'bankApplication', 'bankFormApplication', 'stageByNo', 'StageChip', 'Dash', placementJs)(
  placement, React, assignment.bankApplication, assignment.bankFormApplication,
  stage => ({ title: stage === 11 ? 'Deployed' : `Stage ${stage}` }),
  ({ stage }) => React.createElement('span', { 'data-stage': stage }, `Stage ${stage}`),
  () => React.createElement('span', { 'aria-label': 'None' }, 'None'),
)
const placementStore = applications => ({
  applicationsFor: () => applications,
  activeApplicationFor: () => { throw new Error('Display must include deployed placements') },
  order: orderId => ({ ref: `REF-${orderId}` }),
  orderLabel: orderId => `Job ${orderId}`,
})

test('deployed stage, order fields and form navigation agree in Data Bank', () => {
  const candidate = { id: 'candidate-deployed' }
  const deployed = { id: 'deployment', orderId: 'assigned', currentStage: 11, status: 'deployed', createdAt: '2026-10-01T00:00:00Z' }
  const store = placementStore([deployed])
  assert.deepEqual(placement.bankContext(store).application(candidate.id), { stage: 'Deployed', order: 'REF-assigned' })
  assert.equal(placement.formOrderId(store, candidate.id), 'assigned')
  const opened = []
  const cell = placement.StageCell({ candidate, store, onOpenStage: id => opened.push(id) })
  const html = renderToStaticMarkup(cell)
  assert.match(html, /data-stage="11"/)
  assert.match(html, /See the 11 stages for Job assigned/)
  cell.props.onClick({ stopPropagation() {} })
  assert.deepEqual(opened, ['deployment'])
})

test('a candidate with no current or deployed placement keeps blank stage and order fields', () => {
  const store = placementStore([{ id: 'closed', orderId: 'past', currentStage: 3, status: 'closed', createdAt: '2026-10-01T00:00:00Z' }])
  assert.equal(placement.bankContext(store).application('candidate'), null)
  assert.match(renderToStaticMarkup(placement.StageCell({ candidate: { id: 'candidate' }, store })), /aria-label="None"/)
})

test('the Status cell is only the status chip, never a profile-issue link', () => {
  for (const candidate of [{ status: 'deployed', needsReview: true, phone: '  ' }, { status: 'in_process', needsReview: true, phone: '9999999999' },
    { status: 'needs_review', needsReview: true, phone: '', name: 'Name needs review' }]) {
    const html = renderStatus(candidate)
    assert.equal((html.match(/data-tone=/g) ?? []).length, 1)
    assert.doesNotMatch(html, /issue|<button|Needs review|Phone number missing/)
  }
  assert.deepEqual(status.candidateStatus({ status: 'deployed' }), { label: 'Deployed', tone: 'ok' })
  assert.deepEqual(status.candidateStatus({ status: 'in_process' }), { label: 'In process', tone: 'info' })
  assert.match(renderStatus({ status: 'deployed', needsReview: false, phone: '' }), /^<span[^>]*>Deployed<\/span>$/)
})

test('a needs-review candidate reads as New without a recruiter and In process with one', () => {
  assert.deepEqual(status.candidateStatus({ status: 'needs_review', ownerId: null }), { label: 'New', tone: 'info' })
  assert.deepEqual(status.candidateStatus({ status: 'needs_review', ownerId: '' }), { label: 'New', tone: 'info' })
  assert.deepEqual(status.candidateStatus({ status: 'needs_review', ownerId: 'recruiter' }), { label: 'In process', tone: 'info' })
  assert.match(renderStatus({ status: 'needs_review', ownerId: null }), /data-tone="info"[^>]*>New<\/span>/)
  assert.match(renderStatus({ status: 'needs_review', ownerId: 'recruiter' }), />In process<\/span>/)
  assert.match(renderStatus({ status: 'new', ownerId: null }), />New<\/span>/)
})

test('all known profile gaps keep exact reasons and the relevant staff action for the profile screens', () => {
  const candidate = { status: 'needs_review', name: 'Name needs review', phone: ' ', ownerId: null }
  const issues = candidateReviewIssues(candidate)
  assert.deepEqual(issues.map(issue => issue.label), ['Name missing or unreadable', 'Phone number missing', 'Recruiter not assigned'])
  assert.match(issues.find(issue => issue.key === 'recruiter').action, /Use Assign recruiter/)
})

test('fixing a field removes that reason; explicit profile review remains pending until saved', () => {
  const candidate = { status: 'deployed', name: 'Candidate', ownerId: 'recruiter', phone: '', needsReview: true }
  assert.deepEqual(candidateReviewIssues(candidate).map(issue => issue.key), ['phone'])
  assert.deepEqual(candidateReviewIssues({ ...candidate, phone: '9876543210' }).map(issue => issue.key), ['review'])
  assert.deepEqual(candidateReviewIssues({ ...candidate, phone: '9876543210', needsReview: false }), [])
})

test('the Data Bank lists the eleven PRD columns in the specified order', () => {
  assert.deepEqual(pools.PRD_COLUMNS.map((column) => column.label), [
    'Name', 'Phone', 'Job title', 'Experience', 'Passport', 'Source', 'Recruiter', 'Order', 'Stage', 'Status', 'Date added',
  ])
})

test('every PRD column key is a column the server can filter and sort on', async () => {
  const server = await readFile(new URL('../../backend/app/services/candidate_search.py', import.meta.url), 'utf8')
  for (const { key } of pools.PRD_COLUMNS) assert.match(server, new RegExp(`"${key}":`), `server has no "${key}" column`)
})

test('the filter count ignores blank column filters and counts staff, skills and coverage', () => {
  assert.equal(pools.activeFilterCount({}, '', [], 'all'), 0)
  assert.equal(pools.activeFilterCount({ name: '  ', source: 'Email' }, '', [], 'all'), 1)
  assert.equal(pools.activeFilterCount({ passport: 'Yes', stage: '3' }, 'u2', ['tig', 'mig'], 'all'), 5)
  assert.equal(pools.activeFilterCount({}, '', [], 'missing'), 1)
})

const trends = await load('../src/features/candidates/data-bank-trends.ts')

test('arrival trend compares the same span of last month and never invents a baseline', () => {
  const now = new Date(2026, 9, 10, 12) // 10 Oct 2026, noon
  const day = (month, d, hour = 9) => new Date(2026, month, d, hour).toISOString()
  // this month: 3 arrivals (1, 5, 9 Oct); last month: 2 inside the first 10 days (2, 8 Sep) plus 1 on 20 Sep (outside the span)
  const dates = [day(9, 1), day(9, 5), day(9, 9), day(8, 2), day(8, 8), day(8, 20), undefined, 'not a date']
  const trend = trends.arrivalTrend(dates, now)
  assert.deepEqual({ current: trend.current, previous: trend.previous }, { current: 3, previous: 2 })
  assert.equal(trend.pct, 50)
  assert.equal(trends.arrivalTrend([day(9, 2)], now).pct, null)
  assert.equal(trends.trendCaption(trends.arrivalTrend([day(9, 2)], now)).rest, 'new this month')
  assert.equal(trends.trendCaption(trends.arrivalTrend([], now)).tone, 'quiet')
  assert.equal(trends.trendCaption(trend).lead, '↑ 50.0%')
})

test('weekly arrivals bucket the last twelve weeks, oldest first', () => {
  const now = new Date(2026, 9, 10, 12)
  const ago = (days) => new Date(now.getTime() - days * 86400000).toISOString()
  const weeks = trends.weeklyArrivals([ago(1), ago(2), ago(8), ago(200)], 12, now)
  assert.equal(weeks.length, 12)
  assert.equal(weeks[11], 2)
  assert.equal(weeks[10], 1)
  assert.equal(weeks.reduce((a, b) => a + b, 0), 3)
})
