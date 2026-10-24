import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = (await readFile(new URL('../src/features/jobs/order-counts.ts', import.meta.url), 'utf8')).replace(/^import .*$/gm, '')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { orderCounts, isMatched, isSelected, isDeployed, SELECTED_PAST_STAGE } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const app = (status, currentStage) => ({ status, currentStage })

test('matched means still active on the order; closed and deployed applications are not matched', () => {
  assert.equal(isMatched(app('active', 1)), true)
  assert.equal(isMatched(app('closed', 3)), false)
  assert.equal(isMatched(app('deployed', 11)), false)
})

test('selected means active and past stage 4 (Interview result)', () => {
  assert.equal(SELECTED_PAST_STAGE, 4)
  assert.equal(isSelected(app('active', 4)), false)
  assert.equal(isSelected(app('active', 5)), true)
  assert.equal(isSelected(app('closed', 9)), false)
  assert.equal(isSelected(app('deployed', 11)), false)
})

test('deployed means the application finished', () => {
  assert.equal(isDeployed(app('deployed', 11)), true)
  assert.equal(isDeployed(app('active', 11)), false)
})

test('every screen gets identical numbers from one helper, and closed applications never count', () => {
  const apps = [app('active', 1), app('active', 4), app('active', 5), app('active', 9), app('deployed', 11), app('closed', 2), app('closed', 7)]
  assert.deepEqual(orderCounts(apps), { matched: 4, selected: 2, deployed: 1 })
  assert.deepEqual(orderCounts([]), { matched: 0, selected: 0, deployed: 0 })
})
