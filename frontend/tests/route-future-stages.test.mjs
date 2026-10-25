import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { routeStates, routeSummary, upcomingText } = await load('../src/shared/ui/v2/route-logic.ts')
const { openInitialCallTask, matchDoneMessage, FIRST_CALL_CLOSED_MESSAGE } = await load('../src/features/candidates/initial-call.ts')

const stages = Array.from({ length: 11 }, (_, index) => ({ no: index + 1, title: `Secret title ${index + 1}` }))

test('a route at stage 3 names only the current stage and counts the rest', () => {
  const states = routeStates(stages, 3, [1, 2])
  assert.equal(upcomingText(states), '8 more steps')
  const text = routeSummary('Placement route', stages, states, true)
  assert.match(text, /stage 3 of 11, Secret title 3, 8 more steps/)
  for (let no = 4; no <= 11; no += 1) assert.ok(!text.includes(`Secret title ${no}`))
})

test('the summary is unchanged when upcoming stages are not hidden', () => {
  const states = routeStates(stages, 3, [1, 2])
  assert.equal(routeSummary('Placement route', stages, states), 'Placement route: stage 3 of 11, Secret title 3')
})

test('one step left is singular; a finished route has none', () => {
  assert.equal(upcomingText(routeStates(stages, 10, [1, 2, 3, 4, 5, 6, 7, 8, 9])), '1 more step')
  assert.equal(upcomingText(routeStates(stages, undefined, 11)), '')
  assert.equal(routeSummary('Route', stages, routeStates(stages, undefined, 11), true), 'Route: 11 of 11 stages done')
})

test('a closed route does not name the stages after the one it ended on', () => {
  const states = routeStates(stages, 4, [1, 2, 3], true)
  const text = routeSummary('Route', stages, states, true)
  assert.match(text, /stage 4 of 11, Secret title 4 \(ended here\), 7 more steps/)
  assert.ok(!text.includes('Secret title 5'))
})

test('only a first-call task that is still open counts', () => {
  const tasks = [
    { id: 'a', kind: 'initial_call', candidateId: 'c1', completedAt: '2026-01-01' },
    { id: 'b', kind: 'initial_call', candidateId: 'c1', cancelledAt: '2026-01-01' },
    { id: 'c', kind: 'stage', candidateId: 'c1' },
    { id: 'd', kind: 'initial_call', candidateId: 'c2' },
  ]
  assert.equal(openInitialCallTask(tasks, 'c1'), undefined)
  assert.equal(openInitialCallTask(tasks, 'c2').id, 'd')
  assert.equal(openInitialCallTask([...tasks, { id: 'e', kind: 'initial_call', candidateId: 'c1' }], 'c1').id, 'e')
})

test('the match toast mentions the closed first-call task only when the server says so', () => {
  assert.equal(matchDoneMessage(false), 'Mapped. Stage 1 task raised.')
  assert.equal(matchDoneMessage(undefined), 'Mapped. Stage 1 task raised.')
  assert.ok(matchDoneMessage(true).endsWith(FIRST_CALL_CLOSED_MESSAGE))
})
