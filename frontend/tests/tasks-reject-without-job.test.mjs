import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import test from 'node:test'

const src = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('a candidate with no job can be declined or rejected from the Tasks row menu, without choosing a client or job', async () => {
  const table = await src('screens/tasks/task-table.tsx')
  assert.match(table, /!row\.applicationId && !closed && work \? \[/)
  assert.match(table, /label: 'Not interested \(declined\)'[^\n]*interested: 'no'/)
  assert.match(table, /label: 'Rejected \(not suitable\)'[^\n]*interested: 'rejected'/)
  assert.match(table, /<CallDialog open=\{!!calling\}[^\n]*initialInterested=\{calling\?\.interested \?\? ''\}/)
})

test('the call dialog offers Rejected, says no client or job is needed, and never says the candidate goes back to the Data Bank', async () => {
  const dialog = await src('features/candidates/components/call-dialog.tsx')
  assert.match(dialog, /value: 'rejected', label: 'Rejected \(not suitable\)'/)
  assert.match(dialog, /No client or job is needed here/)
  for (const file of ['features/candidates/components/call-dialog.tsx', 'shared/state/crm-store.tsx', 'features/workflow/components/stage-modal-body.tsx', 'features/jobs/components/delete-job-dialog.tsx']) {
    assert.doesNotMatch(await src(file), /go(es)? back to the Data Bank|back in the Data Bank|stays in the Data Bank/, file)
  }
})

test('the form explains the stage rule and does not call a job swap an application that was rejected', async () => {
  assert.match(await src('features/application-form/process-stages.ts'), /Prescreening needs no client or job\./)
  const stages = await src('features/application-form/components/recruitment-stages.tsx')
  assert.match(stages, /'Removed from this job'/)
  assert.match(stages, /\['unmapped', 'changed_job'\]\.includes/)
})

test('the profile call outcome knows the Rejected answer', async () => {
  assert.match(await src('shared/model/types.ts'), /interested: 'yes' \| 'no' \| 'unreachable' \| 'rejected'/)
  assert.match(await src('features/candidates/activity.ts'), /'Initial call: rejected'/)
})

const sources = async (dir = new URL('../src/', import.meta.url)) => {
  const found = []
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const next = new URL(entry.name + (entry.isDirectory() ? '/' : ''), dir)
    if (entry.isDirectory()) found.push(...await sources(next))
    else if (/\.(ts|tsx)$/.test(entry.name)) found.push(next)
  }
  return found
}

test('no screen anywhere tells people a rejected, declined or removed candidate "goes back to the Data Bank" (they stay visible on their tab and in All)', async () => {
  const offenders = []
  for (const file of await sources()) {
    const text = await readFile(file, 'utf8')
    if (/go(es)? back to the Data Bank|moved back to the Data Bank|back in the Data Bank|returns? to the Data Bank/.test(text)) offenders.push(file.pathname.split('/src/')[1])
  }
  assert.deepEqual(offenders, [], 'the outcome phrase is gone from every file (delete-user / delete-dialog talk about ownership, not outcomes)')
})
