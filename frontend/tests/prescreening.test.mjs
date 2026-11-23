import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
const load = async (path) => {
  const js = ts.transpileModule(await read(path), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const pre = await load('features/application-form/prescreening.ts')

test('Prescreening needs no client or job: save, decline and reject bodies never carry one', () => {
  for (const action of ['save', 'decline', 'reject']) {
    assert.deepEqual(pre.prescreenBody(action, '  spoke to him  ', 'order-1'), { action, feedback: 'spoke to him' }, action)
  }
  assert.deepEqual(pre.prescreenBody('decline', '   '), { action: 'decline' }, 'a reason is optional')
})

test('only "move to next stage" carries the job, and it is blocked until client, job AND feedback are there (client + job from stage 2)', () => {
  assert.deepEqual(pre.prescreenBody('forward', 'Passed.', 'order-1'), { action: 'forward', feedback: 'Passed.', orderId: 'order-1' })
  assert.equal(pre.forwardBlocker({ clientId: '', orderId: '', feedback: 'x' }), pre.STAGE_2_LOCK_TEXT)
  assert.equal(pre.forwardBlocker({ clientId: 'c1', orderId: '', feedback: 'x' }), pre.STAGE_2_LOCK_TEXT, 'a client alone is not enough: the job is what matters')
  assert.equal(pre.forwardBlocker({ clientId: 'c1', orderId: 'o1', feedback: '   ' }), pre.NEED_FEEDBACK)
  assert.equal(pre.forwardBlocker({ clientId: 'c1', orderId: 'o1', feedback: 'Passed.' }), '', 'ready')
  assert.match(pre.STAGE_2_LOCK_TEXT, /required from stage 2/)
})

test('the job list of the chosen client puts available jobs first and keeps the unavailable ones, with their reason', () => {
  const clients = [{ id: 'c1', jobs: [
    { orderId: 'o1', ref: 'A', title: 'Filled job', openings: 0, status: 'filled' },
    { orderId: 'o2', ref: 'B', title: 'Open job', openings: 3, status: 'open' },
    { orderId: 'o3', ref: 'C', title: 'Held job', openings: 2, status: 'on_hold' },
    { orderId: 'o4', ref: 'D', title: 'Another open job', openings: 1, status: 'open' },
  ] }, { id: 'c2', jobs: [] }]
  const reason = (job) => (job.status === 'open' ? '' : job.status)
  assert.deepEqual(pre.jobChoices(clients, 'c1', reason).map((row) => [row.job.orderId, row.reason]), [['o2', ''], ['o4', ''], ['o1', 'filled'], ['o3', 'on_hold']])
  assert.deepEqual(pre.jobChoices(clients, 'c2', reason), [])
  assert.deepEqual(pre.jobChoices(clients, 'nobody', reason), [])
})

test('the chip and the line say where the candidate is, and name the tab for a decided candidate', () => {
  assert.deepEqual(pre.prescreenChip('not_started'), { label: 'Not started', tone: 'idle' })
  assert.deepEqual(pre.prescreenChip('in_progress'), { label: 'In progress', tone: 'busy' })
  assert.deepEqual(pre.prescreenChip('declined'), { label: 'Declined', tone: 'bad' })
  assert.deepEqual(pre.prescreenChip('rejected'), { label: 'Rejected', tone: 'bad' })
  assert.match(pre.prescreenLine('declined'), /Declined tab/)
  assert.match(pre.prescreenLine('rejected'), /Rejected tab/)
  assert.match(pre.prescreenLine('not_started'), /needs no client or job/i)
  assert.doesNotMatch(['not_started', 'in_progress', 'declined', 'rejected', 'on_job'].map(pre.prescreenLine).join(' '), /Data Bank/)
})

test('a failure shows the server sentence, or a plain one when the connection failed', () => {
  assert.equal(pre.prescreenFailure('Open this order before matching candidates.', 'forward'), 'Open this order before matching candidates.')
  assert.match(pre.prescreenFailure('Failed to fetch', 'forward'), /could not be moved to the next stage/)
  assert.match(pre.prescreenFailure('', 'save'), /could not be saved/)
})

test('with no job the stage grid opens Prescreening (stage 1) and keeps stages 2-13 locked; with a job the rows are untouched', () => {
  const rows = Array.from({ length: 13 }, (_, index) => ({ stage: index + 1, name: `S${index + 1}`, key: 'locked', icon: 'lock', chip: 'Locked', date: '', completedBy: '', documents: [], readiness: null, openable: false }))
  const open = pre.prescreenRows(rows, true)
  assert.deepEqual([open[0].key, open[0].chip, open[0].openable], ['current', 'In progress', false])
  assert.ok(open.slice(1).every((row) => row.key === 'locked' && row.chip === 'Locked'))
  assert.equal(pre.prescreenRows(rows, false), rows)
  // after a decline or a rejection the stage-1 tile reads Failed (the same thing the panel says), the rest stay locked
  for (const ended of ['declined', 'rejected']) {
    const done = pre.prescreenRows(rows, true, ended)
    assert.deepEqual([done[0].key, done[0].chip, done[0].openable], ['failed', 'Failed', false], ended)
    assert.ok(done.slice(1).every((row) => row.key === 'locked'))
  }
  assert.equal(pre.prescreenRows(rows, true, 'in_progress')[0].chip, 'In progress')
})

test('the form shows the Prescreening panel when there is no job, wires "Move to next stage" to the server rule and refreshes live', async () => {
  const form = await read('screens/candidates/application-form.tsx')
  assert.match(form, /\{!application && <PrescreeningPanel candidateId=\{id\} canWork=\{canWork\} clients=\{form\.assignment\.options\.clients\}/)
  assert.match(form, /prescreenOpen=\{!application\}/)
  assert.doesNotMatch(form, /Choose a job to begin/, 'the dead-end "choose a job to begin" box is gone')
  const panel = await read('features/application-form/components/prescreening-panel.tsx')
  assert.match(panel, /\/prescreen`/, 'every action goes through the one prescreen endpoint')
  for (const text of ['Save feedback', 'Not interested (decline)', 'Reject (not suitable)', 'Move to next stage']) assert.ok(panel.includes(text), text)
  assert.match(panel, /forwardBlocker\(\{ clientId, orderId, feedback \}\)/, 'Move to next stage is disabled until client, job and feedback are there')
  assert.match(panel, /liveInterval\(load, 20_000, \['candidates', 'applications', 'tasks'\]/, 'a colleague\'s change shows without a refresh')
  assert.match(panel, /typed\.current/, 'a live read never overwrites what the person is typing')
  assert.doesNotMatch(panel, /Data Bank/)
  const stages = await read('features/application-form/components/recruitment-stages.tsx')
  assert.match(stages, /prescreenRows\(stageRows\(form, canOpen\), prescreenOpen && !form\.application, prescreenState\)/)
  assert.match(form, /onState=\{setPrescreenState\}/, 'the stage list follows what the panel read')
})
