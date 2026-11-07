import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { duplicateFromSaveError, finishSavedCandidate, freshWorkspace } = await loadSource('../src/features/candidates/save-flow.ts')

/** A promise the test settles by hand. */
function gate() {
  let release, fail
  const promise = new Promise((resolve, reject) => { release = resolve; fail = reject })
  return { promise, release, fail }
}
const tick = () => new Promise((resolve) => setImmediate(resolve))

test('a refused duplicate save opens the decision dialog from the 409 and nothing else does', () => {
  const match = { id: 'c1', ref: 'CND-1', matchedOn: ['phone number'] }
  assert.equal(duplicateFromSaveError({ status: 409, detail: { message: 'Duplicate found', candidate: match } }), match)
  assert.equal(duplicateFromSaveError({ status: 409, detail: 'The phone, email or passport belong to different candidates.' }), null)
  assert.equal(duplicateFromSaveError({ status: 409, detail: { splitSuggested: true } }), null)
  assert.equal(duplicateFromSaveError({ status: 422, detail: { candidate: match } }), null)
  assert.equal(duplicateFromSaveError(new Error('offline')), null)
  assert.equal(duplicateFromSaveError(undefined), null)
})

test('the draft and the attachments run together, and the saved screen does not wait for the workspace refresh', async () => {
  const log = []
  const draft = gate(), attach = gate(), refresh = gate()
  let done = false
  const finishing = finishSavedCandidate({
    saveDraft: () => { log.push('draft'); return draft.promise },
    attach: () => { log.push('attach'); return attach.promise },
    refresh: () => { log.push('refresh'); return refresh.promise },
  }).then((outcome) => { done = true; return outcome })
  await tick()
  assert.deepEqual(log, ['draft', 'attach'], 'both start at once, the refresh waits for them')
  draft.release(); await tick()
  assert.equal(done, false, 'still attaching')
  attach.release({}); const outcome = await finishing
  assert.deepEqual(outcome.failed, {})
  await tick()
  assert.equal(log.at(-1), 'refresh', 'the refresh is started once the writes are done')
  let settled = false
  void outcome.refreshed.then(() => { settled = true })
  await tick()
  assert.equal(settled, false, 'the caller already has its answer while the refresh is still running')
  refresh.release(); assert.equal(await outcome.refreshed, true)
})

test('a failed draft save never stops the files from being attached, and failed files are reported with the refresh still made', async () => {
  let refreshed = 0
  const outcome = await finishSavedCandidate({
    saveDraft: () => Promise.reject(new Error('draft refused')),
    attach: async () => ({ resume: 'cv.pdf: could not be added' }),
    refresh: async () => { refreshed += 1 },
  })
  assert.deepEqual(outcome.failed, { resume: 'cv.pdf: could not be added' })
  assert.equal(await outcome.refreshed, true)
  assert.equal(refreshed, 1)
})

test('a failed refresh is reported as false and never throws into the save', async () => {
  const outcome = await finishSavedCandidate({
    saveDraft: async () => undefined, attach: async () => ({}), refresh: () => Promise.reject(new Error('timeout')),
  })
  assert.equal(await outcome.refreshed, false)
})

test('opening the profile reuses a refresh that worked and starts a new one only when there was none or it failed', async () => {
  let started = 0
  const refresh = async () => { started += 1 }
  await freshWorkspace(Promise.resolve(true), refresh)
  assert.equal(started, 0)
  await freshWorkspace(null, refresh)
  assert.equal(started, 1)
  await freshWorkspace(Promise.resolve(false), refresh)
  assert.equal(started, 2)
  await assert.rejects(freshWorkspace(Promise.resolve(false), () => Promise.reject(new Error('down'))), /down/)
  // A refresh still running is waited for, not repeated.
  const running = gate()
  let waited = false
  const reuse = freshWorkspace(running.promise, refresh).then(() => { waited = true })
  await tick(); assert.equal(waited, false)
  running.release(true); await reuse
  assert.equal(started, 2)
})
